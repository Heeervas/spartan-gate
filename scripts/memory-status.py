#!/usr/bin/env python3
"""Collect and render a bounded Spartan Gate host-memory audit."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


GIB = 1024**3
MIB = 1024**2
EXPECTED_SWAP_BYTES = 2 * GIB
EXPECTED_SWAPPINESS = 10
SWAP_SIZE_TOLERANCE_BYTES = MIB
WARNING_AVAILABLE_BYTES = 1536 * MIB
CRITICAL_AVAILABLE_BYTES = 768 * MIB
WARNING_SWAP_OUT_BYTES_PER_SECOND = MIB
CRITICAL_SWAP_OUT_BYTES_PER_SECOND = 4 * MIB
WARNING_MEMORY_PSI_FULL = 1.0
CRITICAL_MEMORY_PSI_FULL = 5.0
WARNING_CONTAINER_MEMORY_RATIO = 0.80
CRITICAL_CONTAINER_MEMORY_RATIO = 0.90
DEFAULT_WINDOW_SECONDS = 24 * 60 * 60
DEFAULT_MAX_AGE_SECONDS = 36 * 60 * 60
MAX_STATUS_BYTES = 32 * 1024
MAX_STATE_BYTES = 32 * 1024
MAX_SYSSTAT_BYTES = 8 * 1024 * 1024
PROJECT_LABEL = "com.docker.compose.project=spartan-gate"

ALLOWED_STATES = {"healthy", "warning", "critical"}
ALLOWED_REASONS = {
    "container_memory_critical",
    "container_memory_warning",
    "container_oom",
    "container_restart",
    "container_unhealthy",
    "critical_memory_available",
    "critical_memory_psi",
    "critical_swap_pressure",
    "history_unavailable",
    "host_oom",
    "low_memory_available",
    "memory_contract_drift",
    "memory_psi_warning",
    "sustained_swap_out",
}
EXPECTED_STATUS_KEYS = {
    "version",
    "observed_at",
    "window_seconds",
    "state",
    "reasons",
    "current",
    "history",
    "contract",
    "containers",
    "host_oom_events",
}
EXPECTED_CURRENT_KEYS = {
    "mem_available_bytes",
    "swap_total_bytes",
    "swap_used_bytes",
    "memory_psi_full_avg60",
}
EXPECTED_HISTORY_KEYS = {
    "available",
    "sample_count",
    "avg_host_cpu_percent",
    "peak_host_cpu_percent",
    "avg_load1",
    "peak_load1",
    "min_mem_available_bytes",
    "avg_swap_used_percent",
    "peak_swap_used_percent",
    "max_swap_in_bytes_per_second",
    "max_swap_out_bytes_per_second",
    "peak_iowait_percent",
    "critical_swap_out_low_memory_samples",
}
EXPECTED_CONTRACT_KEYS = {
    "active",
    "size_ok",
    "mode_ok",
    "swappiness_ok",
    "sysctl_persistent",
    "fstab_persistent",
}
EXPECTED_CONTAINER_KEYS = {
    "service",
    "running",
    "health",
    "memory_bytes",
    "swap_bytes",
    "memory_limit_bytes",
    "swap_limit_bytes",
    "memory_ratio",
    "oom_killed",
    "oom_delta",
    "restart_delta",
}
OOM_PATTERN = re.compile(
    r"(?:out of memory|oom-kill|killed process|memory cgroup out of memory)",
    re.IGNORECASE,
)


class StatusError(ValueError):
    pass


def default_status_file() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = f"/run/user/{os.getuid()}"
    return Path(runtime) / "spartan-gate-memory-status.json"


def default_state_file() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    if not state_home:
        state_home = str(Path.home() / ".local" / "state")
    return Path(state_home) / "spartan-gate" / "memory-audit-state.json"


def _nonnegative_number(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise StatusError(f"{field} is invalid")


def validate_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != EXPECTED_STATUS_KEYS:
        raise StatusError("status schema is invalid")
    if payload["version"] != 1:
        raise StatusError("status version is unsupported")
    if (
        isinstance(payload["observed_at"], bool)
        or not isinstance(payload["observed_at"], int)
        or payload["observed_at"] <= 0
    ):
        raise StatusError("observed_at is invalid")
    if payload["window_seconds"] != DEFAULT_WINDOW_SECONDS:
        raise StatusError("window_seconds is invalid")
    if payload["state"] not in ALLOWED_STATES:
        raise StatusError("state is invalid")
    reasons = payload["reasons"]
    if (
        not isinstance(reasons, list)
        or any(reason not in ALLOWED_REASONS for reason in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise StatusError("reasons are invalid")

    current = payload["current"]
    if not isinstance(current, dict) or set(current) != EXPECTED_CURRENT_KEYS:
        raise StatusError("current schema is invalid")
    for field in EXPECTED_CURRENT_KEYS:
        _nonnegative_number(current[field], f"current.{field}")

    history = payload["history"]
    if not isinstance(history, dict) or set(history) != EXPECTED_HISTORY_KEYS:
        raise StatusError("history schema is invalid")
    if not isinstance(history["available"], bool):
        raise StatusError("history.available is invalid")
    for field in EXPECTED_HISTORY_KEYS - {"available"}:
        _nonnegative_number(history[field], f"history.{field}")

    contract = payload["contract"]
    if not isinstance(contract, dict) or set(contract) != EXPECTED_CONTRACT_KEYS:
        raise StatusError("contract schema is invalid")
    if any(not isinstance(contract[field], bool) for field in EXPECTED_CONTRACT_KEYS):
        raise StatusError("contract value is invalid")

    containers = payload["containers"]
    if not isinstance(containers, list):
        raise StatusError("containers are invalid")
    for container in containers:
        if not isinstance(container, dict) or set(container) != EXPECTED_CONTAINER_KEYS:
            raise StatusError("container schema is invalid")
        if not isinstance(container["service"], str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,63}",
            container["service"],
        ):
            raise StatusError("container service is invalid")
        if not isinstance(container["running"], bool):
            raise StatusError("container running state is invalid")
        if container["health"] not in {"healthy", "unhealthy", "starting", "none"}:
            raise StatusError("container health is invalid")
        for field in (
            "memory_bytes",
            "swap_bytes",
            "memory_limit_bytes",
            "swap_limit_bytes",
            "memory_ratio",
            "oom_delta",
            "restart_delta",
        ):
            _nonnegative_number(container[field], f"container.{field}")
        if not isinstance(container["oom_killed"], bool):
            raise StatusError("container oom state is invalid")

    _nonnegative_number(payload["host_oom_events"], "host_oom_events")
    return payload


def load_status(path: Path) -> dict[str, Any]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATUS_BYTES:
            raise StatusError("status file is not a bounded regular file")
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StatusError("status file is unreadable") from exc
    return validate_status(payload)


def _prepare_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = path.parent.stat()
    if not stat.S_ISDIR(metadata.st_mode) or path.parent.is_symlink():
        raise StatusError(f"unsafe output directory: {path.parent}")
    if not os.access(path.parent, os.W_OK | os.X_OK):
        raise StatusError(f"output directory is not writable: {path.parent}")
    if path.exists():
        target = path.stat()
        if not stat.S_ISREG(target.st_mode) or path.is_symlink():
            raise StatusError(f"unsafe output file: {path}")
        if target.st_uid != os.geteuid():
            raise StatusError(f"output file has unexpected owner: {path}")


def atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int) -> None:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    encoded = raw.encode("utf-8")
    limit = MAX_STATUS_BYTES if mode == 0o644 else MAX_STATE_BYTES
    if len(encoded) > limit:
        raise StatusError("serialized state exceeds its size limit")
    _prepare_parent(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def status_age(payload: dict[str, Any], now: int) -> int:
    age = now - payload["observed_at"]
    if age < -60:
        raise StatusError("observed_at is in the future")
    return max(age, 0)


def _gib(value: int | float) -> str:
    return f"{value / GIB:.1f} GiB"


def _human_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 60 * 60:
        return f"{seconds // 60}m"
    if seconds < 48 * 60 * 60:
        return f"{seconds // (60 * 60)}h"
    return f"{seconds // (24 * 60 * 60)}d"


_REASON_LABELS = {
    "container_memory_critical": "a container used at least 90% of its memory limit",
    "container_memory_warning": "a container used at least 80% of its memory limit",
    "container_oom": "a container ran out of memory",
    "container_restart": "a container restarted since the previous audit",
    "container_unhealthy": "a running container is unhealthy",
    "critical_memory_available": "available RAM fell below 0.8 GiB in the last 24h",
    "critical_memory_psi": "memory stalls are critically high",
    "critical_swap_pressure": "low RAM and heavy swap-out happened together",
    "history_unavailable": "the 24-hour sysstat history is unavailable",
    "host_oom": "the host reported an out-of-memory event in the last 24h",
    "low_memory_available": "available RAM fell below 1.5 GiB in the last 24h",
    "memory_contract_drift": "the 2 GiB swap / swappiness 10 setup has drifted",
    "memory_psi_warning": "memory stalls are elevated",
    "sustained_swap_out": "swap-out activity occurred in the last 24h",
}


def _readable_reasons(reasons: list[str]) -> list[str]:
    visible = set(reasons)
    if "critical_swap_pressure" in visible:
        visible.difference_update(
            {
                "critical_memory_available",
                "low_memory_available",
                "sustained_swap_out",
            }
        )
    return [_REASON_LABELS[reason] for reason in reasons if reason in visible]


def format_status(
    payload: dict[str, Any],
    *,
    now: int,
    max_age: int,
) -> tuple[str, bool]:
    age = status_age(payload, now)
    if age > max_age:
        return (
            f"Memory audit: STALE (last updated {_human_age(age)} ago)\n"
            "  Next: run sg-memory collect to refresh it",
            False,
        )

    current = payload["current"]
    history = payload["history"]
    lines = [
        f"Memory audit: {payload['state'].upper()} "
        f"(updated {_human_age(age)} ago)",
        "  Latest sample: "
        f"{_gib(current['mem_available_bytes'])} RAM available | "
        f"swap using {_gib(current['swap_used_bytes'])} of "
        f"{_gib(current['swap_total_bytes'])} | "
        f"memory stalls {current['memory_psi_full_avg60']:.2f}%",
    ]
    if history["available"]:
        lines.extend(
            [
                "  Last 24h: "
                f"RAM low point {_gib(history['min_mem_available_bytes'])} | "
                "peak swap-out "
                f"{history['max_swap_out_bytes_per_second'] / MIB:.1f} MiB/s",
                "  24h load: "
                f"CPU {history['avg_host_cpu_percent']:.1f}% avg, "
                f"{history['peak_host_cpu_percent']:.1f}% peak | "
                f"1-minute load {history['avg_load1']:.2f} avg, "
                f"{history['peak_load1']:.2f} peak | "
                f"I/O wait {history['peak_iowait_percent']:.1f}% peak",
            ]
        )
    else:
        lines.append("  Last 24h: no sysstat history available")

    reasons = _readable_reasons(payload["reasons"])
    if reasons:
        lines.append(f"  Why: {'; '.join(reasons)}")
    return "\n".join(lines), payload["state"] == "healthy"


def empty_history() -> dict[str, Any]:
    return {
        "available": False,
        "sample_count": 0,
        "avg_host_cpu_percent": 0.0,
        "peak_host_cpu_percent": 0.0,
        "avg_load1": 0.0,
        "peak_load1": 0.0,
        "min_mem_available_bytes": 0,
        "avg_swap_used_percent": 0.0,
        "peak_swap_used_percent": 0.0,
        "max_swap_in_bytes_per_second": 0,
        "max_swap_out_bytes_per_second": 0,
        "peak_iowait_percent": 0.0,
        "critical_swap_out_low_memory_samples": 0,
    }


def summarize_history(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if len(samples) < 3:
        return empty_history()
    ordered = sorted(samples, key=lambda item: item["epoch"])
    critical_run = 0
    max_critical_run = 0
    for sample in ordered:
        if (
            sample["swap_out_bytes_per_second"]
            >= CRITICAL_SWAP_OUT_BYTES_PER_SECOND
            and sample["mem_available_bytes"] < WARNING_AVAILABLE_BYTES
        ):
            critical_run += 1
            max_critical_run = max(max_critical_run, critical_run)
        else:
            critical_run = 0
    count = len(ordered)
    return {
        "available": True,
        "sample_count": count,
        "avg_host_cpu_percent": round(
            sum(item["host_cpu_percent"] for item in ordered) / count,
            2,
        ),
        "peak_host_cpu_percent": round(
            max(item["host_cpu_percent"] for item in ordered),
            2,
        ),
        "avg_load1": round(sum(item["load1"] for item in ordered) / count, 2),
        "peak_load1": round(max(item["load1"] for item in ordered), 2),
        "min_mem_available_bytes": min(
            item["mem_available_bytes"] for item in ordered
        ),
        "avg_swap_used_percent": round(
            sum(item["swap_used_percent"] for item in ordered) / count,
            2,
        ),
        "peak_swap_used_percent": round(
            max(item["swap_used_percent"] for item in ordered),
            2,
        ),
        "max_swap_in_bytes_per_second": int(
            max(item["swap_in_bytes_per_second"] for item in ordered)
        ),
        "max_swap_out_bytes_per_second": int(
            max(item["swap_out_bytes_per_second"] for item in ordered)
        ),
        "peak_iowait_percent": round(
            max(item["iowait_percent"] for item in ordered),
            2,
        ),
        "critical_swap_out_low_memory_samples": max_critical_run,
    }


def _timestamp_epoch(
    timestamp: dict[str, Any],
    local_timezone: dt.tzinfo,
) -> int | None:
    try:
        parsed = dt.datetime.fromisoformat(
            f"{timestamp['date']}T{timestamp['time']}"
        )
        if timestamp.get("utc"):
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        else:
            parsed = parsed.replace(tzinfo=local_timezone)
        return int(parsed.timestamp())
    except (KeyError, TypeError, ValueError):
        return None


def parse_sadf_payload(
    payload: Any,
    *,
    start_epoch: int,
    end_epoch: int,
    page_size: int,
) -> list[dict[str, Any]]:
    samples: dict[int, dict[str, Any]] = {}
    try:
        hosts = payload["sysstat"]["hosts"]
    except (KeyError, TypeError):
        return []
    local_timezone = dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
    for host in hosts if isinstance(hosts, list) else []:
        statistics = host.get("statistics", []) if isinstance(host, dict) else []
        for item in statistics if isinstance(statistics, list) else []:
            if not isinstance(item, dict):
                continue
            epoch = _timestamp_epoch(item.get("timestamp", {}), local_timezone)
            if epoch is None or epoch < start_epoch or epoch > end_epoch:
                continue
            cpu_load = item.get("cpu-load", [])
            cpu_all = next(
                (
                    cpu
                    for cpu in cpu_load
                    if isinstance(cpu, dict) and cpu.get("cpu") == "all"
                ),
                None,
            )
            memory = item.get("memory")
            swap_pages = item.get("swap-pages")
            queue = item.get("queue")
            if not all(
                isinstance(value, dict)
                for value in (cpu_all, memory, swap_pages, queue)
            ):
                continue
            try:
                samples[epoch] = {
                    "epoch": epoch,
                    "host_cpu_percent": max(
                        0.0,
                        min(100.0, 100.0 - float(cpu_all["idle"])),
                    ),
                    "iowait_percent": max(0.0, float(cpu_all["iowait"])),
                    "load1": max(0.0, float(queue["ldavg-1"])),
                    "mem_available_bytes": int(memory["avail"]) * 1024,
                    "swap_used_percent": max(
                        0.0,
                        float(memory["swpused-percent"]),
                    ),
                    "swap_in_bytes_per_second": max(
                        0,
                        int(float(swap_pages["pswpin"]) * page_size),
                    ),
                    "swap_out_bytes_per_second": max(
                        0,
                        int(float(swap_pages["pswpout"]) * page_size),
                    ),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return list(samples.values())


def collect_history(
    sysstat_dir: Path,
    *,
    sadf_bin: str,
    now: int,
    window_seconds: int,
) -> dict[str, Any]:
    start = now - window_seconds
    local_now = dt.datetime.fromtimestamp(now).astimezone()
    candidates = {
        sysstat_dir / f"sa{local_now.day:02d}",
        sysstat_dir / f"sa{(local_now - dt.timedelta(days=1)).day:02d}",
    }
    samples: list[dict[str, Any]] = []
    page_size = os.sysconf("SC_PAGE_SIZE")
    for path in sorted(candidates):
        if not path.is_file():
            continue
        try:
            result = subprocess.run(
                [
                    sadf_bin,
                    "-j",
                    str(path),
                    "--",
                    "-r",
                    "-S",
                    "-W",
                    "-q",
                    "-u",
                    "ALL",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if (
            result.returncode != 0
            or len(result.stdout.encode("utf-8")) > MAX_SYSSTAT_BYTES
        ):
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        samples.extend(
            parse_sadf_payload(
                payload,
                start_epoch=start,
                end_epoch=now,
                page_size=page_size,
            )
        )
    deduplicated = {sample["epoch"]: sample for sample in samples}
    return summarize_history(list(deduplicated.values()))


def read_meminfo(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0].endswith(":"):
                values[fields[0][:-1]] = int(fields[1]) * 1024
    except (OSError, UnicodeError, ValueError) as exc:
        raise StatusError("meminfo is unreadable") from exc
    for field in ("MemAvailable", "SwapTotal", "SwapFree"):
        if field not in values or values[field] < 0:
            raise StatusError(f"meminfo field is missing: {field}")
    return values


def read_memory_psi(path: Path) -> float:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StatusError("memory pressure is unreadable") from exc
    for line in lines:
        fields = line.split()
        if fields and fields[0] == "full":
            values = dict(field.split("=", 1) for field in fields[1:] if "=" in field)
            try:
                return max(0.0, float(values["avg60"]))
            except (KeyError, ValueError) as exc:
                raise StatusError("memory pressure is invalid") from exc
    raise StatusError("memory pressure full line is missing")


def _read_integer(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError):
        return None


def collect_contract(
    *,
    proc_root: Path,
    swap_source: Path,
    sysctl_file: Path,
    fstab_file: Path,
    swapon_bin: str,
) -> dict[str, bool]:
    swappiness = _read_integer(proc_root / "sys/vm/swappiness")
    active = False
    active_size = 0
    try:
        result = subprocess.run(
            [
                swapon_bin,
                "--show=NAME,SIZE",
                "--bytes",
                "--raw",
                "--noheadings",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == str(swap_source):
                try:
                    active_size = int(fields[1])
                    active = True
                except ValueError:
                    pass
    try:
        mode_ok = stat.S_IMODE(swap_source.stat().st_mode) == 0o600
    except OSError:
        mode_ok = False
    try:
        sysctl_lines = {
            line.strip()
            for line in sysctl_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        sysctl_persistent = f"vm.swappiness={EXPECTED_SWAPPINESS}" in {
            line.replace(" ", "") for line in sysctl_lines
        }
    except (OSError, UnicodeError):
        sysctl_persistent = False
    fstab_persistent = False
    try:
        for line in fstab_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) >= 3 and fields[0] == str(swap_source) and fields[2] == "swap":
                fstab_persistent = True
                break
    except (OSError, UnicodeError):
        pass
    return {
        "active": active,
        "size_ok": active
        and abs(active_size - EXPECTED_SWAP_BYTES) <= SWAP_SIZE_TOLERANCE_BYTES,
        "mode_ok": mode_ok,
        "swappiness_ok": swappiness == EXPECTED_SWAPPINESS,
        "sysctl_persistent": sysctl_persistent,
        "fstab_persistent": fstab_persistent,
    }


def _read_cgroup_value(path: Path) -> int:
    value = _read_integer(path)
    return value if value is not None and value >= 0 else 0


def _read_memory_events(path: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, raw = line.split(None, 1)
            events[key] = max(0, int(raw))
    except (OSError, UnicodeError, ValueError):
        return {}
    return events


def load_state(path: Path) -> dict[str, Any]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATE_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def collect_containers(
    *,
    docker_bin: str,
    proc_root: Path,
    sys_root: Path,
    previous_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        listed = subprocess.run(
            [
                docker_bin,
                "ps",
                "--filter",
                f"label={PROJECT_LABEL}",
                "--format",
                "{{.ID}}",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], {}
    identifiers = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if listed.returncode != 0 or not identifiers:
        return [], {}
    try:
        inspected = subprocess.run(
            [docker_bin, "inspect", *identifiers],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], {}
    if inspected.returncode != 0:
        return [], {}
    try:
        records = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return [], {}

    previous_containers = previous_state.get("containers", {})
    if not isinstance(previous_containers, dict):
        previous_containers = {}
    public: list[dict[str, Any]] = []
    next_state: dict[str, Any] = {}
    for record in records if isinstance(records, list) else []:
        try:
            labels = record["Config"]["Labels"] or {}
            service = labels["com.docker.compose.service"]
            identifier = record["Id"]
            process_id = int(record["State"]["Pid"])
            running = bool(record["State"]["Running"])
            health_record = record["State"].get("Health")
            health = health_record.get("Status", "none") if health_record else "none"
            restart_count = max(0, int(record.get("RestartCount", 0)))
            oom_killed = bool(record["State"].get("OOMKilled", False))
            memory_limit = max(0, int(record["HostConfig"].get("Memory", 0)))
            memory_swap_total = max(
                0,
                int(record["HostConfig"].get("MemorySwap", 0)),
            )
        except (KeyError, TypeError, ValueError):
            continue
        try:
            cgroup_line = next(
                line
                for line in (proc_root / str(process_id) / "cgroup")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.startswith("0::")
            )
            cgroup_relative = cgroup_line.split("::", 1)[1].lstrip("/")
            cgroup = sys_root / "fs/cgroup" / cgroup_relative
        except (OSError, UnicodeError, StopIteration):
            cgroup = Path("/nonexistent")
        memory_current = _read_cgroup_value(cgroup / "memory.current")
        swap_current = _read_cgroup_value(cgroup / "memory.swap.current")
        events = _read_memory_events(cgroup / "memory.events")
        oom_counter = max(events.get("oom", 0), events.get("oom_kill", 0))
        previous = previous_containers.get(service, {})
        same_container = (
            isinstance(previous, dict) and previous.get("id") == identifier
        )
        restart_delta = (
            max(0, restart_count - int(previous.get("restart_count", 0)))
            if same_container
            else 0
        )
        oom_delta = (
            max(0, oom_counter - int(previous.get("oom_counter", 0)))
            if same_container
            else 0
        )
        ratio = memory_current / memory_limit if memory_limit > 0 else 0.0
        public.append(
            {
                "service": service,
                "running": running,
                "health": health
                if health in {"healthy", "unhealthy", "starting"}
                else "none",
                "memory_bytes": memory_current,
                "swap_bytes": swap_current,
                "memory_limit_bytes": memory_limit,
                "swap_limit_bytes": max(0, memory_swap_total - memory_limit),
                "memory_ratio": round(ratio, 4),
                "oom_killed": oom_killed,
                "oom_delta": oom_delta,
                "restart_delta": restart_delta,
            }
        )
        next_state[service] = {
            "id": identifier,
            "restart_count": restart_count,
            "oom_counter": oom_counter,
        }
    return sorted(public, key=lambda item: item["service"]), next_state


def count_host_oom_events(
    *,
    journalctl_bin: str,
    since_epoch: int,
) -> int:
    try:
        result = subprocess.run(
            [
                journalctl_bin,
                "-k",
                "--no-pager",
                "--output=cat",
                "--since",
                f"@{since_epoch}",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0 or len(result.stdout) > MIB:
        return 0
    return sum(1 for line in result.stdout.splitlines() if OOM_PATTERN.search(line))


def classify_snapshot(snapshot: dict[str, Any]) -> tuple[str, list[str]]:
    warning: set[str] = set()
    critical: set[str] = set()
    current = snapshot["current"]
    history = snapshot["history"]
    contract = snapshot["contract"]

    available_values = [current["mem_available_bytes"]]
    if history["available"]:
        available_values.append(history["min_mem_available_bytes"])
    minimum_available = min(available_values)
    if minimum_available < CRITICAL_AVAILABLE_BYTES:
        critical.add("critical_memory_available")
    elif minimum_available < WARNING_AVAILABLE_BYTES:
        warning.add("low_memory_available")

    psi = current["memory_psi_full_avg60"]
    if psi >= CRITICAL_MEMORY_PSI_FULL:
        critical.add("critical_memory_psi")
    elif psi >= WARNING_MEMORY_PSI_FULL:
        warning.add("memory_psi_warning")

    if not history["available"]:
        warning.add("history_unavailable")
    elif (
        history["max_swap_out_bytes_per_second"]
        >= WARNING_SWAP_OUT_BYTES_PER_SECOND
    ):
        warning.add("sustained_swap_out")
    if history["critical_swap_out_low_memory_samples"] >= 2:
        critical.add("critical_swap_pressure")

    if not all(contract.values()):
        warning.add("memory_contract_drift")
    if snapshot.get("host_oom_events", 0) > 0:
        critical.add("host_oom")

    for container in snapshot["containers"]:
        if container["oom_killed"] or container["oom_delta"] > 0:
            critical.add("container_oom")
        if container["running"] and container["health"] == "unhealthy":
            critical.add("container_unhealthy")
        if container["memory_ratio"] >= CRITICAL_CONTAINER_MEMORY_RATIO:
            critical.add("container_memory_critical")
        elif container["memory_ratio"] >= WARNING_CONTAINER_MEMORY_RATIO:
            warning.add("container_memory_warning")
        if container["restart_delta"] > 0:
            warning.add("container_restart")

    if critical:
        return "critical", sorted(critical | warning)
    if warning:
        return "warning", sorted(warning)
    return "healthy", []


def collect_snapshot(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    now = args.now if args.now is not None else int(time.time())
    meminfo = read_meminfo(args.proc_root / "meminfo")
    current = {
        "mem_available_bytes": meminfo["MemAvailable"],
        "swap_total_bytes": meminfo["SwapTotal"],
        "swap_used_bytes": meminfo["SwapTotal"] - meminfo["SwapFree"],
        "memory_psi_full_avg60": read_memory_psi(
            args.proc_root / "pressure/memory"
        ),
    }
    history = collect_history(
        args.sysstat_dir,
        sadf_bin=args.sadf_bin,
        now=now,
        window_seconds=DEFAULT_WINDOW_SECONDS,
    )
    contract = collect_contract(
        proc_root=args.proc_root,
        swap_source=args.swap_source,
        sysctl_file=args.sysctl_file,
        fstab_file=args.fstab_file,
        swapon_bin=args.swapon_bin,
    )
    previous_state = load_state(args.state_file)
    containers, container_state = collect_containers(
        docker_bin=args.docker_bin,
        proc_root=args.proc_root,
        sys_root=args.sys_root,
        previous_state=previous_state,
    )
    since_epoch = previous_state.get("observed_at", now - DEFAULT_WINDOW_SECONDS)
    if isinstance(since_epoch, bool) or not isinstance(since_epoch, int):
        since_epoch = now - DEFAULT_WINDOW_SECONDS
    since_epoch = max(now - DEFAULT_WINDOW_SECONDS, min(now, since_epoch))
    snapshot: dict[str, Any] = {
        "version": 1,
        "observed_at": now,
        "window_seconds": DEFAULT_WINDOW_SECONDS,
        "state": "healthy",
        "reasons": [],
        "current": current,
        "history": history,
        "contract": contract,
        "containers": containers,
        "host_oom_events": count_host_oom_events(
            journalctl_bin=args.journalctl_bin,
            since_epoch=since_epoch,
        ),
    }
    snapshot["state"], snapshot["reasons"] = classify_snapshot(snapshot)
    validate_status(snapshot)
    next_state = {
        "version": 1,
        "observed_at": now,
        "containers": container_state,
    }
    return snapshot, next_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("collect", "show", "check"))
    parser.add_argument("--status-file", type=Path, default=default_status_file())
    parser.add_argument("--state-file", type=Path, default=default_state_file())
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--sys-root", type=Path, default=Path("/sys"))
    parser.add_argument("--sysstat-dir", type=Path, default=Path("/var/log/sysstat"))
    parser.add_argument("--swap-source", type=Path, default=Path("/swap.img"))
    parser.add_argument(
        "--sysctl-file",
        type=Path,
        default=Path("/etc/sysctl.d/99-spartan-gate-memory.conf"),
    )
    parser.add_argument("--fstab-file", type=Path, default=Path("/etc/fstab"))
    parser.add_argument("--sadf-bin", default="sadf")
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--swapon-bin", default="swapon")
    parser.add_argument("--journalctl-bin", default="journalctl")
    parser.add_argument("--now", type=int, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_age <= 0:
        parser.error("--max-age must be positive")
    now = args.now if args.now is not None else int(time.time())

    if args.command == "collect":
        try:
            _prepare_parent(args.status_file)
            _prepare_parent(args.state_file)
            payload, state = collect_snapshot(args)
            atomic_write_json(args.state_file, state, mode=0o600)
            atomic_write_json(args.status_file, payload, mode=0o644)
            line, _ = format_status(payload, now=now, max_age=args.max_age)
        except (OSError, StatusError) as exc:
            print(f"Memory audit: unavailable ({exc})")
            return 2
        print(line)
        return 0

    try:
        payload = load_status(args.status_file)
        line, healthy = format_status(
            payload,
            now=now,
            max_age=args.max_age,
        )
    except FileNotFoundError:
        line = "Memory audit: unavailable (status not published)"
        healthy = False
    except StatusError:
        line = "Memory audit: unavailable (invalid status)"
        healthy = False
    print(line)
    return 0 if args.command == "show" or healthy else 1


if __name__ == "__main__":
    sys.exit(main())
