#!/usr/bin/env python3
"""Host-side, fail-closed process broker for the Hermes workstation terminal.

The broker is deliberately outside the Hermes container. It owns the execution
key and the SSH client, exposes only a Unix-domain socket, and accepts requests
only from the configured Hermes gateway process. The model never controls SSH
connection parameters.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import importlib.util
import json
import os
import pty
import re
import secrets
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

try:
    from scripts import workstation_gateway_trust as GATEWAY_TRUST
except ImportError:
    import workstation_gateway_trust as GATEWAY_TRUST


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "scripts" / "workstation-bridge.py"
TRANSFER_BROKER_PATH = ROOT / "scripts" / "workstation-transfer-broker.py"
DEFAULT_WORKDIR = PurePosixPath("/home/hermes-worker/workspace")
EXCHANGE_WORKDIR = PurePosixPath("/home/hermes-worker/windows-exchange")
MAX_REQUEST_BYTES = 64 * 1024
MAX_COMMAND_CHARS = 32_000
MAX_STDIN_CHARS = 16_000
MAX_OUTPUT_CHARS = 200_000
MAX_ACTIVE_PROCESSES = 4
FOREGROUND_DEFAULT_TIMEOUT = 180
FOREGROUND_MAX_TIMEOUT = 900
BACKGROUND_DEFAULT_TIMEOUT = 3600
BACKGROUND_MAX_TIMEOUT = 86_400
FINISHED_TTL_SECONDS = 1800
ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[PX^_].*?\x1b\\)",
    re.DOTALL,
)
OWNER_RE = re.compile(r"^[a-f0-9]{32}:g[0-9]{1,12}$")
PROCESS_ID_RE = re.compile(r"^wproc_[a-f0-9]{24}$")
SO_PEERPIDFD = getattr(socket, "SO_PEERPIDFD", 77)

GatewaySnapshot = GATEWAY_TRUST.GatewaySnapshot


class BrokerError(RuntimeError):
    """A request/configuration failure safe to return to the caller."""


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("spartan_workstation_bridge", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise BrokerError("cannot load workstation bridge validation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_transfer_broker_module():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_transfer_broker", TRANSFER_BROKER_PATH
    )
    if spec is None or spec.loader is None:
        raise BrokerError("cannot load workstation transfer broker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TRANSFER = load_transfer_broker_module()


def clean_output(value: str) -> str:
    value = ANSI_RE.sub("", value)
    return "".join(
        char for char in value if char in "\n\r\t" or ord(char) >= 32
    )


def bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise BrokerError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise BrokerError(f"{label} must be between {minimum} and {maximum}")
    return value


def validate_owner(value: Any) -> str:
    if not isinstance(value, str) or not OWNER_RE.fullmatch(value):
        raise BrokerError("invalid owner identity")
    return value


def validate_process_id(value: Any) -> str:
    if not isinstance(value, str) or not PROCESS_ID_RE.fullmatch(value):
        raise BrokerError("invalid process_id")
    return value


def validate_workdir(value: Any) -> str:
    if value is None or value == "":
        candidate = DEFAULT_WORKDIR
    elif isinstance(value, str) and value.startswith("/"):
        candidate = PurePosixPath(value)
    else:
        raise BrokerError("workdir must be an absolute path")
    if ".." in candidate.parts:
        raise BrokerError("workdir traversal is not allowed")
    if not (
        candidate == DEFAULT_WORKDIR
        or candidate.is_relative_to(DEFAULT_WORKDIR)
        or candidate == EXCHANGE_WORKDIR
        or candidate.is_relative_to(EXCHANGE_WORKDIR)
    ):
        raise BrokerError(
            "workdir must be beneath the workstation workspace or Exchange mount"
        )
    return str(candidate)


DEFAULT_GATEWAY_COMMAND = (
    "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes "
    "gateway run --replace"
)



def process_start_time(pid: int) -> str | None:
    try:
        remainder = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(
            ")", 1
        )[1]
        return remainder.split()[19]
    except (FileNotFoundError, IndexError, OSError):
        return None
def parse_default_gateway_pid(output: str) -> int:
    candidates = []
    for line in output.splitlines()[1:]:
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        if fields[2] == DEFAULT_GATEWAY_COMMAND:
            candidates.append(int(fields[0]))
    if len(candidates) != 1:
        raise BrokerError(
            "expected exactly one default Hermes gateway process; "
            f"found {len(candidates)}"
        )
    return candidates[0]


class HealthPublisher:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, state: str, epoch: int, gateway_pid: int | None) -> None:
        payload = {
            "version": 1,
            "state": state,
            "epoch": epoch,
            "gateway_pid": gateway_pid,
            "updated_at": time.time(),
        }
        temporary = self.path.parent / f".{self.path.name}.{os.getpid()}.tmp"
        with self.lock:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory = os.open(
                    self.path.parent, os.O_RDONLY | os.O_DIRECTORY
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)


class DockerGatewayAuthorizer:
    """Pin peers to a trusted container and rotate only after PID death."""

    def __init__(
        self,
        container_name: str,
        baseline: dict[str, Any],
        *,
        resolver: Callable[
            [str], GatewaySnapshot
        ] = GATEWAY_TRUST.capture_gateway_snapshot,
        pidfd_open: Callable[[int], int] = os.pidfd_open,
        pidfd_alive: Callable[[int], bool] = GATEWAY_TRUST.pidfd_alive,
        peer_pidfd_pid: Callable[[int], int] = GATEWAY_TRUST.pidfd_pid,
        peer_validator: Callable[
            [int, int, GatewaySnapshot], None
        ] = GATEWAY_TRUST.validate_peer_process,
        on_rotate: Callable[[], Any] | None = None,
        health_publisher: Callable[[str, int, int | None], None] | None = None,
    ):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", container_name):
            raise BrokerError("invalid broker container_name")
        self.container_name = container_name
        self.baseline = baseline
        self.resolver = resolver
        self.pidfd_open = pidfd_open
        self.pidfd_alive = pidfd_alive
        self.peer_pidfd_pid = peer_pidfd_pid
        self.peer_validator = peer_validator
        self.on_rotate = on_rotate
        self.health_publisher = health_publisher or (lambda *_args: None)
        self.gateway_pid: int | None = None
        self.gateway_pidfd: int | None = None
        self.epoch = 0
        self.revoked_epoch = 0
        self.lock = threading.Lock()
        self.health_publisher("waiting-for-hermes", 0, None)

    def _monitor_pin(self, epoch: int, pinned_pidfd: int) -> None:
        while True:
            try:
                if not self.pidfd_alive(pinned_pidfd):
                    break
            except OSError:
                break
            time.sleep(0.25)
        with self.lock:
            if self.epoch == epoch and self.gateway_pidfd == pinned_pidfd:
                self.health_publisher("waiting-for-hermes", epoch, None)
                if self.on_rotate is not None and self.revoked_epoch < epoch:
                    self.on_rotate()
                    self.revoked_epoch = epoch

    def __call__(
        self, peer_pid: int, peer_uid: int, _peer_gid: int, peer_pidfd: int
    ) -> int:
        if peer_uid != os.getuid():
            raise BrokerError("broker peer uid is not the broker owner")
        try:
            if self.peer_pidfd_pid(peer_pidfd) != peer_pid:
                raise BrokerError("Hermes peer pidfd does not match SO_PEERCRED")
        except GATEWAY_TRUST.GatewayTrustError as exc:
            raise BrokerError(str(exc)) from exc
        with self.lock:
            if self.gateway_pidfd is not None and self.pidfd_alive(self.gateway_pidfd):
                if peer_pid != self.gateway_pid:
                    raise BrokerError(
                        "broker peer is not the pinned Hermes gateway process"
                    )
                return self.epoch

            had_pin = self.gateway_pidfd is not None
            old_epoch = self.epoch
            try:
                snapshot = self.resolver(self.container_name)
            except GATEWAY_TRUST.GatewayTrustError as exc:
                self.health_publisher("waiting-for-hermes", self.epoch, None)
                raise BrokerError(str(exc)) from exc
            if not GATEWAY_TRUST.provenance_matches(
                self.baseline, snapshot.provenance
            ):
                self.health_publisher("repin-required", self.epoch, None)
                raise BrokerError("Hermes container provenance changed; repin required")
            if peer_pid != snapshot.gateway_pid:
                raise BrokerError("broker peer is not the default Hermes gateway")
            try:
                self.peer_validator(peer_pid, peer_pidfd, snapshot)
                pinned_pidfd = self.pidfd_open(peer_pid)
            except (GATEWAY_TRUST.GatewayTrustError, OSError) as exc:
                raise BrokerError("cannot pin the supervised Hermes gateway") from exc
            if self.gateway_pidfd is not None:
                try:
                    os.close(self.gateway_pidfd)
                except OSError:
                    pass
            self.gateway_pid = peer_pid
            self.gateway_pidfd = pinned_pidfd
            self.epoch += 1
            if (
                had_pin
                and self.on_rotate is not None
                and self.revoked_epoch < old_epoch
            ):
                self.on_rotate()
                self.revoked_epoch = old_epoch
            self.health_publisher("ready", self.epoch, peer_pid)
            threading.Thread(
                target=self._monitor_pin,
                args=(self.epoch, pinned_pidfd),
                name="workstation-gateway-pidfd-monitor",
                daemon=True,
            ).start()
            return self.epoch


@dataclass
class Session:
    id: str
    owner: str
    command: str
    workdir: str
    process: subprocess.Popen
    timeout: int
    pty_mode: bool
    started_at: float = field(default_factory=time.time)
    output: str = ""
    output_base: int = 0
    exit_code: int | None = None
    completion_reason: str = "running"
    termination_source: str = ""
    stdin_closed: bool = False
    master_fd: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def append(self, chunk: bytes) -> None:
        text = clean_output(chunk.decode("utf-8", errors="replace"))
        if not text:
            return
        with self.lock:
            self.output += text
            if len(self.output) > MAX_OUTPUT_CHARS:
                removed = len(self.output) - MAX_OUTPUT_CHARS
                self.output = self.output[removed:]
                self.output_base += removed

    def snapshot(self, offset: int | None = None) -> dict[str, Any]:
        with self.lock:
            start = self.output_base if offset is None else max(offset, self.output_base)
            relative = min(max(0, start - self.output_base), len(self.output))
            chunk = self.output[relative:]
            next_offset = self.output_base + len(self.output)
        return {
            "process_id": self.id,
            "command": self.command,
            "workdir": self.workdir,
            "running": not self.done.is_set(),
            "exit_code": self.exit_code,
            "completion_reason": self.completion_reason,
            "termination_source": self.termination_source,
            "output": chunk,
            "offset": start,
            "next_offset": next_offset,
            "truncated": offset is not None and offset < self.output_base,
            "started_at": self.started_at,
        }


class ProcessBroker:
    def __init__(
        self,
        config: dict[str, Any],
        gateway: str,
        *,
        peer_authorizer: Callable[[int, int, int, int], int],
    ):
        self.config = config
        self.gateway = gateway
        self.peer_authorizer = peer_authorizer
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.starting = 0
        self.stopping = threading.Event()
        self.operation_slots = threading.BoundedSemaphore(MAX_ACTIVE_PROCESSES)
        self.gateway_lock = threading.RLock()
        self.gateway_epoch = 0

        self.transfer_manager = TRANSFER.TransferManager(self)

    @contextmanager
    def gateway_guard(self, expected_epoch: int | None):
        self.gateway_lock.acquire()
        try:
            if expected_epoch is not None and expected_epoch != self.gateway_epoch:
                raise BrokerError("Hermes gateway authorization epoch changed")
            yield
        finally:
            self.gateway_lock.release()

    def activate_gateway_epoch(self, epoch: int) -> None:
        with self.gateway_lock:
            if self.gateway_epoch == 0 and epoch == 1:
                self.gateway_epoch = epoch
            elif epoch != self.gateway_epoch:
                raise BrokerError("Hermes gateway authorization epoch changed")

    def revoke_for_gateway_rotation(self) -> dict[str, int]:
        with self.gateway_lock:
            self.gateway_epoch += 1
            with self.lock:
                active = [
                    item for item in self.sessions.values() if not item.done.is_set()
                ]
            for item in active:
                self._terminate(
                    item, signal.SIGTERM, "killed", "gateway_rotation"
                )
            transfer_revoked = self.transfer_manager.revoke_all()
            return {
                "processes": len(active),
                "reservations": transfer_revoked["reservations"],
                "transfers": transfer_revoked["transfers"],
            }

    def ssh_command(self, command: str, workdir: str, pty_mode: bool) -> list[str]:
        known_hosts = Path(self.config["_broker_known_hosts"])
        identity = self.config["execution"]["_identity_path"]
        remote_command = (
            f"cd -- {shlex.quote(workdir)} && "
            f"exec /bin/bash -lc {shlex.quote(command)}"
        )
        result = [
            self.config["_ssh_binary"],
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "HostKeyAlgorithms=ssh-ed25519",
            "-o",
            "PubkeyAcceptedAlgorithms=ssh-ed25519",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ForwardX11=no",
            "-o",
            "PermitLocalCommand=no",
            "-o",
            "RequestTTY=force" if pty_mode else "RequestTTY=no",
            "-i",
            str(identity),
            "-p",
            str(self.config["execution"]["gateway_port"]),
            f"{self.config['remote']['user']}@{self.gateway}",
            remote_command,
        ]
        return result

    def _active_count(self) -> int:
        return sum(not item.done.is_set() for item in self.sessions.values())

    def _prune(self) -> None:
        now = time.time()
        expired: list[str] = []
        with self.lock:
            for process_id, session in self.sessions.items():
                if session.done.is_set() and now - session.started_at > FINISHED_TTL_SECONDS:
                    expired.append(process_id)
            for process_id in expired:
                self.sessions.pop(process_id, None)

    def _spawn(
        self,
        *,
        owner: str,
        command: str,
        workdir: str,
        timeout: int,
        pty_mode: bool,
        gateway_epoch: int | None = None,
    ) -> Session:
        with self.gateway_guard(gateway_epoch):
            return self._spawn_authorized(
                owner=owner,
                command=command,
                workdir=workdir,
                timeout=timeout,
                pty_mode=pty_mode,
            )

    def _spawn_authorized(
        self,
        *,
        owner: str,
        command: str,
        workdir: str,
        timeout: int,
        pty_mode: bool,
    ) -> Session:
        if not self.operation_slots.acquire(blocking=False):
            raise BrokerError(
                f"at most {MAX_ACTIVE_PROCESSES} workstation operations may run"
            )
        with self.lock:
            if self._active_count() + self.starting >= MAX_ACTIVE_PROCESSES:
                self.operation_slots.release()
                raise BrokerError(
                    f"at most {MAX_ACTIVE_PROCESSES} workstation processes may run"
                )
            self.starting += 1
        master_fd: int | None = None
        slave_fd: int | None = None
        try:
            popen_kwargs: dict[str, Any] = {
                "start_new_session": True,
                "close_fds": True,
            }
            if pty_mode:
                master_fd, slave_fd = pty.openpty()
                popen_kwargs.update(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd)
            else:
                popen_kwargs.update(
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            process = subprocess.Popen(
                self.ssh_command(command, workdir, pty_mode),
                **popen_kwargs,
            )
        except Exception:
            with self.lock:
                self.starting -= 1
            if master_fd is not None:
                os.close(master_fd)
            self.operation_slots.release()
            raise
        finally:
            if slave_fd is not None:
                os.close(slave_fd)
        session = Session(
            id=f"wproc_{secrets.token_hex(12)}",
            owner=owner,
            command=command,
            workdir=workdir,
            process=process,
            timeout=timeout,
            pty_mode=pty_mode,
            master_fd=master_fd,
        )
        with self.lock:
            self.sessions[session.id] = session
            self.starting -= 1
        threading.Thread(
            target=self._reader,
            args=(session,),
            name=f"workstation-reader-{session.id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._deadline,
            args=(session,),
            name=f"workstation-deadline-{session.id}",
            daemon=True,
        ).start()
        return session

    def _reader(self, session: Session) -> None:
        try:
            if session.pty_mode:
                assert session.master_fd is not None
                while True:
                    try:
                        chunk = os.read(session.master_fd, 8192)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    session.append(chunk)
            else:
                assert session.process.stdout is not None
                while True:
                    chunk = session.process.stdout.read(8192)
                    if not chunk:
                        break
                    session.append(chunk)
        finally:
            code = session.process.wait()
            with session.lock:
                session.exit_code = code
                if session.completion_reason == "running":
                    session.completion_reason = "exited"
            session.done.set()
            if session.master_fd is not None:
                try:
                    os.close(session.master_fd)
                except OSError:
                    pass
            for stream in (session.process.stdin, session.process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
            self.operation_slots.release()

    def _deadline(self, session: Session) -> None:
        if session.done.wait(session.timeout):
            return
        self._terminate(session, signal.SIGTERM, "timeout", "broker_timeout")

    def _owned(self, owner: str, process_id: Any) -> Session:
        process_id = validate_process_id(process_id)
        with self.lock:
            session = self.sessions.get(process_id)
        if session is None or session.owner != owner:
            raise BrokerError("process not found")
        return session

    def _terminate(
        self, session: Session, sig: signal.Signals, reason: str, source: str
    ) -> None:
        if session.done.is_set():
            return
        with session.lock:
            session.completion_reason = reason
            session.termination_source = source
        try:
            os.killpg(session.process.pid, sig)
        except ProcessLookupError:
            return
        if sig == signal.SIGTERM and not session.done.wait(2):
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _write(self, session: Session, data: str) -> None:
        if session.stdin_closed or session.done.is_set():
            raise BrokerError("process stdin is closed")
        encoded = data.encode("utf-8")
        if session.pty_mode:
            assert session.master_fd is not None
            os.write(session.master_fd, encoded)
        else:
            if session.process.stdin is None:
                raise BrokerError("process stdin is unavailable")
            session.process.stdin.write(encoded)
            session.process.stdin.flush()

    def handle(
        self, request: dict[str, Any], gateway_epoch: int | None = None
    ) -> dict[str, Any]:
        self._prune()
        if not isinstance(request, dict):
            raise BrokerError("request must be an object")
        with self.gateway_guard(gateway_epoch):
            pass
        action = request.get("action")
        if action in {
            "reserve",
            "cancel_reservation",
            "transfer_preflight",
            "transfer_destination",
            "transfer_cancel",
        }:
            try:
                with self.gateway_guard(gateway_epoch):
                    return self.transfer_manager.handle_control(request)
            except TRANSFER.TransferBrokerError as exc:
                raise BrokerError(str(exc)) from exc
        allowed = {
            "action",
            "owner",
            "command",
            "workdir",
            "timeout",
            "background",
            "deferred",
            "pty",
            "process_id",
            "data",
            "offset",
            "rows",
            "cols",
            "reservation",
            "operation_hash",
        }
        unknown = sorted(set(request) - allowed)
        if unknown:
            raise BrokerError(f"unknown request field: {unknown[0]}")
        owner = validate_owner(request.get("owner"))
        action = request.get("action")

        if action == "execute":
            command = request.get("command")
            if not isinstance(command, str) or not command.strip():
                raise BrokerError("command must be a non-empty string")
            if len(command) > MAX_COMMAND_CHARS or "\0" in command:
                raise BrokerError("command is too large or contains NUL")
            background = request.get("background", False)
            deferred = request.get("deferred", False)
            pty_mode = request.get("pty", False)
            if not all(isinstance(value, bool) for value in (background, deferred, pty_mode)):
                raise BrokerError("background, deferred and pty must be booleans")
            timeout = bounded_int(
                request.get("timeout"),
                default=(
                    BACKGROUND_DEFAULT_TIMEOUT
                    if background
                    else FOREGROUND_DEFAULT_TIMEOUT
                ),
                minimum=1,
                maximum=(
                    BACKGROUND_MAX_TIMEOUT if background else FOREGROUND_MAX_TIMEOUT
                ),
                label="timeout",
            )
            workdir = validate_workdir(request.get("workdir"))
            approved_operation = {
                "owner": owner,
                "command": command,
                "workdir": workdir,
                "background": background,
                "pty": pty_mode,
                "timeout": timeout,
            }
            digest = TRANSFER.PROTOCOL.terminal_operation_hash(approved_operation)
            if request.get("operation_hash") != digest:
                raise BrokerError("terminal approval operation hash mismatch")
            try:
                self.transfer_manager.reservations.consume(
                    str(request.get("reservation") or ""), owner, digest
                )
            except TRANSFER.TransferBrokerError as exc:
                raise BrokerError(str(exc)) from exc
            session = self._spawn(
                owner=owner,
                command=command,
                workdir=workdir,
                timeout=timeout,
                pty_mode=pty_mode,
                gateway_epoch=gateway_epoch,
            )
            if background or deferred:
                return session.snapshot()
            session.done.wait(timeout + 3)
            return session.snapshot()

        if action == "list":
            with self.lock:
                items = [
                    item.snapshot()
                    for item in self.sessions.values()
                    if item.owner == owner
                ]
            return {"processes": items}

        if action == "revoke_owner":
            with self.lock:
                items = [
                    item
                    for item in self.sessions.values()
                    if item.owner == owner and not item.done.is_set()
                ]
            for item in items:
                self._terminate(item, signal.SIGTERM, "killed", "session_reset")
            transfer_revoked = self.transfer_manager.revoke_owner(owner)
            return {
                "revoked": len(items),
                "transfer_reservations_revoked": transfer_revoked["reservations"],
                "transfers_revoked": transfer_revoked["transfers"],
            }

        session = self._owned(owner, request.get("process_id"))
        if action == "poll":
            return session.snapshot()
        if action == "log":
            offset = bounded_int(
                request.get("offset"),
                default=max(0, session.snapshot()["next_offset"] - 20_000),
                minimum=0,
                maximum=2**63 - 1,
                label="offset",
            )
            return session.snapshot(offset)
        if action == "wait":
            timeout = bounded_int(
                request.get("timeout"),
                default=FOREGROUND_DEFAULT_TIMEOUT,
                minimum=1,
                maximum=FOREGROUND_MAX_TIMEOUT,
                label="timeout",
            )
            completed = session.done.wait(timeout)
            result = session.snapshot()
            result["wait_timed_out"] = not completed
            return result
        if action in {"write", "submit"}:
            data = request.get("data", "")
            if not isinstance(data, str) or len(data) > MAX_STDIN_CHARS or "\0" in data:
                raise BrokerError("data must be a bounded string without NUL")
            self._write(session, data + ("\n" if action == "submit" else ""))
            return {"process_id": session.id, "written": len(data)}
        if action == "close":
            if not session.stdin_closed:
                if session.pty_mode:
                    self._write(session, "\x04")
                elif session.process.stdin is not None:
                    session.process.stdin.close()
                session.stdin_closed = True
            return {"process_id": session.id, "stdin_closed": True}
        if action == "resize":
            if not session.pty_mode or session.master_fd is None:
                raise BrokerError("resize requires a PTY process")
            rows = bounded_int(
                request.get("rows"), default=24, minimum=5, maximum=200, label="rows"
            )
            cols = bounded_int(
                request.get("cols"), default=80, minimum=20, maximum=500, label="cols"
            )
            fcntl.ioctl(
                session.master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
            return {"process_id": session.id, "rows": rows, "cols": cols}
        if action == "interrupt":
            self._terminate(session, signal.SIGINT, "interrupted", "process.interrupt")
            return session.snapshot()
        if action == "kill":
            self._terminate(session, signal.SIGTERM, "killed", "process.kill")
            return session.snapshot()
        raise BrokerError("unknown action")

    def stop(self) -> None:
        self.stopping.set()
        self.transfer_manager.stop()
        with self.lock:
            active = [item for item in self.sessions.values() if not item.done.is_set()]
        for item in active:
            self._terminate(item, signal.SIGTERM, "killed", "broker_shutdown")


def peer_credentials(connection: socket.socket) -> tuple[int, int, int, int]:
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        peer_pidfd_raw = connection.getsockopt(
            socket.SOL_SOCKET, SO_PEERPIDFD, struct.calcsize("i")
        )
        peer_pidfd = struct.unpack("i", peer_pidfd_raw)[0]
        os.fstat(peer_pidfd)
    except (OSError, struct.error) as exc:
        raise BrokerError("cannot obtain a stable Hermes peer pidfd") from exc
    pid, uid, gid = struct.unpack("3i", raw)
    return pid, uid, gid, peer_pidfd


def serve_connection(connection: socket.socket, broker: ProcessBroker) -> None:
    peer_pidfd: int | None = None
    result: dict[str, Any] | None = None
    try:
        pid, uid, gid, peer_pidfd = peer_credentials(connection)
        gateway_epoch = broker.peer_authorizer(pid, uid, gid, peer_pidfd)
        broker.activate_gateway_epoch(gateway_epoch)
        connection.settimeout(10)
        prefix = connection.recv(
            len(TRANSFER.PROTOCOL.MAGIC),
            socket.MSG_PEEK | socket.MSG_WAITALL,
        )
        if prefix == TRANSFER.PROTOCOL.MAGIC:
            broker.transfer_manager.handle_stream(connection, gateway_epoch)
            return
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = connection.recv(8192)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_REQUEST_BYTES:
                raise BrokerError("request exceeds size limit")
        line = bytes(chunks).split(b"\n", 1)[0]
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrokerError("request is not valid UTF-8 JSON") from exc
        result = {
            "ok": True,
            "result": broker.handle(request, gateway_epoch),
        }
    except BrokerError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception:
        result = {"ok": False, "error": "internal broker error"}
    finally:
        if peer_pidfd is not None:
            try:
                os.close(peer_pidfd)
            except OSError:
                pass
    if result is not None:
        try:
            encoded = (json.dumps(result, ensure_ascii=True) + "\n").encode("utf-8")
            connection.sendall(encoded)
        finally:
            connection.close()


def serve(socket_path: Path, broker: ProcessBroker) -> None:
    if not socket_path.is_absolute():
        raise BrokerError("socket path must be absolute")
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = socket_path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BrokerError("socket directory must be private and broker-owned")
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(8)
    server.settimeout(1)

    def stop_handler(_signum, _frame):
        broker.stopping.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        while not broker.stopping.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            threading.Thread(
                target=serve_connection,
                args=(connection, broker),
                daemon=True,
            ).start()
    finally:
        server.close()
        broker.stop()
        socket_path.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--gateway", required=True)
    result.add_argument("--socket", type=Path, required=True)
    result.add_argument("--known-hosts", type=Path, required=True)
    result.add_argument("--trust", type=Path, required=True)
    result.add_argument("--health", type=Path, required=True)
    result.add_argument("--container", default="spartan_gate_hermes")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        state_path = args.socket.parent
        if args.trust.parent != state_path or args.health.parent != state_path:
            raise BrokerError("broker state files must share one private directory")
        state_info = state_path.lstat()
        if (
            stat.S_ISLNK(state_info.st_mode)
            or not stat.S_ISDIR(state_info.st_mode)
            or state_info.st_uid != os.getuid()
            or stat.S_IMODE(state_info.st_mode) & 0o077
        ):
            raise BrokerError("broker state directory permissions are unsafe")
        bridge = load_bridge_module()
        config = bridge.load_config(args.config)
        gateway = bridge.validate_gateway(args.gateway)
        config["_ssh_binary"] = bridge.require_binary("ssh")
        config["_broker_known_hosts"] = args.known_hosts.resolve()
        expected = (
            f"[{gateway}]:{config['execution']['gateway_port']} "
            f"{config['_host_key_type']} {config['_host_key_blob']}\n"
        )
        if args.known_hosts.read_text(encoding="utf-8") != expected:
            raise BrokerError("broker known_hosts does not contain the exact pinned key")
        try:
            baseline = GATEWAY_TRUST.load_baseline(args.trust, args.container)
        except GATEWAY_TRUST.GatewayTrustError as exc:
            raise BrokerError(str(exc)) from exc
        authorizer = DockerGatewayAuthorizer(
            args.container,
            baseline,
            health_publisher=HealthPublisher(args.health),
        )
        broker = ProcessBroker(
            config,
            gateway,
            peer_authorizer=authorizer,
        )
        authorizer.on_rotate = broker.revoke_for_gateway_rotation
        serve(args.socket, broker)
        return 0
    except (BrokerError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
