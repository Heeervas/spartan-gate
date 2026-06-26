from __future__ import annotations

import asyncio
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
        body["tool_choice"] = request["tool_choice"]
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
            code in {"codex_error", "server_error"}
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
        self._init_slots()

    def slot_status(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for slot in self.slots:
            auth = slot.auth
            out.append({
                "slotIndex": slot.index,
                "accountKey": auth.account_key if auth else None,
                "rateLimitedUntil": slot.rate_limited_until,
                "transientUntil": slot.transient_until,
                "authAvailable": bool(auth and not is_jwt_expired(auth.access_token)),
                "authUnavailableReason": slot.auth_unavailable_reason,
                "authRetryAt": slot.auth_retry_at if slot.auth_retry_at > now else None,
                "inFlight": slot.in_flight,
            })
        return out

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
                        async for chunk in source:
                            yield chunk
                    finally:
                        slot.in_flight = max(0, slot.in_flight - 1)

                result.stream = stream_with_inflight_guard()
            result.slot_index = slot.index
            result.account_key = auth.account_key
            result.input_tokens = token_count_estimate(request)
            if result.status not in {401, 429, 500, 502}:
                if result.status < 400:
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
            response = await self.session.post(
                CODEX_RESPONSES_URL,
                json=body,
                headers=headers,
                timeout=ClientTimeout(total=timeout_ms / 1000),
            )
        except Exception as exc:
            return CodexCallResult(status=502, body={"error": {"message": str(exc), "type": "upstream_error", "code": "codex_error"}}, error=str(exc))
        if response.status != 200:
            text = await response.text()
            response.release()
            return CodexCallResult(status=response.status, body=text.encode(), headers={"Content-Type": "application/json"}, error=text[:300])
        if wants_stream:
            result = CodexCallResult(status=200, headers={"Content-Type": "text/event-stream"})
            result.stream = self._codex_stream_to_chat(response, model_id, result, slot)
            return result
        collected = await self._collect_codex_response(response, model_id, slot.index)
        return collected

    async def _collect_codex_response(self, response: Any, model: str, slot_index: int) -> CodexCallResult:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[str, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        error: dict[str, Any] | None = None
        async for event, data in parse_sse_response(response):
            if not data or data == "[DONE]":
                continue
            parsed = json.loads(data)
            if event == "error" or parsed.get("error"):
                error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
                break
            self._apply_codex_event(event, parsed, text_parts, reasoning_parts, tool_calls, usage)
        if error:
            status = 401 if error.get("type") == "auth_error" else 429 if error.get("type") == "usage_limit_reached" else 502
            return CodexCallResult(status=status, body={"error": error}, error=str(error.get("message") or error.get("code") or "codex_error"))
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        calls = list(tool_calls.values())
        if calls:
            message["tool_calls"] = calls
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or usage.get("cached_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        body = {
            "id": f"chatcmpl_{sha(str(time.time()), 12)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }
        return CodexCallResult(status=200, body=body, input_tokens=prompt_tokens, cached_input_tokens=cached, output_tokens=output_tokens)

    async def _codex_stream_to_chat(self, response: Any, model: str, result: CodexCallResult, slot: CodexSlot) -> AsyncIterator[bytes]:
        chat_id = f"chatcmpl_{sha(str(time.time()), 12)}"
        created = int(time.time())
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[str, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        try:
            async for event, data in parse_sse_response(response):
                if not data or data == "[DONE]":
                    continue
                parsed = json.loads(data)
                if event == "error" or parsed.get("error"):
                    error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
                    result.error = str(error.get("message") or error.get("code") or "codex_error")
                    self._mark_streaming_error(slot, error)
                    yield sse({"error": error}, event="error")
                    break
                chunks = self._apply_codex_event(event, parsed, text_parts, reasoning_parts, tool_calls, usage, stream=True)
                for delta in chunks:
                    yield sse({
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    })
            result.input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or result.input_tokens)
            result.cached_input_tokens = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or usage.get("cached_tokens") or 0)
            result.output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            yield sse({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            })
            yield b"data: [DONE]\n\n"
        finally:
            response.release()

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
        stream: bool = False,
    ) -> list[dict[str, Any]]:
        deltas: list[dict[str, Any]] = []
        if event in {"response.output_text.delta", "response.output_text.annotation.added"}:
            delta = parsed.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
                if stream:
                    deltas.append({"content": delta})
        elif event == "response.reasoning_summary_text.delta":
            delta = parsed.get("delta")
            if isinstance(delta, str):
                reasoning_parts.append(delta)
                if stream:
                    deltas.append({"reasoning_content": delta})
        elif event in {"response.function_call_arguments.delta", "response.output_item.added", "response.output_item.done"}:
            item = parsed.get("item") if isinstance(parsed.get("item"), dict) else parsed
            if item.get("type") == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}")
                call = tool_calls.setdefault(call_id, {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": item.get("name") or "", "arguments": ""},
                })
                if isinstance(item.get("name"), str):
                    call["function"]["name"] = item["name"]
                args = parsed.get("delta") or item.get("arguments")
                if isinstance(args, str):
                    call["function"]["arguments"] += args
        elif event == "response.completed":
            response = parsed.get("response") if isinstance(parsed.get("response"), dict) else parsed
            usage.update(response.get("usage") or {})
            for item in response.get("output") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    for content in item.get("content") or []:
                        if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                            if not text_parts:
                                text_parts.append(content["text"])
                elif item.get("type") == "reasoning":
                    summary = item.get("summary") or []
                    for part in summary:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            reasoning_parts.append(part["text"])
                elif item.get("type") == "function_call":
                    call_id = str(item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}")
                    tool_calls[call_id] = {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": item.get("name") or "", "arguments": item.get("arguments") or "{}"},
                    }
        return deltas

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

    async def usage_snapshot(self, force: bool = False) -> dict[str, Any]:
        del force
        accounts: list[dict[str, Any]] = []
        slot_errors: list[dict[str, Any]] = []
        reset_errors: list[dict[str, Any]] = []
        usage_records: list[dict[str, Any]] = []
        reset_records: list[dict[str, Any]] = []
        persisted_reset = self.telemetry.reset_credit_snapshots()
        for slot in self.slots:
            auth = await self.load_auth(slot)
            account_key = auth.account_key if auth else slot.account_key()
            if not auth or not account_key:
                slot_errors.append({"slotIndex": slot.index, "source": "none", "message": slot.auth_unavailable_reason or "auth unavailable"})
                continue
            row = {"accountKey": account_key, "slotIndexes": [slot.index], "source": "live", "stale": False, "fiveHour": None, "weekly": None, "resetCredits": persisted_reset.get(account_key)}
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
