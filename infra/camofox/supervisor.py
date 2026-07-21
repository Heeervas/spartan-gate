#!/usr/bin/env python3
"""Bounded PID-1 supervisor for Spartan Gate's Camofox service.

The supervisor deliberately owns only local process lifecycle and loopback
readiness. It never talks to Docker, inspects browser profiles, or logs request
data. The internal HTTP credential is passed by a private file path, never in a
process argument.
"""

from __future__ import annotations

import ctypes
import dataclasses
import datetime
import hashlib
import http.client
import json
import math
import os
import pathlib
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any


VERSION = 2
LEGACY_VERSION = 1
DEFAULT_SECRET_FILE = pathlib.Path("/run/spartan-camofox/internal.key")
DEFAULT_READINESS_FILE = pathlib.Path("/run/spartan-camofox/readiness.json")
DEFAULT_LEDGER_FILE = pathlib.Path("/var/lib/spartan-camofox-watchdog/ledger.json")
DEFAULT_CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")
DEFAULT_PROC_ROOT = pathlib.Path("/proc")
DEFAULT_BACKOFFS = (0, 30, 120, 600, 1800)

READY_STATES = frozenset(
    {"starting", "ready", "recovering", "backoff", "unhealthy", "stopping"}
)
CGROUP_STATES = frozenset({"ok", "unknown"})
SAFE_VALUE = re.compile(r"^[a-z0-9_.-]{1,64}$")
REVISION_VALUE = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 16 * 1024
MAX_DEEP_HEALTH_BYTES = 4096
MAX_SAFE_BROWSER_GENERATION = (1 << 53) - 1
DEEP_HEALTH_STAGES = frozenset(
    {
        "cleanup",
        "complete",
        "ensure_browser",
        "evaluate",
        "goto",
        "lifecycle",
        "lifecycle_settle",
        "new_context",
        "new_page",
    }
)
DEEP_HEALTH_FAILURE_CODES = frozenset(
    {
        "browser_lifecycle_degraded",
        "browser_lifecycle_invalidated",
        "browser_lifecycle_queue_timeout",
        "browser_lifecycle_recovering",
        "browser_lifecycle_stuck",
        "browser_navigation_degraded",
        "deep_probe_busy_stale",
        "deep_probe_cleanup_failed",
        "deep_probe_failed",
        "deep_probe_sentinel",
        "deep_probe_timeout",
    }
)


def supervisor_revision(path: pathlib.Path | None = None) -> str:
    source = pathlib.Path(__file__) if path is None else path
    return hashlib.sha256(source.read_bytes()).hexdigest()


SUPERVISOR_REVISION = supervisor_revision()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _private_directory(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("private runtime parent must be a directory")
    os.chmod(path, 0o700)


def _read_bounded(path: pathlib.Path, limit: int = MAX_JSON_BYTES) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid bounded file")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError("invalid bounded file")
        return data.decode("utf-8")
    finally:
        os.close(fd)


def _write_json_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    _private_directory(path.parent)
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("state payload too large")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@dataclasses.dataclass
class Ledger:
    recycle_count: int
    next_allowed_at: float
    healthy_since: float | None
    supervisor_revision: str

    @classmethod
    def clean(cls, revision: str = SUPERVISOR_REVISION) -> "Ledger":
        return cls(
            recycle_count=0,
            next_allowed_at=0.0,
            healthy_since=None,
            supervisor_revision=revision,
        )

    @classmethod
    def fail_closed(
        cls,
        now: float,
        backoffs: Sequence[int],
        revision: str = SUPERVISOR_REVISION,
    ) -> "Ledger":
        return cls(
            recycle_count=len(backoffs) - 1,
            next_allowed_at=now + backoffs[-1],
            healthy_since=None,
            supervisor_revision=revision,
        )

    def record_recycle(self, now: float, backoffs: Sequence[int]) -> int:
        index = min(self.recycle_count, len(backoffs) - 1)
        delay = int(backoffs[index])
        self.recycle_count = min(index + 1, len(backoffs) - 1)
        self.next_allowed_at = now + delay
        self.healthy_since = None
        return delay

    def force_maximum(self, now: float, backoffs: Sequence[int]) -> int:
        self.recycle_count = len(backoffs) - 1
        self.next_allowed_at = now + backoffs[-1]
        self.healthy_since = None
        return int(backoffs[-1])

    def mark_unhealthy(self) -> bool:
        changed = self.healthy_since is not None
        self.healthy_since = None
        return changed

    def mark_healthy(self, now: float, stable_reset_seconds: float) -> bool:
        if self.healthy_since is None:
            self.healthy_since = now
            return False
        if now - self.healthy_since < stable_reset_seconds:
            return False
        self.recycle_count = 0
        self.next_allowed_at = 0.0
        self.healthy_since = now
        return True

    def as_json(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "recycle_count": self.recycle_count,
            "next_allowed_at": self.next_allowed_at,
            "healthy_since": self.healthy_since,
            "supervisor_revision": self.supervisor_revision,
        }


def save_ledger(path: pathlib.Path, ledger: Ledger) -> None:
    _write_json_atomic(path, ledger.as_json())


def load_ledger(
    path: pathlib.Path,
    *,
    now: float,
    backoffs: Sequence[int],
    revision: str = SUPERVISOR_REVISION,
) -> Ledger:
    if REVISION_VALUE.fullmatch(revision) is None:
        raise ValueError("invalid supervisor revision")
    if not path.exists():
        return Ledger.clean(revision)
    normalized = False
    try:
        raw = json.loads(_read_bounded(path))
        if not isinstance(raw, dict) or raw.get("version") not in (LEGACY_VERSION, VERSION):
            raise ValueError("invalid ledger version")
        legacy = raw["version"] == LEGACY_VERSION
        expected = {"version", "recycle_count", "next_allowed_at", "healthy_since"}
        if legacy:
            if set(raw) != expected:
                raise ValueError("invalid legacy ledger")
            stored_revision = None
        else:
            if set(raw) != expected | {"supervisor_revision"}:
                raise ValueError("invalid ledger fields")
            stored_revision = raw.get("supervisor_revision")
            if not isinstance(stored_revision, str) or REVISION_VALUE.fullmatch(
                stored_revision
            ) is None:
                raise ValueError("invalid ledger revision")
        count = raw.get("recycle_count")
        next_allowed = raw.get("next_allowed_at")
        healthy_since = raw.get("healthy_since")
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count < len(backoffs):
            raise ValueError("invalid recycle count")
        if not _finite_number(next_allowed) or next_allowed < 0:
            raise ValueError("invalid ledger timestamp")
        if healthy_since is not None and (
            not _finite_number(healthy_since) or healthy_since < 0
        ):
            raise ValueError("invalid healthy timestamp")
        maximum_future = now + backoffs[-1]
        unsafe_normalized = False
        if next_allowed > maximum_future:
            count = len(backoffs) - 1
            next_allowed = maximum_future
            normalized = True
            unsafe_normalized = True
        if healthy_since is not None and healthy_since > now:
            healthy_since = now
            normalized = True
            unsafe_normalized = True
        if not unsafe_normalized and (legacy or stored_revision != revision):
            # Grant this runtime revision one attempt now, but retain the
            # accumulated failure level. If the attempt fails, record_recycle
            # immediately reapplies that level instead of restarting the
            # backoff ladder.
            ledger = Ledger(count, 0.0, None, revision)
            normalized = True
        else:
            ledger = Ledger(count, float(next_allowed), healthy_since, revision)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        ledger = Ledger.fail_closed(now, backoffs, revision)
        normalized = True
    if normalized:
        save_ledger(path, ledger)
    return ledger


def ensure_internal_secret(path: pathlib.Path) -> str:
    _private_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        token = secrets.token_hex(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, (token + "\n").encode("ascii"))
            os.fsync(fd)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return token
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("invalid internal secret file")
    os.chmod(path, 0o600)
    token = _read_bounded(path, 256).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("invalid internal secret")
    return token


@dataclasses.dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    pgrp: int
    state: str
    start_time: int


def read_process_info(pid: int, proc_root: pathlib.Path = DEFAULT_PROC_ROOT) -> ProcessInfo | None:
    try:
        content = _read_bounded(proc_root / str(pid) / "stat", 4096).strip()
        close = content.rfind(")")
        if close < 0:
            return None
        prefix = content[: content.find("(")].strip()
        fields = content[close + 1 :].strip().split()
        if int(prefix) != pid or len(fields) < 20:
            return None
        return ProcessInfo(
            pid=pid,
            state=fields[0],
            ppid=int(fields[1]),
            pgrp=int(fields[2]),
            start_time=int(fields[19]),
        )
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def scan_processes(proc_root: pathlib.Path = DEFAULT_PROC_ROOT) -> dict[int, ProcessInfo]:
    processes: dict[int, ProcessInfo] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return processes
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = read_process_info(int(entry.name), proc_root)
        if info is not None:
            processes[info.pid] = info
    return processes


def lifecycle_processes(
    root: ProcessInfo,
    pgid: int,
    proc_root: pathlib.Path = DEFAULT_PROC_ROOT,
) -> tuple[ProcessInfo, ...]:
    processes = scan_processes(proc_root)
    selected = {pid for pid, info in processes.items() if info.pgrp == pgid}
    selected.add(root.pid)
    changed = True
    while changed:
        changed = False
        for pid, info in processes.items():
            if info.ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return tuple(processes[pid] for pid in sorted(selected) if pid in processes)


def find_survivors(
    tracked: Sequence[ProcessInfo],
    pgid: int,
    proc_root: pathlib.Path = DEFAULT_PROC_ROOT,
) -> list[ProcessInfo]:
    processes = scan_processes(proc_root)
    identities = {(item.pid, item.start_time) for item in tracked}
    survivors = [
        info
        for info in processes.values()
        if info.pgrp == pgid or (info.pid, info.start_time) in identities
    ]
    return sorted(survivors, key=lambda item: item.pid)


def stop_process_group(
    pgid: int,
    *,
    term_grace: float,
    kill_grace: float,
    survivor_probe: Callable[[], list[ProcessInfo]],
    send_signal: Callable[[int, signal.Signals], None] = os.killpg,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[ProcessInfo]:
    try:
        send_signal(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = monotonic() + term_grace
    while True:
        survivors = survivor_probe()
        if not survivors:
            return []
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(0.25, remaining))

    try:
        send_signal(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = monotonic() + kill_grace
    while True:
        survivors = survivor_probe()
        if not survivors:
            return []
        remaining = deadline - monotonic()
        if remaining <= 0:
            return survivors
        sleep(min(0.25, remaining))


@dataclasses.dataclass(frozen=True)
class CgroupSample:
    status: str
    current: int | None
    maximum: int | None
    percent: float | None
    oom_kill: int | None
    oom_delta: int
    soft_trigger: bool
    hard_trigger: bool


class CgroupMonitor:
    def __init__(self, root: pathlib.Path, *, soft_percent: float, hard_percent: float):
        self.root = root
        self.soft_percent = soft_percent
        self.hard_percent = hard_percent
        self._oom_baseline: int | None = None
        self._hard_pressure_samples = 0

    def _integer(self, name: str) -> int:
        raw = _read_bounded(self.root / name, 4096).strip()
        if not raw.isascii() or not raw.isdigit():
            raise ValueError("invalid cgroup integer")
        value = int(raw)
        if value < 0 or value > 2**63 - 1:
            raise ValueError("invalid cgroup integer")
        return value

    def _oom_kill(self) -> int:
        raw = _read_bounded(self.root / "memory.events", 4096)
        values: dict[str, int] = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit():
                raise ValueError("invalid cgroup event")
            values[parts[0]] = int(parts[1])
        if "oom_kill" not in values or values["oom_kill"] > 2**63 - 1:
            raise ValueError("missing cgroup event")
        return values["oom_kill"]

    def sample(self) -> CgroupSample:
        current: int | None = None
        maximum: int | None = None
        percent: float | None = None
        oom_kill: int | None = None
        status = "ok"
        try:
            current = self._integer("memory.current")
            maximum_raw = _read_bounded(self.root / "memory.max", 4096).strip()
            if maximum_raw == "max":
                status = "unknown"
            elif maximum_raw.isascii() and maximum_raw.isdigit():
                maximum = int(maximum_raw)
                if maximum <= 0 or maximum > 2**63 - 1:
                    raise ValueError("invalid cgroup maximum")
                percent = current * 100.0 / maximum
                if not math.isfinite(percent) or percent < 0:
                    raise ValueError("invalid cgroup ratio")
            else:
                raise ValueError("invalid cgroup maximum")
        except (OSError, UnicodeError, ValueError):
            status = "unknown"
            current = None
            maximum = None
            percent = None
        try:
            oom_kill = self._oom_kill()
        except (OSError, UnicodeError, ValueError):
            status = "unknown"
            oom_kill = None

        oom_delta = 0
        if oom_kill is not None:
            if self._oom_baseline is None or oom_kill < self._oom_baseline:
                self._oom_baseline = oom_kill
            else:
                oom_delta = oom_kill - self._oom_baseline
                self._oom_baseline = oom_kill
        soft = percent is not None and percent >= self.soft_percent
        if percent is not None and percent >= self.hard_percent:
            self._hard_pressure_samples += 1
        else:
            self._hard_pressure_samples = 0
        hard = oom_delta > 0 or self._hard_pressure_samples >= 3
        return CgroupSample(
            status=status,
            current=current,
            maximum=maximum,
            percent=percent,
            oom_kill=oom_kill,
            oom_delta=oom_delta,
            soft_trigger=soft,
            hard_trigger=hard,
        )


@dataclasses.dataclass(frozen=True)
class DeepHealthResult:
    structured: bool
    healthy: bool
    code: str | None
    stage: str | None
    browser_generation: int | None

    def __bool__(self) -> bool:
        raise TypeError("DeepHealthResult must be checked through typed properties")

    @classmethod
    def ordinary_unhealthy(cls) -> "DeepHealthResult":
        return cls(
            structured=False,
            healthy=False,
            code=None,
            stage=None,
            browser_generation=None,
        )

    @property
    def hard_trigger(self) -> bool:
        return bool(
            self.structured
            and not self.healthy
            and self.code == "browser_navigation_degraded"
            and self.stage == "lifecycle"
            and self.browser_generation is not None
        )

    @property
    def recovery_deferred(self) -> bool:
        return bool(
            self.structured
            and not self.healthy
            and self.code == "deep_probe_busy_stale"
            and self.stage == "lifecycle"
            and self.browser_generation is not None
        )

    @property
    def real_ready(self) -> bool:
        return bool(
            self.structured
            and self.healthy
            and self.code == "ready"
            and self.stage == "complete"
            and self.browser_generation is not None
        )

    def log_fields(self) -> dict[str, Any]:
        if not self.structured:
            return {}
        return {
            "code": self.code,
            "stage": self.stage,
            "browser_generation": self.browser_generation,
        }


def _safe_browser_generation(value: Any) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SAFE_BROWSER_GENERATION
    )


def parse_deep_health_response(status: int, body: bytes) -> DeepHealthResult:
    fallback = DeepHealthResult.ordinary_unhealthy()
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or status not in {http.client.OK, http.client.SERVICE_UNAVAILABLE}
        or not isinstance(body, bytes)
        or len(body) > MAX_DEEP_HEALTH_BYTES
    ):
        return fallback
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    ok = payload.get("ok")
    code = payload.get("code")
    stage = payload.get("stage")
    generation = payload.get("browserGeneration")
    if (
        not isinstance(ok, bool)
        or not isinstance(code, str)
        or code not in DEEP_HEALTH_FAILURE_CODES | {"busy", "ready"}
        or not isinstance(stage, str)
        or stage not in DEEP_HEALTH_STAGES
        or not _safe_browser_generation(generation)
    ):
        return fallback
    if status != (http.client.OK if ok else http.client.SERVICE_UNAVAILABLE):
        return fallback
    if code == "ready":
        if not ok or stage != "complete":
            return fallback
    elif code == "busy":
        if not ok or stage != "lifecycle":
            return fallback
    elif ok:
        return fallback
    if code == "browser_navigation_degraded" and stage != "lifecycle":
        return fallback
    return DeepHealthResult(
        structured=True,
        healthy=ok,
        code=code,
        stage=stage,
        browser_generation=generation,
    )


class RecoveryAttemptGuard:
    def __init__(self):
        self._child_pid: int | None = None
        self._attempted: set[tuple[int, int | None]] = set()

    def start_child(self, child_pid: int) -> None:
        if (
            not isinstance(child_pid, int)
            or isinstance(child_pid, bool)
            or child_pid <= 1
        ):
            raise ValueError("invalid recovery child pid")
        self._child_pid = child_pid
        self._attempted.clear()

    def claim(self, child_pid: int, browser_generation: int | None) -> bool:
        if (
            not isinstance(child_pid, int)
            or isinstance(child_pid, bool)
            or child_pid <= 1
        ):
            raise ValueError("invalid recovery child pid")
        if child_pid != self._child_pid:
            raise ValueError("recovery child pid is not current")
        if browser_generation is not None and not _safe_browser_generation(
            browser_generation
        ):
            raise ValueError("invalid recovery browser generation")
        key = (child_pid, browser_generation)
        if key in self._attempted:
            return False
        self._attempted.add(key)
        return True


def recovery_probe_replaced(
    failed: DeepHealthResult, verification: DeepHealthResult
) -> bool:
    return bool(
        failed.browser_generation is not None
        and verification.real_ready
        and verification.browser_generation != failed.browser_generation
    )


class InternalClient:
    HEADER = "X-Spartan-Camofox-Internal"

    def __init__(
        self,
        *,
        port: int,
        secret_file: pathlib.Path,
        health_timeout: float,
        recovery_timeout: float,
        access_key: str = "",
    ):
        self.port = port
        self.secret_file = secret_file
        self.health_timeout = health_timeout
        self.recovery_timeout = recovery_timeout
        self.access_key = access_key
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(
        self, *, path: str, method: str, timeout: float
    ) -> tuple[int, bytes] | None:
        try:
            secret = _read_bounded(self.secret_file, 256).strip()
        except (OSError, UnicodeError, ValueError):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", secret):
            return None
        headers = {self.HEADER: secret, "Connection": "close"}
        if self.access_key:
            headers["Authorization"] = f"Bearer {self.access_key}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=b"" if method == "POST" else None,
            method=method,
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return (
                    int(response.status),
                    response.read(MAX_DEEP_HEALTH_BYTES + 1),
                )
        except urllib.error.HTTPError as error:
            try:
                return (
                    int(error.code),
                    error.read(MAX_DEEP_HEALTH_BYTES + 1),
                )
            except (OSError, TypeError, ValueError):
                return None
            finally:
                error.close()
        except (OSError, TimeoutError, urllib.error.URLError):
            return None

    def deep_health(self) -> DeepHealthResult:
        response = self._request(
            path="/spartan/health/deep", method="GET", timeout=self.health_timeout
        )
        if response is None:
            return DeepHealthResult.ordinary_unhealthy()
        return parse_deep_health_response(*response)

    def recover(self) -> bool:
        response = self._request(
            path="/spartan/recover", method="POST", timeout=self.recovery_timeout
        )
        return bool(response is not None and response[0] == http.client.OK)


class RecoveryPolicy:
    def __init__(self, *, failure_threshold: int):
        self.failure_threshold = failure_threshold
        self.failure_count = 0

    def observe(
        self, *, healthy: bool, hard_trigger: bool, defer_recovery: bool = False
    ) -> str:
        if healthy:
            self.failure_count = 0
        elif not defer_recovery or hard_trigger:
            self.failure_count += 1
        if hard_trigger or self.failure_count >= self.failure_threshold:
            return "recover"
        return "healthy" if healthy else "unhealthy"

    def recovery_succeeded(self) -> None:
        self.failure_count = 0


def write_readiness(
    path: pathlib.Path,
    *,
    state: str,
    updated_at: float,
    child_pid: int,
    child_start_time: int,
    cgroup_status: str,
) -> None:
    if state not in READY_STATES or cgroup_status not in CGROUP_STATES:
        raise ValueError("invalid readiness state")
    if not _finite_number(updated_at) or updated_at < 0:
        raise ValueError("invalid readiness timestamp")
    if not isinstance(child_pid, int) or child_pid < 0:
        raise ValueError("invalid readiness pid")
    if not isinstance(child_start_time, int) or child_start_time < 0:
        raise ValueError("invalid readiness start time")
    _write_json_atomic(
        path,
        {
            "version": VERSION,
            "state": state,
            "updated_at": updated_at,
            "child_pid": child_pid,
            "child_start_time": child_start_time,
            "cgroup_status": cgroup_status,
        },
    )


def check_readiness(
    path: pathlib.Path,
    *,
    now: float,
    max_age: float,
    proc_root: pathlib.Path = DEFAULT_PROC_ROOT,
) -> bool:
    try:
        file_info = path.lstat()
        if (
            not stat.S_ISREG(file_info.st_mode)
            or stat.S_ISLNK(file_info.st_mode)
            or stat.S_IMODE(file_info.st_mode) != 0o600
        ):
            return False
        payload = json.loads(_read_bounded(path))
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return False
        if payload.get("state") != "ready" or payload.get("cgroup_status") != "ok":
            return False
        updated_at = payload.get("updated_at")
        child_pid = payload.get("child_pid")
        child_start_time = payload.get("child_start_time")
        if (
            not _finite_number(updated_at)
            or updated_at > now + 1
            or now - updated_at > max_age
            or now - updated_at < 0
            or not isinstance(child_pid, int)
            or isinstance(child_pid, bool)
            or child_pid <= 1
            or not isinstance(child_start_time, int)
            or isinstance(child_start_time, bool)
            or child_start_time <= 0
        ):
            return False
        process = read_process_info(child_pid, proc_root)
        return bool(
            process is not None
            and process.start_time == child_start_time
            and process.state not in {"D", "X", "Z"}
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


class EventLogger:
    EVENTS = frozenset(
        {
            "supervisor_started",
            "subreaper_unavailable",
            "child_started",
            "child_exited",
            "probe_ready",
            "probe_failed",
            "cgroup_degraded",
            "recovery_requested",
            "recovery_succeeded",
            "recovery_failed",
            "child_signal",
            "child_stopped",
            "cleanup_blocked",
            "backoff_wait",
            "supervisor_stopping",
            "configuration_error",
            "runtime_error",
        }
    )
    FIELDS = frozenset(
        {
            "pid",
            "ppid",
            "state",
            "code",
            "stage",
            "browser_generation",
            "reason",
            "failure_count",
            "percent",
            "oom_delta",
            "exit_code",
            "signal",
            "survivors",
            "delay_seconds",
        }
    )

    def __init__(
        self,
        *,
        stream: IO[str] = sys.stderr,
        now: Callable[[], float] = time.time,
    ):
        self.stream = stream
        self.now = now

    def emit(self, event: str, **fields: Any) -> None:
        if event not in self.EVENTS or not set(fields).issubset(self.FIELDS):
            raise ValueError("non-allowlisted supervisor log field")
        payload: dict[str, Any] = {
            "timestamp": datetime.datetime.fromtimestamp(
                self.now(), tz=datetime.timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "event": event,
        }
        for key, value in fields.items():
            if isinstance(value, str):
                if not SAFE_VALUE.fullmatch(value):
                    raise ValueError("unsafe supervisor log value")
            elif value is not None and not (
                isinstance(value, (int, float, bool)) and not isinstance(value, complex)
            ):
                raise ValueError("unsafe supervisor log value")
            payload[key] = value
        self.stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        self.stream.flush()


def _env_number(
    env: Mapping[str, str],
    name: str,
    default: str,
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> int | float:
    raw = env.get(name, default)
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


@dataclasses.dataclass(frozen=True)
class Config:
    port: int
    monitor_interval: float
    health_timeout: float
    recovery_timeout: float
    failure_threshold: int
    term_grace: float
    kill_grace: float
    stable_reset: float
    readiness_max_age: float
    soft_percent: float
    hard_percent: float
    backoffs: tuple[int, ...]
    secret_file: pathlib.Path
    readiness_file: pathlib.Path
    ledger_file: pathlib.Path
    cgroup_root: pathlib.Path
    proc_root: pathlib.Path
    node_bin: str
    server_js: str
    workdir: str
    max_old_space_size: int
    access_key: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        source = os.environ if env is None else env
        port = int(
            _env_number(source, "CAMOFOX_PORT", "9377", minimum=1, maximum=65535, integer=True)
        )
        monitor = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_MONITOR_INTERVAL_SECONDS",
                "20",
                minimum=1,
                maximum=3600,
            )
        )
        health = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_HEALTH_TIMEOUT_SECONDS",
                "10",
                minimum=0.1,
                maximum=300,
            )
        )
        recovery = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_RECOVERY_TIMEOUT_SECONDS",
                "25",
                minimum=0.1,
                maximum=600,
            )
        )
        threshold = int(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_FAILURE_THRESHOLD",
                "3",
                minimum=1,
                maximum=100,
                integer=True,
            )
        )
        term_grace = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_TERM_GRACE_SECONDS",
                "12",
                minimum=0,
                maximum=300,
            )
        )
        kill_grace = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_KILL_GRACE_SECONDS",
                "3",
                minimum=0,
                maximum=60,
            )
        )
        stable_reset = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_STABLE_RESET_SECONDS",
                "1800",
                minimum=60,
                maximum=86400,
            )
        )
        readiness_max_age = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_READINESS_MAX_AGE_SECONDS",
                "45",
                minimum=2,
                maximum=7200,
            )
        )
        soft = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_MEMORY_SOFT_PERCENT",
                "90",
                minimum=1,
                maximum=99.9,
            )
        )
        hard = float(
            _env_number(
                source,
                "SPARTAN_CAMOFOX_MEMORY_HARD_PERCENT",
                "98",
                minimum=1,
                maximum=100,
            )
        )
        if hard <= soft or readiness_max_age <= monitor:
            raise ValueError("invalid supervisor thresholds")
        raw_backoffs = source.get("SPARTAN_CAMOFOX_BACKOFF_SECONDS", "0,30,120,600,1800")
        try:
            backoffs = tuple(int(part) for part in raw_backoffs.split(","))
        except ValueError:
            raise ValueError("invalid SPARTAN_CAMOFOX_BACKOFF_SECONDS") from None
        if (
            not backoffs
            or len(backoffs) > 32
            or backoffs[0] != 0
            or any(value < 0 or value > 86400 for value in backoffs)
            or any(left > right for left, right in zip(backoffs, backoffs[1:]))
        ):
            raise ValueError("invalid SPARTAN_CAMOFOX_BACKOFF_SECONDS")
        max_old_space_size = int(
            _env_number(
                source,
                "MAX_OLD_SPACE_SIZE",
                "128",
                minimum=16,
                maximum=65536,
                integer=True,
            )
        )
        paths = {
            "secret_file": pathlib.Path(
                source.get("SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE", str(DEFAULT_SECRET_FILE))
            ),
            "readiness_file": pathlib.Path(
                source.get("SPARTAN_CAMOFOX_READINESS_FILE", str(DEFAULT_READINESS_FILE))
            ),
            "ledger_file": pathlib.Path(
                source.get("SPARTAN_CAMOFOX_LEDGER_FILE", str(DEFAULT_LEDGER_FILE))
            ),
            "cgroup_root": pathlib.Path(
                source.get("SPARTAN_CAMOFOX_CGROUP_ROOT", str(DEFAULT_CGROUP_ROOT))
            ),
            "proc_root": pathlib.Path(source.get("SPARTAN_CAMOFOX_PROC_ROOT", str(DEFAULT_PROC_ROOT))),
        }
        if any(not value.is_absolute() for value in paths.values()):
            raise ValueError("supervisor paths must be absolute")
        node_bin = source.get("SPARTAN_CAMOFOX_NODE_BIN", "node")
        server_js = source.get("SPARTAN_CAMOFOX_SERVER_JS", "/app/server.js")
        workdir = source.get("SPARTAN_CAMOFOX_WORKDIR", "/app")
        if not node_bin or not server_js.startswith("/") or not workdir.startswith("/"):
            raise ValueError("invalid child command")
        access_key = source.get("CAMOFOX_ACCESS_KEY", "").strip()
        if len(access_key) > 4096 or "\r" in access_key or "\n" in access_key:
            raise ValueError("invalid CAMOFOX_ACCESS_KEY")
        return cls(
            port=port,
            monitor_interval=monitor,
            health_timeout=health,
            recovery_timeout=recovery,
            failure_threshold=threshold,
            term_grace=term_grace,
            kill_grace=kill_grace,
            stable_reset=stable_reset,
            readiness_max_age=readiness_max_age,
            soft_percent=soft,
            hard_percent=hard,
            backoffs=backoffs,
            node_bin=node_bin,
            server_js=server_js,
            workdir=workdir,
            max_old_space_size=max_old_space_size,
            access_key=access_key,
            **paths,
        )

    def child_command(self) -> tuple[str, ...]:
        return (
            self.node_bin,
            f"--max-old-space-size={self.max_old_space_size}",
            self.server_js,
        )


def enable_subreaper() -> bool:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            return False
        return True
    except (AttributeError, OSError):
        return False


class SupervisorRuntime:
    def __init__(
        self,
        config: Config,
        *,
        logger: EventLogger,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.logger = logger
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.sleep = sleep
        self.client = InternalClient(
            port=config.port,
            secret_file=config.secret_file,
            health_timeout=config.health_timeout,
            recovery_timeout=config.recovery_timeout,
            access_key=config.access_key,
        )
        self.cgroup = CgroupMonitor(
            config.cgroup_root,
            soft_percent=config.soft_percent,
            hard_percent=config.hard_percent,
        )
        self.ledger = load_ledger(
            config.ledger_file,
            now=self.wall_time(),
            backoffs=config.backoffs,
        )
        # A supervisor/process discontinuity cannot count as continuous readiness.
        if self.ledger.mark_unhealthy():
            save_ledger(config.ledger_file, self.ledger)
        self.child: subprocess.Popen[bytes] | None = None
        self.child_info: ProcessInfo | None = None
        self.pgid: int | None = None
        self.tracked: tuple[ProcessInfo, ...] = ()
        self.blocked = False
        self.stop_requested = False
        self._last_ready: bool | None = None
        self.recovery_attempts = RecoveryAttemptGuard()

    def _publish(self, state: str, cgroup_status: str = "unknown") -> None:
        child = self.child_info
        write_readiness(
            self.config.readiness_file,
            state=state,
            updated_at=self.wall_time(),
            child_pid=child.pid if child else 0,
            child_start_time=child.start_time if child else 0,
            cgroup_status=cgroup_status,
        )

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        self.stop_requested = True
        if self.pgid is not None:
            try:
                os.killpg(self.pgid, signum)
            except ProcessLookupError:
                pass

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _reap_adopted(self) -> None:
        direct_pid = self.child.pid if self.child is not None and self.child.poll() is None else None
        for process in scan_processes(self.config.proc_root).values():
            if process.ppid != os.getpid() or process.pid == direct_pid:
                continue
            try:
                os.waitpid(process.pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass

    def _wait(self, seconds: float) -> None:
        deadline = self.monotonic() + max(0.0, seconds)
        while not self.stop_requested:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return
            self._reap_adopted()
            self.sleep(min(0.25, remaining))

    def _wait_backoff(self) -> bool:
        logged = False
        while not self.stop_requested:
            remaining = self.ledger.next_allowed_at - self.wall_time()
            if remaining <= 0:
                return True
            self._publish("backoff")
            if not logged:
                self.logger.emit("backoff_wait", delay_seconds=round(remaining, 3))
                logged = True
            self._wait(min(self.config.monitor_interval, remaining))
        return False

    def _launch(self) -> None:
        child_env = os.environ.copy()
        child_env["SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE"] = str(self.config.secret_file)
        self.child = subprocess.Popen(
            self.config.child_command(),
            cwd=self.config.workdir,
            env=child_env,
            close_fds=True,
            start_new_session=True,
        )
        self.pgid = self.child.pid
        deadline = self.monotonic() + 2
        info = read_process_info(self.child.pid, self.config.proc_root)
        while info is None and self.monotonic() < deadline and self.child.poll() is None:
            self.sleep(0.01)
            info = read_process_info(self.child.pid, self.config.proc_root)
        if info is None:
            raise RuntimeError("child identity unavailable")
        self.child_info = info
        self.recovery_attempts.start_child(info.pid)
        self.tracked = lifecycle_processes(info, self.pgid, self.config.proc_root)
        self.blocked = False
        self.logger.emit(
            "child_started", pid=info.pid, ppid=info.ppid, state=info.state.lower()
        )
        self._publish("starting")

    def _lifecycle_survivors(self) -> list[ProcessInfo]:
        if self.child is not None:
            self.child.poll()
        self._reap_adopted()
        if self.pgid is None:
            return []
        return find_survivors(self.tracked, self.pgid, self.config.proc_root)

    def _send_lifecycle_signal(self, pgid: int, signum: signal.Signals) -> None:
        self.logger.emit("child_signal", signal=int(signum))
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass
        for tracked in self.tracked:
            current = read_process_info(tracked.pid, self.config.proc_root)
            if (
                current is None
                or current.start_time != tracked.start_time
                or current.pgrp == pgid
            ):
                continue
            try:
                os.kill(current.pid, signum)
            except ProcessLookupError:
                pass

    def _stop_lifecycle(self, *, record_recycle: bool) -> bool:
        if self.child_info is not None and self.pgid is not None:
            current = lifecycle_processes(
                self.child_info, self.pgid, self.config.proc_root
            )
            identities = {(item.pid, item.start_time): item for item in self.tracked}
            identities.update({(item.pid, item.start_time): item for item in current})
            self.tracked = tuple(identities.values())
        if self.pgid is None:
            survivors: list[ProcessInfo] = []
        else:
            survivors = stop_process_group(
                self.pgid,
                term_grace=self.config.term_grace,
                kill_grace=self.config.kill_grace,
                survivor_probe=self._lifecycle_survivors,
                send_signal=self._send_lifecycle_signal,
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
        if survivors:
            self.blocked = True
            self.ledger.force_maximum(self.wall_time(), self.config.backoffs)
            save_ledger(self.config.ledger_file, self.ledger)
            if self.child_info is None or read_process_info(
                self.child_info.pid, self.config.proc_root
            ) is None:
                self.child_info = survivors[0]
            self.logger.emit(
                "cleanup_blocked",
                survivors=len(survivors),
                state="d" if any(item.state == "D" for item in survivors) else "other",
                delay_seconds=self.config.backoffs[-1],
            )
            self._publish("unhealthy")
            return False
        if self.child is not None:
            try:
                self.child.wait(timeout=0)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass
        self.logger.emit("child_stopped", survivors=0)
        self.child = None
        self.child_info = None
        self.pgid = None
        self.tracked = ()
        self.blocked = False
        if record_recycle:
            delay = self.ledger.record_recycle(self.wall_time(), self.config.backoffs)
            save_ledger(self.config.ledger_file, self.ledger)
            self.logger.emit("backoff_wait", delay_seconds=delay)
        return True

    def _retry_blocked_cleanup(self) -> bool:
        if not self._wait_backoff():
            return False
        if self._stop_lifecycle(record_recycle=False):
            return True
        return False

    def _shutdown(self) -> int:
        self.logger.emit("supervisor_stopping")
        self._publish("stopping")
        if self.pgid is not None:
            self._stop_lifecycle(record_recycle=False)
        return 0

    def _attempt_internal_recovery(
        self,
        *,
        policy: RecoveryPolicy,
        failed_health: DeepHealthResult,
        cgroup_status: str,
        reason: str,
    ) -> bool:
        child_pid = (
            self.child_info.pid if self.child_info is not None else self.child.pid
        )
        health_fields = failed_health.log_fields()
        if not self.recovery_attempts.claim(
            child_pid, failed_health.browser_generation
        ):
            self.logger.emit(
                "recovery_failed",
                reason="recovery_already_attempted",
                **health_fields,
            )
            return False
        self._publish("recovering", cgroup_status=cgroup_status)
        self.logger.emit(
            "recovery_requested",
            failure_count=policy.failure_count,
            reason=reason,
            **health_fields,
        )
        if not self.client.recover():
            self.logger.emit(
                "recovery_failed", reason="internal_recovery_failed"
            )
            return False
        verification = self.client.deep_health()
        if not recovery_probe_replaced(failed_health, verification):
            self.logger.emit(
                "recovery_failed",
                reason="recovery_probe_not_replaced",
                **verification.log_fields(),
            )
            return False
        policy.recovery_succeeded()
        self.logger.emit("recovery_succeeded", **verification.log_fields())
        self._publish("starting", cgroup_status=cgroup_status)
        return True

    def run(self) -> int:
        self.install_signal_handlers()
        if not enable_subreaper():
            self.logger.emit("subreaper_unavailable")
        self.logger.emit("supervisor_started", pid=os.getpid(), ppid=os.getppid())
        self._publish("starting")
        while not self.stop_requested:
            if self.blocked:
                if not self._retry_blocked_cleanup():
                    continue
            if not self._wait_backoff():
                break
            try:
                self._launch()
            except (OSError, RuntimeError):
                self.logger.emit("runtime_error", reason="child_launch_failed")
                self._stop_lifecycle(record_recycle=True)
                continue
            policy = RecoveryPolicy(failure_threshold=self.config.failure_threshold)
            while not self.stop_requested and not self.blocked:
                if self.child is None:
                    break
                return_code = self.child.poll()
                if return_code is not None:
                    self.logger.emit("child_exited", exit_code=return_code)
                    self._stop_lifecycle(record_recycle=True)
                    break

                sample = self.cgroup.sample()
                if sample.status == "unknown":
                    self.logger.emit("cgroup_degraded", reason="cgroup_unknown")
                deep_health = self.client.deep_health()
                hard_trigger = sample.hard_trigger or deep_health.hard_trigger
                decision = policy.observe(
                    healthy=deep_health.healthy,
                    hard_trigger=hard_trigger,
                    defer_recovery=deep_health.recovery_deferred,
                )
                health_fields = deep_health.log_fields()
                recovery_reason = (
                    "memory_hard"
                    if sample.hard_trigger
                    else (
                        "browser_navigation_degraded"
                        if deep_health.hard_trigger
                        else "deep_unhealthy"
                    )
                )
                fully_healthy = (
                    deep_health.healthy
                    and sample.status == "ok"
                    and not sample.hard_trigger
                )
                if fully_healthy:
                    first_healthy = self.ledger.healthy_since is None
                    reset = self.ledger.mark_healthy(self.wall_time(), self.config.stable_reset)
                    if first_healthy or reset:
                        save_ledger(self.config.ledger_file, self.ledger)
                    self._publish("ready", cgroup_status="ok")
                    if self._last_ready is not True:
                        self.logger.emit(
                            "probe_ready", failure_count=0, **health_fields
                        )
                    self._last_ready = True
                else:
                    if self.ledger.mark_unhealthy():
                        save_ledger(self.config.ledger_file, self.ledger)
                    self._publish("unhealthy", cgroup_status=sample.status)
                    self.logger.emit(
                        "probe_failed",
                        failure_count=policy.failure_count,
                        reason=recovery_reason,
                        percent=round(sample.percent, 3) if sample.percent is not None else None,
                        oom_delta=sample.oom_delta,
                        **health_fields,
                    )
                    self._last_ready = False

                if decision == "recover":
                    if not self._attempt_internal_recovery(
                        policy=policy,
                        failed_health=deep_health,
                        cgroup_status=sample.status,
                        reason=recovery_reason,
                    ):
                        self._stop_lifecycle(record_recycle=True)
                        break
                self._wait(self.config.monitor_interval)
        return self._shutdown()


def run_command() -> int:
    try:
        config = Config.from_env()
        ensure_internal_secret(config.secret_file)
        logger = EventLogger()
        return SupervisorRuntime(config, logger=logger).run()
    except (OSError, ValueError, RuntimeError):
        try:
            EventLogger().emit("configuration_error")
        except Exception:
            pass
        return 2


def check_command() -> int:
    try:
        config = Config.from_env()
        healthy = check_readiness(
            config.readiness_file,
            now=time.time(),
            max_age=config.readiness_max_age,
            proc_root=config.proc_root,
        )
        return 0 if healthy else 1
    except (OSError, ValueError):
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments == ["run"]:
        return run_command()
    if arguments == ["check"]:
        return check_command()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
