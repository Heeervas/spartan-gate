from __future__ import annotations

from .reasoning_replay import allows_auth, unavailable_error

from .attempt import CodexAttempt, outcome_slot, valid_tool_arguments

import asyncio
import base64
import copy
import errno
import fcntl
import json
import math
import os
import secrets
import stat
import struct
import sys
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from aiohttp import ClientError, ClientSession, ClientTimeout
from .pi_usage import CURRENT_REQUEST

from .telemetry import TelemetryStore
from .transport import CodexTransport, TransportError, observe_event
from .sse import frames as sse_frames
from .util import (
    epoch_millis,
    is_jwt_expired,
    jwt_claims,
    now_iso,
    parse_float,
    parse_int,
    sha,
    stable_json,
    text_content,
    token_count_estimate,
)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_RESPONSES_URL = f"{CODEX_BASE_URL}/responses"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
FIVE_HOURS_SECONDS = 18_000
SEVEN_DAYS_SECONDS = 604_800
INLINE_REASONING_PLACEHOLDER = "<think> </think>"
TERMINAL_CODEX_EVENTS = {"response.completed", "response.incomplete", "response.failed", "response.cancelled"}
CODEX_USAGE_DRAIN_TIMEOUT_SECONDS = 15.0
CODEX_USAGE_CACHE_TTL_SECONDS = 60.0
CODEX_RECOVERY_PROBE_MIN_INTERVAL_SECONDS = 5.0
CODEX_STREAM_TIMEOUT_CODE = "codex_stream_timeout"
CODEX_STREAM_ERROR_CODE = "codex_stream_error"
CODEX_TRANSIENT_UNAVAILABLE_CODE = "codex_transient_unavailable"
CODEX_RATE_LIMIT_UNAVAILABLE_CODE = "codex_429"
DEFAULT_CODEX_IMAGE_HOST_MODEL = "gpt-5.5"
DEFAULT_RECOVERY_WAIT_MS = 180_000
DEFAULT_RECOVERY_POLL_MS = 1_000
CODEX_AUTH_STORE_ROOT = Path("/app/data/codex-auth")
CODEX_AUTH_STORE_VERSION = 1
CODEX_AUTH_LOCK_TIMEOUT_SECONDS = 15.0
CODEX_AUTH_MAX_BYTES = 1_048_576

def valid_live_usage_window(source: dict[str, Any]) -> tuple[float, str] | None:
    used = source.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    try:
        used = float(used)
    except OverflowError:
        return None
    if not math.isfinite(used) or not 0 <= used <= 100:
        return None
    reset_at = source.get("reset_at")
    if isinstance(reset_at, bool):
        return None
    if isinstance(reset_at, (int, float)):
        try:
            reset_at = float(reset_at)
        except OverflowError:
            return None
        if not math.isfinite(reset_at) or reset_at <= 0:
            return None
        try:
            reset_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(reset_at))
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(reset_at, str):
        return None
    try:
        parsed_reset = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_reset.tzinfo is None:
        return None
    return used, reset_at


def live_usage_allows_recovery(payload: dict[str, Any]) -> bool:
    rate_limit = payload.get("rate_limit")
    if rate_limit is not None and not isinstance(rate_limit, dict):
        return False
    sources = (payload, rate_limit or {})
    saw_weekly = False
    for source in sources:
        for position in ("primary", "secondary"):
            window = source.get(f"{position}_window")
            if window is None:
                continue
            if not isinstance(window, dict):
                return False
            duration = window.get("limit_window_seconds")
            if duration is None:
                seconds = FIVE_HOURS_SECONDS if position == "primary" else SEVEN_DAYS_SECONDS
            else:
                if isinstance(duration, bool) or not isinstance(duration, int):
                    return False
                seconds = duration
                if seconds not in (FIVE_HOURS_SECONDS, SEVEN_DAYS_SECONDS):
                    return False
            validated = valid_live_usage_window(window)
            if validated is None or validated[0] >= 100:
                return False
            saw_weekly = saw_weekly or seconds == SEVEN_DAYS_SECONDS
    return saw_weekly


def normalize_usage_windows(payload: dict[str, Any]) -> dict[str, tuple[dict[str, Any], int]]:
    """Return recognized usage windows, preferring declared duration over position."""
    rate_limit = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else {}
    candidates = (
        ("primary", payload.get("primary_window")),
        ("secondary", payload.get("secondary_window")),
        ("primary", rate_limit.get("primary_window")),
        ("secondary", rate_limit.get("secondary_window")),
    )
    windows: dict[str, tuple[dict[str, Any], int]] = {}
    for position, candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        duration = candidate.get("limit_window_seconds")
        if duration is None:
            window_name = "fiveHour" if position == "primary" else "weekly"
            seconds = FIVE_HOURS_SECONDS if position == "primary" else SEVEN_DAYS_SECONDS
        else:
            try:
                seconds = int(duration)
            except (TypeError, ValueError):
                continue
            if seconds == FIVE_HOURS_SECONDS:
                window_name = "fiveHour"
            elif seconds == SEVEN_DAYS_SECONDS:
                window_name = "weekly"
            else:
                continue
        windows.setdefault(window_name, (candidate, seconds))
    return windows




class CodexAuthStoreError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class CodexAuthCandidate:
    tokens: dict[str, Any]
    fingerprint: str
    source: str
    expires_at: float | None
    last_refresh_at: str | None = None


@dataclass(frozen=True)
class CodexAuth:
    access_token: str
    refresh_token: str | None = None
    account_id: str | None = None
    source_path: str | None = None

    @property
    def account_key(self) -> str | None:
        return sha(self.account_id, 16) if self.account_id else None


@dataclass
class CodexSlot:
    index: int
    path: str | None = None
    token: str | None = None
    auth: CodexAuth | None = None
    auth_fingerprint: str | None = None
    auth_source_fingerprint: str | None = None
    auth_unavailable_reason: str | None = None
    auth_retry_at: float = 0
    auth_state_source: str | None = None
    auth_expires_at: float | None = None
    auth_last_refresh_at: str | None = None
    auth_persistence_healthy: bool = True
    auth_error_code: str | None = None
    failed_auth_fingerprint: str | None = None
    rate_limited_until: float = 0
    transient_until: float = 0
    in_flight: int = 0
    last_selected_at: float = 0

    def account_key(self) -> str | None:
        return self.auth.account_key if self.auth else None


@dataclass
class CacheLease:
    affinity_key: str
    account_key: str
    slot_index: int
    started_at: float
    last_used_at: float
    nominal_expires_at: float
    max_expires_at: float


@dataclass
class CodexCallResult:
    status: int
    body: dict[str, Any] | bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    stream: AsyncIterator[bytes] | None = None
    slot_index: int | None = None
    account_key: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    error: str | None = None
    actual_tool_call_count: int | None = None
    actual_tool_names: list[str] | None = None
    tool_telemetry_complete: bool | None = None
    native_cache: dict[str, Any] | None = None
    cache_response_id: str | None = field(default=None, repr=False)
    cache_write_tokens: int | None = None
    cache_read_known: bool = False
    first_output_at: float | None = None
    stream_cleanup: Any = field(default=None, repr=False)
    cache_provider_diagnostics: dict[str, Any] | None = None
    _native_tool_aliases: dict[str, str] = field(default_factory=dict, repr=False)
    _native_tool_names: dict[str, str] = field(default_factory=dict, repr=False)
    _native_tool_invalid: bool = False
    completed: bool = False
    usage_seen: bool = False
    usage_event_type: str | None = None
    upstream_response_time_ms: int | None = None
    total_response_time_ms: int | None = None
    transport_details: dict[str, Any] | None = None
    retry_allowed: bool = True
    selection_diagnostics: dict[str, Any] | None = None


@dataclass
class SlotAvailability:
    total: int = 0
    eligible: int = 0
    disabled: int = 0
    auth_unavailable: int = 0
    rate_limited: int = 0
    transient_5xx: int = 0
    earliest_rate_limit_until: float | None = None
    earliest_transient_until: float | None = None

    def diagnostics(self) -> dict[str, int]:
        return {
            "total": self.total,
            "eligible": self.eligible,
            "disabled": self.disabled,
            "auth_unavailable": self.auth_unavailable,
            "rate_limited": self.rate_limited,
            "transient_5xx": self.transient_5xx,
        }

    def earliest_recovery(self) -> float | None:
        candidates = [
            item
            for item in (self.earliest_rate_limit_until, self.earliest_transient_until)
            if item is not None
        ]
        return min(candidates) if candidates else None


@dataclass
class CodexEventState:
    text_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    item_to_call: dict[str, str] = field(default_factory=dict)
    text_item_ids: set[str] = field(default_factory=set)
    tool_arg_delta_ids: set[str] = field(default_factory=set)
    reasoning_part_keys: set[str] = field(default_factory=set)
    reasoning_presence_ids: set[str] = field(default_factory=set)
    usage: dict[str, Any] = field(default_factory=dict)
    usage_seen: bool = False
    usage_event_type: str | None = None
    terminal_error: dict[str, Any] | None = None
    finish_reason: str = "stop"
    saw_output_item: bool = False
    completed: bool = False
    terminal_seen: bool = False

    def text(self) -> str:
        return "".join(self.text_parts)

    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)

    def calls(self) -> list[dict[str, Any]]:
        return list(self.tool_calls.values())

    def has_payload(self) -> bool:
        return bool(self.text() or self.reasoning() or self.tool_calls)


def resolve_auth_path_entry(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    return candidate if candidate.endswith(".json") else str(Path(candidate) / "auth.json")


def resolve_auth_paths(env: dict[str, str] | None = None) -> list[str]:
    env = env or os.environ
    multi = env.get("OPENAI_CODEX_AUTH_PATHS")
    if multi and multi.strip():
        return [path for path in (resolve_auth_path_entry(item) for item in multi.split(",")) if path]
    single = env.get("OPENAI_CODEX_AUTH_PATH")
    if single:
        return [resolve_auth_path_entry(single)]
    if env.get("OPENAI_CODEX_TOKEN"):
        return []
    return [resolve_auth_path_entry(env.get("CODEX_HOME") or str(Path.home() / ".codex"))]


def read_auth_file(path: str) -> tuple[dict[str, Any], str] | None:
    try:
        content = Path(path).read_text(encoding="utf-8")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        return parsed, sha(content, 64)
    except Exception:
        return None


def derive_account_id(tokens: dict[str, Any]) -> str | None:
    explicit = tokens.get("account_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    id_token = tokens.get("id_token")
    claims = jwt_claims(id_token) if isinstance(id_token, str) else None
    for key in ("https://api.openai.com/auth", "account_id", "sub"):
        value = claims.get(key) if claims else None
        if isinstance(value, dict) and isinstance(value.get("account_id"), str):
            return value["account_id"]
        if isinstance(value, str) and value:
            return value
    return None


def build_content_prompt_cache_key(request: dict[str, Any]) -> str | None:
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    system = ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            system = text_content(message.get("content"))
            break
    raw_tools = request.get("tools") if isinstance(request.get("tools"), list) else []
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            shaped = {"type": "function", "name": fn.get("name")}
            if "description" in fn:
                shaped["description"] = fn["description"]
            if "parameters" in fn:
                shaped["parameters"] = fn["parameters"]
            if "strict" in fn:
                shaped["strict"] = fn["strict"]
            tools.append(shaped)
        else:
            tools.append(tool)
    tools.sort(key=lambda item: str(item.get("name") or item.get("type") or ""))
    if not system and not tools:
        return None
    return f"pck_{sha(system + chr(0) + stable_json(tools), 24)}"


def resolve_prompt_cache_key(request: dict[str, Any], session_id: str | None, explicit: str | None) -> str | None:
    raw = explicit or request.get("prompt_cache_key") or request.get("user")
    if isinstance(raw, str) and raw.strip():
        return f"clawroute:{sha(raw.strip(), 32)}"
    content_key = build_content_prompt_cache_key(request)
    if content_key:
        return content_key
    if session_id:
        return f"clawroute:{sha(session_id, 32)}"
    return None


def resolve_affinity_key(request: dict[str, Any], session_id: str | None, explicit: str | None) -> str | None:
    raw = explicit or request.get("prompt_cache_key") or request.get("user")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return build_content_prompt_cache_key(request) or session_id


def session_id_from_request(request: dict[str, Any], prompt_cache_key: str | None = None) -> str | None:
    if prompt_cache_key:
        return sha(f"prompt_cache_key\0{prompt_cache_key}", 16)
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            content = text_content(message.get("content"))
            marker = '"sender_id"'
            if marker in content:
                import re
                match = re.search(r'"sender_id"\s*:\s*"(\d+)"', content)
                if match:
                    return sha(match.group(1), 8)
    content_key = build_content_prompt_cache_key(request)
    return sha(f"prompt_cache_key\0{content_key}", 16) if content_key else None


def replay_assistant_reasoning_enabled(env: dict[str, str] | None = None) -> bool:
    value = (env or os.environ).get("SPARTAN_ROUTE_REPLAY_ASSISTANT_REASONING")
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def chat_messages_to_responses_input(messages: list[dict[str, Any]]) -> list[Any]:
    output: list[Any] = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "developer"}:
            output.append({"role": "developer", "content": text_content(message.get("content"))})
        elif role == "user":
            content = message.get("content")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append({"type": "input_text", "text": item["text"]})
                    elif isinstance(item, dict) and item.get("type") == "image_url":
                        image = item.get("image_url")
                        url = image.get("url") if isinstance(image, dict) else image
                        if isinstance(url, str):
                            parts.append({"type": "input_image", "image_url": url})
                output.append({"role": "user", "content": parts if len(parts) != 1 else parts[0].get("text", parts)})
            else:
                output.append({"role": "user", "content": text_content(content)})
        elif role == "assistant":
            if replay_assistant_reasoning_enabled():
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    output.append({"type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning}]})
            text = text_content(message.get("content"))
            if text:
                output.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                output.append({
                    "type": "function_call",
                    "call_id": call.get("id"),
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments", "{}"),
                })
        elif role == "tool" and message.get("tool_call_id"):
            output.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id"),
                "output": text_content(message.get("content")) or stable_json(message.get("content")),
            })
    return output


def normalize_tool_choice(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if value.get("type") == "function" and isinstance(value.get("function"), dict):
        name = value["function"].get("name")
        if isinstance(name, str) and name:
            return {"type": "function", "name": name}
    return value


def build_codex_request_body(request: dict[str, Any], model_name: str, prompt_cache_key: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model_name,
        "input": chat_messages_to_responses_input(request.get("messages") or []),
        "stream": True,
        "instructions": "",
        "store": False,
    }
    if prompt_cache_key:
        body["prompt_cache_key"] = prompt_cache_key
    if "service_tier" in request:
        body["service_tier"] = request["service_tier"]
    if "top_p" in request:
        body["top_p"] = request["top_p"]
    if isinstance(request.get("tools"), list):
        converted = []
        for tool in request["tools"]:
            if isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                fn = tool["function"]
                item = {"type": "function", "name": fn.get("name")}
                if "description" in fn:
                    item["description"] = fn["description"]
                if "parameters" in fn:
                    item["parameters"] = fn["parameters"]
                if "strict" in fn:
                    item["strict"] = fn["strict"]
                converted.append(item)
            else:
                converted.append(tool)
        body["tools"] = converted
    if "tool_choice" in request:
        body["tool_choice"] = normalize_tool_choice(request["tool_choice"])
    if isinstance(request.get("reasoning_effort"), str):
        effort = request["reasoning_effort"].strip().lower()
        if effort:
            body["reasoning"] = {"effort": effort}
    return body


def build_codex_responses_body(
    request: dict[str, Any],
    model_name: str,
    prompt_cache_key: str | None,
) -> dict[str, Any]:
    """Build a native Codex Responses request without a Chat conversion."""
    body = copy.deepcopy(request)
    body["model"] = model_name
    body["stream"] = True
    body["store"] = False
    if isinstance(body.get("input"), str):
        body["input"] = [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": body["input"]}],
        }]
    body.pop("max_output_tokens", None)
    for key in (
        "spartan_chain_id",
        "spartan_expected_realm",
        "spartan_binding_mac",
        "spartan_cache_lineage",
        "spartan_cache_lineage_reason",
    ):
        body.pop(key, None)
    if prompt_cache_key:
        body["prompt_cache_key"] = prompt_cache_key
    include = body.get("include")
    if not isinstance(include, list):
        include = []
    if "reasoning.encrypted_content" not in include:
        include = [*include, "reasoning.encrypted_content"]
    body["include"] = include
    reasoning = body.get("reasoning")
    if not isinstance(reasoning, dict):
        reasoning = {}
    effort = body.pop("reasoning_effort", None)
    if isinstance(effort, str) and effort.strip():
        reasoning.setdefault("effort", effort.strip().lower())
    reasoning.setdefault("context", "all_turns")
    body["reasoning"] = reasoning
    return body


def build_codex_responses_headers(body: dict[str, Any], auth: CodexAuth) -> dict[str, str]:
    """Keep subscription HTTP cache affinity aligned with the final body key."""
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
    }
    if auth.account_id:
        headers["chatgpt-account-id"] = auth.account_id
    cache_key = body.get("prompt_cache_key")
    if isinstance(cache_key, str) and cache_key:
        headers["session-id"] = cache_key
    return headers


def parse_codex_error(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        error = parsed.get("error", parsed) if isinstance(parsed, dict) else {}
        return error if isinstance(error, dict) else {}
    except Exception:
        return {"message": text}


def should_retry(status: int, text: str) -> bool:
    if status in {401, 500}:
        return True
    error = parse_codex_error(text)
    code = str(error.get("code") or "")
    error_type = str(error.get("type") or error.get("upstream_type") or "")
    message = str(error.get("message") or text)
    if status == 429:
        return error_type == "usage_limit_reached" or code == "usage_limit_reached"
    if status == 502:
        if code.startswith("codex_image_"):
            return False
        return (
            code in {
                "codex_error",
                "server_error",
                "codex_empty_response",
                "codex_incomplete_response",
                CODEX_STREAM_TIMEOUT_CODE,
                CODEX_STREAM_ERROR_CODE,
            }
            or error_type in {"server_error", "upstream_error"}
            or any(word in message.lower() for word in ("aborted", "timeout", "timed out", "fetch failed"))
        )
    return False


def codex_request_timeout_ms() -> int:
    return parse_int(os.environ.get("CODEX_REQUEST_TIMEOUT_MS"), 180_000, 10_000, 900_000)


def codex_image_host_model() -> str:
    value = (os.environ.get("CODEX_IMAGE_HOST_MODEL") or os.environ.get("SPARTAN_ROUTE_CODEX_IMAGE_HOST_MODEL") or "").strip()
    return value or DEFAULT_CODEX_IMAGE_HOST_MODEL


def requested_image_size(size: Any) -> tuple[int, int] | None:
    text = str(size or "").strip()
    if not text or text == "auto" or "x" not in text:
        return None
    width, height = text.split("x", 1)
    try:
        return int(width), int(height)
    except Exception:
        return None


def image_bytes_dimensions(raw: bytes) -> tuple[int, int] | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        return struct.unpack(">II", raw[16:24])
    if raw.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(raw):
            while index < len(raw) and raw[index] == 0xFF:
                index += 1
            if index >= len(raw):
                return None
            marker = raw[index]
            index += 1
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(raw):
                return None
            segment_length = struct.unpack(">H", raw[index:index + 2])[0]
            if marker in {*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0)}:
                if index + 7 <= len(raw):
                    height, width = struct.unpack(">HH", raw[index + 3:index + 7])
                    return width, height
                return None
            index += segment_length
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP" and raw[12:16] == b"VP8X" and len(raw) >= 30:
        width = int.from_bytes(raw[24:27], "little") + 1
        height = int.from_bytes(raw[27:30], "little") + 1
        return width, height
    return None


def image_b64_dimensions(value: str) -> tuple[int, int] | None:
    try:
        return image_bytes_dimensions(base64.b64decode(value, validate=False))
    except Exception:
        return None


class CodexRouter:
    def __init__(
        self,
        session: ClientSession,
        telemetry: TelemetryStore,
        *,
        auth_store_root: Path | None = None,
        control_session: ClientSession | None = None,
        transport_mode: str = "http",
    ):
        self.session = session
        self.control_session = control_session or session
        self.transport = CodexTransport(session, transport_mode)
        self.telemetry = telemetry
        self.slots: list[CodexSlot] = []
        self.model_catalog: Any = None
        self.leases: dict[str, CacheLease] = {}
        self.refresh_locks: dict[str, asyncio.Lock] = {}
        self._availability_event: asyncio.Event | None = None
        self._availability_event_loop: asyncio.AbstractEventLoop | None = None
        self._usage_cache: dict[str, Any] | None = None
        self._last_recovery_probe_at = 0.0
        self._usage_refresh_task: asyncio.Task[dict[str, Any]] | None = None
        self._usage_progress_snapshot: dict[str, Any] | None = None
        self._usage_generation = 0
        self._usage_operation_lock = asyncio.Lock()
        # Constructor-only injection keeps production fixed at /app/data/codex-auth.
        self.auth_store_root = Path(auth_store_root) if auth_store_root is not None else CODEX_AUTH_STORE_ROOT
        self._init_slots()

    def _init_slots(self) -> None:
        token = (os.environ.get("OPENAI_CODEX_TOKEN") or "").strip()
        if token:
            self.slots = [CodexSlot(
                0,
                token=token,
                auth=CodexAuth(access_token=token),
                auth_state_source="static",
                auth_expires_at=self._token_expiry(token),
            )]
            return
        self.slots = [CodexSlot(index, path=path) for index, path in enumerate(resolve_auth_paths())]

    def reset_state(self) -> None:
        if getattr(self, "model_catalog", None) is not None:
            self.model_catalog.reset()
        self._last_recovery_probe_at = 0.0
        self._usage_generation = int(getattr(self, "_usage_generation", 0)) + 1
        task = getattr(self, "_usage_refresh_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._usage_refresh_task = None
        # A cancelled old controller may still be draining owned children.
        # The new generation must not queue behind that retired generation.
        self._usage_operation_lock = asyncio.Lock()
        self.slots = []
        self.leases.clear()
        self.refresh_locks.clear()
        self._usage_progress_snapshot = None
        self._notify_availability_changed()
        self._usage_cache = None
        self._init_slots()

    async def cleanup(self) -> None:
        transport = getattr(self, "transport", None)
        if transport is not None:
            await transport.close()
        self._usage_generation = int(getattr(self, "_usage_generation", 0)) + 1
        task = getattr(self, "_usage_refresh_task", None)
        self._usage_refresh_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"[spartan-route] Codex usage refresh cleanup failed ({type(exc).__name__})", file=sys.stderr)

    def slot_path_label(self, slot: CodexSlot) -> str | None:
        if slot.path:
            return slot.path
        if slot.token:
            return "OPENAI_CODEX_TOKEN"
        return None

    def slot_status(self, include_paths: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        telemetry = getattr(self, "telemetry", None)
        overrides = telemetry.slot_routing_overrides() if telemetry and self.slots else {}
        out = []
        for slot in self.slots:
            auth = slot.auth
            routing = self.slot_routing_status(slot, overrides=overrides)
            out.append({
                "slotIndex": slot.index,
                "accountKey": auth.account_key if auth else None,
                "slotPathHash": sha(slot.path, 16) if slot.path else None,
                "authFingerprintHash": sha(slot.auth_fingerprint, 16) if slot.auth_fingerprint else None,
                "routingEnabled": routing["routingEnabled"],
                "requestedRoutingEnabled": routing["requestedRoutingEnabled"],
                "routingOverrideUpdatedAt": routing["overrideUpdatedAt"],
                "routingIdentityMismatch": routing["identityMismatch"],
                "routingIdentityUnverified": routing["identityUnverified"],
                "rateLimitedUntil": slot.rate_limited_until,
                "transientUntil": slot.transient_until,
                "authAvailable": bool(auth and not is_jwt_expired(auth.access_token)),
                "authUnavailableReason": slot.auth_unavailable_reason,
                "authRetryAt": slot.auth_retry_at if slot.auth_retry_at > now else None,
                "authStateSource": slot.auth_state_source,
                "authExpiresAt": slot.auth_expires_at,
                "authLastRefreshAt": slot.auth_last_refresh_at,
                "authPersistenceHealthy": slot.auth_persistence_healthy,
                "authErrorCode": slot.auth_error_code,
                "inFlight": slot.in_flight,
            })
            if include_paths:
                out[-1]["slotPath"] = self.slot_path_label(slot)
        return out

    def _slot_identity(self, slot: CodexSlot) -> dict[str, str | None]:
        return {
            "accountKey": slot.account_key(),
            "slotPathHash": sha(slot.path, 16) if slot.path else None,
            "authFingerprintHash": sha(slot.auth_fingerprint, 16) if slot.auth_fingerprint else None,
        }

    def _availability_signal(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        event = getattr(self, "_availability_event", None)
        event_loop = getattr(self, "_availability_event_loop", None)
        if event is None or event_loop is not loop:
            self._availability_event = asyncio.Event()
            self._availability_event_loop = loop
        return self._availability_event

    def _notify_availability_changed(self) -> None:
        event = getattr(self, "_availability_event", None)
        if event is not None:
            event.set()

    def slot_routing_status(
        self, slot: CodexSlot, *, overrides: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        telemetry = getattr(self, "telemetry", None)
        override = (
            overrides.get(slot.index) if overrides is not None
            else telemetry.slot_routing_override(slot.index) if telemetry else None
        )
        if not override:
            return {
                "routingEnabled": True,
                "requestedRoutingEnabled": True,
                "identityMismatch": False,
                "identityUnverified": False,
                "overrideUpdatedAt": None,
            }
        identity = self._slot_identity(slot)
        keys = ("accountKey", "slotPathHash")
        mismatch = any(
            bool(override.get(key) and identity.get(key) and override.get(key) != identity.get(key))
            for key in keys
        )
        unverified = any(bool(override.get(key) and not identity.get(key)) for key in keys)
        requested = bool(override.get("routingEnabled"))
        enabled = requested or mismatch
        return {
            "routingEnabled": enabled,
            "requestedRoutingEnabled": requested,
            "identityMismatch": mismatch,
            "identityUnverified": unverified and not requested,
            "overrideUpdatedAt": override.get("updatedAt"),
        }

    async def set_slot_routing_enabled(self, slot_index: int, routing_enabled: bool) -> dict[str, Any]:
        slot = next((candidate for candidate in self.slots if candidate.index == int(slot_index)), None)
        if slot is None:
            raise IndexError("slot not found")
        await self.load_auth(slot)
        identity = self._slot_identity(slot)
        await asyncio.to_thread(self.telemetry.set_slot_routing_enabled,
            slot.index,
            bool(routing_enabled),
            account_key=identity["accountKey"],
            slot_path_hash=identity["slotPathHash"],
            auth_fingerprint_hash=identity["authFingerprintHash"],
        )
        if not routing_enabled:
            self._invalidate_slot_leases(slot)
        self._usage_cache = None
        if getattr(self, "model_catalog", None) is not None:
            self.model_catalog.invalidate_slot(slot.index)
            self.model_catalog.schedule_refresh()
        self._notify_availability_changed()
        return next(status for status in self.slot_status(include_paths=True) if status["slotIndex"] == slot.index)

    def lease_status(self) -> list[dict[str, Any]]:
        self._clear_expired_leases()
        return [
            {
                "affinityKeyHash": sha(lease.affinity_key, 12),
                "slotIndex": lease.slot_index,
                "accountKey": lease.account_key,
                "startedAt": self._iso_from_epoch(lease.started_at),
                "lastUsedAt": self._iso_from_epoch(lease.last_used_at),
                "nominalExpiresAt": self._iso_from_epoch(lease.nominal_expires_at),
                "maxExpiresAt": self._iso_from_epoch(lease.max_expires_at),
            }
            for lease in sorted(self.leases.values(), key=lambda item: item.last_used_at, reverse=True)
        ]

    def balancer_status(self, include_paths: bool = False) -> dict[str, Any]:
        return {"mode": "minimal", "slots": self.slot_status(include_paths=include_paths), "activeLeases": self.lease_status()}

    def _iso_from_epoch(self, value: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(value))

    def _source_identity(self, path: str) -> str:
        return sha(os.path.abspath(os.path.normpath(path)), 64)

    def _token_expiry(self, access_token: Any) -> float | None:
        claims = jwt_claims(access_token) if isinstance(access_token, str) else None
        expiry = claims.get("exp") if claims else None
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
            return None
        numeric = float(expiry)
        return numeric if math.isfinite(numeric) else None

    def _candidate(
        self,
        tokens: Any,
        fingerprint: str,
        source: str,
        last_refresh_at: str | None = None,
    ) -> CodexAuthCandidate | None:
        if not isinstance(tokens, dict):
            return None
        access = tokens.get("access_token")
        if not isinstance(access, str) or not access:
            return None
        return CodexAuthCandidate(
            copy.deepcopy(tokens), fingerprint, source, self._token_expiry(access), last_refresh_at
        )

    def _open_owned_directory(self, path: Path, *, create: bool = False) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            parent_fd = os.open(path.parent, flags)
        except OSError as exc:
            raise CodexAuthStoreError("auth_store_parent_unavailable") from exc
        directory_fd: int | None = None
        try:
            parent_stat = os.fstat(parent_fd)
            if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.geteuid():
                raise CodexAuthStoreError("auth_store_parent_unsafe")
            os.fsync(parent_fd)
            if create:
                try:
                    os.mkdir(path.name, 0o700, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except FileExistsError:
                    pass
            directory_fd = os.open(path.name, flags, dir_fd=parent_fd)
            metadata = os.fstat(directory_fd)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise CodexAuthStoreError("auth_store_unsafe")
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                os.fchmod(directory_fd, 0o700)
            return directory_fd
        except CodexAuthStoreError:
            if directory_fd is not None:
                os.close(directory_fd)
            raise
        except OSError as exc:
            if directory_fd is not None:
                os.close(directory_fd)
            raise CodexAuthStoreError("auth_store_unavailable") from exc
        finally:
            os.close(parent_fd)

    def _read_managed_candidate(self, path: str) -> CodexAuthCandidate | None:
        store_fd = self._open_owned_directory(
            getattr(self, "auth_store_root", CODEX_AUTH_STORE_ROOT), create=True
        )
        identity = self._source_identity(path)
        try:
            # This also re-establishes the durability gate after a prior replace/fsync failure.
            os.fsync(store_fd)
            try:
                file_fd = os.open(
                    f"{identity}.json",
                    os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=store_fd,
                )
            except FileNotFoundError:
                return None
            try:
                metadata = os.fstat(file_fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                    raise CodexAuthStoreError("auth_state_unsafe")
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    os.fchmod(file_fd, 0o600)
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = os.read(file_fd, min(65_536, CODEX_AUTH_MAX_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > CODEX_AUTH_MAX_BYTES:
                        raise CodexAuthStoreError("auth_state_too_large")
            finally:
                os.close(file_fd)
            try:
                document = json.loads(b"".join(chunks))
            except Exception as exc:
                raise CodexAuthStoreError("auth_state_invalid") from exc
            if not isinstance(document, dict) or document.get("version") != CODEX_AUTH_STORE_VERSION:
                raise CodexAuthStoreError("auth_state_schema_invalid")
            if document.get("sourcePathHash") != identity:
                raise CodexAuthStoreError("auth_state_binding_mismatch")
            tokens = document.get("tokens")
            return self._candidate(
                tokens,
                sha(stable_json(tokens), 64),
                "managed",
                document.get("lastRefreshAt") if isinstance(document.get("lastRefreshAt"), str) else None,
            )
        except CodexAuthStoreError:
            raise
        except OSError as exc:
            raise CodexAuthStoreError("auth_state_unavailable") from exc
        finally:
            os.close(store_fd)

    def _load_candidate(
        self,
        path: str,
        store_errors: list[CodexAuthStoreError] | None = None,
    ) -> CodexAuthCandidate | None:
        candidates: list[CodexAuthCandidate] = []
        bootstrap: CodexAuthCandidate | None = None
        snapshot = read_auth_file(path)
        if snapshot:
            data, fingerprint = snapshot
            tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
            candidate = self._candidate(tokens, fingerprint, "bootstrap")
            if candidate:
                bootstrap = candidate
                candidates.append(candidate)
        try:
            managed = self._read_managed_candidate(path)
        except CodexAuthStoreError as exc:
            bootstrap_valid = any(
                item.source == "bootstrap"
                and not is_jwt_expired(str(item.tokens.get("access_token") or ""))
                for item in candidates
            )
            if store_errors is None or not bootstrap_valid:
                raise
            store_errors.append(exc)
            managed = None
        if managed and bootstrap:
            bootstrap_account = derive_account_id(bootstrap.tokens)
            managed_account = derive_account_id(managed.tokens)
            if bootstrap_account and managed_account != bootstrap_account:
                managed = None
        if managed:
            candidates.append(managed)
        if not candidates:
            return None
        # Furthest known expiry wins; immutable bootstrap wins an exact tie.
        return max(
            candidates,
            key=lambda item: (
                item.expires_at is not None,
                item.expires_at or float("-inf"),
                item.source == "bootstrap",
            ),
        )

    def _public_auth_error_code(self, error: CodexAuthStoreError) -> str:
        code = error.code
        if code == "auth_lock_timeout":
            return "refresh_lock_timeout"
        if code.startswith(("auth_store", "auth_state", "auth_lock_")):
            return "auth_store_unavailable"
        if code == "auth_persist_failed":
            return "refresh_persist_failed"
        if code == "auth_refresh_rate_limited":
            return "refresh_rate_limited"
        if code.startswith("auth_refresh_http_unexpected_"):
            return "refresh_invalid_response"
        if code.startswith("auth_refresh_http_"):
            try:
                status_code = int(code.rsplit("_", 1)[-1])
            except ValueError:
                return "refresh_temporarily_unavailable"
            return "reauth_required" if 400 <= status_code < 500 else "refresh_temporarily_unavailable"
        if code in {
            "auth_refresh_invalid_json",
            "auth_refresh_invalid_payload",
            "auth_refresh_access_missing",
            "auth_refresh_account_missing",
        }:
            return "refresh_invalid_response"
        if code in {"auth_refresh_transport"}:
            return "refresh_temporarily_unavailable"
        if code in {"auth_candidate_missing", "auth_refresh_token_missing"}:
            return "reauth_required"
        return "refresh_internal_error"

    def _set_auth_error(
        self,
        slot: CodexSlot,
        error: CodexAuthStoreError,
        fingerprint: str | None = None,
    ) -> None:
        public_code = self._public_auth_error_code(error)
        slot.auth = None
        slot.auth_error_code = public_code
        slot.auth_unavailable_reason = public_code
        slot.auth_persistence_healthy = public_code not in {
            "auth_store_unavailable",
            "refresh_persist_failed",
        }
        if error.retryable:
            slot.auth_retry_at = error.retry_after or (time.time() + 300)
        else:
            slot.failed_auth_fingerprint = fingerprint
            slot.auth_retry_at = 0

    def _adopt_candidate(self, slot: CodexSlot, candidate: CodexAuthCandidate) -> CodexAuth | None:
        tokens = candidate.tokens
        access = tokens.get("access_token")
        account_id = derive_account_id(tokens)
        if not isinstance(access, str) or not account_id:
            return None
        refresh = tokens.get("refresh_token")
        auth = CodexAuth(
            access,
            refresh if isinstance(refresh, str) else None,
            account_id,
            slot.path,
        )
        slot.auth = auth
        slot.auth_fingerprint = candidate.fingerprint
        slot.auth_state_source = candidate.source
        slot.auth_expires_at = candidate.expires_at
        slot.auth_last_refresh_at = candidate.last_refresh_at
        slot.auth_persistence_healthy = True
        slot.auth_error_code = None
        slot.auth_unavailable_reason = None
        slot.auth_retry_at = 0
        slot.failed_auth_fingerprint = None
        return auth

    def _invalidate_changed_auth_candidate(self, slot: CodexSlot) -> bool:
        if not slot.path:
            return False
        snapshot = read_auth_file(slot.path)
        source_fingerprint = snapshot[1] if snapshot else ""
        if slot.auth_source_fingerprint is None:
            slot.auth_source_fingerprint = source_fingerprint
            return False
        if source_fingerprint == slot.auth_source_fingerprint:
            return False

        self._invalidate_slot_leases(slot)
        slot.auth_source_fingerprint = source_fingerprint
        slot.auth = None
        slot.auth_fingerprint = None
        slot.auth_unavailable_reason = None
        slot.auth_retry_at = 0
        slot.auth_state_source = None
        slot.auth_expires_at = None
        slot.auth_last_refresh_at = None
        slot.auth_persistence_healthy = True
        slot.auth_error_code = None
        slot.failed_auth_fingerprint = None
        slot.rate_limited_until = 0
        self._usage_cache = None
        if getattr(self, "model_catalog", None) is not None:
            self.model_catalog.invalidate_slot(slot.index)
        self._notify_availability_changed()
        return True

    async def load_auth(self, slot: CodexSlot) -> CodexAuth | None:
        if slot.token:
            return slot.auth
        self._invalidate_changed_auth_candidate(slot)
        if slot.auth_retry_at > time.time():
            return None
        if slot.auth and not is_jwt_expired(slot.auth.access_token):
            return slot.auth
        if not slot.path:
            slot.auth_unavailable_reason = "missing"
            return None
        store_errors: list[CodexAuthStoreError] = []
        try:
            candidate = self._load_candidate(slot.path, store_errors)
        except CodexAuthStoreError as exc:
            self._set_auth_error(slot, exc)
            return None
        if not candidate:
            slot.auth = None
            slot.auth_unavailable_reason = "missing"
            return None
        if slot.failed_auth_fingerprint == candidate.fingerprint:
            slot.auth_unavailable_reason = slot.auth_error_code or "auth_refresh_terminal"
            return None
        tokens = candidate.tokens
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        account_id = derive_account_id(tokens)
        if is_jwt_expired(str(access or "")):
            if isinstance(refresh, str) and refresh:
                return await self._refresh(slot, candidate)
            slot.auth_unavailable_reason = "expired"
            return None
        if not account_id:
            slot.auth_unavailable_reason = "unknown_account"
            return None
        auth = self._adopt_candidate(slot, candidate)
        if auth and store_errors:
            slot.auth_persistence_healthy = False
            slot.auth_error_code = "auth_store_unavailable"
        return auth

    async def _acquire_process_lock(self, path: str) -> int:
        store_fd = self._open_owned_directory(
            getattr(self, "auth_store_root", CODEX_AUTH_STORE_ROOT), create=True
        )
        identity = self._source_identity(path)
        lock_fd: int | None = None
        try:
            lock_fd = os.open(
                f"{identity}.lock",
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=store_fd,
            )
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise CodexAuthStoreError("auth_lock_unsafe")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                os.fchmod(lock_fd, 0o600)
        except CodexAuthStoreError:
            if lock_fd is not None:
                os.close(lock_fd)
            raise
        except OSError as exc:
            if lock_fd is not None:
                os.close(lock_fd)
            raise CodexAuthStoreError("auth_lock_unavailable") from exc
        finally:
            os.close(store_fd)
        assert lock_fd is not None
        deadline = time.monotonic() + CODEX_AUTH_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(lock_fd)
                    raise CodexAuthStoreError(
                        "auth_lock_timeout", retry_after=time.time() + 5
                    )
                await asyncio.sleep(0.05)
            except OSError as exc:
                os.close(lock_fd)
                raise CodexAuthStoreError("auth_lock_unavailable") from exc

    def _release_process_lock(self, lock_fd: int) -> None:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    async def _refresh(self, slot: CodexSlot, original: CodexAuthCandidate) -> CodexAuth | None:
        assert slot.path
        lock = self.refresh_locks.setdefault(slot.path, asyncio.Lock())
        async with lock:
            lock_fd: int | None = None
            attempted_fingerprint = original.fingerprint
            try:
                lock_fd = await self._acquire_process_lock(slot.path)
                latest = self._load_candidate(slot.path)
                if latest is None:
                    raise CodexAuthStoreError("auth_candidate_missing", retryable=False)
                attempted_fingerprint = latest.fingerprint
                if latest.fingerprint != original.fingerprint and not is_jwt_expired(
                    str(latest.tokens.get("access_token") or "")
                ):
                    return self._adopt_candidate(slot, latest)
                refresh_token = latest.tokens.get("refresh_token")
                if not isinstance(refresh_token, str) or not refresh_token:
                    raise CodexAuthStoreError("auth_refresh_token_missing", retryable=False)
                fallback_account_id = derive_account_id(latest.tokens)
                async with getattr(self, "control_session", self.session).post(
                    OAUTH_TOKEN_URL,
                    data={
                        "client_id": OAUTH_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    allow_redirects=False,
                    timeout=ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        headers = getattr(response, "headers", {})
                        raw_retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
                        try:
                            retry_delay = max(1.0, min(float(raw_retry_after), 3600.0))
                            retry_after = time.time() + retry_delay
                        except (TypeError, ValueError):
                            retry_after = None
                        status_code = int(response.status)
                        if status_code == 429:
                            raise CodexAuthStoreError(
                                "auth_refresh_rate_limited",
                                retry_after=retry_after or time.time() + 300,
                            )
                        if status_code in {408, 409, 425} or 500 <= status_code <= 599:
                            raise CodexAuthStoreError(
                                f"auth_refresh_http_{status_code}", retry_after=retry_after
                            )
                        if 400 <= status_code < 500:
                            raise CodexAuthStoreError(
                                f"auth_refresh_http_{status_code}", retryable=False
                            )
                        raise CodexAuthStoreError(
                            f"auth_refresh_http_unexpected_{status_code}"
                        )
                    try:
                        payload = await response.json()
                    except Exception as exc:
                        raise CodexAuthStoreError("auth_refresh_invalid_json") from exc
                if not isinstance(payload, dict):
                    raise CodexAuthStoreError("auth_refresh_invalid_payload")
                access = payload.get("access_token")
                if not isinstance(access, str) or not access:
                    raise CodexAuthStoreError("auth_refresh_access_missing")
                next_refresh = (
                    payload.get("refresh_token")
                    if isinstance(payload.get("refresh_token"), str)
                    else refresh_token
                )
                account_id = derive_account_id(
                    {
                        "access_token": access,
                        "refresh_token": next_refresh,
                        "id_token": payload.get("id_token"),
                    }
                ) or fallback_account_id
                if not account_id:
                    raise CodexAuthStoreError("auth_refresh_account_missing")
                tokens: dict[str, Any] = {
                    "access_token": access,
                    "refresh_token": next_refresh,
                    "account_id": account_id,
                }
                if isinstance(payload.get("id_token"), str):
                    tokens["id_token"] = payload["id_token"]
                persisted = self._persist_refresh(slot.path, tokens)
                auth = self._adopt_candidate(slot, persisted)
                self._notify_availability_changed()
                return auth
            except CodexAuthStoreError as exc:
                self._set_auth_error(slot, exc, attempted_fingerprint)
                return None
            except (asyncio.TimeoutError, ClientError):
                self._set_auth_error(
                    slot, CodexAuthStoreError("auth_refresh_transport"), attempted_fingerprint
                )
                return None
            except Exception as exc:
                print(f"[spartan-route] Codex auth refresh failed internally ({type(exc).__name__})", file=sys.stderr)
                self._set_auth_error(
                    slot, CodexAuthStoreError("auth_refresh_internal"), attempted_fingerprint
                )
                return None
            finally:
                if lock_fd is not None:
                    self._release_process_lock(lock_fd)

    def _persist_refresh(self, path: str, tokens: dict[str, Any]) -> CodexAuthCandidate:
        store_fd = self._open_owned_directory(
            getattr(self, "auth_store_root", CODEX_AUTH_STORE_ROOT), create=True
        )
        identity = self._source_identity(path)
        filename = f"{identity}.json"
        temporary = f".{identity}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        document = {
            "version": CODEX_AUTH_STORE_VERSION,
            "sourcePathHash": identity,
            "lastRefreshAt": now_iso(),
            "tokens": tokens,
        }
        encoded = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        file_fd: int | None = None
        try:
            file_fd = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=store_fd,
            )
            os.fchmod(file_fd, 0o600)
            offset = 0
            while offset < len(encoded):
                written = os.write(file_fd, encoded[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "short auth state write")
                offset += written
            os.fsync(file_fd)
            os.close(file_fd)
            file_fd = None
            os.replace(
                temporary,
                filename,
                src_dir_fd=store_fd,
                dst_dir_fd=store_fd,
            )
            os.fsync(store_fd)
        except Exception as exc:
            try:
                os.unlink(temporary, dir_fd=store_fd)
            except FileNotFoundError:
                pass
            except Exception:
                pass
            raise CodexAuthStoreError("auth_persist_failed") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(store_fd)
        return CodexAuthCandidate(
            tokens,
            sha(stable_json(tokens), 64),
            "managed",
            self._token_expiry(tokens.get("access_token")),
            document["lastRefreshAt"],
        )

    def _lease_settings(self) -> tuple[str, float, float, float, int]:
        mode = (os.environ.get("CODEX_CACHE_LEASE_MODE") or "keyed").lower()
        if mode not in {"keyed", "global", "off"}:
            mode = "keyed"
        duration = parse_int(os.environ.get("CODEX_CACHE_LEASE_MINUTES"), 60, 1, 1440) * 60
        grace = parse_int(os.environ.get("CODEX_CACHE_LEASE_IDLE_GRACE_MINUTES"), 10, 1, 1440) * 60
        max_age = max(duration, parse_int(os.environ.get("CODEX_CACHE_LEASE_MAX_MINUTES"), 120, 1, 2880) * 60)
        max_entries = parse_int(os.environ.get("CODEX_CACHE_LEASE_MAX_ENTRIES"), 256, 1, 10000)
        return mode, duration, grace, max_age, max_entries

    def _lease_key(self, affinity_key: str) -> str:
        mode, *_ = self._lease_settings()
        return "__global__" if mode == "global" else affinity_key

    def _clear_expired_leases(self) -> None:
        mode, _duration, grace, _max_age, _max_entries = self._lease_settings()
        if mode == "off":
            self.leases.clear()
            return
        now = time.time()
        expired = []
        for key, lease in self.leases.items():
            in_grace = now >= lease.nominal_expires_at
            if now >= lease.max_expires_at or (in_grace and now - lease.last_used_at >= grace):
                expired.append(key)
        for key in expired:
            self.leases.pop(key, None)

    def _remember_lease(self, affinity_key: str | None, slot: CodexSlot | CodexAttempt) -> None:
        slot = outcome_slot(slot)
        if slot is None:
            return
        mode, duration, _grace, max_age, max_entries = self._lease_settings()
        if mode == "off" or not affinity_key or not slot.auth or not slot.auth.account_key:
            return
        now = time.time()
        self.leases[self._lease_key(affinity_key)] = CacheLease(
            affinity_key=affinity_key,
            account_key=slot.auth.account_key,
            slot_index=slot.index,
            started_at=now,
            last_used_at=now,
            nominal_expires_at=now + duration,
            max_expires_at=now + max_age,
        )
        while len(self.leases) > max_entries:
            oldest = min(self.leases.items(), key=lambda item: item[1].last_used_at)[0]
            self.leases.pop(oldest, None)

    def _invalidate_slot_leases(self, slot: CodexSlot) -> None:
        account_key = slot.account_key()
        for key, lease in list(self.leases.items()):
            if lease.slot_index == slot.index or (account_key and lease.account_key == account_key):
                self.leases.pop(key, None)

    async def choose_slot(self, affinity_key: str | None, excluded: set[int], excluded_accounts: set[str], model_id: str | None = None, reasoning_effort: str | None = None) -> tuple[CodexSlot, CodexAuth] | None:
        self._clear_expired_leases()
        now = time.time()
        candidates = []
        for slot, auth in await self._operational_candidates(excluded, excluded_accounts):
            if model_id and getattr(self, "model_catalog", None) is not None and not self.model_catalog.supports(slot, auth, model_id, reasoning_effort):
                continue
            candidates.append((slot, auth))
        if not candidates:
            return None
        if affinity_key:
            lease = self.leases.get(self._lease_key(affinity_key))
            if lease:
                for slot, auth in candidates:
                    if slot.index == lease.slot_index and auth.account_key == lease.account_key:
                        lease.last_used_at = now
                        return slot, auth
                self.leases.pop(self._lease_key(affinity_key), None)
        return sorted(candidates, key=lambda pair: (pair[0].in_flight, pair[0].last_selected_at, pair[0].index))[0]

    async def _operational_candidates(self, excluded: set[int], excluded_accounts: set[str]) -> list[tuple[CodexSlot, CodexAuth]]:
        candidates = []
        for slot in self.slots:
            if slot.index in excluded:
                continue
            if slot.rate_limited_until > time.time() and not self._invalidate_changed_auth_candidate(slot):
                continue
            if slot.transient_until > time.time():
                continue
            auth = await self.load_auth(slot)
            if auth is not None:
                candidates.append((slot, auth))
        # Loading another slot's auth can yield. Recheck mutable gates after all awaits.
        now = time.time()
        return [(slot, auth) for slot, auth in candidates
                if slot.index not in excluded
                and (not auth.account_key or auth.account_key not in excluded_accounts)
                and self.slot_routing_status(slot)["routingEnabled"]
                and slot.rate_limited_until <= now and slot.transient_until <= now
                and slot.auth is auth and allows_auth(auth)]

    async def model_selection_diagnostics(self, excluded: set[int], excluded_accounts: set[str], model_id: str, reasoning_effort: str | None = None) -> dict[str, Any]:
        catalog = getattr(self, "model_catalog", None)
        candidates = await self._operational_candidates(excluded, excluded_accounts)
        slots = []
        eligible = 0
        for slot, auth in candidates:
            detail = catalog.eligibility(slot, auth, model_id, reasoning_effort) if catalog is not None else {"eligible": True, "reason": "eligible"}
            eligible += int(detail["eligible"])
            if len(slots) < 64:
                slots.append({"slot_index": slot.index, "reason": detail["reason"],
                              "catalog_generation": detail.get("catalog_generation", 0),
                              "catalog_age_seconds": detail.get("catalog_age_seconds")})
        return {"requested_model": model_id[:160], "requested_reasoning_effort": reasoning_effort[:32] if isinstance(reasoning_effort, str) else None,
                "operational_eligible": len(candidates), "model_eligible": eligible, "slots": slots}

    async def choose_slot_with_catalog_recovery(self, affinity_key: str | None, excluded: set[int], excluded_accounts: set[str], model_id: str, reasoning_effort: str | None = None, *, diagnostics: dict[str, Any] | None = None, allow_recovery: bool = True) -> tuple[CodexSlot, CodexAuth] | None:
        catalog = getattr(self, "model_catalog", None)
        if catalog is None:
            return await self.choose_slot(affinity_key, excluded, excluded_accounts)
        chosen = await self.choose_slot(affinity_key, excluded, excluded_accounts, model_id, reasoning_effort)
        if chosen is not None:
            return chosen
        detail = await self.model_selection_diagnostics(excluded, excluded_accounts, model_id, reasoning_effort)
        recovery = diagnostics.get("recovery") if diagnostics is not None else None
        if allow_recovery and recovery is None and detail["operational_eligible"] > 0 and detail["model_eligible"] == 0:
            recovery = await catalog.recover()
            # Recovery only updates catalog knowledge. Selection repeats every original gate.
            detail = await self.model_selection_diagnostics(excluded, excluded_accounts, model_id, reasoning_effort)
            chosen = await self.choose_slot(affinity_key, excluded, excluded_accounts, model_id, reasoning_effort)
        elif detail["model_eligible"] > 0:
            # The catalog/auth state changed while the diagnostic scan yielded.
            chosen = await self.choose_slot(affinity_key, excluded, excluded_accounts, model_id, reasoning_effort)
        if recovery is not None:
            detail["recovery"] = recovery
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(detail)
        return chosen

    async def slot_availability(self) -> SlotAvailability:
        now = time.time()
        state = SlotAvailability(total=len(self.slots))
        for slot in self.slots:
            if not self.slot_routing_status(slot)["routingEnabled"]:
                state.disabled += 1
                continue
            if slot.rate_limited_until > now:
                if not self._invalidate_changed_auth_candidate(slot):
                    state.rate_limited += 1
                    if state.earliest_rate_limit_until is None or slot.rate_limited_until < state.earliest_rate_limit_until:
                        state.earliest_rate_limit_until = slot.rate_limited_until
                    continue
            if slot.transient_until > now:
                state.transient_5xx += 1
                if state.earliest_transient_until is None or slot.transient_until < state.earliest_transient_until:
                    state.earliest_transient_until = slot.transient_until
                continue
            auth = await self.load_auth(slot)
            if not auth:
                state.auth_unavailable += 1
                continue
            state.eligible += 1
        return state

    def _recovery_wait_ms(self) -> int:
        return parse_int(os.environ.get("SPARTAN_ROUTE_RECOVERY_WAIT_MS"), DEFAULT_RECOVERY_WAIT_MS, 0, 600_000)

    def _recovery_poll_ms(self) -> int:
        return parse_int(os.environ.get("SPARTAN_ROUTE_RECOVERY_POLL_MS"), DEFAULT_RECOVERY_POLL_MS, 250, 10_000)

    async def _wait_for_temporary_recovery(self, deadline: float) -> bool:
        event = self._availability_signal()
        poll_seconds = self._recovery_poll_ms() / 1000
        while time.time() < deadline:
            state = await self.slot_availability()
            if state.eligible > 0:
                return True
            recovery_at = state.earliest_recovery()
            if recovery_at is None or recovery_at > deadline:
                return False
            now = time.time()
            timeout = min(poll_seconds, max(0.0, recovery_at - now), max(0.0, deadline - now))
            if timeout <= 0:
                await asyncio.sleep(0)
                continue
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        return (await self.slot_availability()).eligible > 0

    def _rate_limit_error(self, state: SlotAvailability) -> dict[str, Any]:
        now = time.time()
        reset_at = state.earliest_rate_limit_until or now
        resets_in_seconds = max(1, int((reset_at - now) + 0.999))
        return {
            "message": "All Codex auth slots are currently rate-limited. Try again later.",
            "type": "upstream_error",
            "code": CODEX_RATE_LIMIT_UNAVAILABLE_CODE,
            "resets_at": int(reset_at),
            "resets_in_seconds": resets_in_seconds,
            "slot_diagnostics": state.diagnostics(),
        }

    def _temporary_unavailable_headers(self, error: dict[str, Any]) -> dict[str, str]:
        seconds = error.get("retry_after_seconds") or error.get("resets_in_seconds")
        try:
            value = max(1, int(seconds))
        except (TypeError, ValueError):
            return {}
        return {"Retry-After": str(value)}

    def mark_rate_limited(self, slot: CodexSlot | CodexAttempt, text: str) -> None:
        slot = outcome_slot(slot)
        if slot is None:
            return
        error = parse_codex_error(text)
        reset = epoch_millis(error.get("resets_at")) or int((time.time() + parse_float(error.get("resets_in_seconds"), 900)) * 1000)
        slot.rate_limited_until = reset / 1000
        self._invalidate_slot_leases(slot)
        self._notify_availability_changed()

    def _quarantine_auth_failure(self, slot: CodexSlot | CodexAttempt) -> None:
        """Keep a rejected auth candidate out of rotation until it changes."""
        slot = outcome_slot(slot)
        if slot is None:
            return
        failed_fingerprint = slot.auth_fingerprint
        slot.auth = None
        slot.auth_error_code = "reauth_required"
        slot.auth_unavailable_reason = "reauth_required"
        slot.auth_retry_at = 0
        if slot.path and failed_fingerprint:
            slot.failed_auth_fingerprint = failed_fingerprint
        self._invalidate_slot_leases(slot)
        self._usage_cache = None
        self._notify_availability_changed()

    async def _probe_rate_limit_recovery(self) -> bool:
        """Coalesce a bounded live usage check for a later incoming request."""
        active = getattr(self, "_usage_refresh_task", None)
        if active is not None and not active.done():
            try:
                await asyncio.shield(active)
                return (await self.slot_availability()).eligible > 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"[spartan-route] Codex recovery usage probe failed ({type(exc).__name__})",
                    file=sys.stderr,
                )
                return False

        now = time.monotonic()
        last_probe = float(getattr(self, "_last_recovery_probe_at", 0.0) or 0.0)
        if now - last_probe < CODEX_RECOVERY_PROBE_MIN_INTERVAL_SECONDS:
            return False

        cache = getattr(self, "_usage_cache", None)
        if isinstance(cache, dict) and cache.get("signature") == self._usage_cache_signature():
            try:
                completed_at = float(cache["expiresAt"]) - CODEX_USAGE_CACHE_TTL_SECONDS
            except (KeyError, TypeError, ValueError):
                completed_at = 0.0
            if time.time() - completed_at < CODEX_RECOVERY_PROBE_MIN_INTERVAL_SECONDS:
                self._last_recovery_probe_at = now
                return False

        try:
            await asyncio.shield(self._ensure_usage_refresh())
            return (await self.slot_availability()).eligible > 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[spartan-route] Codex recovery usage probe failed ({type(exc).__name__})",
                file=sys.stderr,
            )
            return False
        finally:
            self._last_recovery_probe_at = time.monotonic()

    def mark_transient(self, slot: CodexSlot | CodexAttempt) -> None:
        slot = outcome_slot(slot)
        if slot is None:
            return
        cooldown = parse_int(os.environ.get("CODEX_TRANSIENT_5XX_COOLDOWN_MS"), 120_000, 0, 900_000) / 1000
        if cooldown > 0:
            slot.transient_until = time.time() + cooldown
            self._invalidate_slot_leases(slot)
            self._notify_availability_changed()

    async def _unavailable_error(self) -> dict[str, Any]:
        state = await self.slot_availability()
        now = time.time()
        if state.eligible == 0 and state.transient_5xx:
            retry_at = state.earliest_transient_until or now
            return {
                "message": "All Codex auth slots are temporarily unavailable after upstream server errors. Try again shortly.",
                "type": "upstream_error",
                "code": CODEX_TRANSIENT_UNAVAILABLE_CODE,
                "retry_at": self._iso_from_epoch(retry_at),
                "retry_after_seconds": max(1, int((retry_at - now) + 0.999)),
                "slot_diagnostics": state.diagnostics(),
            }
        if state.eligible == 0 and state.rate_limited:
            return self._rate_limit_error(state)
        return {
            "message": "All Codex auth slots are temporarily unavailable.",
            "type": "upstream_error",
            "code": "codex_unavailable",
            "slot_diagnostics": state.diagnostics(),
        }

    async def _unavailable_result(self, selection_diagnostics: dict[str, Any] | None = None) -> CodexCallResult:
        error = unavailable_error(await self._unavailable_error())
        if (selection_diagnostics and selection_diagnostics.get("operational_eligible", 0) > 0
                and selection_diagnostics.get("model_eligible") == 0
                and error.get("slot_diagnostics", {}).get("eligible", 0) > 0):
            error = {"message": "No operational Codex slot currently offers the requested model and reasoning effort.",
                     "type": "upstream_error", "code": "codex_model_unavailable",
                     "slot_diagnostics": error["slot_diagnostics"],
                     "selection_diagnostics": selection_diagnostics}
        status = 429 if error["code"] == CODEX_RATE_LIMIT_UNAVAILABLE_CODE else 503
        return CodexCallResult(
            status=status,
            body={"error": error},
            headers=self._temporary_unavailable_headers(error),
            error=str(error["code"]),
            selection_diagnostics=selection_diagnostics or None,
        )

    async def execute_chat(
        self,
        request: dict[str, Any],
        model_id: str,
        wants_stream: bool,
        session_id: str | None,
        prompt_cache_key: str | None,
        request_id: str,
        reasoning_effort: str | None = None,
    ) -> CodexCallResult:
        model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id
        if reasoning_effort is None:
            reasoning = request.get("reasoning")
            reasoning_effort = request.get("reasoning_effort") or (reasoning.get("effort") if isinstance(reasoning, dict) else None)
        upstream_cache_key = resolve_prompt_cache_key(request, session_id, prompt_cache_key)
        affinity_key = resolve_affinity_key(request, session_id, prompt_cache_key)
        pi_request = CURRENT_REQUEST.get()
        if pi_request is not None:
            pi_request.affinity(affinity_key)
        body = build_codex_request_body(request, model_name, upstream_cache_key)
        slot_count = max(1, len(self.slots))
        initial_state = await self.slot_availability()
        if initial_state.eligible == 0 and initial_state.rate_limited:
            await self._probe_rate_limit_recovery()
        excluded_slots: set[int] = set()
        excluded_accounts: set[str] = set()
        deadline = time.time() + (self._recovery_wait_ms() / 1000)
        selection_diagnostics: dict[str, Any] = {}
        while True:
            for _attempt in range(slot_count):
                chosen = await self.choose_slot_with_catalog_recovery(
                    affinity_key, excluded_slots, excluded_accounts, model_id, reasoning_effort,
                    diagnostics=selection_diagnostics,
                )
                if not chosen:
                    break
                slot, auth = chosen
                attempt = CodexAttempt(slot, auth, slot.auth_fingerprint)
                slot.in_flight, slot.last_selected_at = slot.in_flight + 1, time.time()
                pi_attempt = pi_request.start("codex") if pi_request is not None else None
                try:
                    result = await self._single_chat_call(body, model_id, model_name, wants_stream, slot, auth)
                except BaseException:
                    slot.in_flight = max(0, slot.in_flight - 1)
                    if pi_attempt is not None:
                        await pi_attempt.finish(incomplete=True)
                    raise
                if result.stream is None:
                    slot.in_flight = max(0, slot.in_flight - 1)
                    if pi_attempt is not None:
                        await pi_attempt.finish(result, incomplete=bool(result.error) or not result.completed)
                else:
                    source = result.stream
                    source_cleanup = result.stream_cleanup
                    cleaned = False

                    async def cleanup_stream() -> None:
                        nonlocal cleaned
                        if cleaned:
                            return
                        cleaned = True
                        try:
                            await source.aclose()
                        finally:
                            try:
                                if source_cleanup is not None:
                                    await source_cleanup()
                            finally:
                                slot.in_flight = max(0, slot.in_flight - 1)
                                if pi_attempt is not None:
                                    await pi_attempt.finish(result, incomplete=bool(result.error) or not result.completed)

                    async def stream_with_inflight_guard() -> AsyncIterator[bytes]:
                        try:
                            while True:
                                try:
                                    chunk = await source.__anext__()
                                except StopAsyncIteration:
                                    break
                                yield chunk
                        finally:
                            await cleanup_stream()
                            if result.completed and not result.error and affinity_key:
                                self._remember_lease(affinity_key, attempt)

                    result.stream_cleanup = cleanup_stream
                    result.stream = stream_with_inflight_guard()
                result.selection_diagnostics = selection_diagnostics or None
                result.slot_index = slot.index
                result.account_key = auth.account_key
                if not result.usage_seen:
                    result.input_tokens = token_count_estimate(request)
                if not result.retry_allowed:
                    return result
                if result.status not in {401, 429, 500, 502}:
                    if result.status < 400 and result.stream is None and not result.error:
                        self._remember_lease(affinity_key, attempt)
                    return result
                text = result.body.decode("utf-8", errors="replace") if isinstance(result.body, bytes) else stable_json(result.body)
                if not should_retry(result.status, text):
                    return result
                excluded_slots.add(slot.index)
                if auth.account_key:
                    excluded_accounts.add(auth.account_key)
                if result.status == 401:
                    self._quarantine_auth_failure(attempt)
                elif result.status == 429:
                    self.mark_rate_limited(attempt, text)
                else:
                    self.mark_transient(attempt)

            state = await self.slot_availability()
            if state.eligible == 0 and state.rate_limited and not state.transient_5xx:
                return await self._unavailable_result(selection_diagnostics)
            recovery_at = state.earliest_recovery()
            if recovery_at is not None and recovery_at <= deadline and await self._wait_for_temporary_recovery(deadline):
                excluded_slots.clear()
                excluded_accounts.clear()
                continue
            return await self._unavailable_result(selection_diagnostics)

    async def _single_chat_call(
        self,
        body: dict[str, Any],
        model_id: str,
        model_name: str,
        wants_stream: bool,
        slot: CodexSlot,
        auth: CodexAuth,
    ) -> CodexCallResult:
        slot = CodexAttempt(slot, auth, slot.auth_fingerprint)
        headers = build_codex_responses_headers(body, auth)
        timeout_ms = codex_request_timeout_ms()
        try:
            upstream_started = time.monotonic()
            response = await self.open_inference(body, headers, auth)
            upstream_response_time_ms = max(0, int((time.monotonic() - upstream_started) * 1000))
        except asyncio.TimeoutError:
            upstream_response_time_ms = max(0, int((time.monotonic() - upstream_started) * 1000))
            return CodexCallResult(
                status=502,
                body={
                    "error": {
                        "message": "Codex request timed out while waiting for response headers",
                        "type": "server_error",
                        "code": "codex_error",
                    }
                },
                error="codex_error",
                upstream_response_time_ms=upstream_response_time_ms,
            )
        except Exception as exc:
            return CodexCallResult(status=502, body={"error": {"message": str(exc), "type": "upstream_error", "code": "codex_error"}}, error=str(exc))
        if response.status != 200:
            text = await response.text()
            response.release()
            return CodexCallResult(
                status=response.status,
                body=text.encode(),
                headers={"Content-Type": "application/json"},
                error=text[:300],
                upstream_response_time_ms=None if getattr(response, "is_websocket", False) else upstream_response_time_ms,
                retry_allowed=getattr(response, "retry_allowed", True),
                transport_details=getattr(response, "transport_details", None),
            )
        if wants_stream:
            result = CodexCallResult(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                upstream_response_time_ms=None if getattr(response, "is_websocket", False) else upstream_response_time_ms,
                retry_allowed=getattr(response, "retry_allowed", True),
                transport_details=getattr(response, "transport_details", None),
            )
            result.stream = self._codex_stream_to_chat(response, model_id, result, slot)
            async def cleanup_response() -> None:
                response.release()
            result.stream_cleanup = cleanup_response
            return result
        collected = await self._collect_codex_response(response, model_id, slot)
        collected.upstream_response_time_ms = None if getattr(response, "is_websocket", False) else upstream_response_time_ms
        collected.retry_allowed = collected.retry_allowed and getattr(response, "retry_allowed", True)
        collected.transport_details = getattr(response, "transport_details", None)
        return collected

    async def open_inference(self, body: dict[str, Any], headers: dict[str, str], auth: CodexAuth, scope: str | None = None) -> Any:
        transport = getattr(self, "transport", None)
        if transport is not None:
            return await transport.open(
                CODEX_RESPONSES_URL, body, headers, account=auth.account_key or "static",
                scope=scope or body.get("prompt_cache_key"), timeout=codex_request_timeout_ms() / 1000,
            )
        # Legacy injected test routers and callers retain their single-session seam.
        return await asyncio.wait_for(self.session.post(
            CODEX_RESPONSES_URL, json=body, headers=headers, timeout=ClientTimeout(total=None),
        ), codex_request_timeout_ms() / 1000)

    async def _collect_codex_response(self, response: Any, model: str, slot: CodexSlot) -> CodexCallResult:
        state = CodexEventState()
        try:
            async for event, data in parse_sse_response(response):
                if not data or data == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data)
                except Exception:
                    continue
                if event == "error" or parsed.get("error"):
                    state.terminal_error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
                    break
                self._apply_codex_event_state(event, parsed, state)
                if state.terminal_seen:
                    break
        except asyncio.TimeoutError as exc:
            return self._stream_transport_result(exc, slot, state)
        except TransportError as exc:
            error = self._websocket_stream_error(slot, exc)
            if exc.retry_allowed:
                error = exc.error
            prompt_tokens, cached, output_tokens = self._usage_values(state)
            return CodexCallResult(status=exc.status, body={"error": error}, error=exc.code, retry_allowed=exc.retry_allowed,
                input_tokens=prompt_tokens, cached_input_tokens=cached, output_tokens=output_tokens,
                usage_seen=state.usage_seen, usage_event_type=state.usage_event_type)
        except Exception as exc:
            return self._stream_transport_result(exc, slot, state)
        finally:
            response.release()
        return self._chat_completion_result(state, model)

    async def _codex_stream_to_chat(self, response: Any, model: str, result: CodexCallResult, slot: CodexSlot) -> AsyncIterator[bytes]:
        chat_id = f"chatcmpl_{sha(str(time.time()), 12)}"
        created = int(time.time())
        state = CodexEventState()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
        downstream_open = True

        async def emit(chunk: bytes) -> None:
            if downstream_open:
                await queue.put(chunk)

        async def produce() -> None:
            try:
                await emit(sse({
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }))
                async for event, data in parse_sse_response(response):
                    outputs, done = self._codex_chat_outputs_for_event(event, data, state, result, slot, chat_id, created, model)
                    for output in outputs:
                        await emit(output)
                    if done:
                        return
                result.input_tokens, result.cached_input_tokens, result.output_tokens = self._usage_values(state)
                result.usage_seen = state.usage_seen
                result.usage_event_type = state.usage_event_type
                error = self._chat_terminal_error(state)
                if error is not None:
                    result.error = str(error["code"])
                    self.mark_transient(slot)
                    await emit(sse({"error": error}, event="error"))
                    await emit(b"data: [DONE]\n\n")
                    return
                result.completed = True
                for delta in self._complete_tool_call_deltas(state):
                    await emit(sse({
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }))
                await emit(sse({
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if state.tool_calls else state.finish_reason}],
                    "usage": self._usage_payload(*self._usage_values(state)),
                }))
                await emit(b"data: [DONE]\n\n")
            except asyncio.TimeoutError as exc:
                error = self._stream_transport_error(exc)
                result.error = str(error["code"])
                self._mark_streaming_error(slot, error)
                await emit(sse({"error": error}, event="error"))
                await emit(b"data: [DONE]\n\n")
            except TransportError as exc:
                error = self._websocket_stream_error(slot, exc)
                result.error = self._error_code(error)
                result.completed = False
                await emit(sse({"error": error}, event="error"))
                await emit(b"data: [DONE]\n\n")
            except Exception as exc:
                error = self._stream_transport_error(exc)
                result.error = str(error["code"])
                self._mark_streaming_error(slot, error)
                await emit(sse({"error": error}, event="error"))
                await emit(b"data: [DONE]\n\n")
            finally:
                self._copy_usage_to_result(result, state)
                if state.terminal_seen and state.has_payload() and not result.error and not (state.finish_reason == "length" and state.tool_calls):
                    result.completed = True
                if state.terminal_error and not result.error:
                    result.error = self._error_code(state.terminal_error)
                response.release()
                if downstream_open:
                    await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            downstream_open = False
            while not queue.empty():
                queue.get_nowait()
            try:
                if not producer.done():
                    await asyncio.wait_for(asyncio.shield(producer), timeout=CODEX_USAGE_DRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            if producer.done():
                try:
                    producer.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

    def _codex_chat_outputs_for_event(
        self,
        event: str | None,
        data: str,
        state: CodexEventState,
        result: CodexCallResult,
        slot: CodexSlot,
        chat_id: str,
        created: int,
        model: str,
    ) -> tuple[list[bytes], bool]:
        if not data or data == "[DONE]":
            return [], False
        try:
            parsed = json.loads(data)
        except Exception:
            return [], False
        if event == "error" or parsed.get("error"):
            error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
            self._mark_streaming_error(slot, error)
            client_error = self._stream_client_error(error)
            result.error = self._error_code(client_error)
            return [sse({"error": client_error}, event="error"), b"data: [DONE]\n\n"], True
        chunks = self._apply_codex_event_state(event, parsed, state, stream=True)
        if state.terminal_error:
            self._copy_usage_to_result(result, state)
            self._mark_streaming_error(slot, state.terminal_error)
            client_error = self._stream_client_error(state.terminal_error)
            result.error = self._error_code(client_error)
            return [sse({"error": client_error}, event="error"), b"data: [DONE]\n\n"], True
        outputs = []
        for delta in chunks:
            if "tool_calls" in delta:
                continue
            outputs.append(sse({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }))
        return outputs, False

    def _actual_tool_names(self, state: CodexEventState) -> list[str]:
        return sorted({
            str(function.get("name"))
            for call in state.calls()
            if isinstance(call, dict)
            for function in [call.get("function")]
            if isinstance(function, dict) and function.get("name")
        })

    def _copy_usage_to_result(self, result: CodexCallResult, state: CodexEventState) -> None:
        result.input_tokens, result.cached_input_tokens, result.output_tokens = self._usage_values(state)
        result.usage_seen = state.usage_seen
        result.usage_event_type = state.usage_event_type
        result.actual_tool_call_count = len(state.calls())
        result.actual_tool_names = self._actual_tool_names(state)

    def _complete_tool_call_deltas(self, state: CodexEventState) -> list[dict[str, Any]]:
        deltas: list[dict[str, Any]] = []
        for index, call in enumerate(state.calls()):
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            deltas.append({
                "tool_calls": [{
                    "index": index,
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": function.get("name") or "",
                        "arguments": function.get("arguments") or "",
                    },
                }],
            })
        return deltas

    def _record_usage(self, state: CodexEventState, usage: Any, event: str | None) -> None:
        if not isinstance(usage, dict):
            return
        state.usage.update(usage)
        state.usage_seen |= any(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")
        )
        if event and not state.usage_event_type:
            state.usage_event_type = event

    def _capture_usage(self, event: str | None, parsed: dict[str, Any], state: CodexEventState) -> None:
        self._record_usage(state, parsed.get("usage"), event)
        response = parsed.get("response") if isinstance(parsed.get("response"), dict) else None
        if response is not None:
            self._record_usage(state, response.get("usage"), event)

    def _usage_values(self, state: CodexEventState) -> tuple[int, int, int]:
        usage = state.usage
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or usage.get("cached_tokens") or 0) if isinstance(details, dict) else 0
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return prompt_tokens, cached, output_tokens

    def _usage_payload(self, prompt_tokens: int, cached: int, output_tokens: int) -> dict[str, Any]:
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
            "prompt_tokens_details": {"cached_tokens": cached},
        }

    def _error_code(self, error: dict[str, Any], default: str = "codex_error") -> str:
        code = error.get("code")
        if isinstance(code, str) and code:
            return code
        error_type = error.get("type") or error.get("upstream_type")
        return str(error_type or default)

    def _empty_response_error(self) -> dict[str, Any]:
        return {
            "message": "Codex returned an empty successful response",
            "type": "upstream_error",
            "code": "codex_empty_response",
        }

    def _incomplete_tool_response_error(self) -> dict[str, Any]:
        return {
            "message": "Codex response ended without a valid successful completion",
            "type": "upstream_error",
            "code": "codex_incomplete_response",
        }

    def _stream_transport_error(self, exc: BaseException) -> dict[str, Any]:
        if isinstance(exc, asyncio.TimeoutError):
            return {
                "message": "Codex stream timed out while reading the upstream response",
                "type": "upstream_error",
                "code": CODEX_STREAM_TIMEOUT_CODE,
            }
        return {
            "message": "Codex stream failed while reading the upstream response",
            "type": "upstream_error",
            "code": CODEX_STREAM_ERROR_CODE,
        }

    def _websocket_stream_error(self, slot: CodexSlot, exc: TransportError) -> dict[str, Any]:
        error = dict(exc.error)
        if exc.rejected:
            if exc.status == 401:
                error["type"] = "auth_error"
            elif exc.status == 429:
                error["type"] = "rate_limit_error"
            self._mark_streaming_error(slot, error)
        return self._stream_client_error(error)

    def _stream_transport_result(self, exc: BaseException, slot: CodexSlot, state: CodexEventState) -> CodexCallResult:
        error = self._stream_transport_error(exc)
        self._mark_streaming_error(slot, error)
        prompt_tokens, cached, output_tokens = self._usage_values(state)
        return CodexCallResult(
            status=502,
            body={"error": error},
            input_tokens=prompt_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            error=str(error["code"]),
            usage_seen=state.usage_seen,
            usage_event_type=state.usage_event_type,
            actual_tool_call_count=len(state.calls()),
            actual_tool_names=self._actual_tool_names(state),
        )

    def _is_auth_error_payload(self, error: dict[str, Any]) -> bool:
        code = str(error.get("code") or "")
        error_type = str(error.get("type") or error.get("upstream_type") or "")
        return error_type == "auth_error" or code in {
            "invalid_api_key",
            "invalid_auth",
            "token_invalidated",
            "unauthorized",
        }

    def _is_usage_limit_payload(self, error: dict[str, Any]) -> bool:
        code = str(error.get("code") or "")
        error_type = str(error.get("type") or error.get("upstream_type") or "")
        return error_type in {"usage_limit_reached", "rate_limit_error"} or code in {
            "usage_limit_reached",
            "rate_limit_exceeded",
            "codex_429",
        }

    def _stream_client_error(self, error: dict[str, Any]) -> dict[str, Any]:
        if self._is_auth_error_payload(error) or self._is_usage_limit_payload(error):
            return {
                "message": "The Codex account pool is temporarily unavailable. Retry on a later request.",
                "type": "upstream_error",
                "code": "codex_unavailable",
            }
        return error

    def _codex_error_status(self, error: dict[str, Any]) -> int:
        if self._is_auth_error_payload(error):
            return 401
        if self._is_usage_limit_payload(error):
            return 429
        return 502

    def _error_result(self, error: dict[str, Any], state: CodexEventState | None = None) -> CodexCallResult:
        prompt_tokens = cached = output_tokens = 0
        usage_seen = False
        usage_event_type = None
        if state is not None:
            prompt_tokens, cached, output_tokens = self._usage_values(state)
            usage_seen = state.usage_seen
            usage_event_type = state.usage_event_type
        return CodexCallResult(
            status=self._codex_error_status(error),
            body={"error": error},
            input_tokens=prompt_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            error=self._error_code(error),
            actual_tool_call_count=len(state.calls()) if state is not None else None,
            actual_tool_names=self._actual_tool_names(state) if state is not None else None,
            usage_seen=usage_seen,
            usage_event_type=usage_event_type,
        )

    def _chat_terminal_error(self, state: CodexEventState) -> dict[str, Any] | None:
        if state.terminal_error:
            return state.terminal_error
        if not state.has_payload():
            return self._empty_response_error()
        if not state.terminal_seen or not valid_tool_arguments(state.calls()) or (state.finish_reason == "length" and state.tool_calls):
            return self._incomplete_tool_response_error()
        return None

    def _chat_completion_result(self, state: CodexEventState, model: str) -> CodexCallResult:
        error = self._chat_terminal_error(state)
        if error is not None:
            result = self._error_result(error, state)
            result.retry_allowed = not (state.has_payload() and not state.terminal_seen)
            return result
        prompt_tokens, cached, output_tokens = self._usage_values(state)
        text = state.text()
        calls = state.calls()
        message: dict[str, Any] = {"role": "assistant", "content": text if text else None}
        reasoning = state.reasoning()
        if reasoning:
            message["reasoning_content"] = reasoning
        if calls:
            message["tool_calls"] = calls
        body = {
            "id": f"chatcmpl_{sha(str(time.time()), 12)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else state.finish_reason}],
            "usage": self._usage_payload(prompt_tokens, cached, output_tokens),
        }
        return CodexCallResult(
            status=200,
            body=body,
            input_tokens=prompt_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            actual_tool_call_count=len(calls),
            actual_tool_names=self._actual_tool_names(state),
            completed=True,
            usage_seen=state.usage_seen,
            usage_event_type=state.usage_event_type,
        )

    def _reasoning_key(self, item_id: Any, kind: str, index: Any) -> str:
        return f"{item_id if isinstance(item_id, str) else '__no_item__'}:{kind}:{index if isinstance(index, int) else 0}"

    def _collect_reasoning_presence(self, state: CodexEventState, item_id: Any, stream: bool) -> list[dict[str, Any]]:
        key = item_id if isinstance(item_id, str) else "__reasoning_without_id__"
        if key in state.reasoning_presence_ids:
            return []
        state.reasoning_presence_ids.add(key)
        state.reasoning_parts.append(" ")
        if not state.text_parts:
            state.text_parts.append(INLINE_REASONING_PLACEHOLDER)
            return [{"reasoning_content": " "}, {"content": INLINE_REASONING_PLACEHOLDER}] if stream else []
        return [{"reasoning_content": " "}] if stream else []

    def _append_text_once(self, state: CodexEventState, item_id: Any, text: Any, stream: bool) -> list[dict[str, Any]]:
        if not isinstance(text, str) or not text:
            return []
        already_seen = isinstance(item_id, str) and item_id in state.text_item_ids
        if already_seen:
            return []
        if isinstance(item_id, str):
            state.text_item_ids.add(item_id)
        state.text_parts.append(text)
        return [{"content": text}] if stream else []

    def _append_reasoning_once(
        self,
        state: CodexEventState,
        item_id: Any,
        part_key: str,
        text: Any,
        stream: bool,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str) or not text:
            return []
        if part_key in state.reasoning_part_keys:
            return []
        state.reasoning_part_keys.add(part_key)
        if isinstance(item_id, str):
            state.reasoning_presence_ids.add(item_id)
        state.reasoning_parts.append(text)
        return [{"reasoning_content": text}] if stream else []

    def _ensure_tool_call(self, state: CodexEventState, item: dict[str, Any]) -> tuple[str, dict[str, Any], int] | None:
        call_id = item.get("call_id") or item.get("id")
        name = item.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            return None
        item_id = item.get("id")
        if isinstance(item_id, str):
            state.item_to_call[item_id] = call_id
        created = call_id not in state.tool_calls
        call = state.tool_calls.setdefault(call_id, {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": ""},
        })
        call["function"]["name"] = name
        index = list(state.tool_calls).index(call_id)
        if created:
            state.saw_output_item = True
        return call_id, call, index

    def _process_output_item(self, state: CodexEventState, item: Any, stream: bool) -> list[dict[str, Any]]:
        if not isinstance(item, dict):
            return []
        state.saw_output_item = True
        item_type = item.get("type")
        deltas: list[dict[str, Any]] = []
        if item_type == "message":
            item_id = item.get("id")
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    deltas.extend(self._append_text_once(state, item_id, content.get("text"), stream))
        elif item_type == "reasoning":
            item_id = item.get("id")
            texts: list[str] = []
            for part in item.get("summary") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "reasoning_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
            if texts:
                deltas.extend(self._append_reasoning_once(
                    state,
                    item_id,
                    self._reasoning_key(item_id, "output_item", 0),
                    "\n\n".join(texts),
                    stream,
                ))
            else:
                deltas.extend(self._collect_reasoning_presence(state, item_id, stream))
        elif item_type == "function_call":
            ensured = self._ensure_tool_call(state, item)
            if ensured:
                call_id, call, index = ensured
                args = item.get("arguments")
                if isinstance(args, str) and call_id not in state.tool_arg_delta_ids:
                    call["function"]["arguments"] = args
                if stream:
                    deltas.append({
                        "tool_calls": [{
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call["function"]["name"],
                                "arguments": call["function"]["arguments"],
                            },
                        }],
                    })
        return deltas

    def _apply_codex_event_state(
        self,
        event: str | None,
        parsed: dict[str, Any],
        state: CodexEventState,
        stream: bool = False,
    ) -> list[dict[str, Any]]:
        parsed_type = parsed.get("type")
        if not event and isinstance(parsed_type, str):
            event = parsed_type
        if event in TERMINAL_CODEX_EVENTS:
            state.terminal_seen = True
        self._capture_usage(event, parsed, state)
        deltas: list[dict[str, Any]] = []
        if event in {"response.output_text.delta", "response.output_text.annotation.added"}:
            delta = parsed.get("delta")
            if isinstance(delta, str):
                item_id = parsed.get("item_id")
                if isinstance(item_id, str):
                    state.text_item_ids.add(item_id)
                state.text_parts.append(delta)
                if stream:
                    deltas.append({"content": delta})
        elif event == "response.output_text.done":
            deltas.extend(self._append_text_once(state, parsed.get("item_id"), parsed.get("text"), stream))
        elif event == "response.reasoning_text.delta":
            item_id = parsed.get("item_id")
            key = self._reasoning_key(item_id, "content", parsed.get("content_index"))
            if isinstance(parsed.get("delta"), str):
                state.reasoning_part_keys.add(key)
                if isinstance(item_id, str):
                    state.reasoning_presence_ids.add(item_id)
                state.reasoning_parts.append(parsed["delta"])
                if stream:
                    deltas.append({"reasoning_content": parsed["delta"]})
        elif event == "response.reasoning_text.done":
            deltas.extend(self._append_reasoning_once(
                state,
                parsed.get("item_id"),
                self._reasoning_key(parsed.get("item_id"), "content", parsed.get("content_index")),
                parsed.get("text"),
                stream,
            ))
        elif event == "response.reasoning_summary_text.delta":
            item_id = parsed.get("item_id")
            key = self._reasoning_key(item_id, "summary", parsed.get("summary_index"))
            if isinstance(parsed.get("delta"), str):
                state.reasoning_part_keys.add(key)
                if isinstance(item_id, str):
                    state.reasoning_presence_ids.add(item_id)
                state.reasoning_parts.append(parsed["delta"])
                if stream:
                    deltas.append({"reasoning_content": parsed["delta"]})
        elif event == "response.reasoning_summary_text.done":
            deltas.extend(self._append_reasoning_once(
                state,
                parsed.get("item_id"),
                self._reasoning_key(parsed.get("item_id"), "summary", parsed.get("summary_index")),
                parsed.get("text"),
                stream,
            ))
        elif event == "response.output_item.added":
            item = parsed.get("item") if isinstance(parsed.get("item"), dict) else {}
            if item.get("type") == "function_call":
                ensured = self._ensure_tool_call(state, item)
                if ensured and stream:
                    call_id, call, index = ensured
                    deltas.append({
                        "tool_calls": [{
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": call["function"]["name"], "arguments": ""},
                        }],
                    })
        elif event == "response.output_item.done":
            deltas.extend(self._process_output_item(state, parsed.get("item"), stream))
        elif event == "response.function_call_arguments.delta":
            item_id = parsed.get("item_id")
            delta = parsed.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                call_id = state.item_to_call.get(item_id, item_id)
                call = state.tool_calls.setdefault(call_id, {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                state.tool_arg_delta_ids.add(call_id)
                state.tool_arg_delta_ids.add(item_id)
                call["function"]["arguments"] += delta
                if stream:
                    index = list(state.tool_calls).index(call_id)
                    deltas.append({"tool_calls": [{"index": index, "function": {"arguments": delta}}]})
        elif event == "response.function_call_arguments.done":
            item_id = parsed.get("item_id")
            name = parsed.get("name")
            args = parsed.get("arguments")
            if isinstance(item_id, str):
                call_id = state.item_to_call.get(item_id, item_id)
                call = state.tool_calls.setdefault(call_id, {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name if isinstance(name, str) else "", "arguments": ""},
                })
                if isinstance(name, str):
                    call["function"]["name"] = name
                if isinstance(args, str) and call_id not in state.tool_arg_delta_ids and item_id not in state.tool_arg_delta_ids:
                    call["function"]["arguments"] = args
                    if stream:
                        index = list(state.tool_calls).index(call_id)
                        deltas.append({
                            "tool_calls": [{
                                "index": index,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": call["function"]["name"], "arguments": args},
                            }],
                        })
        elif event in {"response.completed", "response.incomplete", "response.failed", "response.cancelled"}:
            response = parsed.get("response") if isinstance(parsed.get("response"), dict) else parsed
            state.completed = event == "response.completed"
            status = response.get("status")
            if event == "response.incomplete" or status == "incomplete":
                state.finish_reason = "length"
            else:
                state.finish_reason = "stop"
            for item in response.get("output") or []:
                deltas.extend(self._process_output_item(state, item, stream))
            if event in {"response.failed", "response.cancelled"} or status in {"failed", "cancelled"}:
                error = response.get("error") if isinstance(response.get("error"), dict) else {}
                state.terminal_error = {
                    "message": error.get("message") or f"Codex response {status or event.removeprefix('response.')}",
                    "type": error.get("type") or "upstream_error",
                    "code": error.get("code") or f"codex_{status or event.removeprefix('response.')}",
                }
        return deltas

    def _mark_streaming_error(self, slot: CodexSlot, error: dict[str, Any]) -> None:
        code = str(error.get("code") or "")
        error_type = str(error.get("type") or error.get("upstream_type") or "")
        text = stable_json({"error": error})
        if code in {CODEX_STREAM_TIMEOUT_CODE, CODEX_STREAM_ERROR_CODE}:
            return
        if self._is_auth_error_payload(error):
            self._quarantine_auth_failure(slot)
        elif self._is_usage_limit_payload(error):
            self.mark_rate_limited(slot, text)
        elif error_type in {"server_error", "upstream_error"} or code.startswith("codex_5") or code in {"codex_error", "server_error"}:
            self.mark_transient(slot)

    def _apply_codex_event(
        self,
        event: str | None,
        parsed: dict[str, Any],
        text_parts: list[str],
        reasoning_parts: list[str],
        tool_calls: dict[str, dict[str, Any]],
        usage: dict[str, Any],
        item_to_call: dict[str, str] | None = None,
        text_item_ids: set[str] | None = None,
        tool_arg_delta_ids: set[str] | None = None,
        stream: bool = False,
    ) -> list[dict[str, Any]]:
        state = CodexEventState(
            text_parts=text_parts,
            reasoning_parts=reasoning_parts,
            tool_calls=tool_calls,
            usage=usage,
            item_to_call=item_to_call if item_to_call is not None else {},
            text_item_ids=text_item_ids if text_item_ids is not None else set(),
            tool_arg_delta_ids=tool_arg_delta_ids if tool_arg_delta_ids is not None else set(),
        )
        return self._apply_codex_event_state(event, parsed, state, stream=stream)

    def _codex_image_user_text(self, request: dict[str, Any], image_count: int) -> str:
        prompt = str(request.get("prompt") or "").strip()
        output_format = str(request.get("output_format") or "png").strip() or "png"
        size = str(request.get("size") or "1024x1024").strip()
        quality = str(request.get("quality") or "medium").strip()
        if image_count:
            text = (
                "Use the image_generation tool to edit the attached reference image(s). "
                f"Request: {prompt}. Output format: {output_format}."
            )
        else:
            text = (
                "Use the image_generation tool to render the following. "
                f"Request: {prompt}. Output format: {output_format}."
            )
        if size and size != "auto":
            text += f" Size: {size}."
        if quality:
            text += f" Quality: {quality}."
        text += " Do not include explanatory text; produce only the image."
        return text

    def _build_codex_image_body(self, request: dict[str, Any]) -> dict[str, Any]:
        images = [image_url for image_url in (request.get("images") or []) if isinstance(image_url, str) and image_url]
        content: list[dict[str, str]] = [{"type": "input_image", "image_url": image_url} for image_url in images]
        content.append({"type": "input_text", "text": self._codex_image_user_text(request, len(images))})
        output_format = str(request.get("output_format") or "png").strip() or "png"
        size = str(request.get("size") or "1024x1024").strip()
        tool: dict[str, Any] = {"type": "image_generation", "output_format": output_format}
        if size and size != "auto":
            tool["size"] = size
        if images:
            tool["action"] = "edit"
        mask = request.get("mask")
        if isinstance(mask, str) and mask:
            tool["input_image_mask"] = {"image_url": mask}
        input_fidelity = request.get("input_fidelity")
        if input_fidelity in {"low", "high"}:
            tool["input_fidelity"] = input_fidelity
        return {
            "model": codex_image_host_model(),
            "stream": True,
            "instructions": "You are an image generation assistant.",
            "input": [{"type": "message", "role": "user", "content": content}],
            "tools": [tool],
            "tool_choice": "required" if images else "auto",
            "parallel_tool_calls": False,
            "store": False,
            "reasoning": {"effort": "low", "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
            "text": {"verbosity": "low"},
        }

    def _codex_image_error_payload(
        self,
        error_value: Any,
        fallback_message: str,
        fallback_code: str,
        fallback_type: str,
    ) -> dict[str, Any]:
        error_record = error_value if isinstance(error_value, dict) else {}
        code = error_record.get("code")
        error_type = error_record.get("type") or error_record.get("upstream_type")
        raw_message = error_value if isinstance(error_value, str) else error_record.get("message")
        message = raw_message if isinstance(raw_message, str) and raw_message.strip() else fallback_message
        error = {
            "message": message,
            "type": error_type if isinstance(error_type, str) and error_type.strip() else fallback_type,
            "code": code if isinstance(code, str) and code.strip() else fallback_code,
        }
        for key in ("resets_at", "resets_in_seconds"):
            if key in error_record:
                error[key] = error_record[key]
        return error

    def _codex_image_http_error(self, status: int, text: str) -> dict[str, Any]:
        parsed = parse_codex_error(text)
        upstream_message = parsed.get("message") if isinstance(parsed.get("message"), str) else text.strip()
        message = f"Codex API error ({status})"
        if upstream_message:
            message = f"{message}: {upstream_message}"
        return self._codex_image_error_payload(parsed, message, f"codex_{status}", "upstream_error")

    def _codex_image_exception_error(self, exc: BaseException) -> dict[str, Any]:
        if isinstance(exc, asyncio.TimeoutError):
            return {
                "message": "Codex stream timed out while reading the upstream image response",
                "type": "upstream_error",
                "code": CODEX_STREAM_TIMEOUT_CODE,
            }
        message = str(exc).strip() or type(exc).__name__
        return {
            "message": f"Codex image generation failed: {message}",
            "type": "server_error",
            "code": "codex_image_error",
        }

    def _extract_codex_image_result(self, value: Any) -> tuple[str | None, str | None]:
        image_b64 = None
        revised_prompt = None
        if isinstance(value, dict):
            if value.get("type") == "image_generation_call" and isinstance(value.get("result"), str):
                image_b64 = value["result"]
                if isinstance(value.get("revised_prompt"), str):
                    revised_prompt = value["revised_prompt"]
            for key in ("partial_image_b64", "partial_image", "result"):
                raw = value.get(key)
                if isinstance(raw, str) and len(raw) > 100:
                    image_b64 = raw
            for key in ("revised_prompt", "revisedPrompt"):
                raw_prompt = value.get(key)
                if isinstance(raw_prompt, str) and raw_prompt.strip():
                    revised_prompt = raw_prompt.strip()
            for child in value.values():
                child_image, child_prompt = self._extract_codex_image_result(child)
                image_b64 = child_image or image_b64
                revised_prompt = child_prompt or revised_prompt
        elif isinstance(value, list):
            for child in value:
                child_image, child_prompt = self._extract_codex_image_result(child)
                image_b64 = child_image or image_b64
                revised_prompt = child_prompt or revised_prompt
        return image_b64, revised_prompt

    async def _single_image_call(self, request: dict[str, Any], slot: CodexSlot, auth: CodexAuth) -> CodexCallResult:
        body = self._build_codex_image_body(request)
        headers = {
            "Authorization": f"Bearer {auth.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "User-Agent": "codex_cli_rs/0.130.0 (spartan-route image fallback)",
        }
        if auth.account_id:
            headers["chatgpt-account-id"] = auth.account_id
        timeout_ms = codex_request_timeout_ms()
        try:
            response = await asyncio.wait_for(self.session.post(
                CODEX_RESPONSES_URL,
                json=body,
                headers=headers,
                timeout=ClientTimeout(total=None),
            ), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            error = {
                "message": "Codex image request timed out while waiting for response headers",
                "type": "upstream_error",
                "code": "codex_image_header_timeout",
            }
            return CodexCallResult(status=502, body={"error": error}, error=str(error["code"]))
        except Exception as exc:
            error = self._codex_image_exception_error(exc)
            return CodexCallResult(status=502, body={"error": error}, error=str(error["code"]))
        try:
            if response.status != 200:
                error = self._codex_image_http_error(response.status, await response.text())
                return CodexCallResult(status=response.status, body={"error": error}, error=self._error_code(error))
            image_b64 = None
            revised_prompt = None
            async for event, data in parse_sse_response(response):
                if not data or data == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data)
                except Exception:
                    continue
                if event == "error" or (isinstance(parsed, dict) and parsed.get("error")):
                    error_value = parsed.get("error") if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else parsed
                    error = self._codex_image_error_payload(error_value, "Codex image generation failed", "codex_image_error", "upstream_error")
                    return CodexCallResult(status=self._codex_error_status(error), body={"error": error}, error=self._error_code(error))
                response_record = parsed.get("response") if isinstance(parsed, dict) and isinstance(parsed.get("response"), dict) else {}
                status = response_record.get("status") if isinstance(response_record, dict) else None
                if event in {"response.failed", "response.cancelled"} or status in {"failed", "cancelled", "incomplete"}:
                    upstream_error = response_record.get("error") if isinstance(response_record.get("error"), dict) else {}
                    fallback_status = status if isinstance(status, str) and status else event.removeprefix("response.")
                    error = self._codex_image_error_payload(upstream_error, f"Codex image response {fallback_status}", f"codex_image_{fallback_status}", "upstream_error")
                    return CodexCallResult(status=self._codex_error_status(error), body={"error": error}, error=self._error_code(error))
                found_image, found_prompt = self._extract_codex_image_result(parsed)
                image_b64 = found_image or image_b64
                revised_prompt = found_prompt or revised_prompt
            if not image_b64:
                error = {
                    "message": "Codex response contained no image_generation_call result",
                    "type": "server_error",
                    "code": "codex_image_empty",
                }
                return CodexCallResult(status=502, body={"error": error}, error=str(error["code"]))
            requested_size = requested_image_size(request.get("size") or "1024x1024")
            actual_size = image_b64_dimensions(image_b64)
            item = {"b64_json": image_b64, **({"revised_prompt": revised_prompt} if revised_prompt else {})}
            if requested_size and actual_size and requested_size != actual_size:
                item["actual_size"] = f"{actual_size[0]}x{actual_size[1]}"
            return CodexCallResult(status=200, body={"created": int(time.time()), "data": [item]}, completed=True)
        except Exception as exc:
            error = self._codex_image_exception_error(exc)
            return CodexCallResult(status=502, body={"error": error}, error=str(error["code"]))
        finally:
            response.release()

    async def execute_image(self, request: dict[str, Any]) -> CodexCallResult:
        slot_count = max(1, len(self.slots))
        initial_state = await self.slot_availability()
        if initial_state.eligible == 0 and initial_state.rate_limited:
            await self._probe_rate_limit_recovery()
        excluded_slots: set[int] = set()
        excluded_accounts: set[str] = set()
        deadline = time.time() + (self._recovery_wait_ms() / 1000)
        while True:
            for _attempt in range(slot_count):
                chosen = await self.choose_slot(None, excluded_slots, excluded_accounts)
                if not chosen:
                    break
                slot, auth = chosen
                attempt = CodexAttempt(slot, auth, slot.auth_fingerprint)
                slot.in_flight += 1
                slot.last_selected_at = time.time()
                try:
                    result = await self._single_image_call(request, slot, auth)
                finally:
                    slot.in_flight = max(0, slot.in_flight - 1)
                result.slot_index = slot.index
                result.account_key = auth.account_key
                result.input_tokens = token_count_estimate(request)
                if result.status not in {401, 429, 500, 502}:
                    return result
                text = result.body.decode("utf-8", errors="replace") if isinstance(result.body, bytes) else stable_json(result.body)
                if not should_retry(result.status, text):
                    return result
                excluded_slots.add(slot.index)
                if auth.account_key:
                    excluded_accounts.add(auth.account_key)
                if result.status == 401:
                    self._quarantine_auth_failure(attempt)
                elif result.status == 429:
                    self.mark_rate_limited(attempt, text)
                else:
                    self.mark_transient(attempt)

            state = await self.slot_availability()
            if state.eligible == 0 and state.rate_limited and not state.transient_5xx:
                return await self._unavailable_result()
            recovery_at = state.earliest_recovery()
            if recovery_at is not None and recovery_at <= deadline and await self._wait_for_temporary_recovery(deadline):
                excluded_slots.clear()
                excluded_accounts.clear()
                continue
            return await self._unavailable_result()

    def _usage_cache_signature(self) -> tuple[Any, ...]:
        return tuple(
            (
                slot.index,
                self.slot_path_label(slot),
                slot.account_key(),
                slot.auth_unavailable_reason,
                int(slot.rate_limited_until),
                int(slot.transient_until),
                self.slot_routing_status(slot)["routingEnabled"],
            )
            for slot in self.slots
        )

    async def usage_snapshot(self, force: bool = False, slot_indexes: set[int] | None = None) -> dict[str, Any]:
        if slot_indexes is not None:
            # Finish any older controller before fetching a newer targeted reading.
            # Shield its shared task from cancellation of this request.
            task = getattr(self, "_usage_refresh_task", None)
            if task is not None and not task.done():
                await asyncio.shield(task)
            async with self._usage_operation_guard():
                return await self._targeted_usage_snapshot(slot_indexes)

        task = getattr(self, "_usage_refresh_task", None)
        if task is not None and not task.done():
            if force:
                return copy.deepcopy(await asyncio.shield(task))
            snapshot = getattr(self, "_usage_progress_snapshot", None)
            if snapshot is not None:
                return copy.deepcopy(snapshot)
        if task is not None and task.done() and not force:
            snapshot = getattr(self, "_usage_progress_snapshot", None)
            progress = snapshot.get("refreshProgress") if isinstance(snapshot, dict) else None
            if isinstance(progress, dict) and progress.get("status") == "failed":
                return copy.deepcopy(snapshot)

        signature = self._usage_cache_signature()
        if not force and getattr(self, "_usage_cache", None):
            if self._usage_cache["expiresAt"] > time.time() and self._usage_cache["signature"] == signature:
                cached_payload = copy.deepcopy(self._usage_cache["payload"])
                cached_payload.update({
                    "cached": True,
                    "source": "memory",
                    "refreshing": False,
                    "cacheExpiresAt": self._iso_from_epoch(self._usage_cache["expiresAt"]),
                })
                return cached_payload

        refresh = self._ensure_usage_refresh()
        if force:
            return copy.deepcopy(await asyncio.shield(refresh))
        return copy.deepcopy(self._usage_progress_snapshot or self._persisted_usage_snapshot())

    def _usage_operation_guard(self) -> asyncio.Lock:
        lock = getattr(self, "_usage_operation_lock", None)
        if lock is None:
            lock = self._usage_operation_lock = asyncio.Lock()
        return lock

    async def _targeted_usage_snapshot(self, slot_indexes: set[int]) -> dict[str, Any]:
        generation = int(getattr(self, "_usage_generation", 0))
        payload = await self._fetch_usage_snapshot(slot_indexes)
        discovery_semaphore = asyncio.Semaphore(4)

        async def discover_identity(slot: CodexSlot) -> None:
            async with discovery_semaphore:
                await self.load_auth(slot)

        await asyncio.gather(*(
            discover_identity(slot)
            for slot in self.slots
            if not slot.account_key()
        ))
        persisted = self._persisted_usage_snapshot()
        accounts_by_key = {
            str(account.get("accountKey")): copy.deepcopy(account)
            for account in persisted.get("accounts", [])
            if account.get("accountKey")
        }
        for live in payload.get("accounts", []):
            account_key = str(live.get("accountKey") or "")
            if not account_key:
                continue
            previous = accounts_by_key.get(account_key, {})
            merged = {**previous, **copy.deepcopy(live)}
            merged["slotIndexes"] = sorted({
                int(index)
                for index in [
                    *(previous.get("slotIndexes") or []),
                    *(live.get("slotIndexes") or []),
                ]
            })
            merged["slotPaths"] = list(dict.fromkeys([
                *(previous.get("slotPaths") or []),
                *(live.get("slotPaths") or []),
            ]))
            accounts_by_key[account_key] = merged
        payload["accounts"] = sorted(
            accounts_by_key.values(),
            key=lambda row: min(row.get("slotIndexes") or [9999]),
        )
        telemetry_errors = []
        for error in [
            *(persisted.get("telemetryErrors") or []),
            *(payload.get("telemetryErrors") or []),
        ]:
            if error not in telemetry_errors:
                telemetry_errors.append(error)
        if telemetry_errors:
            payload["telemetryErrors"] = telemetry_errors
        payload["partial"] = bool(
            persisted.get("partial")
            or payload.get("slotErrors")
            or payload.get("resetCreditErrors")
            or telemetry_errors
        )
        has_persisted = any(
            account.get("source") == "persisted"
            for account in payload["accounts"]
        )
        payload.update({
            "cached": False,
            "source": "mixed" if has_persisted else "live",
            "refreshing": False,
            "observedAt": now_iso(),
        })
        expires_at = time.time() + CODEX_USAGE_CACHE_TTL_SECONDS
        payload["cacheExpiresAt"] = self._iso_from_epoch(expires_at)
        if generation == int(getattr(self, "_usage_generation", 0)):
            self._usage_cache = {
                "expiresAt": expires_at,
                "signature": self._usage_cache_signature(),
                "payload": copy.deepcopy(payload),
            }
            self._publish_usage_snapshot(generation, payload)
        return payload

    def trigger_usage_refresh(self) -> dict[str, Any]:
        self._ensure_usage_refresh()
        return copy.deepcopy(self._usage_progress_snapshot or self._persisted_usage_snapshot())

    def _publish_usage_snapshot(self, generation: int, payload: dict[str, Any]) -> bool:
        if generation != int(getattr(self, "_usage_generation", 0)):
            return False
        self._usage_progress_snapshot = copy.deepcopy(payload)
        return True

    def _ensure_usage_refresh(self) -> asyncio.Task[dict[str, Any]]:
        task = getattr(self, "_usage_refresh_task", None)
        if task is None or task.done():
            generation = int(getattr(self, "_usage_generation", 0)) + 1
            self._usage_generation = generation
            payload = self._persisted_usage_snapshot()
            payload.update({
                "cached": True,
                "source": "persisted",
                "refreshing": True,
                "refreshProgress": {
                    "status": "discovering",
                    "completedAccounts": 0,
                    "totalAccounts": None,
                    "pendingSlotIndexes": sorted(slot.index for slot in self.slots),
                },
            })
            self._publish_usage_snapshot(generation, payload)
            task = asyncio.create_task(self._refresh_usage_cache(generation))
            self._usage_refresh_task = task
        return task

    async def _refresh_usage_cache(self, generation: int) -> dict[str, Any]:
        async with self._usage_operation_guard():
            return await self._refresh_usage_cache_locked(generation)

    async def _refresh_usage_cache_locked(self, generation: int) -> dict[str, Any]:
        payload = copy.deepcopy(self._usage_progress_snapshot or self._persisted_usage_snapshot())
        total_accounts: int | None = None
        completed_accounts = 0
        cancelled = False
        failed = False
        discovery_tasks: list[asyncio.Task[Any]] = []
        account_tasks: list[asyncio.Task[Any]] = []
        try:
            discovery_semaphore = asyncio.Semaphore(4)

            async def discover(slot: CodexSlot) -> tuple[CodexSlot, CodexAuth | None]:
                async with discovery_semaphore:
                    return slot, await self.load_auth(slot)

            discovered: list[tuple[CodexSlot, CodexAuth | None]] = []
            discovery_slot_errors: list[dict[str, Any]] = []
            pending_discovery = {slot.index for slot in self.slots}
            discovery_tasks = [asyncio.create_task(discover(slot)) for slot in self.slots]
            for future in asyncio.as_completed(discovery_tasks):
                slot, auth = await future
                discovered.append((slot, auth))
                pending_discovery.discard(slot.index)
                if not auth or not auth.account_key:
                    discovery_slot_errors.append({
                        "slotIndex": slot.index,
                        "source": "none",
                        "message": "authentication unavailable",
                    })
                discovery_payload = copy.deepcopy(payload)
                discovery_payload["slotErrors"] = copy.deepcopy(discovery_slot_errors)
                discovery_payload["partial"] = bool(
                    discovery_slot_errors or discovery_payload.get("partial")
                )
                discovery_payload["refreshProgress"] = {
                    "status": "discovering",
                    "completedAccounts": 0,
                    "totalAccounts": None,
                    "pendingSlotIndexes": sorted(pending_discovery),
                }
                self._publish_usage_snapshot(generation, discovery_payload)

            groups: dict[str, dict[str, Any]] = {}
            active_slots: dict[str, list[CodexSlot]] = {}
            slot_errors = discovery_slot_errors
            for slot, auth in sorted(discovered, key=lambda item: item[0].index):
                account_key = auth.account_key if auth else None
                if not auth or not account_key:
                    continue
                groups.setdefault(account_key, {"auth": auth, "slots": []})["slots"].append(slot)
                active_slots.setdefault(account_key, []).append(slot)

            payload = self._persisted_usage_snapshot(active_slots)
            payload["slotErrors"] = slot_errors
            payload["partial"] = bool(slot_errors or payload.get("partial"))
            total_accounts = len(groups)
            pending_indexes = sorted(
                slot.index for group in groups.values() for slot in group["slots"]
            )
            payload.update({
                "cached": True,
                "source": "persisted",
                "refreshing": total_accounts > 0,
                "refreshProgress": {
                    "status": "running" if total_accounts else "complete",
                    "completedAccounts": 0,
                    "totalAccounts": total_accounts,
                    "pendingSlotIndexes": pending_indexes,
                },
            })
            self._publish_usage_snapshot(generation, payload)

            persisted_reset: dict[str, dict[str, Any]] = {}
            if total_accounts:
                try:
                    persisted_reset = self.telemetry.reset_credit_snapshots()
                except Exception as exc:
                    self.telemetry.record_health_failure("read", "reset_credit_snapshots")
                    print(f"[spartan-route] telemetry reset-credit read failed ({type(exc).__name__})", file=sys.stderr)
                    telemetry_error = {
                        "code": "telemetry_read_failed",
                        "operation": "reset_credit_snapshots",
                    }
                    if telemetry_error not in payload.setdefault("telemetryErrors", []):
                        payload["telemetryErrors"].append(telemetry_error)
                    payload["partial"] = True
                else:
                    self.telemetry.record_health_success("read", "reset_credit_snapshots")

            account_semaphore = asyncio.Semaphore(4)

            async def fetch_group(account_key: str, group: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                async with account_semaphore:
                    result = await self._fetch_usage_account(
                        account_key,
                        group["auth"],
                        group["slots"],
                        persisted_reset.get(account_key),
                        True,
                    )
                    return account_key, result

            account_tasks = [
                asyncio.create_task(fetch_group(account_key, group))
                for account_key, group in groups.items()
            ]
            for future in asyncio.as_completed(account_tasks):
                account_key, item = await future
                persistence_errors: list[dict[str, str]] = []
                operations = (
                    (
                        "usage_snapshots",
                        item["usageRecords"],
                        lambda records: self.telemetry.upsert_usage_snapshots(
                            records,
                            current_account_keys=set(groups),
                        ),
                    ),
                    (
                        "reset_credit_snapshots",
                        [item["resetRecord"]] if item.get("resetRecord") else [],
                        self.telemetry.upsert_reset_credits,
                    ),
                )
                for operation, records, persist in operations:
                    try:
                        persist(records)
                    except Exception as exc:
                        self.telemetry.record_health_failure("write", operation)
                        print(f"[spartan-route] telemetry {operation} write failed ({type(exc).__name__})", file=sys.stderr)
                        persistence_errors.append({
                            "code": "telemetry_persistence_failed",
                            "operation": operation,
                        })
                    else:
                        self.telemetry.record_health_success("write", operation)

                accounts = [
                    account for account in payload.get("accounts", [])
                    if account.get("accountKey") != account_key
                ]
                accounts.append(item["row"])
                accounts.sort(key=lambda row: min(row.get("slotIndexes") or [9999]))
                payload["accounts"] = accounts
                if item.get("usageError"):
                    payload.setdefault("slotErrors", []).append(item["usageError"])
                if item.get("resetError"):
                    payload.setdefault("resetCreditErrors", []).append(item["resetError"])
                for error in persistence_errors:
                    errors = payload.setdefault("telemetryErrors", [])
                    if error not in errors:
                        errors.append(error)

                completed_accounts += 1
                finished = {slot.index for slot in groups[account_key]["slots"]}
                pending_indexes = [index for index in pending_indexes if index not in finished]
                payload["partial"] = bool(
                    payload.get("slotErrors")
                    or payload.get("resetCreditErrors")
                    or payload.get("telemetryErrors")
                )
                payload.update({
                    "cached": False,
                    "source": "live" if completed_accounts == total_accounts else "mixed",
                    "refreshing": completed_accounts < total_accounts,
                    "observedAt": now_iso(),
                    "refreshProgress": {
                        "status": "complete" if completed_accounts == total_accounts else "running",
                        "completedAccounts": completed_accounts,
                        "totalAccounts": total_accounts,
                        "pendingSlotIndexes": pending_indexes,
                    },
                })
                self._publish_usage_snapshot(generation, payload)
        except asyncio.CancelledError:
            cancelled = True
            owned_tasks = [*discovery_tasks, *account_tasks]
            for owned_task in owned_tasks:
                if not owned_task.done():
                    owned_task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
        except Exception as exc:
            failed = True
            owned_tasks = [*discovery_tasks, *account_tasks]
            for owned_task in owned_tasks:
                if not owned_task.done():
                    owned_task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
            print(f"[spartan-route] Codex usage refresh failed ({type(exc).__name__})", file=sys.stderr)
        finally:
            if cancelled or failed:
                payload.update({
                    "cached": completed_accounts == 0,
                    "source": "persisted" if completed_accounts == 0 else "mixed",
                    "refreshing": False,
                    "partial": True,
                    "refreshProgress": {
                        "status": "failed",
                        "completedAccounts": completed_accounts,
                        "totalAccounts": total_accounts,
                        "pendingSlotIndexes": [],
                    },
                    "refreshError": {
                        "code": "usage_refresh_cancelled" if cancelled else "usage_refresh_failed",
                    },
                })
                self._publish_usage_snapshot(generation, payload)
            elif total_accounts is not None:
                payload["refreshing"] = False
                payload["refreshProgress"] = {
                    "status": "complete",
                    "completedAccounts": completed_accounts,
                    "totalAccounts": total_accounts,
                    "pendingSlotIndexes": [],
                }
                if total_accounts == 0:
                    payload.update({"cached": True, "source": "persisted"})
                self._publish_usage_snapshot(generation, payload)

            if generation == int(getattr(self, "_usage_generation", 0)) and not cancelled and not failed:
                expires_at = time.time() + CODEX_USAGE_CACHE_TTL_SECONDS
                payload["cacheExpiresAt"] = self._iso_from_epoch(expires_at)
                self._usage_cache = {
                    "expiresAt": expires_at,
                    "signature": self._usage_cache_signature(),
                    "payload": copy.deepcopy(payload),
                }
                self._publish_usage_snapshot(generation, payload)
        return copy.deepcopy(payload)

    def _persisted_usage_snapshot(
        self,
        active_slots: dict[str, list[CodexSlot]] | None = None,
    ) -> dict[str, Any]:
        telemetry_errors: list[dict[str, str]] = []
        try:
            usage_rows = self.telemetry.usage_snapshots()
        except Exception as exc:
            self.telemetry.record_health_failure("read", "usage_snapshots")
            print(f"[spartan-route] telemetry usage snapshot read failed ({type(exc).__name__})", file=sys.stderr)
            usage_rows = []
            telemetry_errors.append({"code": "telemetry_read_failed", "operation": "usage_snapshots"})
        else:
            self.telemetry.record_health_success("read", "usage_snapshots")
        try:
            reset_rows = self.telemetry.reset_credit_snapshots()
        except Exception as exc:
            self.telemetry.record_health_failure("read", "reset_credit_snapshots")
            print(f"[spartan-route] telemetry reset-credit read failed ({type(exc).__name__})", file=sys.stderr)
            reset_rows = {}
            telemetry_errors.append({"code": "telemetry_read_failed", "operation": "reset_credit_snapshots"})
        else:
            self.telemetry.record_health_success("read", "reset_credit_snapshots")

        if active_slots is None:
            active_slots = {}
            for slot in self.slots:
                account_key = slot.account_key()
                if account_key:
                    active_slots.setdefault(account_key, []).append(slot)

        accounts: dict[str, dict[str, Any]] = {}
        observed_at: str | None = None
        for record in usage_rows:
            account_key = str(record.get("account_key") or "")
            current_slots = active_slots.get(account_key)
            if not account_key or not current_slots:
                continue
            row = accounts.setdefault(account_key, {
                "accountKey": account_key,
                "slotIndexes": sorted(slot.index for slot in current_slots),
                "slotPaths": list(dict.fromkeys(
                    path for path in (self.slot_path_label(slot) for slot in current_slots) if path
                )),
                "source": "persisted",
                "stale": True,
                "fiveHour": None,
                "weekly": None,
                "resetCredits": reset_rows.get(account_key),
            })
            updated_at = str(record.get("updated_at") or "")
            if updated_at and (observed_at is None or updated_at > observed_at):
                observed_at = updated_at
            window_name = str(record.get("window") or "")
            if window_name not in {"fiveHour", "weekly"}:
                continue
            used = max(0.0, min(100.0, parse_float(record.get("used_percent"), 0.0)))
            row[window_name] = {
                "window": window_name,
                "usedPercent": used,
                "remainingPercent": 100.0 - used,
                "resetAt": record.get("reset_at"),
                "windowMinutes": int(record.get("window_minutes") or 0),
                "updatedAt": updated_at or None,
            }
        result: dict[str, Any] = {
            "accounts": sorted(accounts.values(), key=lambda row: min(row["slotIndexes"] or [9999])),
            "slotErrors": [],
            "resetCreditErrors": [],
            "partial": bool(telemetry_errors),
            "observedAt": observed_at,
        }
        if telemetry_errors:
            result["telemetryErrors"] = telemetry_errors
        return result

    async def _fetch_usage_snapshot(self, slot_indexes: set[int] | None = None) -> dict[str, Any]:
        generation = int(getattr(self, "_usage_generation", 0))
        telemetry_errors: list[dict[str, str]] = []
        try:
            persisted_reset = self.telemetry.reset_credit_snapshots()
        except Exception as exc:
            self.telemetry.record_health_failure("read", "reset_credit_snapshots")
            print(f"[spartan-route] telemetry reset-credit read failed ({type(exc).__name__})", file=sys.stderr)
            persisted_reset = {}
            telemetry_errors.append({"code": "telemetry_read_failed", "operation": "reset_credit_snapshots"})
        else:
            self.telemetry.record_health_success("read", "reset_credit_snapshots")

        groups: dict[str, dict[str, Any]] = {}
        slot_errors: list[dict[str, Any]] = []
        for slot in self.slots:
            if slot_indexes is not None and slot.index not in slot_indexes:
                continue
            auth = await self.load_auth(slot)
            account_key = auth.account_key if auth else slot.account_key()
            if not auth or not account_key:
                slot_errors.append({
                    "slotIndex": slot.index,
                    "source": "none",
                    "message": slot.auth_unavailable_reason or "auth unavailable",
                })
                continue
            group = groups.setdefault(account_key, {"auth": auth, "slots": []})
            group["slots"].append(slot)

        semaphore = asyncio.Semaphore(4)

        async def fetch_group(account_key: str, group: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._fetch_usage_account(
                    account_key,
                    group["auth"],
                    group["slots"],
                    persisted_reset.get(account_key),
                    slot_indexes is not None,
                )

        fetched = await asyncio.gather(
            *(fetch_group(account_key, group) for account_key, group in groups.items())
        )
        accounts = [item["row"] for item in fetched]
        reset_errors = [item["resetError"] for item in fetched if item.get("resetError")]
        slot_errors.extend(item["usageError"] for item in fetched if item.get("usageError"))
        usage_records = [record for item in fetched for record in item["usageRecords"]]
        reset_records = [item["resetRecord"] for item in fetched if item.get("resetRecord")]

        # reset_state can retire this targeted operation while its network
        # fetch is pending. Validate before either durable write, not only before
        # publishing the final memory cache. There are no awaits in this batch.
        if generation == int(getattr(self, "_usage_generation", 0)):
            for operation, persist in (
                (
                    "usage_snapshots",
                    lambda: self.telemetry.upsert_usage_snapshots(
                        usage_records,
                        current_account_keys=set(groups),
                        full_pool_refresh=slot_indexes is None,
                    ),
                ),
                ("reset_credit_snapshots", lambda: self.telemetry.upsert_reset_credits(reset_records)),
            ):
                try:
                    persist()
                except Exception as exc:
                    self.telemetry.record_health_failure("write", operation)
                    print(f"[spartan-route] telemetry {operation} write failed ({type(exc).__name__})", file=sys.stderr)
                    telemetry_errors.append({"code": "telemetry_persistence_failed", "operation": operation})
                else:
                    self.telemetry.record_health_success("write", operation)
        result = {
            "accounts": accounts,
            "slotErrors": slot_errors,
            "resetCreditErrors": reset_errors,
            "partial": bool(slot_errors or reset_errors or telemetry_errors),
        }
        if telemetry_errors:
            result["telemetryErrors"] = telemetry_errors
        return result

    async def _fetch_usage_account(
        self,
        account_key: str,
        auth: CodexAuth,
        slots: list[CodexSlot],
        persisted_reset: dict[str, Any] | None,
        clear_recovered_limit: bool,
    ) -> dict[str, Any]:
        generation = int(getattr(self, "_usage_generation", 0))
        slot = slots[0]
        slot_paths = [path for path in (self.slot_path_label(item) for item in slots) if path]
        row = {
            "accountKey": account_key,
            "slotIndexes": [item.index for item in slots],
            "slotPaths": list(dict.fromkeys(slot_paths)),
            "source": "live",
            "stale": False,
            "fiveHour": None,
            "weekly": None,
            "resetCredits": persisted_reset,
        }
        headers = {"Authorization": f"Bearer {auth.access_token}"}
        if auth.account_id:
            headers["chatgpt-account-id"] = auth.account_id

        async def fetch_usage() -> Any:
            async with getattr(self, "control_session", self.session).get(CODEX_USAGE_URL, headers=headers, timeout=ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise RuntimeError(f"Codex usage HTTP {response.status}")
                return await response.json()

        async def fetch_reset() -> Any:
            reset_headers = {**headers, "OpenAI-Beta": "codex-1", "originator": "Codex Desktop"}
            async with getattr(self, "control_session", self.session).get(CODEX_RESET_CREDITS_URL, headers=reset_headers, timeout=ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise RuntimeError(f"Codex reset credits HTTP {response.status}")
                return await response.json()

        usage_result, reset_result = await asyncio.gather(fetch_usage(), fetch_reset(), return_exceptions=True)
        usage_records: list[dict[str, Any]] = []
        usage_error: dict[str, Any] | None = None
        reset_error: dict[str, Any] | None = None
        reset_record: dict[str, Any] | None = None

        if isinstance(usage_result, BaseException):
            usage_error = {
                "slotIndex": slot.index,
                "source": "none",
                "message": "Codex usage check failed",
            }
        elif not isinstance(usage_result, dict):
            usage_error = {"slotIndex": slot.index, "source": "none", "message": "Codex usage payload was not an object"}
        else:
            invalid_window = False
            for window_name, (source, seconds) in normalize_usage_windows(usage_result).items():
                validated = valid_live_usage_window(source)
                if validated is None:
                    invalid_window = True
                    continue
                used, reset_at = validated
                window = {
                    "window": window_name,
                    "usedPercent": used,
                    "remainingPercent": 100.0 - used,
                    "resetAt": reset_at,
                    "windowMinutes": seconds // 60,
                    "updatedAt": now_iso(),
                }
                row[window_name] = window
                usage_records.append({
                    "account_key": account_key,
                    "slot_index": slot.index,
                    "window": window_name,
                    "used_percent": used,
                    "reset_at": reset_at,
                    "window_minutes": seconds // 60,
                })
            if row["weekly"] is None:
                usage_records = []
                usage_error = {
                    "slotIndex": slot.index,
                    "source": "none",
                    "message": "Codex usage payload did not contain a recognized weekly window",
                }
            elif invalid_window:
                usage_error = {
                    "slotIndex": slot.index,
                    "source": "none",
                    "message": "Codex usage payload contained an invalid reported window",
                }
            elif (
                clear_recovered_limit
                and generation == int(getattr(self, "_usage_generation", 0))
                and live_usage_allows_recovery(usage_result)
            ):
                for item in slots:
                    if item.rate_limited_until > 0:
                        item.rate_limited_until = 0
                        self._usage_cache = None
                        self._notify_availability_changed()

        if isinstance(reset_result, BaseException):
            reset_error = {
                "slotIndex": slot.index,
                "source": "persisted" if row["resetCredits"] else "none",
                "message": "Codex reset-credit check failed",
            }
        else:
            try:
                reset = normalize_reset_credits(reset_result, account_key, slot.index)
            except Exception as exc:
                reset_error = {
                    "slotIndex": slot.index,
                    "source": "persisted" if row["resetCredits"] else "none",
                    "message": "Codex reset-credit payload was invalid",
                }
            else:
                row["resetCredits"] = reset["api"]
                reset_record = reset["db"]

        return {
            "row": row,
            "usageError": usage_error,
            "resetError": reset_error,
            "usageRecords": usage_records,
            "resetRecord": reset_record,
        }


async def parse_sse_response(response: Any) -> AsyncIterator[tuple[str | None, str]]:
    direct = getattr(response, "iter_events", None)
    if direct is not None:
        async for event, data in direct():
            yield event, data
        return
    async for event, data, _raw in sse_frames(response.content.iter_chunked(8192)):
        details = getattr(response, "transport_details", None)
        if details is not None:
            try:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    observe_event(details, response.transport_started, event, payload)
            except (ValueError, TypeError):
                pass
        yield event, data


def parse_sse_block(block: str) -> tuple[str | None, str]:
    event = None
    data_lines = []
    for raw in block.splitlines():
        line = raw.strip("\r")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return event, "\n".join(data_lines)


def sse(value: dict[str, Any], event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(value, separators=(',', ':'))}\n\n".encode("utf-8")


def normalize_reset_credits(payload: Any, account_key: str, slot_index: int) -> dict[str, Any]:
    credits_raw: list[Any] = []
    if isinstance(payload, dict):
        for key in ("rate_limit_reset_credits", "reset_credits", "credits", "grants", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                credits_raw = value
                break
            if isinstance(value, dict) and isinstance(value.get("data"), list):
                credits_raw = value["data"]
                break
    credits = []
    for index, item in enumerate(credits_raw):
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id") or item.get("credit_id") or item.get("reset_credit_id") or item.get("grant_id") or f"{slot_index}:{index}"
        credit_key = sha(str(raw_id), 16)
        credits.append({
            "credit_key": credit_key,
            "status": str(item.get("status") or "available"),
            "reset_type": item.get("reset_type") or item.get("type"),
            "title": item.get("title") or item.get("name"),
            "granted_at": item.get("granted_at") or item.get("created_at"),
            "expires_at": item.get("expires_at") or item.get("expiration_time"),
            "redeemed_at": item.get("redeemed_at") or item.get("used_at"),
        })
    available = None
    if isinstance(payload, dict):
        for key in ("available_count", "count", "available"):
            if isinstance(payload.get(key), (int, float)):
                available = int(payload[key])
                break
    if available is None:
        available = len([item for item in credits if item.get("status") == "available"])
    api = {
        "accountKey": account_key,
        "slotIndex": slot_index,
        "availableCount": available,
        "detailsAvailable": bool(credits),
        "source": "live",
        "updatedAt": now_iso(),
        "credits": [{
            "creditKey": item["credit_key"],
            "status": item["status"],
            "resetType": item["reset_type"],
            "title": item["title"],
            "grantedAt": item["granted_at"],
            "expiresAt": item["expires_at"],
            "redeemedAt": item["redeemed_at"],
        } for item in credits],
    }
    return {"api": api, "db": {"account_key": account_key, "slot_index": slot_index, "available_count": available, "details_available": bool(credits), "credits": credits}}
