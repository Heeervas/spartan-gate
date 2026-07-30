#!/usr/bin/env python3
"""Add a fail-closed, target-scoped approval adapter for trusted plugins."""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/tools/approval.py")
MARKER = "# --- Spartan Gate scoped workstation approval adapter ---"

PATCH = r'''

# --- Spartan Gate scoped workstation approval adapter ---
_spartan_approval_scope_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "spartan_approval_scope", default=""
)
_spartan_approval_label_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "spartan_approval_label", default=""
)


def _spartan_scoped_key(pattern_key: str) -> str:
    scope = _spartan_approval_scope_ctx.get()
    if not scope:
        return pattern_key
    prefix = f"spartan_scope:{scope}:"
    return pattern_key if pattern_key.startswith(prefix) else prefix + pattern_key


def _spartan_labeled_description(description: str) -> str:
    label = _spartan_approval_label_ctx.get()
    if not label:
        return description
    prefix = f"Target: {label}. "
    return description if description.startswith(prefix) else prefix + description


_spartan_original_is_approved = is_approved
_spartan_original_approve_session = approve_session
_spartan_original_approve_permanent = approve_permanent
_spartan_original_command_allowlist = _command_matches_permanent_allowlist
_spartan_original_await_gateway_decision = _await_gateway_decision
_spartan_original_submit_pending = submit_pending
_spartan_original_prompt_dangerous_approval = prompt_dangerous_approval
_spartan_original_fire_approval_hook = _fire_approval_hook
_spartan_original_check_all_command_guards = check_all_command_guards


def is_approved(session_key: str, pattern_key: str) -> bool:
    return _spartan_original_is_approved(session_key, _spartan_scoped_key(pattern_key))


def approve_session(session_key: str, pattern_key: str):
    return _spartan_original_approve_session(
        session_key, _spartan_scoped_key(pattern_key)
    )


def approve_permanent(pattern_key: str):
    return _spartan_original_approve_permanent(_spartan_scoped_key(pattern_key))


def _command_matches_permanent_allowlist(command: str) -> bool:
    # Free-form command allowlist entries belong to the normal terminal. They
    # must never silently transfer authority to a remote workstation target.
    if _spartan_approval_scope_ctx.get():
        return False
    return _spartan_original_command_allowlist(command)


def _await_gateway_decision(session_key: str, notify_cb, approval_data: dict, **kwargs):
    data = dict(approval_data)
    if _spartan_approval_scope_ctx.get():
        data["pattern_key"] = _spartan_scoped_key(str(data.get("pattern_key") or ""))
        data["pattern_keys"] = [
            _spartan_scoped_key(str(key)) for key in data.get("pattern_keys", [])
        ]
        data["description"] = _spartan_labeled_description(
            str(data.get("description") or "")
        )
    return _spartan_original_await_gateway_decision(
        session_key, notify_cb, data, **kwargs
    )


def submit_pending(session_key: str, data: dict):
    pending = dict(data)
    if _spartan_approval_scope_ctx.get():
        pending["pattern_key"] = _spartan_scoped_key(
            str(pending.get("pattern_key") or "")
        )
        if "pattern_keys" in pending:
            pending["pattern_keys"] = [
                _spartan_scoped_key(str(key))
                for key in pending.get("pattern_keys", [])
            ]
        pending["description"] = _spartan_labeled_description(
            str(pending.get("description") or "")
        )
    return _spartan_original_submit_pending(session_key, pending)


def _fire_approval_hook(hook_name: str, **kwargs):
    payload = dict(kwargs)
    if _spartan_approval_scope_ctx.get():
        if "pattern_key" in payload:
            payload["pattern_key"] = _spartan_scoped_key(
                str(payload.get("pattern_key") or "")
            )
        if "pattern_keys" in payload:
            payload["pattern_keys"] = [
                _spartan_scoped_key(str(key))
                for key in payload.get("pattern_keys", [])
            ]
        if "description" in payload:
            payload["description"] = _spartan_labeled_description(
                str(payload.get("description") or "")
            )
    return _spartan_original_fire_approval_hook(hook_name, **payload)


def prompt_dangerous_approval(command: str, description: str, *args, **kwargs):
    return _spartan_original_prompt_dangerous_approval(
        command, _spartan_labeled_description(description), *args, **kwargs
    )


def check_scoped_command_guards(
    command: str,
    env_type: str,
    *,
    approval_scope: str,
    display_label: str,
    approval_callback=None,
    has_host_access: bool = False,
    require_human_context: bool = True,
) -> dict:
    """Run the canonical guards with approvals isolated to one fixed target."""
    if not approval_scope or not re.fullmatch(r"[a-z0-9_.-]{1,64}", approval_scope):
        return {
            "approved": False,
            "message": "BLOCKED: invalid scoped-approval identity.",
        }
    if not display_label or len(display_label) > 120:
        return {
            "approved": False,
            "message": "BLOCKED: invalid scoped-approval display label.",
        }
    if require_human_context and not (
        _is_gateway_approval_context()
        or _is_interactive_cli()
        or env_var_enabled("HERMES_EXEC_ASK")
    ):
        return {
            "approved": False,
            "message": (
                f"BLOCKED: {display_label} execution requires an active "
                "interactive owner approval context."
            ),
        }
    scope_token = _spartan_approval_scope_ctx.set(approval_scope)
    label_token = _spartan_approval_label_ctx.set(display_label)
    try:
        return _spartan_original_check_all_command_guards(
            command,
            env_type,
            approval_callback=approval_callback,
            has_host_access=has_host_access,
        )
    finally:
        _spartan_approval_label_ctx.reset(label_token)
        _spartan_approval_scope_ctx.reset(scope_token)


def request_one_time_tool_approval(
    tool_name: str,
    reason: str,
    *,
    command: str,
    workdir: str,
    approval_scope: str,
    display_label: str,
) -> dict:
    """Ask the gateway owner once, with no yolo/session/permanent shortcut."""
    if (
        not isinstance(command, str)
        or not command.strip()
        or "\0" in command
        or not isinstance(workdir, str)
        or not workdir.startswith("/")
        or "\0" in workdir
        or "\n" in workdir
        or "\r" in workdir
    ):
        return {
            "approved": False,
            "message": (
                f"BLOCKED: {display_label} interactive approval requires a "
                "displayable command and workdir."
            ),
        }
    if not _is_gateway_approval_context() or env_var_enabled("HERMES_CRON_SESSION"):
        return {
            "approved": False,
            "message": (
                f"BLOCKED: {display_label} interactive access requires a "
                "live gateway owner."
            ),
        }
    if not approval_scope or not re.fullmatch(r"[a-z0-9_.-]{1,64}", approval_scope):
        return {"approved": False, "message": "BLOCKED: invalid approval scope."}
    session_key = get_current_session_key()
    with _lock:
        notify_cb = _gateway_notify_cbs.get(session_key)
    if notify_cb is None:
        return {
            "approved": False,
            "message": (
                f"BLOCKED: {display_label} interactive approval callback is "
                "unavailable. Do not retry."
            ),
        }
    scope_token = _spartan_approval_scope_ctx.set(approval_scope)
    label_token = _spartan_approval_label_ctx.set(display_label)
    try:
        key = _spartan_scoped_key(f"one_time:{tool_name}:{time.time_ns()}")
        decision = _spartan_original_await_gateway_decision(
            session_key,
            notify_cb,
            {
                "command": command,
                "pattern_key": key,
                "pattern_keys": [key],
                "description": _spartan_labeled_description(
                    f"{reason}\nWorking directory: {workdir}"
                ),
                "allow_permanent": False,
                "allow_session": False,
            },
            surface="gateway",
        )
    finally:
        _spartan_approval_label_ctx.reset(label_token)
        _spartan_approval_scope_ctx.reset(scope_token)
    choice = decision.get("choice")
    if decision.get("notify_failed") or not decision.get("resolved") or choice in {
        None,
        "deny",
    }:
        return {
            "approved": False,
            "message": (
                f"BLOCKED: {display_label} interactive access was denied or "
                "timed out. Silence is not consent; do not retry."
            ),
            "user_consent": False,
        }
    return {"approved": True, "message": None, "user_approved": True}
'''


def patch_source(source: str) -> str:
    if MARKER in source:
        return source
    required = (
        "def check_all_command_guards(",
        "def _await_gateway_decision(",
        "def request_tool_approval(",
    )
    missing = [anchor for anchor in required if anchor not in source]
    if missing:
        raise ValueError(f"missing upstream approval anchor: {missing[0]}")
    return source.rstrip() + PATCH + "\n"


def patch() -> None:
    try:
        source = TARGET.read_text(encoding="utf-8")
        patched = patch_source(source)
    except (OSError, ValueError) as exc:
        print(f"FATAL: workstation approval scope patch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if patched == source:
        print("SKIP: workstation approval scope already present")
        return
    TARGET.write_text(patched, encoding="utf-8")
    print(f"OK: workstation approval scope applied to {TARGET}")


if __name__ == "__main__":
    patch()
