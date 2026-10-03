"""Hermes plugin that gives Spartan Route stable cache identity lineage."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
from pathlib import Path
from collections import OrderedDict
from typing import Any


PLUGIN_NAME = "spartan-cache-lineage"
LINEAGE_SCHEMA_VERSION = 2
LINEAGE_FIELD = "spartan_cache_lineage"
LINEAGE_REASON_FIELD = "spartan_cache_lineage_reason"
DEFAULT_MAX_TRACKED_KEYS = 512
MAX_FOREGROUND_FRONTIERS = 8
MAX_STATE_BYTES = 8 * 1024 * 1024
ROUTE_HOST_MARKERS = ("spartan-route:", "clawroute:")
REASONING_CHAIN_FIELD = "spartan_chain_id"
REASONING_REALM_FIELD = "spartan_expected_realm"
REASONING_MAC_FIELD = "spartan_binding_mac"
REASONING_ITEM_CHAIN_FIELD = "_spartan_chain_id"
REASONING_ITEM_REALM_FIELD = "_spartan_reasoning_realm"
REASONING_ITEM_MAC_FIELD = "_spartan_binding_mac"
REASONING_REALMS = {"codex_subscription", "openai_api"}

_lock = threading.Lock()
_state: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_state_bytes = 0


def register(ctx: Any) -> None:
    ctx.register_middleware("llm_request", _llm_request_middleware)
    ctx.register_hook("post_api_request", _post_api_request)


def _llm_request_middleware(
    request: dict[str, Any] | None = None,
    session_id: str = "",
    base_url: str = "",
    api_mode: str = "",
    **_context: Any,
) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return None
    if api_mode and api_mode not in {"chat_completions", "codex_responses"}:
        return None
    if not _is_spartan_route_request(request, base_url):
        return None

    updated = dict(request)
    extra_body = dict(updated.get("extra_body") or {})
    reasoning_reset = False
    if api_mode == "codex_responses":
        legacy_effort = updated.pop("reasoning_effort", None)
        if isinstance(legacy_effort, str) and legacy_effort.strip():
            reasoning = dict(updated.get("reasoning") or {})
            reasoning.setdefault("effort", legacy_effort.strip().lower())
            updated["reasoning"] = reasoning
        binding = _reasoning_binding(session_id)
        if binding is None:
            binding = {
                REASONING_CHAIN_FIELD: "invalid",
                REASONING_REALM_FIELD: "invalid",
                REASONING_MAC_FIELD: "invalid",
            }
        input_items = updated.get("input")
        if isinstance(input_items, list):
            updated["input"], reasoning_reset = _sanitize_responses_input(input_items, binding)
        extra_body.update(binding)

    logical_cache_key = _clean_string(extra_body.get("prompt_cache_key"))
    if not logical_cache_key:
        logical_cache_key = _clean_string(updated.get("prompt_cache_key"))
    if not logical_cache_key and session_id:
        logical_cache_key = f"hermes:{session_id}"
        extra_body["prompt_cache_key"] = logical_cache_key
    elif logical_cache_key:
        extra_body.setdefault("prompt_cache_key", logical_cache_key)

    if logical_cache_key:
        review_origin, review_id = _review_context(_context) if api_mode == "codex_responses" else (False, "")
        lineage, reason = _lineage_for_request(
            session_id=session_id,
            logical_cache_key=logical_cache_key,
            request=updated,
            api_mode=api_mode,
            review_origin=review_origin,
            review_id=review_id,
        )
        extra_body[LINEAGE_FIELD] = lineage
        extra_body[LINEAGE_REASON_FIELD] = reason

    reasoning_effort = _infer_reasoning_effort(updated, extra_body)
    if api_mode != "codex_responses" and reasoning_effort and "reasoning_effort" not in updated:
        updated["reasoning_effort"] = reasoning_effort

    if extra_body:
        updated["extra_body"] = extra_body
    if updated == request:
        return None
    return {
        "request": updated,
        "source": PLUGIN_NAME,
        "reason": "reasoning_chain_reset" if reasoning_reset else (extra_body.get(LINEAGE_REASON_FIELD) or "cache_identity"),
    }


def _active_hermes_home() -> str:
    try:
        from hermes_constants import get_hermes_home

        value = get_hermes_home()
    except Exception:
        value = os.environ.get("HERMES_HOME") or "/opt/data"
    return str(Path(value).expanduser().resolve())


def _reasoning_binding(session_id: str) -> dict[str, str] | None:
    secret_hex = _clean_string(os.environ.get("SPARTAN_REASONING_BINDING_SECRET")).lower()
    realm = _clean_string(os.environ.get("SPARTAN_REASONING_REALM")).lower()
    if len(secret_hex) != 64 or any(char not in "0123456789abcdef" for char in secret_hex):
        return None
    if realm not in REASONING_REALMS or not session_id:
        return None
    secret = bytes.fromhex(secret_hex)
    raw_chain = f"{_active_hermes_home()}\0{session_id}".encode()
    chain_id = hmac.new(secret, raw_chain, hashlib.sha256).hexdigest()
    binding_mac = hmac.new(secret, f"{chain_id}\0{realm}".encode(), hashlib.sha256).hexdigest()
    return {
        REASONING_CHAIN_FIELD: chain_id,
        REASONING_REALM_FIELD: realm,
        REASONING_MAC_FIELD: binding_mac,
    }


def _reasoning_item_matches(item: dict[str, Any], binding: dict[str, str]) -> bool:
    return (
        hmac.compare_digest(str(item.get(REASONING_ITEM_CHAIN_FIELD) or ""), binding[REASONING_CHAIN_FIELD])
        and hmac.compare_digest(str(item.get(REASONING_ITEM_REALM_FIELD) or ""), binding[REASONING_REALM_FIELD])
        and hmac.compare_digest(str(item.get(REASONING_ITEM_MAC_FIELD) or ""), binding[REASONING_MAC_FIELD])
    )


def _strip_reasoning_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_spartan_")}


def _sanitize_responses_input(
    input_items: list[Any],
    binding: dict[str, str],
) -> tuple[list[Any], bool]:
    reset = any(
        isinstance(item, dict)
        and item.get("type") == "reasoning"
        and item.get("encrypted_content")
        and not _reasoning_item_matches(item, binding)
        for item in input_items
    )
    cleaned: list[Any] = []
    for item in input_items:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        if item.get("type") == "reasoning" and item.get("encrypted_content"):
            if reset or not _reasoning_item_matches(item, binding):
                continue
            cleaned.append(_strip_reasoning_metadata(item))
            continue
        updated = _strip_reasoning_metadata(item)
        if reset and updated.get("type") == "message" and updated.get("role") == "assistant":
            updated.pop("id", None)
        cleaned.append(updated)
    return cleaned, reset


def _post_api_request(
    assistant_message: Any = None,
    session_id: str = "",
    base_url: str = "",
    api_mode: str = "",
    **_context: Any,
) -> None:
    if api_mode != "codex_responses" or assistant_message is None:
        return
    if not _is_spartan_route_request({}, base_url):
        return
    binding = _reasoning_binding(session_id)
    if binding is None:
        return
    raw_items = getattr(assistant_message, "codex_reasoning_items", None)
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        for key in tuple(item):
            if key.startswith("_spartan_"):
                item.pop(key, None)
        item[REASONING_ITEM_CHAIN_FIELD] = binding[REASONING_CHAIN_FIELD]
        item[REASONING_ITEM_REALM_FIELD] = binding[REASONING_REALM_FIELD]
        item[REASONING_ITEM_MAC_FIELD] = binding[REASONING_MAC_FIELD]


def _is_spartan_route_request(request: dict[str, Any], context_base_url: str) -> bool:
    request_base_url = _clean_string(request.get("base_url")) or context_base_url
    configured_base_url = _clean_string(os.environ.get("OPENAI_BASE_URL"))
    if configured_base_url and request_base_url:
        return _normalize_base_url(request_base_url) == _normalize_base_url(configured_base_url)
    if request_base_url:
        normalized = _normalize_base_url(request_base_url)
        return any(marker in normalized for marker in ROUTE_HOST_MARKERS)
    if configured_base_url and _env_enabled("SPARTAN_CACHE_LINEAGE_ALLOW_MISSING_BASE_URL", True):
        normalized = _normalize_base_url(configured_base_url)
        return any(marker in normalized for marker in ROUTE_HOST_MARKERS)
    return False


def _review_context(context: dict[str, Any]) -> tuple[bool, str]:
    """Read Hermes' context-local origin once; never infer a review from prompt text."""
    try:
        from tools.skill_provenance import get_current_write_origin

        origin = get_current_write_origin()
    except Exception:
        return False, ""  # Older Hermes retains its existing lineage behavior.
    if origin != "background_review":
        return False, ""
    identifiers = tuple(_clean_string(context.get(key)) for key in ("task_id", "turn_id"))
    return True, _fingerprint(identifiers) if any(identifiers) else ""


def _next_lineage(
    previous: dict[str, Any] | None, history: tuple[str, ...], tool_fp: str, seed: int = 0,
) -> tuple[int, str]:
    if previous is None:
        return seed, "baseline"
    lineage = int(previous.get("lineage") or 0)
    previous_history = previous.get("history")
    if previous.get("tool_fp", "") != tool_fp:
        return lineage + 1, "tool_schema_changed"
    if previous_history is None:
        return lineage, "baseline"  # Oversized history was deliberately not retained.
    if previous_history == history:
        return lineage, "retry"
    if _extends(history, previous_history):
        return lineage, "prefix"
    return lineage + 1, "history_rewrite"


def _extends(history: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(history) >= len(prefix) and history[:len(prefix)] == prefix


def _review_seed(
    foreground: dict[str, Any] | None, history: tuple[str, ...], tool_fp: str, model_fp: str,
) -> int:
    if foreground is None:
        return 0
    for frame in reversed(foreground.get("frontiers", ())):
        if (frame["tool_fp"] == tool_fp and frame["model_fp"] == model_fp
                and _extends(history, frame["history"])):
            return frame["lineage"]
    return int(foreground.get("lineage") or 0)


def _retained_size(value: Any) -> int:
    """Conservative Python allocation size: shared children are charged more than once."""
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_retained_size(k) + _retained_size(v) for k, v in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(_retained_size(item) for item in value)
    return size


def _store_lineage(key: str, entry: dict[str, Any], max_keys: int) -> None:
    """Called under _lock; bound all lanes and snapshots together."""
    global _state_bytes
    entry["_retained_bytes"] = MAX_STATE_BYTES  # Reserve the size-counter integer itself.
    size = _retained_size(key) + _retained_size(entry)
    # Trim snapshots first, then retain only the counter if this history cannot fit.
    while size + sys.getsizeof(OrderedDict()) > MAX_STATE_BYTES and entry.get("frontiers"):
        entry["frontiers"] = entry["frontiers"][1:]
        size = _retained_size(key) + _retained_size(entry)
    if size + sys.getsizeof(OrderedDict()) > MAX_STATE_BYTES:
        entry["history"] = None
        entry["frontiers"] = ()
        size = _retained_size(key) + _retained_size(entry)
    old = _state.pop(key, None)
    if old is not None:
        _state_bytes -= old["_retained_bytes"]
    entry["_retained_bytes"] = size
    _state[key] = entry
    _state_bytes += size
    while _state and (len(_state) > max_keys or _state_bytes + sys.getsizeof(_state) > MAX_STATE_BYTES):
        _, evicted = _state.popitem(last=False)
        _state_bytes -= evicted["_retained_bytes"]


def _lineage_for_request(
    *,
    session_id: str,
    logical_cache_key: str,
    request: dict[str, Any],
    api_mode: str,
    review_origin: bool = False,
    review_id: str = "",
) -> tuple[int, str]:
    transport = api_mode or (
        "codex_responses" if "input" in request and "messages" not in request else "chat_completions"
    )
    history_fps = _history_fingerprints(request, transport)
    tools = request.get("tools")
    tool_fp = _fingerprint(_canonical_tools(tools)) if isinstance(tools, list) else ""
    realm = (
        _clean_string(os.environ.get("SPARTAN_REASONING_REALM")).lower() or "invalid"
        if transport == "codex_responses"
        else "chat"
    )
    state_key = _state_key(session_id, logical_cache_key, transport, realm)
    max_keys = _max_tracked_keys()
    model_fp = _fingerprint({"model": request.get("model")})
    with _lock:
        foreground = _state.get(state_key)
        if transport == "codex_responses" and review_origin:
            seed = _review_seed(foreground, history_fps, tool_fp, model_fp)
            if not review_id:
                return seed, "baseline"  # Recognized but unidentifiable reviews are read-only.
            lane_key = _fingerprint(("review", state_key, review_id, model_fp))
            lineage, reason = _next_lineage(_state.get(lane_key), history_fps, tool_fp, seed)
            _store_lineage(lane_key, {
                "lineage": lineage, "history": history_fps, "tool_fp": tool_fp,
            }, max_keys)
            return lineage, reason

        lineage, reason = _next_lineage(foreground, history_fps, tool_fp)
        entry = {"lineage": lineage, "history": history_fps, "tool_fp": tool_fp}
        if transport == "codex_responses":
            frame = {**entry, "model_fp": model_fp}
            frontiers = tuple(foreground.get("frontiers", ())) if foreground else ()
            # Repeated retries must not consume the historical snapshot allowance.
            frontiers = tuple(item for item in frontiers if item != frame)
            entry["frontiers"] = (*frontiers, frame)[-MAX_FOREGROUND_FRONTIERS:]
        _store_lineage(state_key, entry, max_keys)
        return lineage, reason


def _history_fingerprints(request: dict[str, Any], transport: str) -> tuple[str, ...]:
    if transport == "codex_responses":
        instructions_fp = _fingerprint({"instructions": request.get("instructions")})
        input_items = request.get("input")
        if isinstance(input_items, list):
            item_fps = tuple(_fingerprint(item) for item in input_items)
        elif input_items is None:
            item_fps = ()
        else:
            item_fps = (_fingerprint({"input": input_items}),)
        return (instructions_fp, *item_fps)
    messages = request.get("messages")
    return tuple(_fingerprint(item) for item in messages) if isinstance(messages, list) else ()


def _state_key(
    session_id: str,
    logical_cache_key: str,
    transport: str,
    realm: str,
) -> str:
    raw = (
        f"v{LINEAGE_SCHEMA_VERSION}\0{transport}\0{realm}\0"
        f"{_active_hermes_home()}\0{session_id}\0{logical_cache_key}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _fingerprint(value: Any) -> str:
    encoded = _stable_json(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _canonical_tools(tools: Any) -> Any:
    if not isinstance(tools, list):
        return []
    return tools


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        return json.dumps(str(value), ensure_ascii=True)


def _infer_reasoning_effort(request: dict[str, Any], extra_body: dict[str, Any]) -> str | None:
    explicit = _clean_string(request.get("reasoning_effort")) or _clean_string(extra_body.get("reasoning_effort"))
    if explicit:
        return _normalize_reasoning_effort(explicit)
    for container in (request, extra_body):
        reasoning = container.get("reasoning")
        if isinstance(reasoning, dict):
            effort = _clean_string(reasoning.get("effort"))
            if effort:
                return _normalize_reasoning_effort(effort)
    return None


def _normalize_reasoning_effort(value: str) -> str:
    normalized = value.strip().lower()
    return "low" if normalized == "minimal" else normalized


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _max_tracked_keys() -> int:
    raw = os.environ.get("SPARTAN_CACHE_LINEAGE_MAX_KEYS")
    if not raw:
        return DEFAULT_MAX_TRACKED_KEYS
    try:
        return max(1, min(8192, int(raw)))
    except ValueError:
        return DEFAULT_MAX_TRACKED_KEYS


def _reset_for_tests() -> None:
    global _state_bytes
    with _lock:
        _state.clear()
        _state_bytes = 0
