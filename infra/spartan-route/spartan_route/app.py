from __future__ import annotations

import base64
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, FormData, web
from aiohttp.client_exceptions import ClientConnectionResetError
from aiohttp.web_request import FileField

from .codex import CodexCallResult, CodexRouter, build_content_prompt_cache_key, resolve_prompt_cache_key, session_id_from_request, sse
from .config import ModelEntry, SpartanRouteConfig, load_config
from .telemetry import RoutingLogEntry, TelemetryQueue, TelemetryStore
from .util import json_error, now_iso, sanitize_preview, sha, stable_json, text_content, token_count_estimate


@dataclass(frozen=True)
class RoutedRequest:
    original_model: str
    routed_model: str
    actual_model: str
    provider: str
    tier: str
    reason: str
    entry: ModelEntry


@dataclass
class ExecutionResult:
    status: int
    body: dict[str, Any] | bytes | None = None
    headers: dict[str, str] | None = None
    stream: AsyncIterator[bytes] | None = None
    slot_index: int | None = None
    account_key: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    usage_seen: bool = False
    usage_event_type: str | None = None


@dataclass
class SpartanRouteState:
    config: SpartanRouteConfig
    telemetry: TelemetryStore
    queue: TelemetryQueue
    session: ClientSession | None = None
    codex: CodexRouter | None = None
    recent_session_traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_guard: dict[str, dict[str, Any]] = field(default_factory=dict)


STATE_KEY = web.AppKey("spartan_route_state", SpartanRouteState)
CACHE_GUARD_EXPECTED_MIN_TOKENS = 1024
CACHE_GUARD_LOW_MIN_TOKENS = 20_000
CACHE_GUARD_LOW_RATIO = 0.20
CACHE_GUARD_CONSECUTIVE_LOW = 2


def provider_for_model(model_id: str) -> str:
    if model_id.startswith("codex/"):
        return "codex"
    if model_id.startswith("openai/") or model_id.startswith("gpt-") or model_id.startswith("o"):
        return "openai"
    if model_id.startswith("openrouter/"):
        return "openrouter"
    if model_id.startswith("anthropic/"):
        return "anthropic"
    if model_id.startswith("google/"):
        return "google"
    if model_id.startswith("deepseek/"):
        return "deepseek"
    if model_id.startswith("x-ai/"):
        return "x-ai"
    if model_id.startswith("stepfun/"):
        return "stepfun"
    if model_id.startswith("ollama/"):
        return "ollama"
    return "openai"


def normalize_model_id(model_id: Any) -> str:
    model = str(model_id or "clawroute/auto")
    return model.removeprefix("custom-1/")


def classify_request(body: dict[str, Any]) -> tuple[str, str, float]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    input_tokens = token_count_estimate(body)
    has_tools = bool(body.get("tools"))
    text = "\n".join(text_content(message.get("content")) for message in messages if isinstance(message, dict))
    lower = text.lower()
    frontier_markers = ("architecture", "security", "migration", "refactor", "debug", "root cause")
    if has_tools or input_tokens >= 48_000 or any(marker in lower for marker in frontier_markers):
        return "complex", "tools, large context, or engineering-heavy prompt", 0.78
    if input_tokens <= 64 and len(messages) <= 2:
        return "heartbeat", "small health-check-sized request", 0.8
    if input_tokens <= 4_000:
        return "simple", "small request", 0.72
    return "moderate", "medium context request", 0.7


def route_request(config: SpartanRouteConfig, body: dict[str, Any]) -> RoutedRequest:
    original = str(body.get("model") or "clawroute/auto")
    normalized = normalize_model_id(original)
    tier = "passthrough"
    reason = "direct model request"

    if normalized in {"clawroute/auto", "auto"}:
        tier, reason, _confidence = classify_request(body)
        routed = config.tier_models.get(tier, {}).get("primary") or config.baseline_model
    else:
        routed = normalized

    if not config.enabled:
        actual = config.baseline_model if normalized in {"clawroute/auto", "auto"} else normalized
        reason = "routing disabled"
    elif config.dry_run:
        actual = config.baseline_model if normalized in {"clawroute/auto", "auto"} else normalized
        reason = f"dry-run decision: {routed}"
    else:
        actual = routed

    entry = config.models.get(actual) or ModelEntry(actual, provider_for_model(actual))
    return RoutedRequest(
        original_model=original,
        routed_model=routed,
        actual_model=actual,
        provider=entry.provider,
        tier=tier,
        reason=reason,
        entry=entry,
    )


def provider_base_url(provider: str) -> str | None:
    if provider == "openai":
        return (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if provider == "openrouter":
        return (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    if provider == "ollama":
        return (os.environ.get("OLLAMA_ENDPOINT") or "http://ollama:11434").rstrip("/") + "/v1"
    return None


def upstream_model_id(model_id: str, provider: str) -> str:
    prefixes = {
        "openai": "openai/",
        "openrouter": "openrouter/",
        "ollama": "ollama/",
    }
    prefix = prefixes.get(provider)
    return model_id[len(prefix):] if prefix and model_id.startswith(prefix) else model_id


def extract_usage(payload: Any) -> tuple[int, int, int]:
    if not isinstance(payload, dict):
        return 0, 0, 0
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    output = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return prompt, cached, output


def apply_usage(result: ExecutionResult, payload: Any) -> None:
    prompt, cached, output = extract_usage(payload)
    result.input_tokens = prompt or result.input_tokens
    result.cached_input_tokens = cached or result.cached_input_tokens
    result.output_tokens = output or result.output_tokens


def parse_json_bytes(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


async def iter_sse_payloads(source: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, str, bytes]]:
    buffer = ""
    async for chunk in source:
        text = chunk.decode("utf-8", errors="replace")
        buffer += text
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = None
            lines: list[str] = []
            for raw in block.splitlines():
                line = raw.strip("\r")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    lines.append(line[5:].lstrip())
            data = "\n".join(lines)
            if data:
                yield event, data, f"{block}\n\n".encode("utf-8")
    if buffer.strip():
        event = None
        lines = []
        for raw in buffer.splitlines():
            line = raw.strip("\r")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                lines.append(line[5:].lstrip())
        data = "\n".join(lines)
        if data:
            yield event, data, buffer.encode("utf-8")


async def provider_stream(response: Any, result: ExecutionResult) -> AsyncIterator[bytes]:
    try:
        async for event, data, raw in iter_sse_payloads(response.content.iter_chunked(8192)):
            del event
            if data and data != "[DONE]":
                parsed = parse_json_bytes(data.encode("utf-8"))
                if isinstance(parsed, dict):
                    apply_usage(result, parsed)
                    if parsed.get("error") and not result.error:
                        result.error = stable_json(parsed.get("error"))
            yield raw
    finally:
        response.release()


async def execute_provider_chat(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    wants_stream: bool,
) -> ExecutionResult:
    provider = route.provider
    base_url = provider_base_url(provider)
    if not base_url:
        message = f"Provider '{provider}' is not implemented in spartan-route v1."
        return ExecutionResult(503, body=json_error(message, 503, "provider_not_supported", "invalid_request_error")[0], error=message)

    api_key = state.config.api_key(provider)
    if provider != "ollama" and not api_key:
        message = f"Missing API key for provider '{provider}'."
        return ExecutionResult(503, body=json_error(message, 503, "provider_key_missing", "authentication_error")[0], error=message)

    session = state.session
    if session is None:
        return ExecutionResult(503, body=json_error("HTTP session is not ready", 503, "session_unavailable")[0], error="session_unavailable")

    upstream_body = dict(body)
    upstream_body["model"] = upstream_model_id(route.actual_model, provider)
    if provider != "openai":
        upstream_body.pop("prompt_cache_key", None)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost")
        headers["X-Title"] = os.environ.get("OPENROUTER_APP_TITLE", "Spartan Gate")

    timeout_ms = int(os.environ.get("SPARTAN_ROUTE_PROVIDER_TIMEOUT_MS") or "180000")
    try:
        response = await session.post(
            f"{base_url}/chat/completions",
            json=upstream_body,
            headers=headers,
            timeout=ClientTimeout(total=timeout_ms / 1000),
        )
    except Exception as exc:
        message = str(exc)
        return ExecutionResult(502, body=json_error(message, 502, "provider_request_failed", "upstream_error")[0], error=message)

    content_type = response.headers.get("Content-Type", "application/json")
    if wants_stream and 200 <= response.status < 300:
        result = ExecutionResult(response.status, headers={"Content-Type": "text/event-stream"})
        result.stream = provider_stream(response, result)
        return result

    data = await response.read()
    response.release()
    result = ExecutionResult(response.status, body=data, headers={"Content-Type": content_type})
    parsed = parse_json_bytes(data)
    apply_usage(result, parsed)
    if response.status >= 400:
        result.error = sanitize_preview(data.decode("utf-8", errors="replace"))
    return result


async def execute_chat(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    wants_stream: bool,
    session_id: str | None,
    prompt_cache_key: str | None,
    request_id: str,
) -> ExecutionResult:
    if route.provider == "codex":
        if state.codex is None:
            return ExecutionResult(503, body=json_error("Codex router is not ready", 503, "codex_unavailable")[0], error="codex_unavailable")
        codex_result: CodexCallResult = await state.codex.execute_chat(
            body,
            route.actual_model,
            wants_stream,
            session_id,
            prompt_cache_key,
            request_id,
        )
        execution = ExecutionResult(
            status=codex_result.status,
            body=codex_result.body,
            headers=codex_result.headers,
            slot_index=codex_result.slot_index,
            account_key=codex_result.account_key,
            input_tokens=codex_result.input_tokens,
            cached_input_tokens=codex_result.cached_input_tokens,
            output_tokens=codex_result.output_tokens,
            error=codex_result.error,
            usage_seen=codex_result.usage_seen,
            usage_event_type=codex_result.usage_event_type,
        )
        if codex_result.stream is not None:
            execution.stream = sync_codex_stream_result(codex_result, execution)
        return execution
    return await execute_provider_chat(state, body, route, wants_stream)


async def sync_codex_stream_result(source_result: CodexCallResult, target: ExecutionResult) -> AsyncIterator[bytes]:
    source = source_result.stream
    try:
        if source is not None:
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
        target.input_tokens = source_result.input_tokens
        target.cached_input_tokens = source_result.cached_input_tokens
        target.output_tokens = source_result.output_tokens
        target.error = source_result.error
        target.usage_seen = source_result.usage_seen
        target.usage_event_type = source_result.usage_event_type


def request_prompt_cache_key(body: dict[str, Any]) -> str | None:
    value = body.get("prompt_cache_key") or body.get("user")
    return value.strip() if isinstance(value, str) and value.strip() else None


def request_prompt_cache_key_source(body: dict[str, Any], session_id: str | None) -> str:
    prompt_cache_key = body.get("prompt_cache_key")
    if isinstance(prompt_cache_key, str) and prompt_cache_key.strip():
        return "prompt_cache_key"
    user = body.get("user")
    if isinstance(user, str) and user.strip():
        return "user"
    if build_content_prompt_cache_key(body):
        return "content_prompt_cache_key"
    if session_id:
        return "session_id"
    return "none"


def resolve_turn_id(body: dict[str, Any], session_id: str | None, request_id: str) -> str:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    for key in ("turn_id", "turnId", "request_id", "requestId"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    last_user = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            last_user = text_content(message.get("content"))
            break
    if session_id and last_user:
        return sha(f"{session_id}\0{last_user[-4000:]}", 16)
    return request_id


def tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str):
        return tool["function"]["name"]
    return str(tool.get("name") or tool.get("type") or "")


def tool_schema_fingerprint(tools: Any) -> str | None:
    if not isinstance(tools, list) or not tools:
        return None
    shaped = [tool for tool in tools if isinstance(tool, dict)]
    shaped.sort(key=tool_name)
    return sha(stable_json(shaped), 16)


def request_shape_snapshot(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    message_fingerprints: list[str] = []
    message_roles: list[str | None] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_roles.append(message.get("role") if isinstance(message.get("role"), str) else None)
        message_fingerprints.append(sha(stable_json(message), 16))
    tool_fp = tool_schema_fingerprint(tools)
    request_fp = sha(stable_json({"messages": message_fingerprints, "toolSchema": tool_fp}), 16)
    return {
        "requestFingerprint": request_fp,
        "messageFingerprints": message_fingerprints,
        "messageRoles": message_roles,
        "toolSchemaFingerprint": tool_fp,
    }


def compare_session_trace(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"comparison": "baseline", "commonPrefixMessages": 0, "previousMessageCount": 0}
    current_messages = current.get("messageFingerprints") if isinstance(current.get("messageFingerprints"), list) else []
    previous_messages = previous.get("messageFingerprints") if isinstance(previous.get("messageFingerprints"), list) else []
    common = 0
    for left, right in zip(previous_messages, current_messages):
        if left != right:
            break
        common += 1
    if current.get("toolSchemaFingerprint") != previous.get("toolSchemaFingerprint"):
        comparison = "tool_schema_changed"
    elif current.get("requestFingerprint") == previous.get("requestFingerprint"):
        comparison = "retry"
    elif common == len(previous_messages) and len(current_messages) >= len(previous_messages):
        comparison = "prefix"
    else:
        comparison = "history_rewrite"
    return {
        "comparison": comparison,
        "commonPrefixMessages": common,
        "previousMessageCount": len(previous_messages),
    }


def cache_guard_key(
    actual_model: str,
    cache_key_hash: str | None,
    slot_index: int | None,
    account_key: str | None,
    tool_schema_fingerprint: str | None,
) -> str:
    return stable_json({
        "model": actual_model,
        "cache": cache_key_hash,
        "slot": slot_index,
        "account": account_key,
        "tools": tool_schema_fingerprint,
    })


def update_cache_guard(
    state: SpartanRouteState,
    route: RoutedRequest,
    result: ExecutionResult,
    input_tokens: int,
    cache_key_hash: str | None,
    tool_schema_fingerprint: str | None,
    expected_cache_hit: bool,
    cache_hit_ratio: float,
) -> bool:
    if not cache_key_hash:
        return False
    key = cache_guard_key(
        route.actual_model,
        cache_key_hash,
        result.slot_index,
        result.account_key,
        tool_schema_fingerprint,
    )
    entry = state.cache_guard.setdefault(key, {"consecutiveLow": 0})
    if not expected_cache_hit or not result.usage_seen or input_tokens < CACHE_GUARD_LOW_MIN_TOKENS:
        entry["consecutiveLow"] = 0
        return False
    if cache_hit_ratio < CACHE_GUARD_LOW_RATIO:
        entry["consecutiveLow"] = int(entry.get("consecutiveLow") or 0) + 1
    else:
        entry["consecutiveLow"] = 0
    if len(state.cache_guard) > 512:
        oldest_key = next(iter(state.cache_guard))
        state.cache_guard.pop(oldest_key, None)
    return int(entry.get("consecutiveLow") or 0) >= CACHE_GUARD_CONSECUTIVE_LOW


def estimate_shape_sizes(body: dict[str, Any]) -> tuple[int, int]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    return len(stable_json(messages)), len(stable_json(tools))


def build_request_shape_diagnostics(snapshot: dict[str, Any]) -> dict[str, Any]:
    message_fps = snapshot.get("messageFingerprints") if isinstance(snapshot.get("messageFingerprints"), list) else []
    return {
        "request": snapshot.get("requestFingerprint"),
        "messagesHead": message_fps[:8],
        "messagesTail": message_fps[-8:] if len(message_fps) > 8 else [],
        "toolSchema": snapshot.get("toolSchemaFingerprint"),
    }


def log_request(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    result: ExecutionResult,
    started: float,
    request_id: str,
    session_id: str | None,
    turn_id: str,
    api_kind: str,
) -> None:
    input_tokens = result.input_tokens or token_count_estimate(body)
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    explicit_cache_key = request_prompt_cache_key(body)
    upstream_cache_key = resolve_prompt_cache_key(body, session_id, explicit_cache_key)
    cache_key_hash = sha(upstream_cache_key, 12) if upstream_cache_key else None
    cache_key_source = request_prompt_cache_key_source(body, session_id)
    message_chars, tool_schema_chars = estimate_shape_sizes(body)
    shape_snapshot = request_shape_snapshot(body)
    tool_schema_fp = shape_snapshot.get("toolSchemaFingerprint")
    trace_key = f"{session_id or 'none'}:{cache_key_hash or 'none'}"
    previous_trace = state.recent_session_traces.get(trace_key)
    same_session_trace = compare_session_trace(shape_snapshot, previous_trace)
    state.recent_session_traces[trace_key] = shape_snapshot
    if len(state.recent_session_traces) > 512:
        oldest_key = next(iter(state.recent_session_traces))
        state.recent_session_traces.pop(oldest_key, None)
    comparison = same_session_trace.get("comparison")
    expected_cache_hit = bool(upstream_cache_key and input_tokens >= CACHE_GUARD_EXPECTED_MIN_TOKENS and comparison in {"prefix", "retry"})
    cache_hit_ratio = (result.cached_input_tokens / input_tokens) if input_tokens > 0 else 0.0
    cache_low = update_cache_guard(
        state,
        route,
        result,
        input_tokens,
        cache_key_hash,
        tool_schema_fp if isinstance(tool_schema_fp, str) else None,
        expected_cache_hit,
        cache_hit_ratio,
    )
    context = {
        "routeReason": route.reason,
        "provider": route.provider,
        "status": result.status,
        "msg_count": len(messages),
        "has_system": any(isinstance(message, dict) and message.get("role") == "system" for message in messages),
        "tool_count": len(tools),
        "last_role": messages[-1].get("role") if messages and isinstance(messages[-1], dict) else None,
        "cache_key_present": bool(upstream_cache_key),
        "cache_key_hash": cache_key_hash,
        "cache_key_source": cache_key_source,
        "usage_seen": result.usage_seen,
        "usage_event_type": result.usage_event_type,
        "expected_cache_hit": expected_cache_hit,
        "cache_hit_ratio": round(cache_hit_ratio, 4),
        "cache_low": cache_low,
        "request_api_kind": api_kind,
        "requested_reasoning_effort": body.get("reasoning_effort") if isinstance(body.get("reasoning_effort"), str) else None,
        "message_chars": message_chars,
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_rough_tokens": max(0, tool_schema_chars // 4),
        "tool_schema_fingerprint": tool_schema_fp,
        "request_trace": {
            "version": 1,
            "requestFingerprint": shape_snapshot.get("requestFingerprint"),
            "messageCount": len(shape_snapshot.get("messageFingerprints") or []),
        },
        "same_session_cache_trace": same_session_trace,
        "request_shape_hashes": build_request_shape_diagnostics(shape_snapshot),
    }
    state.queue.log(
        RoutingLogEntry(
            timestamp=now_iso(),
            original_model=route.original_model,
            routed_model=route.routed_model,
            actual_model=route.actual_model,
            tier=route.tier,
            classification_reason=route.reason,
            confidence=classify_request(body)[2] if route.tier != "passthrough" else 0.0,
            input_tokens=input_tokens,
            cached_input_tokens=result.cached_input_tokens,
            output_tokens=result.output_tokens,
            response_time_ms=max(0, int((time.time() - started) * 1000)),
            had_tool_calls=bool(body.get("tools")),
            is_dry_run=state.config.dry_run,
            session_id=session_id,
            error=result.error if result.status >= 400 or result.error else None,
            prompt_preview=sanitize_preview(stable_json(body.get("messages"))) if state.config.log_content else None,
            context_info=stable_json(context),
            request_api_kind=api_kind,
            requested_reasoning_effort=body.get("reasoning_effort") if isinstance(body.get("reasoning_effort"), str) else None,
            selected_codex_slot_index=result.slot_index,
            selected_codex_account_key=result.account_key,
            request_id=request_id,
            turn_id=turn_id,
        )
    )


async def write_execution_response(
    request: web.Request,
    result: ExecutionResult,
    on_done: Callable[[], None] | None = None,
    stream: AsyncIterator[bytes] | None = None,
    content_type: str | None = None,
) -> web.StreamResponse:
    source = stream or result.stream
    if source is not None:
        response = web.StreamResponse(
            status=result.status,
            headers={
                "Content-Type": content_type or (result.headers or {}).get("Content-Type", "text/event-stream"),
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        client_disconnected = False
        try:
            async for chunk in source:
                try:
                    await response.write(chunk)
                except (BrokenPipeError, ClientConnectionResetError, ConnectionResetError, RuntimeError):
                    client_disconnected = True
                    close = getattr(source, "aclose", None)
                    if callable(close):
                        await close()
                    break
        finally:
            if on_done:
                on_done()
            if not client_disconnected:
                try:
                    await response.write_eof()
                except (BrokenPipeError, ClientConnectionResetError, ConnectionResetError, RuntimeError):
                    pass
        return response

    if on_done:
        on_done()
    headers = result.headers or {}
    body = result.body if result.body is not None else {}
    if isinstance(body, (bytes, bytearray)):
        return web.Response(status=result.status, body=body, headers=headers)
    return web.json_response(body, status=result.status, headers=headers)


def responses_input_to_chat_messages(input_value: Any) -> list[dict[str, Any]]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if not isinstance(input_value, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in input_value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role in {"system", "developer"}:
            messages.append({"role": "system", "content": item.get("content", "")})
        elif role in {"user", "assistant"}:
            messages.append({"role": role, "content": item.get("content", "")})
        elif item.get("type") == "message":
            messages.append({"role": item.get("role") or "assistant", "content": item.get("content", "")})
        elif item.get("type") == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"), "content": item.get("output", "")})
    return messages


def responses_body_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": body.get("model"),
        "messages": responses_input_to_chat_messages(body.get("input")),
        "stream": bool(body.get("stream")),
    }
    for source, target in (
        ("tools", "tools"),
        ("tool_choice", "tool_choice"),
        ("top_p", "top_p"),
        ("reasoning_effort", "reasoning_effort"),
        ("prompt_cache_key", "prompt_cache_key"),
        ("user", "user"),
        ("metadata", "metadata"),
    ):
        if source in body:
            out[target] = body[source]
    return out


def chat_to_responses_body(body: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = text_content(message.get("content")) if isinstance(message, dict) else ""
    response_id = str(body.get("id") or f"resp_{sha(str(time.time()), 12)}").replace("chatcmpl_", "resp_")
    output = []
    if content:
        output.append({
            "type": "message",
            "id": f"msg_{sha(content, 12)}",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
        })
    return {
        "id": response_id,
        "object": "response",
        "created_at": body.get("created") or int(time.time()),
        "status": "completed" if not body.get("error") else "failed",
        "model": original.get("model") or body.get("model"),
        "output": output,
        "usage": body.get("usage"),
        "error": body.get("error"),
    }


async def chat_sse_to_responses_sse(
    source: AsyncIterator[bytes],
    response_id: str,
    model: str,
) -> AsyncIterator[bytes]:
    created = int(time.time())
    yield sse({"id": response_id, "object": "response", "created_at": created, "model": model, "status": "in_progress"}, "response.created")
    async for _event, data, _raw in iter_sse_payloads(source):
        if data == "[DONE]":
            continue
        parsed = parse_json_bytes(data.encode("utf-8"))
        if not isinstance(parsed, dict):
            continue
        if parsed.get("error"):
            yield sse({"error": parsed["error"]}, "error")
            continue
        for choice in parsed.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if isinstance(delta.get("content"), str):
                yield sse({"type": "response.output_text.delta", "delta": delta["content"], "response_id": response_id}, "response.output_text.delta")
            if isinstance(delta.get("reasoning_content"), str):
                yield sse({"type": "response.reasoning_summary_text.delta", "delta": delta["reasoning_content"], "response_id": response_id}, "response.reasoning_summary_text.delta")
            if choice.get("finish_reason"):
                yield sse({"type": "response.output_text.done", "response_id": response_id}, "response.output_text.done")
    yield sse({"id": response_id, "object": "response", "created_at": created, "model": model, "status": "completed", "output": []}, "response.completed")


def response_json_error(message: str, status: int, code: str, error_type: str = "server_error") -> web.Response:
    body, http_status = json_error(message, status, code, error_type)
    return web.json_response(body, status=http_status)


async def read_json_object(request: web.Request) -> dict[str, Any] | web.Response:
    try:
        body = await request.json()
    except Exception:
        return response_json_error("Invalid JSON body", 400, "invalid_json", "invalid_request_error")
    if not isinstance(body, dict):
        return response_json_error("JSON body must be an object", 400, "invalid_request", "invalid_request_error")
    return body


def get_state(request: web.Request) -> SpartanRouteState:
    return request.app[STATE_KEY]


async def chat_completions(request: web.Request) -> web.StreamResponse:
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    if not body.get("model"):
        body["model"] = "clawroute/auto"
    state = get_state(request)
    request_id = uuid.uuid4().hex[:12]
    started = time.time()
    prompt_cache_key = request_prompt_cache_key(body)
    session_id = session_id_from_request(body, prompt_cache_key)
    turn_id = resolve_turn_id(body, session_id, request_id)
    route = route_request(state.config, body)
    result = await execute_chat(state, body, route, bool(body.get("stream")), session_id, prompt_cache_key, request_id)
    return await write_execution_response(
        request,
        result,
        on_done=lambda: log_request(state, body, route, result, started, request_id, session_id, turn_id, "chat_completions"),
    )


async def responses(request: web.Request) -> web.StreamResponse:
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    if not body.get("model"):
        return response_json_error("model is required", 400, "missing_model", "invalid_request_error")
    if "input" not in body:
        return response_json_error("input is required", 400, "missing_input", "invalid_request_error")

    state = get_state(request)
    chat_body = responses_body_to_chat(body)
    request_id = uuid.uuid4().hex[:12]
    started = time.time()
    prompt_cache_key = request_prompt_cache_key(chat_body)
    session_id = session_id_from_request(chat_body, prompt_cache_key)
    turn_id = resolve_turn_id(chat_body, session_id, request_id)
    route = route_request(state.config, chat_body)
    wants_stream = bool(body.get("stream"))
    result = await execute_chat(state, chat_body, route, wants_stream, session_id, prompt_cache_key, request_id)

    def done() -> None:
        log_request(state, chat_body, route, result, started, request_id, session_id, turn_id, "responses")

    if wants_stream and result.stream is not None:
        transformed = chat_sse_to_responses_sse(result.stream, f"resp_{request_id}", route.actual_model)
        return await write_execution_response(request, result, on_done=done, stream=transformed, content_type="text/event-stream")

    if isinstance(result.body, (bytes, bytearray)):
        parsed = parse_json_bytes(bytes(result.body))
    else:
        parsed = result.body
    if isinstance(parsed, dict) and result.status < 400:
        result.body = chat_to_responses_body(parsed, body)
    return await write_execution_response(request, result, on_done=done)


async def proxy_openai_json(state: SpartanRouteState, endpoint: str, body: dict[str, Any]) -> ExecutionResult:
    api_key = state.config.api_key("openai")
    if not api_key:
        return ExecutionResult(503, body=json_error("Missing OPENAI_API_KEY", 503, "provider_key_missing", "authentication_error")[0], error="provider_key_missing")
    session = state.session
    if session is None:
        return ExecutionResult(503, body=json_error("HTTP session is not ready", 503, "session_unavailable")[0], error="session_unavailable")
    payload = dict(body)
    if isinstance(payload.get("model"), str) and payload["model"].startswith("openai/"):
        payload["model"] = payload["model"][7:]
    async with session.post(
        f"{provider_base_url('openai')}/{endpoint.lstrip('/')}",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=ClientTimeout(total=180),
    ) as response:
        data = await response.read()
        return ExecutionResult(response.status, body=data, headers={"Content-Type": response.headers.get("Content-Type", "application/json")}, error=sanitize_preview(data.decode("utf-8", "replace")) if response.status >= 400 else None)


async def image_generations(request: web.Request) -> web.StreamResponse:
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    state = get_state(request)
    model = normalize_model_id(body.get("model") or "gpt-image-2")
    if model.startswith("codex/") or not state.config.api_key("openai"):
        if state.codex is None:
            return response_json_error("Codex router is not ready", 503, "codex_unavailable")
        result = state.codex.execute_image({**body, "model": model})
        codex_result = await result
        return await write_execution_response(request, ExecutionResult(codex_result.status, body=codex_result.body, headers=codex_result.headers, error=codex_result.error))
    result = await proxy_openai_json(state, "/images/generations", {**body, "model": model})
    return await write_execution_response(request, result)


def data_url_from_file(field: FileField) -> str:
    raw = field.file.read()
    media_type = field.content_type if field.content_type and field.content_type.startswith("image/") else "image/png"
    return f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"


async def proxy_openai_multipart(state: SpartanRouteState, endpoint: str, fields: Any) -> ExecutionResult:
    api_key = state.config.api_key("openai")
    if not api_key:
        return ExecutionResult(503, body=json_error("Missing OPENAI_API_KEY", 503, "provider_key_missing", "authentication_error")[0], error="provider_key_missing")
    session = state.session
    if session is None:
        return ExecutionResult(503, body=json_error("HTTP session is not ready", 503, "session_unavailable")[0], error="session_unavailable")
    form = FormData()
    for name, value in fields.items():
        if isinstance(value, FileField):
            value.file.seek(0)
            form.add_field(name, value.file.read(), filename=value.filename, content_type=value.content_type)
        else:
            text = str(value)
            if name == "model" and text.startswith("openai/"):
                text = text[7:]
            form.add_field(name, text)
    async with session.post(
        f"{provider_base_url('openai')}/{endpoint.lstrip('/')}",
        data=form,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=ClientTimeout(total=180),
    ) as response:
        data = await response.read()
        return ExecutionResult(response.status, body=data, headers={"Content-Type": response.headers.get("Content-Type", "application/json")}, error=sanitize_preview(data.decode("utf-8", "replace")) if response.status >= 400 else None)


async def image_edits(request: web.Request) -> web.StreamResponse:
    try:
        fields = await request.post()
    except Exception:
        return response_json_error("Invalid multipart body", 400, "invalid_multipart", "invalid_request_error")
    state = get_state(request)
    model = normalize_model_id(fields.get("model") or "gpt-image-2")
    if state.config.api_key("openai") and not model.startswith("codex/"):
        result = await proxy_openai_multipart(state, "/images/edits", fields)
        return await write_execution_response(request, result)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    if fields.get("mask") is not None:
        return response_json_error("Codex image edits in spartan-route v1 do not support mask uploads.", 400, "mask_not_supported", "invalid_request_error")
    images = []
    for name, value in fields.items():
        if name in {"image", "image[]"} and isinstance(value, FileField):
            value.file.seek(0)
            images.append(data_url_from_file(value))
    if not images:
        return response_json_error("At least one image file is required.", 400, "missing_image", "invalid_request_error")
    payload = {
        "prompt": str(fields.get("prompt") or ""),
        "size": str(fields.get("size") or "1024x1024"),
        "quality": str(fields.get("quality") or "medium"),
        "output_format": str(fields.get("output_format") or "png"),
        "background": str(fields.get("background") or "opaque"),
        "images": images,
    }
    if fields.get("output_compression") is not None:
        payload["output_compression"] = str(fields.get("output_compression"))
    codex_result = await state.codex.execute_image(payload)
    return await write_execution_response(request, ExecutionResult(codex_result.status, body=codex_result.body, headers=codex_result.headers, error=codex_result.error))


async def models(request: web.Request) -> web.Response:
    state = get_state(request)

    def model_payload(entry: ModelEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "object": "model",
            "created": 1_700_000_000,
            "owned_by": entry.provider,
            "max_context": entry.max_context,
            "context_length": entry.max_context,
            "max_model_len": entry.max_context,
            "tool_capable": entry.tool_capable,
            "multimodal": entry.multimodal,
        }

    auto = {
        "id": "clawroute/auto",
        "object": "model",
        "created": 1_700_000_000,
        "owned_by": "spartan-route",
        "max_context": 1_000_000,
        "context_length": 1_000_000,
        "max_model_len": 1_000_000,
        "tool_capable": True,
        "multimodal": True,
        "description": "Auto-routes with spartan-route v1 policy",
    }
    return web.json_response({"object": "list", "data": [auto, {**auto, "id": "custom-1/clawroute/auto"}, *[model_payload(m) for m in state.config.models.values() if m.enabled]]})


async def model_detail(request: web.Request) -> web.Response:
    state = get_state(request)
    model_id = normalize_model_id(request.match_info["model_id"])
    if model_id in {"clawroute/auto", "auto"}:
        return web.json_response({"id": request.match_info["model_id"], "object": "model", "owned_by": "spartan-route", "max_context": 1_000_000, "tool_capable": True, "multimodal": True})
    entry = state.config.models.get(model_id)
    if not entry:
        return response_json_error(f"The model '{request.match_info['model_id']}' does not exist", 404, "model_not_found", "invalid_request_error")
    return web.json_response({"id": entry.id, "object": "model", "owned_by": entry.provider, "max_context": entry.max_context, "tool_capable": entry.tool_capable, "multimodal": entry.multimodal})


async def api_models(request: web.Request) -> web.Response:
    state = get_state(request)
    return web.json_response({
        "models": [
            {
                "id": entry.id,
                "provider": entry.provider,
                "maxContext": entry.max_context,
                "inputCostPer1M": entry.input_cost_per_1m,
                "outputCostPer1M": entry.output_cost_per_1m,
                "toolCapable": entry.tool_capable,
                "multimodal": entry.multimodal,
                "enabled": entry.enabled,
            }
            for entry in state.config.models.values()
        ]
    })


async def health(request: web.Request) -> web.Response:
    state = get_state(request)
    return web.json_response({
        "ok": True,
        "service": "spartan-route",
        "version": "0.1.0",
        "enabled": state.config.enabled,
        "codexSlots": state.codex.slot_status() if state.codex else [],
    })


async def stats(request: web.Request) -> web.Response:
    state = get_state(request)
    rows = state.telemetry.daily_cache_rows()
    totals = {
        "requests": sum(int(row["requests"] or 0) for row in rows),
        "inputTokens": sum(int(row["input_tokens"] or 0) for row in rows),
        "cachedInputTokens": sum(int(row["cached_input_tokens"] or 0) for row in rows),
        "errors": sum(int(row["errors"] or 0) for row in rows),
    }
    totals["cacheHitPercent"] = (totals["cachedInputTokens"] / totals["inputTokens"] * 100) if totals["inputTokens"] else 0
    return web.json_response({"service": "spartan-route", "totals": totals, "dailyCache": rows, "codexSlots": state.codex.slot_status() if state.codex else []})


async def api_routing_live(request: web.Request) -> web.Response:
    state = get_state(request)
    limit = int(request.query.get("limit") or "30")
    return web.json_response(state.telemetry.live_sessions(limit))


async def api_routing_turn(request: web.Request) -> web.Response:
    state = get_state(request)
    return web.json_response(state.telemetry.turn_requests(request.match_info["turn_id"]))


async def api_codex_cache_day(request: web.Request) -> web.Response:
    state = get_state(request)
    return web.json_response({"days": state.telemetry.daily_cache_rows()})


async def api_codex_usage(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    payload = await state.codex.usage_snapshot(force=request.query.get("force") == "true")
    payload["cacheDaily"] = state.telemetry.daily_cache_rows()
    payload["slots"] = state.codex.slot_status()
    return web.json_response(payload)


async def dashboard_file(request: web.Request) -> web.StreamResponse:
    state = get_state(request)
    name = "dashboard-codex.html" if request.path == "/dashboard-codex" else "dashboard.html"
    path = (state.config.web_dir / name).resolve()
    if not path.exists() or not path.is_file():
        return response_json_error(f"Dashboard file not found: {name}", 404, "dashboard_not_found")
    return web.FileResponse(path)


async def unsupported(request: web.Request) -> web.Response:
    del request
    return response_json_error("Endpoint is not supported by spartan-route v1.", 400, "unsupported_endpoint", "invalid_request_error")


@web.middleware
async def auth_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    state = get_state(request)
    token = state.config.token
    path = request.path
    if (
        not token
        or path == "/health"
        or path == "/v1/models"
        or path.startswith("/v1/models/")
        or request.method == "OPTIONS"
    ):
        return await handler(request)

    auth = request.headers.get("Authorization", "")
    candidate = ""
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
    elif request.method == "GET":
        candidate = request.query.get("token", "")
    if candidate and hmac.compare_digest(candidate, token):
        return await handler(request)
    return web.json_response({"error": {"message": "Unauthorized. Provide Bearer token in Authorization header.", "type": "authentication_error", "code": "unauthorized"}}, status=401)


async def startup(app: web.Application) -> None:
    state: SpartanRouteState = app[STATE_KEY]
    state.session = ClientSession(trust_env=True)
    state.codex = CodexRouter(state.session, state.telemetry)
    state.queue.start()


async def cleanup(app: web.Application) -> None:
    state: SpartanRouteState = app[STATE_KEY]
    await state.queue.stop()
    if state.session:
        await state.session.close()
    state.telemetry.close()


def create_app(config: SpartanRouteConfig | None = None, telemetry: TelemetryStore | None = None) -> web.Application:
    config = config or load_config()
    telemetry = telemetry or TelemetryStore(config.db_path)
    app = web.Application(middlewares=[auth_middleware], client_max_size=64 * 1024 * 1024)
    app[STATE_KEY] = SpartanRouteState(config=config, telemetry=telemetry, queue=TelemetryQueue(telemetry))
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", health)
    app.router.add_get("/stats", stats)
    app.router.add_get("/dashboard", dashboard_file)
    app.router.add_get("/dashboard-codex", dashboard_file)
    app.router.add_get("/api/models", api_models)
    app.router.add_get("/api/routing/live", api_routing_live)
    app.router.add_get("/api/routing/turns/{turn_id}", api_routing_turn)
    app.router.add_get("/api/codex/cache/day", api_codex_cache_day)
    app.router.add_get("/api/codex/usage", api_codex_usage)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_post("/v1/responses", responses)
    app.router.add_post("/v1/images/generations", image_generations)
    app.router.add_post("/v1/images/edits", image_edits)
    app.router.add_get("/v1/models", models)
    app.router.add_get(r"/v1/models/{model_id:.+}", model_detail)
    app.router.add_post("/v1/completions", unsupported)
    app.router.add_post("/v1/embeddings", unsupported)
    return app


def main() -> None:
    config = load_config()
    app = create_app(config)
    if not config.token:
        print("Warning: CLAWROUTE_TOKEN is not set; spartan-route accepts local requests without bearer auth.", flush=True)
    web.run_app(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
