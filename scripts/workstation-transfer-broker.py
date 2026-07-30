#!/usr/bin/env python3
"""Streaming transfer extension for workstation-terminal-broker."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import pathlib
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import zlib
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIR = (
    ROOT / "infra" / "hermes-plugins" / "spartan-workstation-terminal"
)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROTOCOL = _load(
    "spartan_workstation_transfer_protocol",
    PLUGIN_DIR / "transfer_protocol.py",
)
CORE = _load(
    "spartan_workstation_transfer_core",
    PLUGIN_DIR / "transfer_core.py",
)

OWNER_RE = re.compile(r"^[a-f0-9]{32}:g[0-9]{1,12}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
TRANSFER_RE = re.compile(r"^wtx_[a-f0-9]{24}$")
RESERVATION_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_RESERVATIONS = 4
MAX_ACTIVE_TRANSFERS = 2
MAX_ACTIVE_OPERATIONS = 4
RESERVATION_TTL_SECONDS = 600
MAX_HELPER_OUTPUT = 64 * 1024
REMOTE_ROOTS = {
    "workspace": pathlib.Path("/home/hermes-worker/workspace"),
    "exchange": pathlib.Path("/home/hermes-worker/windows-exchange"),
}


class TransferBrokerError(RuntimeError):
    pass


@dataclass
class Reservation:
    token: str
    owner: str
    operation_hash: str
    expires_at: float


class ReservationStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = RESERVATION_TTL_SECONDS,
        maximum: int = MAX_RESERVATIONS,
    ):
        self.ttl_seconds = ttl_seconds
        self.maximum = maximum
        self.lock = threading.Lock()
        self.items: dict[str, Reservation] = {}

    def _prune_locked(self) -> None:
        now = time.monotonic()
        for token in [
            token for token, item in self.items.items() if item.expires_at <= now
        ]:
            self.items.pop(token, None)

    def reserve(self, owner: str, operation_hash: str) -> dict[str, Any]:
        _validate_owner(owner)
        _validate_hash(operation_hash, "operation_hash")
        with self.lock:
            self._prune_locked()
            if len(self.items) >= self.maximum:
                raise TransferBrokerError("too many pending workstation approvals")
            token = secrets.token_hex(32)
            self.items[token] = Reservation(
                token=token,
                owner=owner,
                operation_hash=operation_hash,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
        return {"reservation": token, "ttl_seconds": self.ttl_seconds}

    def consume(self, token: str, owner: str, operation_hash: str) -> None:
        if not isinstance(token, str) or not RESERVATION_RE.fullmatch(token):
            raise TransferBrokerError("invalid approval reservation")
        _validate_owner(owner)
        _validate_hash(operation_hash, "operation_hash")
        with self.lock:
            item = self.items.get(token)
            if item is None:
                raise TransferBrokerError("approval reservation is missing or consumed")
            if item.expires_at <= time.monotonic():
                self.items.pop(token, None)
                raise TransferBrokerError("approval reservation expired")
            if item.owner != owner or item.operation_hash != operation_hash:
                raise TransferBrokerError("approval reservation does not match operation")
            self.items.pop(token, None)

    def cancel(self, token: str, owner: str) -> bool:
        with self.lock:
            item = self.items.get(token)
            if item is None or item.owner != owner:
                return False
            self.items.pop(token, None)
            return True

    def revoke_owner(self, owner: str) -> int:
        with self.lock:
            tokens = [
                token for token, item in self.items.items() if item.owner == owner
            ]
            for token in tokens:
                self.items.pop(token, None)
            return len(tokens)
    def clear(self) -> int:
        with self.lock:
            count = len(self.items)
            self.items.clear()
            return count



@dataclass
class ActiveTransfer:
    transfer_id: str
    owner: str
    process: subprocess.Popen
    connection: socket.socket
    cancelled: threading.Event


def _validate_owner(value: Any) -> str:
    if not isinstance(value, str) or not OWNER_RE.fullmatch(value):
        raise TransferBrokerError("invalid owner identity")
    return value


def _validate_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise TransferBrokerError(f"invalid {label}")
    return value


def _validate_stream_summary(control: dict[str, Any]) -> None:
    kind = control.get("kind")
    if kind not in {"file", "directory"}:
        raise TransferBrokerError("invalid transfer object kind")
    entry_count = control.get("entry_count")
    total_bytes = control.get("total_bytes")
    if (
        isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or not 1 <= entry_count <= CORE.MAX_ENTRIES
    ):
        raise TransferBrokerError("invalid transfer entry count")
    if (
        isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or not 0 <= total_bytes <= CORE.MAX_TOTAL_BYTES
    ):
        raise TransferBrokerError("invalid transfer byte count")
    _validate_hash(control.get("manifest_sha256"), "manifest_sha256")
    for field in ("replace", "allow_sensitive", "background"):
        if not isinstance(control.get(field), bool):
            raise TransferBrokerError(f"{field} must be a boolean")
    timeout = control.get("timeout")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= 7200
    ):
        raise TransferBrokerError("invalid transfer timeout")
    soft_count = control.get("soft_sensitive_count")
    samples = control.get("sensitive_samples")
    if (
        isinstance(soft_count, bool)
        or not isinstance(soft_count, int)
        or not 0 <= soft_count <= CORE.SENSITIVE_SAMPLE_LIMIT
        or not isinstance(samples, list)
        or len(samples) != soft_count
    ):
        raise TransferBrokerError("invalid sensitive transfer summary")
    if soft_count and not control["allow_sensitive"]:
        raise TransferBrokerError("sensitive transfer was not explicitly allowed")
    for sample in samples:
        if (
            not isinstance(sample, dict)
            or set(sample) != {"path", "level", "reason"}
            or sample.get("level") != "soft"
            or not isinstance(sample.get("reason"), str)
            or len(sample["reason"]) > 200
        ):
            raise TransferBrokerError("invalid sensitive transfer sample")
        CORE.validate_relative(sample.get("path"))
    destination = control.get("expected_destination")
    if not isinstance(destination, dict) or not isinstance(
        destination.get("exists"), bool
    ):
        raise TransferBrokerError("invalid expected destination")
    if destination["exists"] is False:
        if set(destination) != {"exists"} or control["replace"]:
            raise TransferBrokerError("invalid absent destination expectation")
        return
    required = {"exists", "kind", "size", "sha256", "device", "inode", "ctime_ns"}
    if set(destination) != required or destination.get("kind") != "file":
        raise TransferBrokerError("invalid existing destination expectation")
    if not control["replace"] or kind != "file":
        raise TransferBrokerError("existing destination requires file replacement")
    _validate_hash(destination.get("sha256"), "destination sha256")
    for field in ("size", "device", "inode", "ctime_ns"):
        value = destination.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TransferBrokerError("invalid existing destination metadata")


def _bounded_json_output(value: bytes) -> dict[str, Any]:
    if len(value) > MAX_HELPER_OUTPUT:
        raise TransferBrokerError("remote helper response exceeds size limit")
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferBrokerError("remote helper returned invalid JSON") from exc
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        message = decoded.get("error") if isinstance(decoded, dict) else None
        raise TransferBrokerError(str(message or "remote helper failed"))
    result = decoded.get("result")
    if not isinstance(result, dict):
        raise TransferBrokerError("remote helper returned invalid result")
    return result


class TransferManager:
    def __init__(
        self,
        broker: Any,
        *,
        remote_roots: dict[str, pathlib.Path] | None = None,
        ssh_factory: Callable[[str, dict[str, Any]], list[str]] | None = None,
        minimum_free_bytes: int = CORE.MIN_FREE_BYTES,
    ):
        self.broker = broker
        self.remote_roots = remote_roots or dict(REMOTE_ROOTS)
        self.ssh_factory = ssh_factory
        self.minimum_free_bytes = minimum_free_bytes
        self.reservations = ReservationStore()
        self.lock = threading.Lock()
        self.active: dict[str, ActiveTransfer] = {}
        self.starting = 0
        self.exchange_probe_lock = threading.Lock()
        self.exchange_capability: dict[str, Any] | None = None
        self.exchange_capability_error = ""
        source = (PLUGIN_DIR / "transfer_core.py").read_bytes()
        self._remote_source = base64.b85encode(zlib.compress(source, 9)).decode(
            "ascii"
        )

    def _root(self, label: Any) -> pathlib.Path:
        if not isinstance(label, str) or label not in self.remote_roots:
            raise TransferBrokerError("unknown remote root")
        return self.remote_roots[label]

    def _request_for_remote(
        self,
        request: dict[str, Any],
        *,
        source: bool,
    ) -> dict[str, Any]:
        root = self._root(request.get("remote_root"))
        path = request.get("remote_path")
        try:
            CORE.validate_relative(path)
            if request.get("remote_root") == "exchange" and not source:
                CORE.validate_windows_paths([path])
        except CORE.TransferError as exc:
            raise TransferBrokerError(str(exc)) from exc
        return {
            "root": str(root),
            "path": path,
            "allow_sensitive": bool(request.get("allow_sensitive")),
            "windows_destination": bool(
                request.get("remote_root") == "exchange" and not source
            ),
        }

    def _command(self, mode: str, request: dict[str, Any]) -> list[str]:
        if self.ssh_factory is not None:
            return self.ssh_factory(mode, request)
        bootstrap = (
            "import base64,zlib;"
            f"exec(zlib.decompress(base64.b85decode({self._remote_source!r})))"
        )
        command = (
            f"exec python3 -c {shlex.quote(bootstrap)} "
            f"{shlex.quote(mode)} {shlex.quote(json.dumps(request, sort_keys=True))}"
        )
        return self.broker.ssh_command(
            command,
            str(pathlib.Path(request["root"])),
            False,
        )

    def _remote_json(
        self,
        mode: str,
        request: dict[str, Any],
        *,
        timeout: int = 900,
    ) -> dict[str, Any]:
        result = subprocess.run(
            self._command(mode, request),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            try:
                decoded = json.loads(result.stderr[-MAX_HELPER_OUTPUT:].decode("utf-8"))
                message = decoded.get("error")
            except Exception:
                message = None
            raise TransferBrokerError(str(message or "remote helper failed"))
        return _bounded_json_output(result.stdout)
    def _ensure_exchange_upload(self) -> dict[str, Any]:
        with self.exchange_probe_lock:
            if self.exchange_capability is not None:
                return self.exchange_capability
            if self.exchange_capability_error:
                raise TransferBrokerError(self.exchange_capability_error)
            try:
                result = self._remote_json(
                    "probe",
                    {
                        "root": str(self._root("exchange")),
                        "path": ".probe",
                    },
                    timeout=60,
                )
                required = {
                    "atomic_noreplace",
                    "regular_file_replace",
                    "file_fsync",
                    "directory_fsync",
                }
                if any(result.get(item) is not True for item in required):
                    raise TransferBrokerError(
                        "Exchange upload capability probe was incomplete"
                    )
                self.exchange_capability = result
                return result
            except Exception as exc:
                message = (
                    "Exchange remains download-only because its atomic/fsync "
                    f"capability probe failed: {exc}"
                )
                self.exchange_capability_error = message
                raise TransferBrokerError(message) from exc


    def handle_control(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise TransferBrokerError("transfer control must be an object")
        action = request.get("action")
        allowed_by_action = {
            "reserve": {"action", "owner", "operation_hash"},
            "cancel_reservation": {"action", "owner", "reservation"},
            "transfer_preflight": {
                "action",
                "owner",
                "remote_root",
                "remote_path",
                "allow_sensitive",
            },
            "transfer_destination": {
                "action",
                "owner",
                "remote_root",
                "remote_path",
            },
            "transfer_cancel": {"action", "owner", "transfer_id"},
        }
        allowed = allowed_by_action.get(action)
        if allowed is None:
            raise TransferBrokerError("unknown transfer control action")
        unknown = sorted(set(request) - allowed)
        if unknown:
            raise TransferBrokerError(f"unknown request field: {unknown[0]}")
        owner = _validate_owner(request.get("owner"))
        if action == "reserve":
            return self.reservations.reserve(owner, request.get("operation_hash"))
        if action == "cancel_reservation":
            return {
                "cancelled": self.reservations.cancel(
                    str(request.get("reservation") or ""), owner
                )
            }
        if action == "transfer_preflight":
            remote = self._request_for_remote(request, source=True)
            return self._remote_json("preflight", remote)
        if action == "transfer_destination":
            if request.get("remote_root") == "exchange":
                self._ensure_exchange_upload()
            remote = self._request_for_remote(request, source=False)
            return self._remote_json("destination", remote)
        transfer_id = request.get("transfer_id")
        if not isinstance(transfer_id, str) or not TRANSFER_RE.fullmatch(transfer_id):
            raise TransferBrokerError("invalid transfer id")
        with self.lock:
            active = self.active.get(transfer_id)
        if active is None or active.owner != owner:
            raise TransferBrokerError("transfer not found")
        active.cancelled.set()
        try:
            os.killpg(active.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            active.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return {"transfer_id": transfer_id, "cancel_requested": True}

    def revoke_owner(self, owner: str) -> dict[str, int]:
        reservations = self.reservations.revoke_owner(owner)
        with self.lock:
            active = [item for item in self.active.values() if item.owner == owner]
        for item in active:
            item.cancelled.set()
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                item.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return {"reservations": reservations, "transfers": len(active)}

    def revoke_all(self) -> dict[str, int]:
        reservations = self.reservations.clear()
        with self.lock:
            active = list(self.active.values())
        for item in active:
            item.cancelled.set()
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                item.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return {"reservations": reservations, "transfers": len(active)}

    def stop(self) -> None:
        self.reservations.clear()
        with self.lock:
            active = list(self.active.values())
        for item in active:
            item.cancelled.set()
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                item.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    def _validate_stream_control(self, control: dict[str, Any]) -> tuple[str, str, str]:
        allowed = set(PROTOCOL.OPERATION_FIELDS) | {
            "operation_hash",
            "reservation",
            "kind",
            "soft_sensitive_count",
            "sensitive_samples",
        }
        unknown = sorted(set(control) - allowed)
        if unknown:
            raise TransferBrokerError(f"unknown transfer stream field: {unknown[0]}")
        operation = control.get("operation")
        if operation not in {"upload", "download"}:
            raise TransferBrokerError("invalid transfer operation")
        owner = _validate_owner(control.get("owner"))
        transfer_id = control.get("transfer_id")
        if not isinstance(transfer_id, str) or not TRANSFER_RE.fullmatch(transfer_id):
            raise TransferBrokerError("invalid transfer id")
        CORE.validate_relative(control.get("local_path"))
        CORE.validate_relative(control.get("remote_path"))
        if control.get("local_workspace") != "default":
            profile = control.get("local_workspace")
            if not isinstance(profile, str) or not CORE.PROFILE_RE.fullmatch(profile):
                raise TransferBrokerError("invalid local workspace label")
        self._root(control.get("remote_root"))
        if operation == "upload" and control.get("remote_root") == "exchange":
            CORE.validate_windows_paths([control["remote_path"]])
        _validate_stream_summary(control)
        digest = PROTOCOL.operation_hash(control)
        if control.get("operation_hash") != digest:
            raise TransferBrokerError("transfer operation hash mismatch")
        return operation, owner, transfer_id

    def _spawn(self, mode: str, request: dict[str, Any]) -> subprocess.Popen:
        return subprocess.Popen(
            self._command(mode, request),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )

    @staticmethod
    def _drain_stderr(process: subprocess.Popen, sink: bytearray) -> None:
        assert process.stderr is not None
        while True:
            chunk = process.stderr.read(8192)
            if not chunk:
                break
            room = MAX_HELPER_OUTPUT - len(sink)
            if room > 0:
                sink.extend(chunk[:room])

    def _claim_slot(self, transfer_id: str) -> None:
        with self.lock:
            if len(self.active) + self.starting >= MAX_ACTIVE_TRANSFERS:
                raise TransferBrokerError(
                    "workstation transfer concurrency limit reached"
                )
            if transfer_id in self.active:
                raise TransferBrokerError("transfer id is already active")
            if not self.broker.operation_slots.acquire(blocking=False):
                raise TransferBrokerError(
                    "workstation operation concurrency limit reached"
                )
            self.starting += 1

    def _release_claim(self) -> None:
        with self.lock:
            self.starting = max(0, self.starting - 1)

    def _register_active(
        self,
        transfer_id: str,
        owner: str,
        process: subprocess.Popen,
        connection: socket.socket,
    ) -> ActiveTransfer:
        with self.lock:
            active = ActiveTransfer(
                transfer_id,
                owner,
                process,
                connection,
                threading.Event(),
            )
            self.active[transfer_id] = active
            self.starting = max(0, self.starting - 1)
            return active

    def _remote_stream_request(self, control: dict[str, Any]) -> dict[str, Any]:
        return {
            "root": str(self._root(control["remote_root"])),
            "path": control["remote_path"],
            "summary": {
                key: control[key]
                for key in (
                    "kind",
                    "entry_count",
                    "total_bytes",
                    "manifest_sha256",
                    "soft_sensitive_count",
                    "sensitive_samples",
                )
            },
            "transfer_id": control["transfer_id"],
            "replace": bool(control.get("replace")),
            "expected_destination": control.get("expected_destination"),
            "minimum_free_bytes": self.minimum_free_bytes,
            "windows_destination": control["remote_root"] == "exchange",
        }

    def handle_stream(
        self, connection: socket.socket, gateway_epoch: int | None = None
    ) -> None:
        process: subprocess.Popen | None = None
        transfer_id = ""
        slot_claimed = False
        operation_slot_claimed = False
        try:
            connection.settimeout(60)
            PROTOCOL.recv_preamble(connection)
            control = PROTOCOL.recv_control(connection)
            operation, owner, transfer_id = self._validate_stream_control(control)
            if operation == "upload" and control.get("remote_root") == "exchange":
                self._ensure_exchange_upload()
            digest = PROTOCOL.operation_hash(control)
            guard = (
                self.broker.gateway_guard(gateway_epoch)
                if gateway_epoch is not None
                else nullcontext()
            )
            with guard:
                self.reservations.consume(
                    str(control.get("reservation") or ""), owner, digest
                )
                remote_request = self._remote_stream_request(control)
                self._claim_slot(transfer_id)
                slot_claimed = True
                operation_slot_claimed = True
                process = self._spawn(
                    "receive" if operation == "upload" else "send",
                    remote_request,
                )
                active = self._register_active(transfer_id, owner, process, connection)
                slot_claimed = False
            stderr = bytearray()
            stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process, stderr),
                daemon=True,
            )
            stderr_thread.start()
            if operation == "upload":
                assert process.stdin is not None
                while True:
                    frame_type, payload = PROTOCOL.recv_frame(connection)
                    if frame_type == PROTOCOL.FRAME_DATA:
                        process.stdin.write(payload)
                        process.stdin.flush()
                    elif frame_type == PROTOCOL.FRAME_END:
                        break
                    else:
                        raise TransferBrokerError("unexpected upload frame")
                process.stdin.close()
                assert process.stdout is not None
                output = process.stdout.read(MAX_HELPER_OUTPUT + 1)
                code = process.wait(timeout=int(control.get("timeout") or 1800))
                stderr_thread.join(timeout=2)
                if active.cancelled.is_set():
                    raise TransferBrokerError("transfer cancelled")
                if code != 0:
                    try:
                        message = json.loads(stderr.decode("utf-8")).get("error")
                    except Exception:
                        message = None
                    raise TransferBrokerError(str(message or "remote receiver failed"))
                result = _bounded_json_output(output)
            else:
                frame_type, payload = PROTOCOL.recv_frame(connection)
                if frame_type != PROTOCOL.FRAME_END or payload:
                    raise TransferBrokerError("download control must end before data")
                assert process.stdin is not None
                process.stdin.close()
                assert process.stdout is not None
                while True:
                    chunk = process.stdout.read(PROTOCOL.MAX_DATA_BYTES)
                    if not chunk:
                        break
                    PROTOCOL.send_frame(connection, PROTOCOL.FRAME_DATA, chunk)
                code = process.wait(timeout=int(control.get("timeout") or 1800))
                stderr_thread.join(timeout=2)
                if active.cancelled.is_set():
                    raise TransferBrokerError("transfer cancelled")
                if code != 0:
                    try:
                        message = json.loads(stderr.decode("utf-8")).get("error")
                    except Exception:
                        message = None
                    raise TransferBrokerError(str(message or "remote sender failed"))
                result = {
                    key: control[key]
                    for key in (
                        "kind",
                        "entry_count",
                        "total_bytes",
                        "manifest_sha256",
                        "soft_sensitive_count",
                        "sensitive_samples",
                    )
                }
                PROTOCOL.send_frame(connection, PROTOCOL.FRAME_END)
            PROTOCOL.send_frame(
                connection,
                PROTOCOL.FRAME_RESULT,
                PROTOCOL.canonical_json({"ok": True, "result": result}),
            )
        except Exception as exc:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                PROTOCOL.send_frame(
                    connection,
                    PROTOCOL.FRAME_ERROR,
                    PROTOCOL.canonical_json({"ok": False, "error": str(exc)}),
                )
            except OSError:
                pass
        finally:
            if slot_claimed:
                self._release_claim()
            if operation_slot_claimed:
                self.broker.operation_slots.release()
            if transfer_id:
                with self.lock:
                    self.active.pop(transfer_id, None)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            try:
                connection.close()
            except OSError:
                pass
