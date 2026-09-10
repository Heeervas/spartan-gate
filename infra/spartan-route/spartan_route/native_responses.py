from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from aiohttp import ClientTimeout

from .codex import (
    CODEX_RESPONSES_URL,
    CodexAuth,
    CodexCallResult,
    CodexRouter,
    CodexSlot,
    TERMINAL_CODEX_EVENTS,
    build_codex_responses_body,
    codex_request_timeout_ms,
    parse_codex_error,
    parse_sse_response,
    resolve_affinity_key,
    resolve_prompt_cache_key,
    should_retry,
    sse,
)
from .util import stable_json, token_count_estimate

CODEX_RESPONSES_REALM = "codex_subscription"
CODEX_RESPONSES_MAX_ATTEMPTS = 3
CODEX_RESPONSES_PRECOMMIT_MAX_EVENTS = 64
CODEX_RESPONSES_PRECOMMIT_MAX_BYTES = 256 * 1024


@dataclass
class OpenedResponse:
    response: Any
    response_time_ms: int


def response_event_name(event: str | None, payload: dict[str, Any]) -> str:
    return str(event or payload.get("type") or "message")


def response_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    nested = payload.get("response")
    if isinstance(nested, dict):
        return nested
    if payload.get("object") == "response":
        return payload
    return None


def annotate_realm(payload: dict[str, Any], realm: str) -> dict[str, Any]:
    updated = copy.deepcopy(payload)
    target = response_object(updated)
    if target is not None:
        metadata = dict(target.get("metadata") or {})
        metadata["spartan_reasoning_realm"] = realm
        target["metadata"] = metadata
    return updated


def usage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    response = response_object(payload)
    usage = response.get("usage") if response is not None else payload.get("usage")
    return usage if isinstance(usage, dict) else None


def usage_values(usage: dict[str, Any] | None) -> tuple[int, int, int, int | None]:
    if not isinstance(usage, dict):
        return 0, 0, 0, None
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_tokens = int(input_details.get("cached_tokens") or usage.get("cached_tokens") or 0) if isinstance(input_details, dict) else 0
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    output_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    raw_reasoning = output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
    try:
        reasoning_tokens = int(raw_reasoning) if raw_reasoning is not None else None
    except (TypeError, ValueError):
        reasoning_tokens = None
    if reasoning_tokens is not None:
        reasoning_tokens = max(0, min(output_tokens, reasoning_tokens))
    return input_tokens, cached_tokens, output_tokens, reasoning_tokens


def capture_tool_item(result: CodexCallResult, item: Any, index: Any = None) -> None:
    if not isinstance(item, dict):
        result._native_tool_invalid = True
        return
    kind = item.get("type")
    if not isinstance(kind, str):
        result._native_tool_invalid = True
        return
    if not kind.endswith("_call"):
        return
    aliases = [f"{key}:{item[key]}" for key in ("id", "call_id") if isinstance(item.get(key), str) and item[key]]
    if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
        aliases.append(f"index:{index}")
    if not aliases:
        # A name is not an identity: two invocations can share a name.
        result._native_tool_invalid = True
        return
    existing = {result._native_tool_aliases[a] for a in aliases if a in result._native_tool_aliases}
    key = sorted(existing)[0] if existing else aliases[0]
    for other in existing - {key}:
        previous_name = result._native_tool_names.pop(other, "")
        if previous_name:
            result._native_tool_names[key] = previous_name
        for alias, target in list(result._native_tool_aliases.items()):
            if target == other:
                result._native_tool_aliases[alias] = key
    for alias in aliases:
        result._native_tool_aliases[alias] = key
    name = item.get("name")
    if not isinstance(name, str) or not name:
        name = result._native_tool_names.get(key) or kind.removesuffix("_call")
    result._native_tool_names[key] = name
    result.actual_tool_call_count = len(result._native_tool_names)
    result.actual_tool_names = sorted(set(result._native_tool_names.values()))


def capture_telemetry(result: CodexCallResult, payload: dict[str, Any], event_name: str) -> None:
    if result.tool_telemetry_complete is None:
        result.tool_telemetry_complete = False
    usage = usage_from_payload(payload)
    if usage is not None:
        (
            result.input_tokens,
            result.cached_input_tokens,
            result.output_tokens,
            result.reasoning_tokens,
        ) = usage_values(usage)
        result.usage_seen = True
        result.usage_event_type = event_name
    response = response_object(payload)
    output = response.get("output") if response is not None else None
    if response is not None and "output" in response and not isinstance(output, list):
        result._native_tool_invalid = True
    if isinstance(output, list):
        for index, item in enumerate(output):
            capture_tool_item(result, item, index)
    if event_name in {"response.output_item.added", "response.output_item.done"}:
        capture_tool_item(result, payload.get("item"), payload.get("output_index"))
    if event_name in TERMINAL_CODEX_EVENTS:
        result.tool_telemetry_complete = bool(
            event_name == "response.completed" and response is not None
            and response.get("status") == "completed" and not result._native_tool_invalid
        )
        if result.tool_telemetry_complete:
            result.actual_tool_call_count = len(result._native_tool_names)
            result.actual_tool_names = sorted(set(result._native_tool_names.values()))


def sanitized_error_code(text: str) -> str:
    error = parse_codex_error(text)
    return str(error.get("code") or error.get("type") or "codex_error")


async def open_response(
    router: CodexRouter,
    body: dict[str, Any],
    auth: CodexAuth,
) -> OpenedResponse | CodexCallResult:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
    }
    if auth.account_id:
        headers["chatgpt-account-id"] = auth.account_id
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            router.session.post(
                CODEX_RESPONSES_URL,
                json=body,
                headers=headers,
                timeout=ClientTimeout(total=None),
            ),
            timeout=codex_request_timeout_ms() / 1000,
        )
    except asyncio.TimeoutError:
        return CodexCallResult(
            status=502,
            body={"error": {"message": "Codex request timed out while waiting for response headers", "type": "server_error", "code": "codex_error"}},
            error="codex_error",
            upstream_response_time_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except Exception as exc:
        return CodexCallResult(
            status=502,
            body={"error": {"message": "Codex Responses request failed", "type": "upstream_error", "code": "codex_error"}},
            error=f"codex_error:{type(exc).__name__}",
            upstream_response_time_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    elapsed = max(0, int((time.monotonic() - started) * 1000))
    if response.status != 200:
        text = await response.text()
        response.release()
        return CodexCallResult(
            status=response.status,
            body=text.encode(),
            headers={"Content-Type": "application/json"},
            error=sanitized_error_code(text),
            upstream_response_time_ms=elapsed,
        )
    return OpenedResponse(response, elapsed)


async def collect_response(
    router: CodexRouter,
    opened: OpenedResponse,
    slot: CodexSlot,
    realm: str,
) -> CodexCallResult:
    result = CodexCallResult(status=200, upstream_response_time_ms=opened.response_time_ms)
    terminal: dict[str, Any] | None = None
    completed_output_items: list[dict[str, Any]] = []
    try:
        async for event, data in parse_sse_response(opened.response):
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except Exception:
                result._native_tool_invalid = True
                continue
            if not isinstance(payload, dict):
                result._native_tool_invalid = True
                continue
            event_name = response_event_name(event, payload)
            capture_telemetry(result, payload, event_name)
            if event_name == "response.output_item.done" and isinstance(payload.get("item"), dict):
                completed_output_items.append(copy.deepcopy(payload["item"]))
            if event_name == "error" or (isinstance(payload.get("error"), dict) and "response" not in payload):
                error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
                result.status = router._codex_error_status(error)
                result.body = {"error": router._stream_client_error(error)}
                result.error = router._error_code(error)
                return result
            if event_name in TERMINAL_CODEX_EVENTS:
                current = response_object(payload)
                if current is not None:
                    terminal = annotate_realm(current, realm)
                break
    except Exception as exc:
        error = router._stream_transport_error(exc)
        router._mark_streaming_error(slot, error)
        result.status = 502
        result.body = {"error": error}
        result.error = router._error_code(error)
        return result
    finally:
        opened.response.release()
    if terminal is None:
        error = router._empty_response_error()
        router.mark_transient(slot)
        result.status = 502
        result.body = {"error": error}
        result.error = router._error_code(error)
        return result
    if not isinstance(terminal.get("output"), list) or not terminal.get("output"):
        if completed_output_items:
            terminal["output"] = completed_output_items
    result.body = terminal
    status = str(terminal.get("status") or "").lower()
    result.completed = status == "completed"
    if status in {"failed", "cancelled", "incomplete"}:
        error = terminal.get("error")
        result.error = router._error_code(error) if isinstance(error, dict) else f"response_{status}"
    return result


async def prepare_stream(
    router: CodexRouter,
    opened: OpenedResponse,
    result: CodexCallResult,
    slot: CodexSlot,
    realm: str,
) -> bool:
    events = parse_sse_response(opened.response)
    buffered: list[bytes] = []
    buffered_bytes = 0
    try:
        while True:
            try:
                event, data = await events.__anext__()
            except StopAsyncIteration:
                opened.response.release()
                return False
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except Exception:
                result._native_tool_invalid = True
                continue
            if not isinstance(payload, dict):
                result._native_tool_invalid = True
                continue
            event_name = response_event_name(event, payload)
            encoded = sse(annotate_realm(payload, realm), event_name)
            buffered.append(encoded)
            buffered_bytes += len(encoded)
            capture_telemetry(result, payload, event_name)
            if (
                len(buffered) > CODEX_RESPONSES_PRECOMMIT_MAX_EVENTS
                or buffered_bytes > CODEX_RESPONSES_PRECOMMIT_MAX_BYTES
                or event_name not in {"response.created", "response.in_progress"}
            ):
                break
    except Exception:
        opened.response.release()
        return False

    async def stream() -> AsyncIterator[bytes]:
        try:
            for chunk in buffered:
                yield chunk
            async for event, data in events:
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except Exception:
                    result._native_tool_invalid = True
                    continue
                if not isinstance(payload, dict):
                    result._native_tool_invalid = True
                    continue
                event_name = response_event_name(event, payload)
                capture_telemetry(result, payload, event_name)
                current = response_object(payload)
                if event_name in TERMINAL_CODEX_EVENTS:
                    status = str(current.get("status") or "") if current is not None else ""
                    result.completed = status == "completed"
                    if status in {"failed", "cancelled", "incomplete"}:
                        error = current.get("error") if current is not None else None
                        result.error = router._error_code(error) if isinstance(error, dict) else f"response_{status}"
                elif event_name == "error":
                    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
                    result.error = router._error_code(error)
                yield sse(annotate_realm(payload, realm), event_name)
        except Exception as exc:
            error = router._stream_transport_error(exc)
            result.error = router._error_code(error)
            router._mark_streaming_error(slot, error)
            yield sse({"error": error}, "error")
        finally:
            opened.response.release()

    result.stream = stream()
    return True


async def execute_codex_responses(
    router: CodexRouter,
    request: dict[str, Any],
    model_id: str,
    wants_stream: bool,
    session_id: str | None,
    prompt_cache_key: str | None,
) -> CodexCallResult:
    model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    upstream_cache_key = resolve_prompt_cache_key(request, session_id, prompt_cache_key)
    affinity_key = str(request.get("spartan_chain_id") or "").strip() or resolve_affinity_key(request, session_id, prompt_cache_key)
    body = build_codex_responses_body(request, model_name, upstream_cache_key)
    max_attempts = min(CODEX_RESPONSES_MAX_ATTEMPTS, max(1, len(router.slots)))
    excluded_slots: set[int] = set()
    excluded_accounts: set[str] = set()
    last_result: CodexCallResult | None = None

    for _attempt in range(max_attempts):
        chosen = await router.choose_slot(affinity_key, excluded_slots, excluded_accounts)
        if not chosen:
            break
        slot, auth = chosen
        slot.in_flight += 1
        slot.last_selected_at = time.time()
        opened = await open_response(router, body, auth)
        if isinstance(opened, CodexCallResult):
            slot.in_flight = max(0, slot.in_flight - 1)
            opened.slot_index = slot.index
            opened.account_key = auth.account_key
            last_result = opened
            text = opened.body.decode("utf-8", errors="replace") if isinstance(opened.body, bytes) else stable_json(opened.body)
            if not should_retry(opened.status, text):
                return opened
            excluded_slots.add(slot.index)
            if auth.account_key:
                excluded_accounts.add(auth.account_key)
            if opened.status == 401:
                router._quarantine_auth_failure(slot)
            elif opened.status == 429:
                router.mark_rate_limited(slot, text)
            else:
                router.mark_transient(slot)
            continue

        result = CodexCallResult(
            status=200,
            slot_index=slot.index,
            account_key=auth.account_key,
            input_tokens=token_count_estimate(request),
            upstream_response_time_ms=opened.response_time_ms,
        )
        if wants_stream:
            committed = await prepare_stream(router, opened, result, slot, CODEX_RESPONSES_REALM)
            if committed:
                source = result.stream

                async def guarded_stream() -> AsyncIterator[bytes]:
                    try:
                        if source is not None:
                            async for chunk in source:
                                yield chunk
                    finally:
                        if result.completed and not result.error and affinity_key:
                            router._remember_lease(affinity_key, slot)
                        slot.in_flight = max(0, slot.in_flight - 1)

                result.stream = guarded_stream()
                return result
            slot.in_flight = max(0, slot.in_flight - 1)
            excluded_slots.add(slot.index)
            if auth.account_key:
                excluded_accounts.add(auth.account_key)
            router.mark_transient(slot)
            last_result = CodexCallResult(
                status=502,
                body={"error": {"message": "Codex Responses stream ended before commit", "type": "upstream_error", "code": "codex_stream_error"}},
                error="codex_stream_error",
            )
            continue

        collected = await collect_response(router, opened, slot, CODEX_RESPONSES_REALM)
        slot.in_flight = max(0, slot.in_flight - 1)
        collected.slot_index = slot.index
        collected.account_key = auth.account_key
        if collected.status < 400 or not should_retry(collected.status, stable_json(collected.body)):
            if collected.completed and not collected.error and affinity_key:
                router._remember_lease(affinity_key, slot)
            return collected
        excluded_slots.add(slot.index)
        if auth.account_key:
            excluded_accounts.add(auth.account_key)
        error_text = stable_json(collected.body)
        if collected.status == 401:
            router._quarantine_auth_failure(slot)
        elif collected.status == 429:
            router.mark_rate_limited(slot, error_text)
        else:
            router.mark_transient(slot)
        last_result = collected

    if last_result is not None:
        return last_result
    return await router._unavailable_result()
