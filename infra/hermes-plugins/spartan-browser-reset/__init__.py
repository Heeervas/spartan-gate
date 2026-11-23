"""Opt-in, deletion-only adapter over the live official Camofox binding."""

import json
import re
from urllib.parse import quote
from uuid import uuid4

import requests

NAME = "spartan-browser-reset"
RESIDUAL_NOTE = "may_reappear_after_camofox_restart_repeat_reset"
MAX_RESPONSE_BYTES = 16 * 1024
SCHEMA = {
    "name": "browser_reset_site",
    "description": (
        "WARNING: logs you out of the current site AND its shared parent domain, "
        "including sibling subdomains and other ports. Deletes matching cookies "
        "from the current tracked Camofox tab, checkpoints, then reloads by GET "
        "without HTTP cache (not service-worker cache). Cannot read cookies. "
        "Partitioned cookies may remain; deleted_with_residual means a cookie "
        "may reappear after a Camofox restart: repeat the reset after restart. "
        "Other tabs or the site may recreate cookies. No automatic retries."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
STATES = {
    "cookies": {"deleted", "deleted_with_residual", "none", "partial", "failed"},
    "persisted": {"saved", "unknown", "not_attempted"},
    "reload": {"reloaded", "skipped_navigated", "not_attempted", "failed"},
}
CODES = {
    "unauthorized",
    "invalid_request",
    "scope_denied",
    "operator_denied",
    "trace_denied",
    "persistence_unavailable",
    "busy",
    "invalid_site",
    "checkpoint_unknown",
    "cookies_failed",
    "reload_failed",
    "reset_failed",
}


class ResetDenied(Exception):
    """Only locally selected, fixed error codes cross the tool boundary."""


def _navigation_client(official):
    from tools.registry import registry

    # Resolve in the effective scope, including a session override of navigate.
    entry = registry.get_entry("browser_navigate")
    tools = getattr(official.module, "camofox_tools", None)
    handler = getattr(tools, "handle_navigate", None)
    if (
        "browser_navigate" not in official.tools_registered
        or entry is None
        or handler is None
        or entry.handler is not handler
    ):
        raise ResetDenied("binding_unavailable")
    client = getattr(tools, "client", None)
    if client is None or getattr(official.module, "camofox_client", None) is not client:
        raise ResetDenied("binding_unavailable")
    return client


def _live_client():
    from hermes_cli.plugins import get_plugin_manager
    from spartan_browser_policy import camofox_policy_enabled

    if not camofox_policy_enabled():
        raise ResetDenied("backend_unavailable")
    manager = get_plugin_manager()
    own = manager._plugins.get(NAME)
    official = manager._plugins.get("camofox")
    for loaded in (own, official):
        if loaded is None or not loaded.enabled or loaded.error or loaded.module is None:
            raise ResetDenied("plugin_unavailable")
    if getattr(own.module, "handle_reset", None) is not handle_reset:
        raise ResetDenied("plugin_unavailable")
    client = _navigation_client(official)
    if not client.get_camofox_url():
        raise ResetDenied("backend_unavailable")
    return client


def _available():
    try:
        _live_client()
        return True
    except Exception:
        return False


def _identity(client, session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise ResetDenied("session_required")
    with client._sessions_lock:
        session = client._sessions.get(client._session_key(session_id))
        if not isinstance(session, dict):
            raise ResetDenied("tab_required")
        identity = tuple(session.get(key) for key in ("user_id", "session_key", "tab_id"))
        if not all(isinstance(value, str) and value.strip() for value in identity):
            raise ResetDenied("tab_required")
        return identity


def _site(value):
    # Display-only: an unrepresentable host must not hide an already-executed outcome.
    if (
        isinstance(value, str)
        and len(value) <= 253
        and re.fullmatch(r"[A-Za-z0-9._\[\]:-]+", value)
    ):
        return value
    return None


def _project(data):
    if not isinstance(data, dict) or type(data.get("ok")) is not bool:
        raise ResetDenied("invalid_response")
    result = {"ok": data["ok"]}
    for key, allowed in STATES.items():
        value = data.get(key)
        if not isinstance(value, str) or value not in allowed:
            raise ResetDenied("invalid_response")
        result[key] = value
    result["site"] = _site(data.get("site"))
    if "code" in data:
        code = data["code"]
        if not isinstance(code, str) or code not in CODES:
            raise ResetDenied("invalid_response")
        result["code"] = code
    if result["cookies"] == "deleted_with_residual":
        if data.get("residual_note") != RESIDUAL_NOTE:
            raise ResetDenied("invalid_response")
        result["residual_note"] = RESIDUAL_NOTE
    return result


def _transport(client, identity):
    user_id, session_key, tab_id = identity
    body = {"userId": user_id, "sessionKey": session_key, "operationId": str(uuid4())}
    path = "/tabs/" + quote(tab_id, safe="") + "/spartan/reset-site"
    with requests.Session() as transport:
        transport.trust_env = False
        with transport.request(
            "POST",
            client.get_camofox_url() + path,
            headers={**client._auth_headers(), "Content-Type": "application/json"},
            data=json.dumps(body).encode("utf-8"),
            timeout=(5, 145),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code not in {200, 400, 401, 403, 409, 413, 500, 503}:
                raise ResetDenied("transport_failed")
            payload = bytearray()
            for chunk in response.iter_content(4096):
                if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ResetDenied("invalid_response")
                payload.extend(chunk)
            result = _project(json.loads(payload))
            if result["ok"] and response.status_code != 200:
                raise ResetDenied("invalid_response")
            return result


def handle_reset(args, *, session_id=None, **_context):
    try:
        if not isinstance(args, dict) or args:
            raise ResetDenied("invalid_arguments")
        client = _live_client()
        return json.dumps(_transport(client, _identity(client, session_id)))
    except ResetDenied as exc:
        return json.dumps({"ok": False, "code": str(exc)})
    except Exception:
        return json.dumps({"ok": False, "code": "transport_failed"})


def register(ctx):
    ctx.register_tool(
        name="browser_reset_site",
        toolset="camofox",
        schema=SCHEMA,
        handler=handle_reset,
        check_fn=_available,
    )
