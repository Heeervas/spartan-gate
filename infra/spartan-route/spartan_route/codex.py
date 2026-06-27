from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from aiohttp import ClientSession, ClientTimeout

from .telemetry import TelemetryStore
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
CODEX_STREAM_TIMEOUT_CODE = "codex_stream_timeout"
CODEX_STREAM_ERROR_CODE = "codex_stream_error"


@dataclass
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
    auth_unavailable_reason: str | None = None
    auth_retry_at: float = 0
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
    error: str | None = None
    completed: bool = False
    usage_seen: bool = False
    usage_event_type: str | None = None
    upstream_response_time_ms: int | None = None
    total_response_time_ms: int | None = None


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
        body["reasoning"] = {"effort": request["reasoning_effort"]}
    return body


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


class CodexRouter:
    def __init__(self, session: ClientSession, telemetry: TelemetryStore):
        self.session = session
        self.telemetry = telemetry
        self.slots: list[CodexSlot] = []
        self.leases: dict[str, CacheLease] = {}
        self.refresh_locks: dict[str, asyncio.Lock] = {}
        self._usage_cache: dict[str, Any] | None = None
        self._init_slots()

    def _init_slots(self) -> None:
        token = (os.environ.get("OPENAI_CODEX_TOKEN") or "").strip()
        if token:
            self.slots = [CodexSlot(0, token=token, auth=CodexAuth(access_token=token))]
            return
        self.slots = [CodexSlot(index, path=path) for index, path in enumerate(resolve_auth_paths())]

    def reset_state(self) -> None:
        self.slots = []
        self.leases.clear()
        self.refresh_locks.clear()
        self._usage_cache = None
        self._init_slots()

    def slot_path_label(self, slot: CodexSlot) -> str | None:
        if slot.path:
            return slot.path
        if slot.token:
            return "OPENAI_CODEX_TOKEN"
        return None

    def slot_status(self, include_paths: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for slot in self.slots:
            auth = slot.auth
            routing = self.slot_routing_status(slot)
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

    def slot_routing_status(self, slot: CodexSlot) -> dict[str, Any]:
        telemetry = getattr(self, "telemetry", None)
        override = telemetry.slot_routing_override(slot.index) if telemetry else None
        if not override:
            return {
                "routingEnabled": True,
                "requestedRoutingEnabled": True,
                "identityMismatch": False,
                "identityUnverified": False,
                "overrideUpdatedAt": None,
            }
        identity = self._slot_identity(slot)
        keys = ("accountKey", "slotPathHash", "authFingerprintHash")
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
        self.telemetry.set_slot_routing_enabled(
            slot.index,
            bool(routing_enabled),
            account_key=identity["accountKey"],
            slot_path_hash=identity["slotPathHash"],
            auth_fingerprint_hash=identity["authFingerprintHash"],
        )
        if not routing_enabled:
            self._invalidate_slot_leases(slot)
        self._usage_cache = None
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

    async def load_auth(self, slot: CodexSlot) -> CodexAuth | None:
        if slot.token:
            return slot.auth
        if slot.auth_retry_at > time.time():
            return None
        if slot.auth and not is_jwt_expired(slot.auth.access_token):
            return slot.auth
        if not slot.path:
            slot.auth_unavailable_reason = "missing"
            return None
        snapshot = read_auth_file(slot.path)
        if not snapshot:
            slot.auth = None
            slot.auth_unavailable_reason = "missing"
            return None
        data, fingerprint = snapshot
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
        access = tokens.get("access_token") if isinstance(tokens, dict) else None
        refresh = tokens.get("refresh_token") if isinstance(tokens, dict) else None
        account_id = derive_account_id(tokens if isinstance(tokens, dict) else {})
        if not isinstance(access, str) or not access:
            slot.auth = None
            slot.auth_unavailable_reason = "missing"
            return None
        if is_jwt_expired(access):
            if isinstance(refresh, str) and refresh:
                refreshed = await self._refresh(slot, refresh, account_id)
                if refreshed:
                    return refreshed
                slot.auth_retry_at = time.time() + 300
                slot.auth_unavailable_reason = "expired_refresh_failed"
                return None
            slot.auth_unavailable_reason = "expired"
            return None
        if not account_id:
            slot.auth_unavailable_reason = "unknown_account"
            return None
        slot.auth = CodexAuth(access, refresh if isinstance(refresh, str) else None, account_id, slot.path)
        slot.auth_fingerprint = fingerprint
        slot.auth_unavailable_reason = None
        return slot.auth

    async def _refresh(self, slot: CodexSlot, refresh_token: str, fallback_account_id: str | None = None) -> CodexAuth | None:
        assert slot.path
        lock = self.refresh_locks.setdefault(slot.path, asyncio.Lock())
        async with lock:
            try:
                async with self.session.post(
                    OAUTH_TOKEN_URL,
                    json={"client_id": OAUTH_CLIENT_ID, "grant_type": "refresh_token", "refresh_token": refresh_token},
                    timeout=ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
            except Exception:
                return None
            access = payload.get("access_token")
            if not isinstance(access, str) or not access:
                return None
            next_refresh = payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else refresh_token
            account_id = derive_account_id({"access_token": access, "refresh_token": next_refresh, "id_token": payload.get("id_token")}) or fallback_account_id
            if not account_id:
                return None
            auth = CodexAuth(access, next_refresh, account_id, slot.path)
            slot.auth = auth
            slot.auth_unavailable_reason = None
            slot.auth_retry_at = 0
            self._persist_refresh(slot.path, access, next_refresh, payload.get("id_token"))
            return auth

    def _persist_refresh(self, path: str, access: str, refresh: str, id_token: Any) -> None:
        snapshot = read_auth_file(path)
        if not snapshot:
            return
        data, _fingerprint = snapshot
        tokens = data.setdefault("tokens", {})
        if isinstance(tokens, dict):
            tokens["access_token"] = access
            tokens["refresh_token"] = refresh
            if isinstance(id_token, str):
                tokens["id_token"] = id_token
        tmp = f"{path}.tmp"
        try:
            Path(tmp).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass

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

    def _remember_lease(self, affinity_key: str | None, slot: CodexSlot) -> None:
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

    async def choose_slot(self, affinity_key: str | None, excluded: set[int], excluded_accounts: set[str]) -> tuple[CodexSlot, CodexAuth] | None:
        self._clear_expired_leases()
        now = time.time()
        candidates: list[tuple[CodexSlot, CodexAuth]] = []
        for slot in self.slots:
            if slot.index in excluded:
                continue
            if slot.rate_limited_until > now or slot.transient_until > now:
                continue
            auth = await self.load_auth(slot)
            if not auth:
                continue
            if auth.account_key and auth.account_key in excluded_accounts:
                continue
            if not self.slot_routing_status(slot)["routingEnabled"]:
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

    def mark_rate_limited(self, slot: CodexSlot, text: str) -> None:
        error = parse_codex_error(text)
        reset = epoch_millis(error.get("resets_at")) or int((time.time() + parse_float(error.get("resets_in_seconds"), 900)) * 1000)
        slot.rate_limited_until = reset / 1000
        self._invalidate_slot_leases(slot)

    def mark_transient(self, slot: CodexSlot) -> None:
        cooldown = parse_int(os.environ.get("CODEX_TRANSIENT_5XX_COOLDOWN_MS"), 120_000, 0, 900_000) / 1000
        if cooldown > 0:
            slot.transient_until = time.time() + cooldown
            self._invalidate_slot_leases(slot)

    async def execute_chat(
        self,
        request: dict[str, Any],
        model_id: str,
        wants_stream: bool,
        session_id: str | None,
        prompt_cache_key: str | None,
        request_id: str,
    ) -> CodexCallResult:
        model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id
        upstream_cache_key = resolve_prompt_cache_key(request, session_id, prompt_cache_key)
        affinity_key = resolve_affinity_key(request, session_id, prompt_cache_key)
        body = build_codex_request_body(request, model_name, upstream_cache_key)
        slot_count = max(1, len(self.slots))
        excluded_slots: set[int] = set()
        excluded_accounts: set[str] = set()
        last_error: CodexCallResult | None = None
        for _attempt in range(slot_count):
            chosen = await self.choose_slot(affinity_key, excluded_slots, excluded_accounts)
            if not chosen:
                break
            slot, auth = chosen
            slot.in_flight += 1
            slot.last_selected_at = time.time()
            try:
                result = await self._single_chat_call(body, model_id, model_name, wants_stream, slot, auth)
            except Exception:
                slot.in_flight = max(0, slot.in_flight - 1)
                raise
            if result.stream is None:
                slot.in_flight = max(0, slot.in_flight - 1)
            else:
                source = result.stream

                async def stream_with_inflight_guard() -> AsyncIterator[bytes]:
                    try:
                        while True:
                            try:
                                chunk = await source.__anext__()
                            except StopAsyncIteration:
                                break
                            yield chunk
                    finally:
                        close = getattr(source, "aclose", None)
                        if callable(close):
                            await close()
                        if result.completed and not result.error and affinity_key:
                            self._remember_lease(affinity_key, slot)
                        slot.in_flight = max(0, slot.in_flight - 1)

                result.stream = stream_with_inflight_guard()
            result.slot_index = slot.index
            result.account_key = auth.account_key
            result.input_tokens = token_count_estimate(request)
            if result.status not in {401, 429, 500, 502}:
                if result.status < 400 and result.stream is None and not result.error:
                    self._remember_lease(affinity_key, slot)
                return result
            text = result.body.decode("utf-8", errors="replace") if isinstance(result.body, bytes) else stable_json(result.body)
            if not should_retry(result.status, text):
                return result
            excluded_slots.add(slot.index)
            if auth.account_key:
                excluded_accounts.add(auth.account_key)
            if result.status == 401:
                slot.auth = None
                slot.auth_unavailable_reason = "expired"
                self._invalidate_slot_leases(slot)
            elif result.status == 429:
                self.mark_rate_limited(slot, text)
            else:
                self.mark_transient(slot)
            last_error = result
        if last_error:
            return last_error
        return CodexCallResult(
            status=503,
            body={"error": {"message": "All Codex auth slots are temporarily unavailable.", "type": "upstream_error", "code": "codex_unavailable"}},
            error="codex_unavailable",
        )

    async def _single_chat_call(
        self,
        body: dict[str, Any],
        model_id: str,
        model_name: str,
        wants_stream: bool,
        slot: CodexSlot,
        auth: CodexAuth,
    ) -> CodexCallResult:
        headers = {
            "Authorization": f"Bearer {auth.access_token}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
        }
        if auth.account_id:
            headers["chatgpt-account-id"] = auth.account_id
        timeout_ms = parse_int(os.environ.get("CODEX_REQUEST_TIMEOUT_MS"), 180_000, 10_000, 900_000)
        try:
            upstream_started = time.monotonic()
            response = await self.session.post(
                CODEX_RESPONSES_URL,
                json=body,
                headers=headers,
                timeout=ClientTimeout(total=timeout_ms / 1000),
            )
            upstream_response_time_ms = max(0, int((time.monotonic() - upstream_started) * 1000))
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
                upstream_response_time_ms=upstream_response_time_ms,
            )
        if wants_stream:
            result = CodexCallResult(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                upstream_response_time_ms=upstream_response_time_ms,
            )
            result.stream = self._codex_stream_to_chat(response, model_id, result, slot)
            return result
        collected = await self._collect_codex_response(response, model_id, slot)
        collected.upstream_response_time_ms = upstream_response_time_ms
        return collected

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
                if state.terminal_error:
                    break
        except asyncio.TimeoutError as exc:
            return self._stream_transport_result(exc, slot, state)
        except Exception as exc:
            return self._stream_transport_result(exc, slot, state)
        finally:
            response.release()
        return self._chat_completion_result(state, model)

    async def _codex_stream_to_chat(self, response: Any, model: str, result: CodexCallResult, slot: CodexSlot) -> AsyncIterator[bytes]:
        chat_id = f"chatcmpl_{sha(str(time.time()), 12)}"
        created = int(time.time())
        state = CodexEventState()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        downstream_open = True

        async def emit(chunk: bytes) -> None:
            if downstream_open:
                await queue.put(chunk)

        async def produce() -> None:
            buffer = ""
            try:
                await emit(sse({
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }))
                async for chunk in response.content.iter_chunked(8192):
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        event, data = parse_sse_block(block)
                        outputs, done = self._codex_chat_outputs_for_event(event, data, state, result, slot, chat_id, created, model)
                        for output in outputs:
                            await emit(output)
                        if done:
                            return
                if buffer.strip():
                    event, data = parse_sse_block(buffer)
                    outputs, done = self._codex_chat_outputs_for_event(event, data, state, result, slot, chat_id, created, model)
                    for output in outputs:
                        await emit(output)
                    if done:
                        return
                result.input_tokens, result.cached_input_tokens, result.output_tokens = self._usage_values(state)
                result.usage_seen = state.usage_seen
                result.usage_event_type = state.usage_event_type
                if not state.has_payload():
                    error = self._empty_response_error()
                    result.error = str(error["code"])
                    self.mark_transient(slot)
                    await emit(sse({"error": error}, event="error"))
                    await emit(b"data: [DONE]\n\n")
                    return
                if state.finish_reason == "length" and state.tool_calls:
                    error = self._incomplete_tool_response_error()
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
            if not producer.done():
                try:
                    await asyncio.wait_for(asyncio.shield(producer), timeout=CODEX_USAGE_DRAIN_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    producer.cancel()
                except asyncio.CancelledError:
                    producer.cancel()
                    raise
                except Exception:
                    pass
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
            result.error = self._error_code(error)
            self._mark_streaming_error(slot, error)
            return [sse({"error": error}, event="error"), b"data: [DONE]\n\n"], True
        chunks = self._apply_codex_event_state(event, parsed, state, stream=True)
        if state.terminal_error:
            self._copy_usage_to_result(result, state)
            result.error = self._error_code(state.terminal_error)
            self._mark_streaming_error(slot, state.terminal_error)
            return [sse({"error": state.terminal_error}, event="error"), b"data: [DONE]\n\n"], True
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

    def _copy_usage_to_result(self, result: CodexCallResult, state: CodexEventState) -> None:
        result.input_tokens, result.cached_input_tokens, result.output_tokens = self._usage_values(state)
        result.usage_seen = state.usage_seen
        result.usage_event_type = state.usage_event_type

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
        state.usage_seen = True
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
            "message": "Codex returned an incomplete tool-call response",
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
        )

    def _codex_error_status(self, error: dict[str, Any]) -> int:
        code = str(error.get("code") or "")
        error_type = str(error.get("type") or error.get("upstream_type") or "")
        if error_type == "auth_error" or code in {"invalid_api_key", "invalid_auth", "unauthorized"}:
            return 401
        if error_type in {"usage_limit_reached", "rate_limit_error"} or code in {"usage_limit_reached", "rate_limit_exceeded", "codex_429"}:
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
            usage_seen=usage_seen,
            usage_event_type=usage_event_type,
        )

    def _chat_completion_result(self, state: CodexEventState, model: str) -> CodexCallResult:
        if state.terminal_error:
            return self._error_result(state.terminal_error, state)
        prompt_tokens, cached, output_tokens = self._usage_values(state)
        if not state.has_payload():
            return CodexCallResult(
                status=502,
                body={"error": self._empty_response_error()},
                input_tokens=prompt_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                error="codex_empty_response",
                usage_seen=state.usage_seen,
                usage_event_type=state.usage_event_type,
            )
        if state.finish_reason == "length" and state.tool_calls:
            return CodexCallResult(
                status=502,
                body={"error": self._incomplete_tool_response_error()},
                input_tokens=prompt_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                error="codex_incomplete_response",
                usage_seen=state.usage_seen,
                usage_event_type=state.usage_event_type,
            )
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
        if error_type == "auth_error" or code in {"invalid_auth", "unauthorized"}:
            slot.auth = None
            slot.auth_unavailable_reason = "expired"
            self._invalidate_slot_leases(slot)
        elif error_type in {"usage_limit_reached", "rate_limit_error"} or code in {"usage_limit_reached", "rate_limit_exceeded", "codex_429"}:
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

    async def execute_image(self, request: dict[str, Any]) -> CodexCallResult:
        chosen = await self.choose_slot(None, set(), set())
        if not chosen:
            return CodexCallResult(status=503, body={"error": {"message": "No Codex auth slot available", "type": "auth_error", "code": "codex_auth_missing"}})
        slot, auth = chosen
        prompt = str(request.get("prompt") or "")
        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        for image_url in request.get("images") or []:
            if isinstance(image_url, str) and image_url:
                content.append({"type": "input_image", "image_url": image_url})
        tool: dict[str, Any] = {
            "type": "image_generation",
            "model": "gpt-image-2",
            "quality": request.get("quality") or "medium",
            "size": request.get("size") or "1024x1024",
            "output_format": request.get("output_format") or "png",
            "background": request.get("background") or "opaque",
            "partial_images": 1,
        }
        if len(content) > 1:
            tool["action"] = "edit"
        if request.get("output_compression") is not None:
            tool["output_compression"] = parse_float(request.get("output_compression"), 0.0)
        body = {
            "model": "gpt-5.4",
            "input": [{"role": "user", "content": content if len(content) > 1 else prompt}],
            "tools": [tool],
            "tool_choice": {"type": "allowed_tools", "mode": "required", "tools": [{"type": "image_generation"}]},
            "instructions": "You must fulfill image requests by using the image_generation tool.",
            "stream": True,
            "store": False,
        }
        headers = {"Authorization": f"Bearer {auth.access_token}", "Content-Type": "application/json", "OpenAI-Beta": "responses=experimental"}
        if auth.account_id:
            headers["chatgpt-account-id"] = auth.account_id
        try:
            async with self.session.post(CODEX_RESPONSES_URL, json=body, headers=headers, timeout=ClientTimeout(total=180)) as response:
                if response.status != 200:
                    return CodexCallResult(status=response.status, body={"error": parse_codex_error(await response.text())})
                image_b64 = None
                revised_prompt = None
                async for event, data in parse_sse_response(response):
                    if not data or data == "[DONE]":
                        continue
                    parsed = json.loads(data)
                    if event == "response.image_generation_call.partial_image":
                        image_b64 = parsed.get("partial_image_b64") or image_b64
                    elif event in {"response.output_item.done", "response.completed"}:
                        items = []
                        if isinstance(parsed.get("item"), dict):
                            items = [parsed["item"]]
                        elif isinstance(parsed.get("response"), dict):
                            items = parsed["response"].get("output") or []
                        for item in items:
                            if isinstance(item, dict) and item.get("type") == "image_generation_call":
                                image_b64 = item.get("result") or image_b64
                                revised_prompt = item.get("revised_prompt") or revised_prompt
                if not image_b64:
                    return CodexCallResult(status=502, body={"error": {"message": "Codex response contained no image result", "type": "server_error", "code": "codex_image_empty"}})
                return CodexCallResult(status=200, body={"created": int(time.time()), "data": [{"b64_json": image_b64, **({"revised_prompt": revised_prompt} if revised_prompt else {})}]})
        except Exception as exc:
            return CodexCallResult(status=502, body={"error": {"message": str(exc), "type": "server_error", "code": "codex_image_error"}})

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
        if slot_indexes is None and not force and self._usage_cache:
            if self._usage_cache["expiresAt"] > time.time() and self._usage_cache["signature"] == self._usage_cache_signature():
                cached_payload = copy.deepcopy(self._usage_cache["payload"])
                cached_payload["cached"] = True
                cached_payload["cacheExpiresAt"] = self._iso_from_epoch(self._usage_cache["expiresAt"])
                return cached_payload

        payload = await self._fetch_usage_snapshot(slot_indexes)
        payload["cached"] = False
        if slot_indexes is None:
            expires_at = time.time() + CODEX_USAGE_CACHE_TTL_SECONDS
            self._usage_cache = {
                "expiresAt": expires_at,
                "signature": self._usage_cache_signature(),
                "payload": copy.deepcopy(payload),
            }
            payload["cacheExpiresAt"] = self._iso_from_epoch(expires_at)
        else:
            self._usage_cache = None
        return payload

    async def _fetch_usage_snapshot(self, slot_indexes: set[int] | None = None) -> dict[str, Any]:
        accounts: list[dict[str, Any]] = []
        slot_errors: list[dict[str, Any]] = []
        reset_errors: list[dict[str, Any]] = []
        usage_records: list[dict[str, Any]] = []
        reset_records: list[dict[str, Any]] = []
        persisted_reset = self.telemetry.reset_credit_snapshots()
        for slot in self.slots:
            if slot_indexes is not None and slot.index not in slot_indexes:
                continue
            auth = await self.load_auth(slot)
            account_key = auth.account_key if auth else slot.account_key()
            if not auth or not account_key:
                slot_errors.append({"slotIndex": slot.index, "source": "none", "message": slot.auth_unavailable_reason or "auth unavailable"})
                continue
            slot_path = self.slot_path_label(slot)
            row = {
                "accountKey": account_key,
                "slotIndexes": [slot.index],
                "slotPaths": [slot_path] if slot_path else [],
                "source": "live",
                "stale": False,
                "fiveHour": None,
                "weekly": None,
                "resetCredits": persisted_reset.get(account_key),
            }
            headers = {"Authorization": f"Bearer {auth.access_token}"}
            if auth.account_id:
                headers["chatgpt-account-id"] = auth.account_id
            try:
                async with self.session.get(CODEX_USAGE_URL, headers=headers, timeout=ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Codex usage HTTP {response.status}")
                    payload = await response.json()
                for window_name, source, seconds in (
                    ("fiveHour", payload.get("primary_window") or (payload.get("rate_limit") or {}).get("primary_window"), FIVE_HOURS_SECONDS),
                    ("weekly", payload.get("secondary_window") or (payload.get("rate_limit") or {}).get("secondary_window"), SEVEN_DAYS_SECONDS),
                ):
                    if isinstance(source, dict):
                        used = parse_float(source.get("used_percent"), 0.0)
                        reset_at = source.get("reset_at")
                        if isinstance(reset_at, (int, float)):
                            reset_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(float(reset_at)))
                        if isinstance(reset_at, str):
                            row[window_name] = {"window": window_name, "usedPercent": used, "resetAt": reset_at, "windowMinutes": seconds // 60, "updatedAt": now_iso()}
                            usage_records.append({"account_key": account_key, "slot_index": slot.index, "window": window_name, "used_percent": used, "reset_at": reset_at, "window_minutes": seconds // 60})
            except Exception as exc:
                slot_errors.append({"slotIndex": slot.index, "source": "none", "message": str(exc)})
            try:
                headers_reset = dict(headers)
                headers_reset.update({"OpenAI-Beta": "codex-1", "originator": "Codex Desktop"})
                async with self.session.get(CODEX_RESET_CREDITS_URL, headers=headers_reset, timeout=ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Codex reset credits HTTP {response.status}")
                    payload = await response.json()
                reset = normalize_reset_credits(payload, account_key, slot.index)
                row["resetCredits"] = reset["api"]
                reset_records.append(reset["db"])
            except Exception as exc:
                reset_errors.append({"slotIndex": slot.index, "source": "persisted" if row["resetCredits"] else "none", "message": str(exc)})
            accounts.append(row)
        self.telemetry.upsert_usage_snapshots(usage_records)
        self.telemetry.upsert_reset_credits(reset_records)
        return {"accounts": accounts, "slotErrors": slot_errors, "resetCreditErrors": reset_errors, "partial": bool(slot_errors or reset_errors)}


async def parse_sse_response(response: Any) -> AsyncIterator[tuple[str | None, str]]:
    buffer = ""
    async for chunk in response.content.iter_chunked(8192):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event, data = parse_sse_block(block)
            if data:
                yield event, data
    if buffer.strip():
        event, data = parse_sse_block(buffer)
        if data:
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
