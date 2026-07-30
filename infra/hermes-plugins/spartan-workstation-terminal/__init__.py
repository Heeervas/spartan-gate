"""Scoped remote terminal tools for the fixed Hermes Workstation target."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import stat
import threading
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any


PLUGIN_NAME = "spartan-workstation-terminal"
APPROVAL_SCOPE = "hermes_workstation"
DISPLAY_LABEL = "Hermes Workstation"
DEFAULT_SOCKET = "/run/spartan-workstation/workstation-terminal.sock"
DEFAULT_WORKDIR = PurePosixPath("/home/hermes-worker/workspace")
EXCHANGE_WORKDIR = PurePosixPath("/home/hermes-worker/windows-exchange")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_COMMAND_CHARS = 32_000
MAX_STDIN_CHARS = 16_000
TRANSFER_PATH = Path(__file__).resolve().with_name("transfer.py")

_lock = threading.Lock()
_generations: dict[str, int] = {}
_interactive_processes: dict[str, str] = {}
_cancelled_notifications: set[str] = set()
_transfer_runtime: Any | None = None


def _load_transfer_module():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_transfer", TRANSFER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load workstation transfer plugin")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TRANSFER = _load_transfer_module()


TERMINAL_SCHEMA = {
    "name": "workstation_terminal",
    "description": (
        "Execute a command on the fixed Hermes Workstation through the "
        "operator-managed scoped SSH broker. This is separate from the normal "
        "Docker/local terminal. Host, user, key, port, SSH options and network "
        "destination are fixed and cannot be supplied. Commands require a live "
        "owner context; dangerous commands use the normal Hermes approval "
        "policy. PTY or stdin-capable sessions require an additional explicit "
        "one-time approval. Workdir is limited to the Linux workspace or the "
        "declared Windows Exchange mount."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Command to run on Hermes Workstation.",
            },
            "background": {
                "type": "boolean",
                "default": False,
                "description": "Start a managed background process.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Foreground default 180s/max 900s; background default "
                    "1h/max 24h."
                ),
            },
            "workdir": {
                "type": "string",
                "description": (
                    "Absolute directory under /home/hermes-worker/workspace "
                    "or /home/hermes-worker/windows-exchange."
                ),
            },
            "pty": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Allocate a PTY. Requires explicit one-time owner approval."
                ),
            },
            "interactive": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Permit later stdin write/submit/close actions. Requires "
                    "explicit one-time owner approval; PTY implies this."
                ),
            },
            "notify_on_complete": {
                "type": "boolean",
                "default": False,
                "description": (
                    "For background jobs, enqueue one normal Hermes completion "
                    "notification. Mutually exclusive with watch_patterns."
                ),
            },
            "watch_patterns": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "maxLength": 200},
                "description": (
                    "Rare literal output markers for a long-lived background "
                    "process. Rate-limited and mutually exclusive with "
                    "notify_on_complete."
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


PROCESS_SCHEMA = {
    "name": "workstation_process",
    "description": (
        "Manage processes created by workstation_terminal. Process IDs and "
        "ownership are isolated from the normal process tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "poll",
                    "log",
                    "wait",
                    "write",
                    "submit",
                    "close",
                    "resize",
                    "interrupt",
                    "kill",
                ],
            },
            "process_id": {"type": "string"},
            "data": {
                "type": "string",
                "description": "Bounded stdin data for write/submit.",
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 900},
            "offset": {"type": "integer", "minimum": 0},
            "rows": {"type": "integer", "minimum": 5, "maximum": 200},
            "cols": {"type": "integer", "minimum": 20, "maximum": 500},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def register(ctx: Any) -> None:
    global _transfer_runtime
    ctx.register_tool(
        name="workstation_terminal",
        toolset="workstation",
        schema=TERMINAL_SCHEMA,
        handler=_handle_terminal,
        check_fn=_check_available,
        description=TERMINAL_SCHEMA["description"],
        emoji="🖥️",
    )
    ctx.register_tool(
        name="workstation_process",
        toolset="workstation",
        schema=PROCESS_SCHEMA,
        handler=_handle_process,
        check_fn=_check_available,
        description=PROCESS_SCHEMA["description"],
        emoji="⚙️",
    )
    if _transfer_runtime is None:
        _transfer_runtime = TRANSFER.TransferRuntime(
            broker_call=_broker_call,
            socket_path=_socket_path,
            session_key=_session_key,
            owner_for=_owner_for,
            error_response=_error,
        )
    TRANSFER.register(ctx, _transfer_runtime, _check_available)
    ctx.register_hook("on_session_reset", _on_session_reset)


def _socket_path() -> str:
    # Operator-controlled environment only. It is not part of either tool
    # schema and cannot be supplied by the model.
    return os.environ.get("SPARTAN_WORKSTATION_BROKER_SOCKET", DEFAULT_SOCKET)


def _check_available() -> bool:
    try:
        from tools.approval import (
            check_scoped_command_guards,
            request_one_time_tool_approval,
        )

        del check_scoped_command_guards, request_one_time_tool_approval
        info = os.stat(_socket_path(), follow_symlinks=False)
        return (
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.getuid()
            and not (stat.S_IMODE(info.st_mode) & 0o077)
        )
    except (ImportError, OSError):
        return False


def _session_key() -> str:
    from tools.approval import get_current_session_key

    value = get_current_session_key(default="") or ""
    if not value:
        raise RuntimeError("workstation tool has no active Hermes session")
    return value


def _owner_for(session_key: str) -> str:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    with _lock:
        generation = _generations.setdefault(session_key, 0)
    return f"{digest}:g{generation}"


def _validate_workdir(value: Any) -> str:
    if value is None or value == "":
        candidate = DEFAULT_WORKDIR
    elif isinstance(value, str) and value.startswith("/"):
        candidate = PurePosixPath(value)
    else:
        raise ValueError("workdir must be an absolute path")
    if ".." in candidate.parts or not (
        candidate == DEFAULT_WORKDIR
        or candidate.is_relative_to(DEFAULT_WORKDIR)
        or candidate == EXCHANGE_WORKDIR
        or candidate.is_relative_to(EXCHANGE_WORKDIR)
    ):
        raise ValueError(
            "workdir must be beneath the workstation workspace or Exchange mount"
        )
    return str(candidate)


def _broker_call(payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    encoded = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise RuntimeError("workstation broker request exceeds the size limit")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect(_socket_path())
        connection.sendall(encoded)
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = connection.recv(8192)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise RuntimeError("workstation broker response exceeds the size limit")
    finally:
        connection.close()
    try:
        response = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("workstation broker returned an invalid response") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        message = response.get("error") if isinstance(response, dict) else None
        raise RuntimeError(str(message or "workstation broker rejected the request"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("workstation broker returned an invalid result")
    return result


def _redact_result(result: dict[str, Any]) -> dict[str, Any]:
    from agent.redact import redact_sensitive_text, redact_terminal_output

    cleaned = dict(result)
    command = str(cleaned.get("command") or "")
    if command:
        cleaned["command"] = redact_sensitive_text(command, code_file=True)
    output = cleaned.get("output")
    if isinstance(output, str) and output:
        cleaned["output"] = redact_terminal_output(output, command)
    processes = cleaned.get("processes")
    if isinstance(processes, list):
        cleaned["processes"] = [
            _redact_result(item) if isinstance(item, dict) else item
            for item in processes
        ]
    return cleaned


def _error(message: str) -> str:
    return json.dumps(
        {"ok": False, "error": message, "target": DISPLAY_LABEL},
        ensure_ascii=False,
    )


def _wait_foreground(owner: str, process_id: str, timeout: int) -> dict[str, Any]:
    from tools.interrupt import is_interrupted

    deadline = time.monotonic() + timeout + 3
    while True:
        if is_interrupted():
            result = _broker_call(
                {"action": "kill", "owner": owner, "process_id": process_id}
            )
            result["interrupted"] = True
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _broker_call(
                {"action": "kill", "owner": owner, "process_id": process_id}
            )
        wait_seconds = max(1, min(2, int(remaining)))
        result = _broker_call(
            {
                "action": "wait",
                "owner": owner,
                "process_id": process_id,
                "timeout": wait_seconds,
            },
            timeout=wait_seconds + 5,
        )
        if not result.get("running"):
            return result


def _handle_terminal(args: dict[str, Any], **_context: Any) -> str:
    reservation = ""
    owner = ""
    try:
        if not _check_available():
            return _error("workstation terminal capability is unavailable")
        command = args.get("command")
        if (
            not isinstance(command, str)
            or not command.strip()
            or len(command) > MAX_COMMAND_CHARS
            or "\0" in command
        ):
            return _error("command must be a bounded non-empty string without NUL")
        background = args.get("background", False)
        pty_mode = args.get("pty", False)
        interactive = args.get("interactive", False) or pty_mode
        notify = args.get("notify_on_complete", False)
        patterns = args.get("watch_patterns") or []
        if not all(
            isinstance(value, bool)
            for value in (background, pty_mode, interactive, notify)
        ):
            return _error("background, pty, interactive and notify must be booleans")
        if interactive and not background:
            return _error("PTY/stdin capability requires background=true")
        if (notify or patterns) and not background:
            return _error("notifications require background=true")
        if notify and patterns:
            return _error("notify_on_complete and watch_patterns are mutually exclusive")
        if not isinstance(patterns, list) or len(patterns) > 8 or any(
            not isinstance(item, str) or not item or len(item) > 200
            for item in patterns
        ):
            return _error("watch_patterns must contain at most 8 bounded strings")
        workdir = _validate_workdir(args.get("workdir"))
        timeout_value = args.get("timeout")
        if timeout_value is not None and (
            isinstance(timeout_value, bool) or not isinstance(timeout_value, int)
        ):
            return _error("timeout must be an integer")
        maximum = 86_400 if background else 900
        if timeout_value is not None and not 1 <= timeout_value <= maximum:
            return _error(f"timeout must be between 1 and {maximum}")
        timeout = timeout_value if timeout_value is not None else (3600 if background else 180)

        session_key = _session_key()
        owner = _owner_for(session_key)
        payload: dict[str, Any] = {
            "action": "execute",
            "owner": owner,
            "command": command,
            "workdir": workdir,
            "background": background,
            "deferred": True,
            "pty": pty_mode,
            "timeout": timeout,
        }
        digest = TRANSFER.PROTOCOL.terminal_operation_hash(payload)
        reservation = str(
            _broker_call(
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

        from tools.approval import (
            check_scoped_command_guards,
            request_one_time_tool_approval,
        )

        guarded = check_scoped_command_guards(
            command,
            "ssh",
            approval_scope=APPROVAL_SCOPE,
            display_label=DISPLAY_LABEL,
            has_host_access=True,
            require_human_context=True,
        )
        if not guarded.get("approved"):
            _broker_call(
                {
                    "action": "cancel_reservation",
                    "owner": owner,
                    "reservation": reservation,
                }
            )
            reservation = ""
            return _error(str(guarded.get("message") or "command approval denied"))
        if interactive:
            approval = request_one_time_tool_approval(
                "workstation_terminal",
                (
                    "Interactive PTY/stdin grants an ongoing input channel. "
                    "Later keystrokes cannot be inspected by command guards."
                ),
                command=command,
                workdir=workdir,
                approval_scope=APPROVAL_SCOPE,
                display_label=DISPLAY_LABEL,
            )
            if not approval.get("approved"):
                _broker_call(
                    {
                        "action": "cancel_reservation",
                        "owner": owner,
                        "reservation": reservation,
                    }
                )
                reservation = ""
                return _error(
                    str(approval.get("message") or "interactive approval denied")
                )

        payload["operation_hash"] = digest
        payload["reservation"] = reservation
        result = _broker_call(payload, timeout=15)
        reservation = ""
        process_id = str(result.get("process_id") or "")
        if not background and process_id and result.get("running"):
            result = _wait_foreground(owner, process_id, timeout)
        if interactive and process_id:
            with _lock:
                _interactive_processes[process_id] = owner
        if background and process_id and (notify or patterns):
            threading.Thread(
                target=_watch_process,
                args=(session_key, owner, process_id, command, notify, patterns),
                name=f"workstation-notify-{process_id}",
                daemon=True,
            ).start()
        return json.dumps(
            {"ok": True, "target": DISPLAY_LABEL, **_redact_result(result)},
            ensure_ascii=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if reservation and owner:
            try:
                _broker_call(
                    {
                        "action": "cancel_reservation",
                        "owner": owner,
                        "reservation": reservation,
                    }
                )
            except Exception:
                pass
        return _error(str(exc))

def _handle_process(args: dict[str, Any], **_context: Any) -> str:
    try:
        if not _check_available():
            return _error("workstation terminal capability is unavailable")
        action = args.get("action")
        allowed = {
            "list",
            "poll",
            "log",
            "wait",
            "write",
            "submit",
            "close",
            "resize",
            "interrupt",
            "kill",
        }
        if action not in allowed:
            return _error("unknown workstation process action")
        session_key = _session_key()
        owner = _owner_for(session_key)
        payload: dict[str, Any] = {"action": action, "owner": owner}
        if action != "list":
            process_id = args.get("process_id")
            if not isinstance(process_id, str) or not process_id:
                return _error(f"process_id is required for {action}")
            payload["process_id"] = process_id
            if action in {"write", "submit", "close", "resize"}:
                with _lock:
                    interactive_owner = _interactive_processes.get(process_id)
                if interactive_owner != owner:
                    return _error(
                        "stdin/PTY control was not explicitly approved for this process"
                    )
            if action in {"log", "wait"}:
                with _lock:
                    _cancelled_notifications.add(process_id)
        for field in ("timeout", "offset", "rows", "cols"):
            if field in args:
                payload[field] = args[field]
        if action in {"write", "submit"}:
            data = args.get("data", "")
            if (
                not isinstance(data, str)
                or len(data) > MAX_STDIN_CHARS
                or "\0" in data
            ):
                return _error("stdin data must be a bounded string without NUL")
            payload["data"] = data
        response_timeout = 15
        if action == "wait":
            wait_timeout = args.get("timeout", 180)
            if (
                isinstance(wait_timeout, bool)
                or not isinstance(wait_timeout, int)
                or not 1 <= wait_timeout <= 900
            ):
                return _error("wait timeout must be between 1 and 900")
            response_timeout = wait_timeout + 10
        result = _broker_call(
            payload,
            timeout=response_timeout,
        )
        if action in {"kill", "close"}:
            with _lock:
                _interactive_processes.pop(str(args.get("process_id") or ""), None)
        return json.dumps(
            {"ok": True, "target": DISPLAY_LABEL, **_redact_result(result)},
            ensure_ascii=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _error(str(exc))


def _queue_notification(event: dict[str, Any]) -> None:
    try:
        from tools.process_registry import process_registry

        process_registry.completion_queue.put(event)
    except Exception:
        # Notification delivery is optional; process lifecycle and output stay
        # available through workstation_process even if the gateway queue is
        # unavailable.
        return


def _watch_process(
    session_key: str,
    owner: str,
    process_id: str,
    command: str,
    notify_on_complete: bool,
    patterns: list[str],
) -> None:
    offset = 0
    last_match_at = 0.0
    suppressed_windows = 0
    watch_enabled = bool(patterns)
    while True:
        try:
            result = _broker_call(
                {
                    "action": "log",
                    "owner": owner,
                    "process_id": process_id,
                    "offset": offset,
                }
            )
        except Exception:
            return
        offset = int(result.get("next_offset") or offset)
        output = str(result.get("output") or "")
        if watch_enabled and output:
            matched = next(
                (
                    (pattern, line)
                    for line in output.splitlines()
                    for pattern in patterns
                    if pattern in line
                ),
                None,
            )
            if matched:
                now = time.monotonic()
                if now - last_match_at >= 15:
                    last_match_at = now
                    suppressed_windows = 0
                    _queue_notification(
                        {
                            "type": "watch_match",
                            "session_id": process_id,
                            "session_key": session_key,
                            "command": command,
                            "pattern": matched[0],
                            "output": matched[1][-2000:],
                            "suppressed": 0,
                        }
                    )
                else:
                    suppressed_windows += 1
                    if suppressed_windows >= 3:
                        watch_enabled = False
                        notify_on_complete = True
        if not result.get("running"):
            with _lock:
                cancelled = process_id in _cancelled_notifications
                _interactive_processes.pop(process_id, None)
            if notify_on_complete and not cancelled:
                _queue_notification(
                    {
                        "type": "completion",
                        "session_id": process_id,
                        "session_key": session_key,
                        "command": command,
                        "exit_code": result.get("exit_code"),
                        "completion_reason": result.get("completion_reason"),
                        "termination_source": result.get("termination_source"),
                        "output": output[-2000:],
                        "started_at": result.get("started_at"),
                    }
                )
            return
        time.sleep(2)


def _on_session_reset(**kwargs: Any) -> None:
    session_key = str(kwargs.get("session_key") or "")
    if not session_key:
        try:
            session_key = _session_key()
        except RuntimeError:
            return
    owner = _owner_for(session_key)
    if _transfer_runtime is not None:
        _transfer_runtime.reset_owner(owner)
    try:
        _broker_call({"action": "revoke_owner", "owner": owner})
    except Exception:
        # Generation must not advance until revocation is acknowledged. This
        # prevents orphaned processes from escaping ownership after reset.
        return
    with _lock:
        _generations[session_key] = _generations.get(session_key, 0) + 1
        stale = [
            process_id
            for process_id, process_owner in _interactive_processes.items()
            if process_owner == owner
        ]
        for process_id in stale:
            _interactive_processes.pop(process_id, None)
            _cancelled_notifications.add(process_id)

def _reset_for_tests() -> None:
    global _transfer_runtime
    if _transfer_runtime is not None:
        _transfer_runtime.reset_for_tests()
        _transfer_runtime = None
    with _lock:
        _generations.clear()
        _interactive_processes.clear()
        _cancelled_notifications.clear()
