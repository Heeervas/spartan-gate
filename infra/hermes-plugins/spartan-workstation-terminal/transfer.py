"""Approval-gated file and directory transfer for Hermes Workstation."""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_SOCKET = "/run/spartan-workstation/workstation-terminal.sock"
DEFAULT_WORKSPACE = Path("/opt/data/workspace")
PROFILE_ROOT = Path("/opt/data/profiles")
DISPLAY_LABEL = "Hermes Workstation"
APPROVAL_SCOPE = "hermes_workstation"
MAX_TIMEOUT = 7200
DEFAULT_TIMEOUT = 1800
FINISHED_TTL_SECONDS = 1800


def _load_sibling(name: str):
    path = PLUGIN_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"spartan_workstation_{name}", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load workstation {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load_sibling("transfer_core")
PROTOCOL = _load_sibling("transfer_protocol")


TRANSFER_SCHEMA = {
    "name": "workstation_transfer",
    "description": (
        "Transfer one regular file or directory between a Hermes workspace and "
        "the fixed Hermes Workstation. Every transfer requires explicit "
        "one-time approval. Paths are relative to fixed roots; SSH/network "
        "parameters and arbitrary absolute paths are unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["upload", "download"]},
            "local_workspace": {
                "type": "string",
                "default": "default",
                "description": (
                    "default, or an existing Hermes profile label whose "
                    "workspace is under /opt/data/profiles."
                ),
            },
            "local_path": {
                "type": "string",
                "description": "Relative path inside the selected Hermes workspace.",
            },
            "remote_root": {
                "type": "string",
                "enum": ["workspace", "exchange"],
            },
            "remote_path": {
                "type": "string",
                "description": "Relative path inside the selected workstation root.",
            },
            "replace": {
                "type": "boolean",
                "default": False,
                "description": "Replace an existing regular file, never a directory.",
            },
            "allow_sensitive": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Permit soft-sensitive paths after highlighted approval. "
                    "Private keys and credential-store paths remain forbidden."
                ),
            },
            "background": {"type": "boolean", "default": False},
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TIMEOUT,
            },
        },
        "required": ["operation", "local_path", "remote_root", "remote_path"],
        "additionalProperties": False,
    },
}


STATUS_SCHEMA = {
    "name": "workstation_transfer_status",
    "description": (
        "List, inspect, wait for, or cancel transfers started by the current "
        "Hermes session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "wait", "cancel"],
            },
            "transfer_id": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 900},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


@dataclass
class TransferJob:
    transfer_id: str
    owner: str
    operation: str
    local_workspace: str
    local_path: str
    remote_root: str
    remote_path: str
    timeout: int
    state: str = "approved"
    result: dict[str, Any] | None = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    connection: socket.socket | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            value = {
                "transfer_id": self.transfer_id,
                "operation": self.operation,
                "local_workspace": self.local_workspace,
                "local_path": self.local_path,
                "remote_root": self.remote_root,
                "remote_path": self.remote_path,
                "state": self.state,
                "running": not self.done.is_set(),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
            if self.result is not None:
                value["result"] = dict(self.result)
            if self.error:
                value["error"] = self.error
            return value


class TransferRuntime:
    def __init__(
        self,
        *,
        broker_call: Callable[..., dict[str, Any]],
        socket_path: Callable[[], str],
        session_key: Callable[[], str],
        owner_for: Callable[[str], str],
        error_response: Callable[[str], str],
    ):
        self.broker_call = broker_call
        self.socket_path = socket_path
        self.session_key = session_key
        self.owner_for = owner_for
        self.error_response = error_response
        self.lock = threading.Lock()
        self.jobs: dict[str, TransferJob] = {}

    @staticmethod
    def _workspace(label: Any) -> tuple[str, Path]:
        if label in (None, "", "default"):
            return "default", DEFAULT_WORKSPACE
        if not isinstance(label, str) or not CORE.PROFILE_RE.fullmatch(label):
            raise CORE.TransferError("invalid local workspace label")
        return label, PROFILE_ROOT / label / "workspace"

    def _prune(self) -> None:
        cutoff = time.time() - FINISHED_TTL_SECONDS
        with self.lock:
            stale = [
                transfer_id
                for transfer_id, job in self.jobs.items()
                if job.done.is_set() and (job.finished_at or 0) < cutoff
            ]
            for transfer_id in stale:
                self.jobs.pop(transfer_id, None)

    @staticmethod
    def _validate_args(args: dict[str, Any]) -> dict[str, Any]:
        operation = args.get("operation")
        if operation not in {"upload", "download"}:
            raise CORE.TransferError("operation must be upload or download")
        remote_root = args.get("remote_root")
        if remote_root not in {"workspace", "exchange"}:
            raise CORE.TransferError("unknown remote root")
        local_path = args.get("local_path")
        remote_path = args.get("remote_path")
        CORE.validate_relative(local_path)
        CORE.validate_relative(remote_path)
        if operation == "upload" and remote_root == "exchange":
            CORE.validate_windows_paths([remote_path])
        replace = args.get("replace", False)
        allow_sensitive = args.get("allow_sensitive", False)
        background = args.get("background", False)
        if not all(
            isinstance(value, bool)
            for value in (replace, allow_sensitive, background)
        ):
            raise CORE.TransferError(
                "replace, allow_sensitive and background must be booleans"
            )
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= MAX_TIMEOUT
        ):
            raise CORE.TransferError(
                f"timeout must be between 1 and {MAX_TIMEOUT}"
            )
        return {
            "operation": operation,
            "local_path": local_path,
            "remote_root": remote_root,
            "remote_path": remote_path,
            "replace": replace,
            "allow_sensitive": allow_sensitive,
            "background": background,
            "timeout": timeout,
        }

    @staticmethod
    def _validate_destination(
        summary: dict[str, Any],
        destination: dict[str, Any],
        replace: bool,
    ) -> None:
        if not destination.get("exists"):
            if replace:
                raise CORE.TransferError(
                    "replace=true requires an existing regular file"
                )
            return
        if destination.get("kind") == "directory":
            raise CORE.TransferError("an existing directory is never replaced or merged")
        if not replace:
            raise CORE.TransferError("destination already exists; replace=true is required")
        if summary.get("kind") != "file" or destination.get("kind") != "file":
            raise CORE.TransferError("replace is supported only for regular files")

    @staticmethod
    def _approval_text(control: dict[str, Any]) -> tuple[str, str]:
        arrow = (
            f"Hermes {control['local_workspace']}:{control['local_path']} -> "
            f"Hermes Workstation {control['remote_root']}:{control['remote_path']}"
            if control["operation"] == "upload"
            else
            f"Hermes Workstation {control['remote_root']}:{control['remote_path']} -> "
            f"Hermes {control['local_workspace']}:{control['local_path']}"
        )
        lines = [
            "One-time bounded workstation transfer.",
            f"Direction: {control['operation']}",
            f"Object: {control['kind']}; entries={control['entry_count']}; "
            f"bytes={control['total_bytes']}",
            f"Manifest SHA256: {control['manifest_sha256']}",
            f"Replace existing regular file: {str(control['replace']).lower()}",
        ]
        destination = control["expected_destination"]
        if destination.get("exists"):
            lines.append(
                "Observed destination SHA256: "
                + str(destination.get("sha256") or "<not-a-file>")
            )
        if control.get("soft_sensitive_count"):
            lines.append(
                "SENSITIVE CONTENT: "
                + json.dumps(
                    control.get("sensitive_samples") or [],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if control["replace"]:
            lines.append(
                "Replacement is rechecked immediately before publish, but "
                "publication is not linearizable against an uncooperative writer."
            )
        reason = "\n".join(lines)
        if len(reason) > 8000:
            raise CORE.TransferError("approval summary exceeds display limit")
        return arrow, reason

    def _preflight(
        self, values: dict[str, Any], owner: str, workspace: Path
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if values["operation"] == "upload":
            summary = CORE.preflight_source(
                workspace,
                values["local_path"],
                allow_sensitive=values["allow_sensitive"],
                windows_destination=values["remote_root"] == "exchange",
            )
            destination = self.broker_call(
                {
                    "action": "transfer_destination",
                    "owner": owner,
                    "remote_root": values["remote_root"],
                    "remote_path": values["remote_path"],
                }
            )
        else:
            summary = self.broker_call(
                {
                    "action": "transfer_preflight",
                    "owner": owner,
                    "remote_root": values["remote_root"],
                    "remote_path": values["remote_path"],
                    "allow_sensitive": values["allow_sensitive"],
                },
                timeout=min(values["timeout"], 900) + 10,
            )
            destination = CORE.destination_metadata(
                workspace, values["local_path"]
            )
        self._validate_destination(summary, destination, values["replace"])
        return summary, destination

    def start(self, args: dict[str, Any], **_context: Any) -> str:
        reservation = ""
        owner = ""
        try:
            values = self._validate_args(args)
            workspace_label, workspace = self._workspace(
                args.get("local_workspace")
            )
            session_key = self.session_key()
            owner = self.owner_for(session_key)
            summary, destination = self._preflight(values, owner, workspace)
            transfer_id = f"wtx_{secrets.token_hex(12)}"
            control = {
                **values,
                "owner": owner,
                "transfer_id": transfer_id,
                "local_workspace": workspace_label,
                **CORE.public_summary(summary),
                "expected_destination": destination,
            }
            digest = PROTOCOL.operation_hash(control)
            reservation = str(
                self.broker_call(
                    {
                        "action": "reserve",
                        "owner": owner,
                        "operation_hash": digest,
                    }
                ).get("reservation")
                or ""
            )
            if not reservation:
                raise RuntimeError("broker did not create an approval reservation")
            command, reason = self._approval_text(control)
            from tools.approval import request_one_time_tool_approval

            approval = request_one_time_tool_approval(
                "workstation_transfer",
                reason,
                command=command,
                workdir=str(workspace),
                approval_scope=APPROVAL_SCOPE,
                display_label=DISPLAY_LABEL,
            )
            if not approval.get("approved"):
                self.broker_call(
                    {
                        "action": "cancel_reservation",
                        "owner": owner,
                        "reservation": reservation,
                    }
                )
                return self.error_response(
                    str(approval.get("message") or "transfer approval denied")
                )
            control["operation_hash"] = digest
            control["reservation"] = reservation
            job = TransferJob(
                transfer_id=transfer_id,
                owner=owner,
                operation=values["operation"],
                local_workspace=workspace_label,
                local_path=values["local_path"],
                remote_root=values["remote_root"],
                remote_path=values["remote_path"],
                timeout=values["timeout"],
            )
            with self.lock:
                self.jobs[transfer_id] = job
            worker = threading.Thread(
                target=self._run,
                args=(job, workspace, summary, destination, control),
                name=f"workstation-transfer-{transfer_id}",
                daemon=True,
            )
            worker.start()
            if values["background"]:
                return json.dumps(
                    {"ok": True, "target": DISPLAY_LABEL, **job.snapshot()},
                    ensure_ascii=False,
                )
            return self._wait_result(job, values["timeout"])
        except (CORE.TransferError, OSError, RuntimeError, ValueError) as exc:
            if reservation and owner:
                try:
                    self.broker_call(
                        {
                            "action": "cancel_reservation",
                            "owner": owner,
                            "reservation": reservation,
                        }
                    )
                except Exception:
                    pass
            return self.error_response(str(exc))

    def _run(
        self,
        job: TransferJob,
        workspace: Path,
        summary: dict[str, Any],
        destination: dict[str, Any],
        control: dict[str, Any],
    ) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(job.timeout)
            connection.connect(self.socket_path())
            with job.lock:
                job.connection = connection
                job.state = "running"
            PROTOCOL.send_preamble(connection)
            PROTOCOL.send_control(connection, control)
            if job.operation == "upload":
                CORE.stream_tar(
                    workspace,
                    job.local_path,
                    summary,
                    PROTOCOL.FramedWriter(connection),
                )
                PROTOCOL.send_frame(connection, PROTOCOL.FRAME_END)
                frame_type, payload = PROTOCOL.recv_frame(connection)
                result = self._decode_result(frame_type, payload)
            else:
                PROTOCOL.send_frame(connection, PROTOCOL.FRAME_END)
                result = CORE.receive_tar(
                    PROTOCOL.FramedReader(connection),
                    workspace,
                    job.local_path,
                    summary,
                    transfer_id=job.transfer_id,
                    replace=control["replace"],
                    expected_destination=destination,
                )
                frame_type, payload = PROTOCOL.recv_frame(connection)
                remote = self._decode_result(frame_type, payload)
                if remote.get("manifest_sha256") != result.get("manifest_sha256"):
                    raise CORE.TransferError("remote result does not match local publish")
            if result.get("manifest_sha256") != summary.get("manifest_sha256"):
                raise CORE.TransferError("transfer result does not match approved manifest")
            with job.lock:
                if job.cancelled.is_set():
                    raise CORE.TransferError("transfer cancelled")
                job.result = result
                job.state = "completed"
        except Exception as exc:
            with job.lock:
                job.error = "transfer cancelled" if job.cancelled.is_set() else str(exc)
                job.state = "cancelled" if job.cancelled.is_set() else "failed"
        finally:
            try:
                connection.close()
            except OSError:
                pass
            with job.lock:
                job.connection = None
                job.finished_at = time.time()
            job.done.set()

    @staticmethod
    def _decode_result(frame_type: int, payload: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(payload.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CORE.TransferError("broker returned invalid transfer result") from exc
        if frame_type == PROTOCOL.FRAME_ERROR:
            raise CORE.TransferError(str(decoded.get("error") or "transfer failed"))
        if frame_type != PROTOCOL.FRAME_RESULT or decoded.get("ok") is not True:
            raise CORE.TransferError("broker returned invalid transfer result")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise CORE.TransferError("broker returned invalid transfer result")
        return result

    def _wait_result(self, job: TransferJob, timeout: int) -> str:
        try:
            from tools.interrupt import is_interrupted
        except ImportError:
            is_interrupted = lambda: False
        deadline = time.monotonic() + timeout + 3
        while not job.done.wait(0.25):
            if is_interrupted() or time.monotonic() >= deadline:
                self._cancel(job)
                job.done.wait(3)
                break
        snapshot = job.snapshot()
        if snapshot["state"] == "completed":
            return json.dumps(
                {"ok": True, "target": DISPLAY_LABEL, **snapshot},
                ensure_ascii=False,
            )
        return self.error_response(snapshot.get("error") or "transfer did not complete")

    def status(self, args: dict[str, Any], **_context: Any) -> str:
        try:
            self._prune()
            action = args.get("action")
            if action not in {"list", "poll", "wait", "cancel"}:
                raise CORE.TransferError("unknown transfer status action")
            owner = self.owner_for(self.session_key())
            if action == "list":
                with self.lock:
                    jobs = [
                        job.snapshot()
                        for job in self.jobs.values()
                        if job.owner == owner
                    ]
                return json.dumps(
                    {"ok": True, "target": DISPLAY_LABEL, "transfers": jobs},
                    ensure_ascii=False,
                )
            transfer_id = args.get("transfer_id")
            if (
                not isinstance(transfer_id, str)
                or not CORE.TRANSFER_ID_RE.fullmatch(transfer_id)
            ):
                raise CORE.TransferError("valid transfer_id is required")
            with self.lock:
                job = self.jobs.get(transfer_id)
            if job is None or job.owner != owner:
                raise CORE.TransferError("transfer not found")
            if action == "cancel":
                self._cancel(job)
            elif action == "wait":
                timeout = args.get("timeout", 180)
                if (
                    isinstance(timeout, bool)
                    or not isinstance(timeout, int)
                    or not 1 <= timeout <= 900
                ):
                    raise CORE.TransferError("wait timeout must be between 1 and 900")
                job.done.wait(timeout)
            return json.dumps(
                {"ok": True, "target": DISPLAY_LABEL, **job.snapshot()},
                ensure_ascii=False,
            )
        except (CORE.TransferError, OSError, RuntimeError, ValueError) as exc:
            return self.error_response(str(exc))

    def _cancel(self, job: TransferJob) -> None:
        job.cancelled.set()
        with job.lock:
            connection = job.connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self.broker_call(
                {
                    "action": "transfer_cancel",
                    "owner": job.owner,
                    "transfer_id": job.transfer_id,
                }
            )
        except Exception:
            pass

    def reset_owner(self, owner: str) -> None:
        with self.lock:
            jobs = [
                job for job in self.jobs.values()
                if job.owner == owner and not job.done.is_set()
            ]
        for job in jobs:
            self._cancel(job)

    def reset_for_tests(self) -> None:
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            if not job.done.is_set():
                self._cancel(job)
        with self.lock:
            self.jobs.clear()


def register(
    ctx: Any,
    runtime: TransferRuntime,
    check_fn: Callable[[], bool] | None = None,
) -> None:
    ctx.register_tool(
        check_fn=check_fn,
        name="workstation_transfer",
        toolset="workstation",
        schema=TRANSFER_SCHEMA,
        handler=runtime.start,
        description=TRANSFER_SCHEMA["description"],
        emoji="📦",
    )
    ctx.register_tool(
        check_fn=check_fn,
        name="workstation_transfer_status",
        toolset="workstation",
        schema=STATUS_SCHEMA,
        handler=runtime.status,
        description=STATUS_SCHEMA["description"],
        emoji="📋",
    )
