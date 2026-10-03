from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

from aiohttp import ClientSession, ClientTimeout, FormData, web
from aiohttp.client_exceptions import ClientConnectionResetError
from aiohttp.web_request import FileField

from .codex import CodexCallResult, CodexRouter, build_content_prompt_cache_key, resolve_prompt_cache_key, session_id_from_request, sse
from .codex_catalog import CodexModelDiscovery, SlotCatalog
from .config import AUTO_ROUTING_TIERS, ModelEntry, SpartanRouteConfig, load_config
from .native_responses import annotate_realm, capture_telemetry, execute_codex_responses, response_event_name, usage_values
from .native_cache import NativeCacheDiagnostics, Observation, request_diagnostics
from .cache_experiments import CacheExperiments, ProbeTicket, ProbeRejected, PROBE_HEADER
from .realtime import RealtimeConfig, RT_KEY, VOICE_PATHS, public_model as realtime_model, register as register_realtime, voice_dispatch
from .telemetry import RoutingLogEntry, TelemetryQueue, TelemetryStore
from .transport import create_client_session
from .sse import frames as parse_sse_frames
from .util import (
    json_error,
    now_iso,
    parse_bool,
    parse_float,
    parse_int,
    sanitize_preview,
    sha,
    stable_json,
    strip_metadata_preamble,
    text_content,
    token_count_estimate,
)


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
    reasoning_tokens: int | None = None
    error: str | None = None
    actual_tool_call_count: int | None = None
    actual_tool_names: list[str] | None = None
    tool_telemetry_complete: bool | None = None
    native_cache: dict[str, Any] | None = None
    stream_cleanup: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    _responses_telemetry: CodexCallResult | None = field(default=None, repr=False)
    _actual_tool_call_keys: set[str] = field(default_factory=set, repr=False)
    usage_seen: bool = False
    usage_event_type: str | None = None
    upstream_response_time_ms: int | None = None
    total_response_time_ms: int | None = None
    transport_details: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoutingPolicySnapshot:
    tier_models: Mapping[str, str]
    overrides: Mapping[str, str]
    source: str
    updated_at: str | None
    warning: str | None
    etag: str


@dataclass
class SpartanRouteState:
    config: SpartanRouteConfig
    telemetry: TelemetryStore
    queue: TelemetryQueue
    session: ClientSession | None = None
    control_session: ClientSession | None = None
    codex: CodexRouter | None = None
    codex_catalog: CodexModelDiscovery | None = None
    recent_session_traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    routing_policy: RoutingPolicySnapshot | None = None
    routing_policy_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cache_guard: dict[str, dict[str, Any]] = field(default_factory=dict)
    native_cache: NativeCacheDiagnostics = field(default_factory=NativeCacheDiagnostics)
    cache_experiments: CacheExperiments = field(init=False)

    def __post_init__(self) -> None:
        self.cache_experiments = CacheExperiments(Path(self.config.db_path).parent, self.native_cache.epoch, self.telemetry)


STATE_KEY = web.AppKey("spartan_route_state", SpartanRouteState)
CACHE_GUARD_EXPECTED_MIN_TOKENS = 1024
CACHE_GUARD_LOW_MIN_TOKENS = 20_000
CACHE_GUARD_LOW_RATIO = 0.20
CACHE_GUARD_CONSECUTIVE_LOW = 2
CACHE_LIKELY_MIN_PREFIX_MESSAGES = 50
CACHE_LIKELY_MIN_NON_TOOL_TOKENS = 50_000
CACHE_STALL_DIAGNOSTIC_MIN_TOKENS = 50_000
CACHE_STALL_DIAGNOSTIC_LOW_RATIO = 0.20
CACHE_STALL_ERROR_CODES = {"codex_stream_timeout", "codex_stream_error", "codex_empty_response"}
SPARTAN_CACHE_LINEAGE_FIELD = "spartan_cache_lineage"
SPARTAN_CACHE_LINEAGE_REASON_FIELD = "spartan_cache_lineage_reason"
SPARTAN_CHAIN_ID_FIELD = "spartan_chain_id"
SPARTAN_EXPECTED_REALM_FIELD = "spartan_expected_realm"
SPARTAN_BINDING_MAC_FIELD = "spartan_binding_mac"
SPARTAN_REASONING_REALMS = {"codex_subscription", "openai_api"}
SPARTAN_PRIVATE_REQUEST_FIELDS = {
    SPARTAN_CACHE_LINEAGE_FIELD,
    SPARTAN_CACHE_LINEAGE_REASON_FIELD,
    SPARTAN_CHAIN_ID_FIELD,
    SPARTAN_EXPECTED_REALM_FIELD,
    SPARTAN_BINDING_MAC_FIELD,
}
CODEX_ULTRA_REQUIRES_APP_SERVER_CODE = "codex_ultra_requires_app_server"
CODEX_MAX_REQUIRES_GPT56_CODE = "codex_max_requires_gpt_5_6"
CONTEXT_VARIANT_SUFFIX = "-282kcontext"
CONTEXT_VARIANT_TOKENS = 282_000


class InvalidContextVariant(ValueError):
    pass


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


IMPLEMENTED_CHAT_PROVIDERS = frozenset({"codex", "openai", "openrouter", "ollama"})
INVALID_ROUTING_OVERRIDE_WARNING = (
    "Stored routing override is invalid; file defaults are active."
)


def file_routing_models(config: SpartanRouteConfig) -> dict[str, str]:
    return {
        tier: config.tier_models[tier]["primary"]
        for tier in AUTO_ROUTING_TIERS
    }


def model_is_routing_eligible(config: SpartanRouteConfig, model_id: str) -> bool:
    entry = config.models.get(model_id)
    return bool(
        entry
        and entry.enabled
        and entry.tool_capable
        and entry.provider in IMPLEMENTED_CHAT_PROVIDERS
    )


def _routing_policy_etag(
    effective: Mapping[str, str],
    overrides: Mapping[str, str],
    updated_at: str | None,
) -> str:
    canonical = stable_json({
        "effective": dict(effective),
        "overrides": dict(overrides),
        "updatedAt": updated_at,
    })
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def make_routing_policy_snapshot(
    config: SpartanRouteConfig,
    overrides: Mapping[str, str] | None = None,
    *,
    updated_at: str | None = None,
    warning: str | None = None,
) -> RoutingPolicySnapshot:
    file_models = file_routing_models(config)
    override_models = dict(overrides or {})
    effective = override_models if override_models else file_models
    return RoutingPolicySnapshot(
        tier_models=MappingProxyType(dict(effective)),
        overrides=MappingProxyType(override_models),
        source="runtime_override" if override_models else "file",
        updated_at=updated_at,
        warning=warning,
        etag=_routing_policy_etag(effective, override_models, updated_at),
    )


def build_routing_policy_snapshot(
    config: SpartanRouteConfig,
    telemetry: TelemetryStore,
) -> RoutingPolicySnapshot:
    rows = telemetry.routing_model_override_rows()
    if not rows:
        return make_routing_policy_snapshot(config)

    try:
        tiers = {str(row["tier"]) for row in rows}
        if len(rows) != len(AUTO_ROUTING_TIERS) or tiers != set(AUTO_ROUTING_TIERS):
            raise ValueError("invalid tier set")
        overrides = {
            str(row["tier"]): str(row["model_id"])
            for row in rows
        }
        if not all(model_is_routing_eligible(config, model_id) for model_id in overrides.values()):
            raise ValueError("ineligible model")
        updated_at = max(str(row["updated_at"]) for row in rows)
    except (KeyError, TypeError, ValueError):
        return make_routing_policy_snapshot(
            config,
            warning=INVALID_ROUTING_OVERRIDE_WARNING,
        )
    return make_routing_policy_snapshot(
        config,
        overrides,
        updated_at=updated_at,
    )


def normalize_model_id(model_id: Any) -> str:
    model = str(model_id or "clawroute/auto")
    for prefix in ("custom-1/", "spartan-route/"):
        model = model.removeprefix(prefix)
    return model


def context_variant_eligible(entry: ModelEntry) -> bool:
    return entry.enabled and entry.tool_capable and entry.max_context >= CONTEXT_VARIANT_TOKENS


def context_variant_entry(config: SpartanRouteConfig, model_id: str) -> ModelEntry | None:
    if model_id in config.models or not model_id.endswith(CONTEXT_VARIANT_SUFFIX):
        return None
    base = config.models.get(model_id.removesuffix(CONTEXT_VARIANT_SUFFIX))
    if base is None or not context_variant_eligible(base):
        return None
    return replace(base, id=model_id, max_context=CONTEXT_VARIANT_TOKENS)


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


def route_request(
    config: SpartanRouteConfig,
    body: dict[str, Any],
    tier_primary_models: Mapping[str, str] | None = None,
) -> RoutedRequest:
    original = str(body.get("model") or "clawroute/auto")
    normalized = normalize_model_id(original)
    if normalized.endswith(CONTEXT_VARIANT_SUFFIX) and normalized not in config.models:
        if context_variant_entry(config, normalized) is None:
            raise InvalidContextVariant(normalized)
        normalized = normalized.removesuffix(CONTEXT_VARIANT_SUFFIX)
    tier = "passthrough"
    reason = "direct model request"

    if normalized in {"clawroute/auto", "auto"}:
        tier, reason, _confidence = classify_request(body)
        routed = (
            (tier_primary_models or {}).get(tier)
            or config.tier_models.get(tier, {}).get("primary")
            or config.baseline_model
        )
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
    source = payload.get("response") if isinstance(payload, dict) and isinstance(payload.get("response"), dict) else payload
    prompt, cached, output = extract_usage(source)
    result.input_tokens = prompt or result.input_tokens
    result.cached_input_tokens = cached or result.cached_input_tokens
    result.output_tokens = output or result.output_tokens
    if isinstance(source, dict):
        _prompt, _cached, _output, reasoning = usage_values(source.get("usage"))
        if reasoning is not None:
            result.reasoning_tokens = reasoning


def apply_tool_call_telemetry(result: ExecutionResult, payload: Any) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return
    if result.actual_tool_call_count is None:
        result.actual_tool_call_count = 0
        result.actual_tool_names = []
    names = set(result.actual_tool_names or [])
    for choice_position, choice in enumerate(payload["choices"]):
        if not isinstance(choice, dict):
            continue
        raw_choice_index = choice.get("index")
        choice_index = raw_choice_index if isinstance(raw_choice_index, (int, str)) else choice_position
        source = choice.get("message") if isinstance(choice.get("message"), dict) else choice.get("delta")
        if not isinstance(source, dict) or not isinstance(source.get("tool_calls"), list):
            continue
        for call_index, call in enumerate(source["tool_calls"]):
            if not isinstance(call, dict):
                continue
            key = f"{choice_index}:{call.get('index', call_index)}"
            result._actual_tool_call_keys.add(key)
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"]:
                names.add(function["name"])
    result.actual_tool_call_count = len(result._actual_tool_call_keys)
    result.actual_tool_names = sorted(names)


def parse_json_bytes(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


async def iter_sse_payloads(source: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, str, bytes]]:
    async for event, data, raw in parse_sse_frames(source):
        yield event, data, raw


async def provider_stream(response: Any, result: ExecutionResult) -> AsyncIterator[bytes]:
    try:
        async for event, data, raw in iter_sse_payloads(response.content.iter_chunked(8192)):
            del event
            if data and data != "[DONE]":
                parsed = parse_json_bytes(data.encode("utf-8"))
                if isinstance(parsed, dict):
                    apply_usage(result, parsed)
                    apply_tool_call_telemetry(result, parsed)
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
    prompt_cache_key: str | None,
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
    if provider == "openai":
        if prompt_cache_key:
            upstream_body["prompt_cache_key"] = prompt_cache_key
    else:
        upstream_body.pop("prompt_cache_key", None)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost")
        headers["X-Title"] = os.environ.get("OPENROUTER_APP_TITLE", "Spartan Gate")

    timeout_ms = int(os.environ.get("SPARTAN_ROUTE_PROVIDER_TIMEOUT_MS") or "180000")
    try:
        upstream_started = time.monotonic()
        response = await session.post(
            f"{base_url}/chat/completions",
            json=upstream_body,
            headers=headers,
            timeout=ClientTimeout(total=timeout_ms / 1000),
        )
        upstream_response_time_ms = max(0, int((time.monotonic() - upstream_started) * 1000))
    except Exception as exc:
        message = str(exc)
        return ExecutionResult(502, body=json_error(message, 502, "provider_request_failed", "upstream_error")[0], error=message)

    content_type = response.headers.get("Content-Type", "application/json")
    if wants_stream and 200 <= response.status < 300:
        result = ExecutionResult(
            response.status,
            headers={"Content-Type": "text/event-stream"},
            upstream_response_time_ms=upstream_response_time_ms,
        )
        result.stream = provider_stream(response, result)
        return result

    data = await response.read()
    response.release()
    result = ExecutionResult(
        response.status,
        body=data,
        headers={"Content-Type": content_type},
        upstream_response_time_ms=upstream_response_time_ms,
    )
    parsed = parse_json_bytes(data)
    apply_usage(result, parsed)
    apply_tool_call_telemetry(result, parsed)
    if response.status >= 400:
        result.error = sanitize_preview(data.decode("utf-8", errors="replace"))
    return result


def apply_responses_telemetry(result: ExecutionResult, payload: dict[str, Any], event: str) -> None:
    if result._responses_telemetry is None:
        result._responses_telemetry = CodexCallResult(status=result.status)
    observed = result._responses_telemetry
    capture_telemetry(observed, payload, event)
    result.actual_tool_call_count = observed.actual_tool_call_count
    result.actual_tool_names = observed.actual_tool_names
    result.tool_telemetry_complete = observed.tool_telemetry_complete
    result.usage_seen = observed.usage_seen
    result.usage_event_type = observed.usage_event_type
    if observed.usage_seen:
        result.cached_input_tokens = observed.cached_input_tokens


def finish_provider_cache(result: ExecutionResult, diagnostics: NativeCacheDiagnostics, observation: Observation, delivered: bool) -> None:
    observed = result._responses_telemetry
    result.native_cache = request_diagnostics([diagnostics.finish(
        observation, success=bool(delivered and observed and observed.completed and not result.error),
        cache_write_tokens=observed.cache_write_tokens if observed else None,
        cache_read_known=bool(observed and observed.cache_read_known), status=result.status, error=result.error,
        first_output_at=observed.first_output_at if observed else None,
        provider_diagnostics=observed.cache_provider_diagnostics if observed else None,
    )])


async def provider_responses_stream(
    response: Any,
    result: ExecutionResult,
    realm: str,
    diagnostics: NativeCacheDiagnostics | None = None,
    observation: Observation | None = None,
) -> AsyncIterator[bytes]:
    drained = False
    try:
        async for event, data, _raw in iter_sse_payloads(response.content.iter_chunked(8192)):
            if not data or data == "[DONE]":
                continue
            parsed = parse_json_bytes(data.encode("utf-8"))
            if not isinstance(parsed, dict):
                if result._responses_telemetry is None:
                    result._responses_telemetry = CodexCallResult(status=result.status)
                result._responses_telemetry._native_tool_invalid = True
                continue
            event_name = response_event_name(event, parsed)
            updated = annotate_realm(parsed, realm)
            apply_usage(result, updated)
            apply_responses_telemetry(result, updated, event_name)
            if observation is not None and diagnostics is not None:
                observation.output(updated, event_name, diagnostics.clock())
            if updated.get("error") and not result.error:
                result.error = stable_json(updated.get("error"))
            yield sse(updated, event_name)
        drained = True
    finally:
        response.release()
        if diagnostics is not None and observation is not None:
            finish_provider_cache(result, diagnostics, observation, drained)


async def execute_provider_responses(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    wants_stream: bool,
    prompt_cache_key: str | None,
) -> ExecutionResult:
    provider = route.provider
    if provider != "openai":
        message = f"Provider '{provider}' does not support native Responses in Spartan Route."
        return ExecutionResult(503, body=json_error(message, 503, "responses_provider_not_supported", "invalid_request_error")[0], error="responses_provider_not_supported")
    base_url = provider_base_url(provider)
    api_key = state.config.api_key(provider)
    if not base_url or not api_key:
        return ExecutionResult(503, body=json_error("Missing OPENAI_API_KEY", 503, "provider_key_missing", "authentication_error")[0], error="provider_key_missing")
    if state.session is None:
        return ExecutionResult(503, body=json_error("HTTP session is not ready", 503, "session_unavailable")[0], error="session_unavailable")

    upstream_body = strip_private_spartan_fields(body)
    upstream_body["model"] = upstream_model_id(route.actual_model, provider)
    upstream_body["store"] = False
    if prompt_cache_key:
        upstream_body["prompt_cache_key"] = prompt_cache_key
    include = upstream_body.get("include")
    if not isinstance(include, list):
        include = []
    if "reasoning.encrypted_content" not in include:
        include = [*include, "reasoning.encrypted_content"]
    upstream_body["include"] = include
    timeout_ms = int(os.environ.get("SPARTAN_ROUTE_PROVIDER_TIMEOUT_MS") or "180000")
    started = time.monotonic()
    observation = state.native_cache.begin(
        upstream_body, endpoint=f"{base_url}/responses", realm="openai_api", model=upstream_body["model"],
        account=None, credential_scope=state.native_cache.digest(api_key),
        chain=str(body.get("spartan_chain_id") or session_id_from_request(body, prompt_cache_key) or "") or None,
        cache_key=upstream_body.get("prompt_cache_key"),
    )
    try:
        response = await state.session.post(
            f"{base_url}/responses",
            json=upstream_body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=ClientTimeout(total=timeout_ms / 1000),
        )
    except Exception:
        return ExecutionResult(502, body=json_error("Provider Responses request failed", 502, "provider_request_failed", "upstream_error")[0], error="provider_request_failed",
                               native_cache=request_diagnostics([state.native_cache.finish(observation, success=False, status=502, error="provider_request_failed")]))
    except BaseException:
        state.native_cache.finish(observation, success=False, error="request_aborted")
        raise
    elapsed = max(0, int((time.monotonic() - started) * 1000))
    if wants_stream and 200 <= response.status < 300:
        result = ExecutionResult(response.status, headers={"Content-Type": "text/event-stream"}, upstream_response_time_ms=elapsed)
        result.stream = provider_responses_stream(response, result, "openai_api", state.native_cache, observation)

        async def cleanup_api_stream() -> None:
            response.release()
            finish_provider_cache(result, state.native_cache, observation, False)

        result.stream_cleanup = cleanup_api_stream
        return result
    try:
        data = await response.read()
    except BaseException:
        state.native_cache.finish(observation, success=False, error="request_aborted")
        raise
    finally:
        response.release()
    parsed = parse_json_bytes(data)
    if isinstance(parsed, dict) and response.status < 300:
        parsed = annotate_realm(parsed, "openai_api")
        data = stable_json(parsed).encode("utf-8")
    result = ExecutionResult(response.status, body=data, headers={"Content-Type": response.headers.get("Content-Type", "application/json")}, upstream_response_time_ms=elapsed)
    apply_usage(result, parsed)
    if isinstance(parsed, dict):
        apply_responses_telemetry(result, parsed, f"response.{parsed.get('status', 'unknown')}")
    if response.status >= 400:
        result.error = sanitize_preview(data.decode("utf-8", errors="replace"))
    observed = result._responses_telemetry
    result.native_cache = request_diagnostics([state.native_cache.finish(
        observation, success=bool(observed and observed.completed and not result.error),
        cache_write_tokens=observed.cache_write_tokens if observed else None,
        cache_read_known=bool(observed and observed.cache_read_known), status=result.status, error=result.error,
        first_output_at=observed.first_output_at if observed else None,
        provider_diagnostics=observed.cache_provider_diagnostics if observed else None,
    )])
    return result


def requested_reasoning_effort(body: Mapping[str, Any]) -> str | None:
    explicit = body.get("reasoning_effort")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    reasoning = body.get("reasoning")
    nested = reasoning.get("effort") if isinstance(reasoning, Mapping) else None
    return nested.strip().lower() if isinstance(nested, str) and nested.strip() else None


def reasoning_realm_for_route(route: RoutedRequest) -> str:
    return "codex_subscription" if route.provider == "codex" else "openai_api"


def reasoning_binding_error(
    state: SpartanRouteState,
    body: Mapping[str, Any],
    realm: str,
) -> ExecutionResult | None:
    values = {
        SPARTAN_CHAIN_ID_FIELD: body.get(SPARTAN_CHAIN_ID_FIELD),
        SPARTAN_EXPECTED_REALM_FIELD: body.get(SPARTAN_EXPECTED_REALM_FIELD),
        SPARTAN_BINDING_MAC_FIELD: body.get(SPARTAN_BINDING_MAC_FIELD),
    }
    if all(value is None for value in values.values()):
        input_items = body.get("input")
        has_encrypted_replay = isinstance(input_items, list) and any(
            isinstance(item, Mapping)
            and item.get("type") == "reasoning"
            and bool(item.get("encrypted_content"))
            for item in input_items
        )
        if realm == "codex_subscription" and has_encrypted_replay:
            return ExecutionResult(
                400,
                body=json_error(
                    "Signed reasoning binding is required for Codex subscription replay",
                    400,
                    "reasoning_binding_required",
                    "invalid_request_error",
                )[0],
                error="reasoning_binding_required",
            )
        return None
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        return ExecutionResult(400, body=json_error("Incomplete reasoning binding", 400, "reasoning_binding_invalid", "invalid_request_error")[0], error="reasoning_binding_invalid")
    chain_id = str(values[SPARTAN_CHAIN_ID_FIELD]).strip().lower()
    expected = str(values[SPARTAN_EXPECTED_REALM_FIELD]).strip().lower()
    supplied_mac = str(values[SPARTAN_BINDING_MAC_FIELD]).strip().lower()
    secret_hex = str(os.environ.get("SPARTAN_REASONING_BINDING_SECRET") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", secret_hex):
        return ExecutionResult(503, body=json_error("Reasoning binding is unavailable", 503, "reasoning_binding_unavailable")[0], error="reasoning_binding_unavailable")
    if not re.fullmatch(r"[0-9a-f]{64}", chain_id) or not re.fullmatch(r"[0-9a-f]{64}", supplied_mac) or expected not in SPARTAN_REASONING_REALMS:
        return ExecutionResult(400, body=json_error("Invalid reasoning binding", 400, "reasoning_binding_invalid", "invalid_request_error")[0], error="reasoning_binding_invalid")
    calculated = hmac.new(bytes.fromhex(secret_hex), f"{chain_id}\0{expected}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied_mac):
        return ExecutionResult(400, body=json_error("Invalid reasoning binding", 400, "reasoning_binding_invalid", "invalid_request_error")[0], error="reasoning_binding_invalid")
    if expected != realm:
        return ExecutionResult(409, body=json_error("Reasoning chain belongs to a different provider realm; start a new session or branch", 409, "reasoning_realm_mismatch", "invalid_request_error")[0], error="reasoning_realm_mismatch")
    try:
        if not state.telemetry.bind_reasoning_chain(chain_id, realm):
            return ExecutionResult(409, body=json_error("Reasoning chain belongs to a different provider realm; start a new session or branch", 409, "reasoning_realm_mismatch", "invalid_request_error")[0], error="reasoning_realm_mismatch")
    except Exception:
        return ExecutionResult(503, body=json_error("Reasoning binding store is unavailable", 503, "reasoning_binding_unavailable")[0], error="reasoning_binding_unavailable")
    return None


def codex_responses_capability_error(body: Mapping[str, Any]) -> ExecutionResult | None:
    code = ""
    message = ""
    if body.get("store") is True:
        code = "codex_responses_store_not_supported"
        message = "Codex subscription Responses requires store=false and manual encrypted reasoning replay."
    elif body.get("previous_response_id") is not None:
        code = "codex_previous_response_not_supported"
        message = "previous_response_id is not supported for Codex subscription; replay response items explicitly."
    elif body.get("context_management") is not None:
        code = "codex_compaction_not_supported"
        message = "Native Responses compaction is not supported by the current Hermes client."
    elif isinstance(body.get("input"), list) and any(
        isinstance(item, Mapping) and item.get("type") == "compaction" for item in body["input"]
    ):
        code = "codex_compaction_not_supported"
        message = "Compaction items are not replayable by the current Hermes client."
    else:
        reasoning = body.get("reasoning")
        if isinstance(reasoning, Mapping) and reasoning.get("mode") is not None:
            code = "codex_reasoning_mode_not_supported"
            message = "reasoning.mode is not enabled for Codex subscription Responses."
        tools = body.get("tools")
        if not code and isinstance(tools, list) and any(
            not isinstance(tool, Mapping) or tool.get("type") != "function" for tool in tools
        ):
            code = "codex_tool_type_not_supported"
            message = "Codex subscription Responses currently supports function tools only."
    if not code:
        return None
    return ExecutionResult(
        status=400,
        body={"error": {"message": message, "type": "invalid_request_error", "code": code}},
        error=code,
    )


def codex_model_admission_error(state: SpartanRouteState, model_id: str, effort: str | None) -> ExecutionResult | None:
    if model_id not in state.config.models:
        return ExecutionResult(
            404,
            body={"error": {"message": "Codex model is not in the Route catalog.", "type": "invalid_request_error", "code": "model_not_found"}},
            error="model_not_found",
        )
    if state.codex_catalog is not None and state.codex_catalog.known_unavailable(model_id, effort):
        return ExecutionResult(
            503,
            body={"error": {"message": "No enabled Codex account currently offers this model.", "type": "upstream_error", "code": "codex_model_unavailable"}},
            error="codex_model_unavailable",
        )
    return None


def codex_reasoning_validation_error(body: Mapping[str, Any], model_id: str, catalog: CodexModelDiscovery | None = None) -> ExecutionResult | None:
    effort = requested_reasoning_effort(body) or ""
    if effort == "ultra":
        code = CODEX_ULTRA_REQUIRES_APP_SERVER_CODE
        message = (
            "Ultra requires the stateful Codex app-server bridge; "
            "Spartan Route will not silently downgrade it to max."
        )
    elif (
        effort == "max"
        and "gpt-5.6" not in model_id.lower()
        and model_id.lower().removeprefix("codex/") not in {"gpt-6-astra", "gpt-6-sol", "gpt-6-luna"}
        and not (catalog and catalog.supports_effort(model_id, "max"))
    ):
        code = CODEX_MAX_REQUIRES_GPT56_CODE
        message = "Codex reasoning effort max is supported only for GPT-5.6 and GPT-6 models."
    else:
        return None
    return ExecutionResult(
        status=400,
        body={"error": {"message": message, "type": "invalid_request_error", "code": code}},
        error=code,
    )


async def execute_chat(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    wants_stream: bool,
    session_id: str | None,
    prompt_cache_key: str | None,
    request_id: str,
) -> ExecutionResult:
    upstream_body = strip_private_spartan_fields(body)
    if route.provider == "codex":
        reasoning_error = codex_reasoning_validation_error(upstream_body, route.actual_model, state.codex_catalog)
        if reasoning_error is not None:
            return reasoning_error
        model_error = codex_model_admission_error(state, route.actual_model, requested_reasoning_effort(upstream_body))
        if model_error is not None:
            return model_error
        if state.codex is None:
            return ExecutionResult(503, body=json_error("Codex router is not ready", 503, "codex_unavailable")[0], error="codex_unavailable")
        codex_result: CodexCallResult = await state.codex.execute_chat(
            upstream_body,
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
            actual_tool_call_count=codex_result.actual_tool_call_count,
            actual_tool_names=codex_result.actual_tool_names,
            tool_telemetry_complete=codex_result.tool_telemetry_complete,
            native_cache=codex_result.native_cache,
            usage_seen=codex_result.usage_seen,
            usage_event_type=codex_result.usage_event_type,
            upstream_response_time_ms=codex_result.upstream_response_time_ms,
            total_response_time_ms=codex_result.total_response_time_ms,
            transport_details=codex_result.transport_details,
        )
        if codex_result.stream is not None:
            execution.stream = sync_codex_stream_result(codex_result, execution)

            async def cleanup_native_stream() -> None:
                if codex_result.stream_cleanup is not None:
                    await codex_result.stream_cleanup()
                copy_codex_telemetry(codex_result, execution)

            execution.stream_cleanup = cleanup_native_stream
        return execution
    return await execute_provider_chat(state, upstream_body, route, wants_stream, prompt_cache_key)


async def execute_responses(
    state: SpartanRouteState,
    body: dict[str, Any],
    route: RoutedRequest,
    wants_stream: bool,
    session_id: str | None,
    prompt_cache_key: str | None,
    probe: ProbeTicket | None = None,
) -> ExecutionResult:
    if route.provider == "codex":
        capability_error = codex_responses_capability_error(body)
        if capability_error is not None:
            return capability_error
        reasoning_error = codex_reasoning_validation_error(body, route.actual_model, state.codex_catalog)
        if reasoning_error is not None:
            return reasoning_error
        model_error = codex_model_admission_error(state, route.actual_model, requested_reasoning_effort(body))
        if model_error is not None:
            return model_error
        if state.codex is None:
            return ExecutionResult(503, body=json_error("Codex router is not ready", 503, "codex_unavailable")[0], error="codex_unavailable")
        codex_result = await execute_codex_responses(
            state.codex, body, route.actual_model, wants_stream, session_id, prompt_cache_key, state.native_cache,
            state.cache_experiments, probe,
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
            reasoning_tokens=codex_result.reasoning_tokens,
            error=codex_result.error,
            actual_tool_call_count=codex_result.actual_tool_call_count,
            actual_tool_names=codex_result.actual_tool_names,
            tool_telemetry_complete=codex_result.tool_telemetry_complete,
            native_cache=codex_result.native_cache,
            usage_seen=codex_result.usage_seen,
            usage_event_type=codex_result.usage_event_type,
            upstream_response_time_ms=codex_result.upstream_response_time_ms,
            total_response_time_ms=codex_result.total_response_time_ms,
            transport_details=codex_result.transport_details,
        )
        if codex_result.stream is not None:
            execution.stream = sync_codex_stream_result(codex_result, execution)

            async def cleanup_native_stream() -> None:
                if codex_result.stream_cleanup is not None:
                    await codex_result.stream_cleanup()
                copy_codex_telemetry(codex_result, execution)

            execution.stream_cleanup = cleanup_native_stream
        return execution
    return await execute_provider_responses(state, body, route, wants_stream, prompt_cache_key)


def copy_codex_telemetry(source_result: CodexCallResult, target: ExecutionResult) -> None:
    for name in (
        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "error",
        "actual_tool_call_count", "actual_tool_names", "tool_telemetry_complete", "native_cache",
        "usage_seen", "usage_event_type", "upstream_response_time_ms", "total_response_time_ms",
        "transport_details",
    ):
        setattr(target, name, getattr(source_result, name))


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
        copy_codex_telemetry(source_result, target)


def request_prompt_cache_key(body: dict[str, Any]) -> str | None:
    value = body.get("prompt_cache_key") or body.get("user")
    return value.strip() if isinstance(value, str) and value.strip() else None


def request_prompt_cache_lineage(body: dict[str, Any]) -> str | None:
    value = body.get(SPARTAN_CACHE_LINEAGE_FIELD)
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= 1_000_000:
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() and int(stripped) <= 1_000_000:
            return str(int(stripped))
    return None


def cache_key_with_lineage(prompt_cache_key: str | None, lineage: str | None) -> str | None:
    if not prompt_cache_key or lineage is None:
        return prompt_cache_key
    return f"{prompt_cache_key}\0spartan-cache-lineage:{lineage}"


def request_logical_cache_identity(body: dict[str, Any], session_id: str | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    content_key = build_content_prompt_cache_key(body)
    if content_key:
        return content_key
    return session_id


def request_effective_prompt_cache_key(body: dict[str, Any], session_id: str | None, explicit: str | None) -> str | None:
    lineage = request_prompt_cache_lineage(body)
    if lineage is None:
        return explicit
    return cache_key_with_lineage(request_logical_cache_identity(body, session_id, explicit), lineage)


def strip_private_spartan_fields(body: dict[str, Any]) -> dict[str, Any]:
    if not any(field in body for field in SPARTAN_PRIVATE_REQUEST_FIELDS):
        return body
    return {key: value for key, value in body.items() if key not in SPARTAN_PRIVATE_REQUEST_FIELDS}


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


def cache_likely_min_prefix_messages() -> int:
    return parse_int(
        os.environ.get("SPARTAN_ROUTE_CACHE_LIKELY_MIN_PREFIX_MESSAGES"),
        CACHE_LIKELY_MIN_PREFIX_MESSAGES,
        0,
        100_000,
    )


def cache_likely_min_non_tool_tokens() -> int:
    return parse_int(
        os.environ.get("SPARTAN_ROUTE_CACHE_LIKELY_MIN_NON_TOOL_TOKENS"),
        CACHE_LIKELY_MIN_NON_TOOL_TOKENS,
        0,
        2_000_000,
    )


def classify_cache_expectation(
    upstream_cache_key: str | None,
    input_tokens: int,
    non_tool_input_tokens: int,
    same_session_trace: dict[str, Any],
    result: ExecutionResult,
) -> dict[str, Any]:
    comparison = str(same_session_trace.get("comparison") or "baseline")
    common_prefix_messages = int(same_session_trace.get("commonPrefixMessages") or 0)
    cache_prefix_candidate = bool(
        upstream_cache_key
        and input_tokens >= CACHE_GUARD_EXPECTED_MIN_TOKENS
        and comparison in {"prefix", "retry"}
    )

    if not upstream_cache_key:
        reason = "no_key"
    elif comparison == "baseline":
        reason = "baseline"
    elif comparison == "history_rewrite":
        reason = "history_rewrite"
    elif comparison == "tool_schema_changed":
        reason = "tool_schema_changed"
    elif comparison == "retry":
        reason = "retry"
    elif comparison != "prefix":
        reason = "not_prefix"
    elif result.error:
        reason = "error"
    elif not result.usage_seen:
        reason = "usage_missing"
    elif common_prefix_messages < cache_likely_min_prefix_messages():
        reason = "shallow_prefix"
    elif non_tool_input_tokens < cache_likely_min_non_tool_tokens():
        reason = "small_prefix_tokens"
    else:
        reason = "mature_prefix"

    return {
        "cache_prefix_candidate": cache_prefix_candidate,
        "cache_likely": reason == "mature_prefix",
        "cache_expectation_reason": reason,
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
    cache_likely: bool,
    cache_hit_ratio: float,
) -> bool:
    if not cache_key_hash:
        return False

    def trim_guard() -> None:
        if len(state.cache_guard) > 512:
            oldest_key = next(iter(state.cache_guard))
            state.cache_guard.pop(oldest_key, None)

    key = cache_guard_key(
        route.actual_model,
        cache_key_hash,
        result.slot_index,
        result.account_key,
        tool_schema_fingerprint,
    )
    entry = state.cache_guard.setdefault(key, {"consecutiveLow": 0})
    stall_diagnostics_enabled = parse_bool(os.environ.get("SPARTAN_ROUTE_CACHE_STALL_GUARD_ENABLED"), True)
    stall_min_tokens = parse_int(
        os.environ.get("SPARTAN_ROUTE_CACHE_STALL_MIN_INPUT_TOKENS"),
        CACHE_STALL_DIAGNOSTIC_MIN_TOKENS,
        0,
        2_000_000,
    )
    stall_low_ratio = max(0.0, min(1.0, parse_float(os.environ.get("SPARTAN_ROUTE_CACHE_STALL_LOW_CACHE_RATIO"), CACHE_STALL_DIAGNOSTIC_LOW_RATIO)))
    stream_stall_error = result.error in CACHE_STALL_ERROR_CODES
    if (
        stall_diagnostics_enabled
        and stream_stall_error
        and input_tokens >= stall_min_tokens
        and cache_hit_ratio < stall_low_ratio
        and (expected_cache_hit or result.error in {"codex_stream_timeout", "codex_stream_error"})
    ):
        entry["consecutiveStreamFailures"] = int(entry.get("consecutiveStreamFailures") or 0) + 1
        entry["lastError"] = result.error
        entry["lastInputTokens"] = input_tokens
        entry["lastCacheHitRatio"] = round(cache_hit_ratio, 4)
        entry["lastExpectedCacheHit"] = bool(expected_cache_hit)
        entry["diagnosticOnly"] = True
    elif result.usage_seen and cache_hit_ratio >= stall_low_ratio:
        entry["consecutiveStreamFailures"] = 0
    if not cache_likely or not result.usage_seen or input_tokens < CACHE_GUARD_LOW_MIN_TOKENS:
        entry["consecutiveLow"] = 0
        trim_guard()
        return False
    if cache_hit_ratio < CACHE_GUARD_LOW_RATIO:
        entry["consecutiveLow"] = int(entry.get("consecutiveLow") or 0) + 1
    else:
        entry["consecutiveLow"] = 0
    trim_guard()
    return int(entry.get("consecutiveLow") or 0) >= CACHE_GUARD_CONSECUTIVE_LOW


def cache_guard_diagnostic_context(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {"diagnostic_only": True, "consecutive_stream_failures": 0}
    return {
        "diagnostic_only": True,
        "consecutive_stream_failures": int(entry.get("consecutiveStreamFailures") or 0),
        "last_error": entry.get("lastError"),
        "last_input_tokens": entry.get("lastInputTokens"),
        "last_cache_hit_ratio": entry.get("lastCacheHitRatio"),
        "last_expected_cache_hit": entry.get("lastExpectedCacheHit"),
    }


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


def message_text_preview(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return " ".join(parts)


def prompt_preview_from_request(body: dict[str, Any], log_content: bool) -> str | None:
    if not log_content:
        return None
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = strip_metadata_preamble(message_text_preview(message))
            return sanitize_preview(text, 300) if text else None
    return None


def _tool_call_summary(call: Any) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = function.get("name")
    args = function.get("arguments")
    if not isinstance(name, str):
        return None
    argument_keys: list[str] = []
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                argument_keys = list(parsed.keys())[:20]
        except Exception:
            argument_keys = []
    return {
        "name": name,
        "argumentKeys": argument_keys,
        "argumentChars": len(args) if isinstance(args, str) else 0,
    }


def _trace_item(message: dict[str, Any], log_content: bool) -> dict[str, Any] | None:
    role = message.get("role") if isinstance(message.get("role"), str) else "unknown"
    if role == "system":
        return None
    item: dict[str, Any] = {"role": role, "chars": len(stable_json(message))}
    if role == "assistant":
        tool_calls = [_tool_call_summary(call) for call in message.get("tool_calls") or []]
        tool_calls = [call for call in tool_calls if call is not None]
        if tool_calls:
            item["toolCalls"] = tool_calls
    if log_content:
        text = message_text_preview(message)
        if role == "user":
            text = strip_metadata_preamble(text)
        if text:
            item["preview"] = sanitize_preview(text, 240 if role == "tool" else 300)
    return item


def request_trace_from_request(body: dict[str, Any], snapshot: dict[str, Any], log_content: bool) -> dict[str, Any]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    items = [
        item for item in (_trace_item(message, log_content) for message in messages if isinstance(message, dict))
        if item is not None
    ]
    capped_items = items[-24:]
    role_counts: dict[str, int] = {}
    for item in items:
        role = str(item.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    tool_call_count = sum(len(item.get("toolCalls") or []) for item in items)
    tool_result_count = role_counts.get("tool", 0)
    if role_counts.get("user", 0) > 0:
        phase = "user_input"
    elif tool_result_count > 0:
        phase = "tool_results"
    elif role_counts.get("assistant", 0) > 0:
        phase = "assistant_continuation"
    else:
        phase = "baseline"
    message_fingerprints = snapshot.get("messageFingerprints") if isinstance(snapshot.get("messageFingerprints"), list) else []
    return {
        "version": 1,
        "requestFingerprint": snapshot.get("requestFingerprint"),
        "messageFingerprints": message_fingerprints,
        "messageCount": len(message_fingerprints),
        "phase": phase,
        "delta": {
            "comparison": "baseline",
            "addedMessageCount": len(items),
            "removedMessageCount": 0,
            "addedChars": sum(int(item.get("chars") or 0) for item in items),
            "roleCounts": role_counts,
            "toolCallCount": tool_call_count,
            "toolResultCount": tool_result_count,
            "items": capped_items,
            "omittedItems": max(0, len(items) - len(capped_items)),
            "toolSchemas": {
                "status": "baseline",
                "count": len(body.get("tools") if isinstance(body.get("tools"), list) else []),
                "chars": len(stable_json(body.get("tools") or [])),
            },
        },
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
    cache_lineage = request_prompt_cache_lineage(body)
    effective_prompt_cache_key = request_effective_prompt_cache_key(body, session_id, explicit_cache_key)
    logical_upstream_cache_key = resolve_prompt_cache_key(body, session_id, explicit_cache_key)
    upstream_cache_key = resolve_prompt_cache_key(body, session_id, effective_prompt_cache_key)
    cache_key_hash = sha(logical_upstream_cache_key, 12) if logical_upstream_cache_key else None
    effective_cache_key_hash = sha(upstream_cache_key, 12) if upstream_cache_key else None
    cache_key_source = request_prompt_cache_key_source(body, session_id)
    message_chars, tool_schema_chars = estimate_shape_sizes(body)
    native_cache = (result.native_cache or {"version": 2, "comparison": "unknown", "reason": "not_observed"}) if api_kind == "responses" else None
    if native_cache is not None:
        hashes = native_cache.get("hashes") or {}
        shape_snapshot = {"requestFingerprint": sha(stable_json(hashes), 16) if hashes else None,
                          "toolSchemaFingerprint": hashes.get("tools")}
        same_session_trace = None
    else:
        shape_snapshot = request_shape_snapshot(body)
        trace_key = f"{session_id or 'none'}:{effective_cache_key_hash or 'none'}"
        previous_trace = state.recent_session_traces.get(trace_key)
        same_session_trace = compare_session_trace(shape_snapshot, previous_trace)
        state.recent_session_traces[trace_key] = shape_snapshot
        if len(state.recent_session_traces) > 512:
            oldest_key = next(iter(state.recent_session_traces))
            state.recent_session_traces.pop(oldest_key, None)
    tool_schema_fp = shape_snapshot.get("toolSchemaFingerprint")
    request_trace = request_trace_from_request(body, shape_snapshot, state.config.log_content)
    if native_cache is not None:
        request_trace.update(version=2, messageCount=len(messages))
        request_trace["delta"]["comparison"] = "native_diagnostics"
    tool_schema_rough_tokens = max(0, tool_schema_chars // 4)
    non_tool_input_tokens = max(0, input_tokens - tool_schema_rough_tokens)
    cache_expectation = classify_cache_expectation(
        upstream_cache_key,
        input_tokens,
        non_tool_input_tokens,
        same_session_trace,
        result,
    ) if native_cache is None else {
        "cache_prefix_candidate": False, "cache_likely": False, "cache_expectation_reason": "native_v2",
    }
    expected_cache_hit = bool(cache_expectation["cache_prefix_candidate"])
    cache_likely = bool(cache_expectation["cache_likely"])
    cache_hit_ratio = (result.cached_input_tokens / input_tokens) if input_tokens > 0 else 0.0
    cache_low = update_cache_guard(
        state,
        route,
        result,
        input_tokens,
        effective_cache_key_hash,
        tool_schema_fp if isinstance(tool_schema_fp, str) else None,
        expected_cache_hit,
        cache_likely,
        cache_hit_ratio,
    ) if native_cache is None else False
    guard_entry = (
        state.cache_guard.get(cache_guard_key(
            route.actual_model,
            effective_cache_key_hash,
            result.slot_index,
            result.account_key,
            tool_schema_fp if isinstance(tool_schema_fp, str) else None,
        ))
        if effective_cache_key_hash
        else None
    )
    transport = None
    if result.transport_details:
        transport = {key: value for key, value in result.transport_details.items() if not key.startswith("_")}
        upstream_started = result.transport_details.get("_started_monotonic")
        if isinstance(upstream_started, (int, float)):
            preparation_ms = max(0, int((upstream_started - started) * 1000))
            transport["routing_preparation_ms"] = preparation_ms
            for key in ("first_event_ms", "first_reasoning_ms", "first_text_ms", "first_tool_ms", "first_output_ms"):
                if key in transport:
                    transport["request_" + key] = preparation_ms + transport[key]
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
        "effective_cache_key_hash": effective_cache_key_hash,
        "cache_key_source": cache_key_source,
        "cache_lineage": cache_lineage,
        "cache_lineage_present": cache_lineage is not None,
        "cache_lineage_reason": body.get(SPARTAN_CACHE_LINEAGE_REASON_FIELD)
        if isinstance(body.get(SPARTAN_CACHE_LINEAGE_REASON_FIELD), str)
        else None,
        "tool_telemetry_version": 2 if result.tool_telemetry_complete is not None else None,
        "tool_telemetry_complete": result.tool_telemetry_complete,
        "usage_seen": result.usage_seen,
        "usage_event_type": result.usage_event_type,
        "expected_cache_hit": expected_cache_hit,
        "cache_prefix_candidate": cache_expectation["cache_prefix_candidate"],
        "cache_likely": cache_likely,
        "cache_expectation_reason": cache_expectation["cache_expectation_reason"],
        "cache_hit_ratio": round(cache_hit_ratio, 4),
        "cache_low": cache_low,
        "cache_stall_diagnostic": cache_guard_diagnostic_context(guard_entry),
        "request_api_kind": api_kind,
        "native_cache": native_cache,
        "transport": transport,
        "cache_diagnostic_version": 2 if native_cache is not None else 1,
        "requested_reasoning_effort": requested_reasoning_effort(body),
        "reasoning_tokens": result.reasoning_tokens,
        "message_chars": message_chars,
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_rough_tokens": tool_schema_rough_tokens,
        "non_tool_input_tokens": non_tool_input_tokens,
        "tool_schema_fingerprint": tool_schema_fp,
        "request_trace": request_trace,
        "same_session_cache_trace": same_session_trace,
        "request_shape_hashes": build_request_shape_diagnostics(shape_snapshot),
    }
    total_response_time_ms = (
        result.total_response_time_ms
        if result.total_response_time_ms is not None
        else max(0, int((time.monotonic() - started) * 1000))
    )
    upstream_response_time_ms = result.upstream_response_time_ms
    if result.transport_details and result.transport_details.get("kind") == "websocket":
        primary_response_time_ms = 0  # WS first-event time is not HTTP header latency.
    else:
        primary_response_time_ms = upstream_response_time_ms if upstream_response_time_ms is not None else total_response_time_ms
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
            response_time_ms=primary_response_time_ms,
            upstream_response_time_ms=upstream_response_time_ms,
            total_response_time_ms=total_response_time_ms,
            had_tool_calls=bool(body.get("tools")),
            actual_tool_call_count=result.actual_tool_call_count,
            actual_tool_names=result.actual_tool_names,
            is_dry_run=state.config.dry_run,
            session_id=session_id,
            error=result.error if result.status >= 400 or result.error else None,
            prompt_preview=prompt_preview_from_request(body, state.config.log_content),
            context_info=stable_json(context),
            request_api_kind=api_kind,
            requested_reasoning_effort=requested_reasoning_effort(body),
            reasoning_tokens=result.reasoning_tokens,
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
        client_disconnected = False
        prepared = False
        try:
            await response.prepare(request)
            prepared = True
            async for chunk in source:
                try:
                    await response.write(chunk)
                except (BrokenPipeError, ClientConnectionResetError, ConnectionResetError, RuntimeError):
                    client_disconnected = True
                    close = getattr(source, "aclose", None)
                    if callable(close):
                        await close()
                    break
        except BaseException:
            client_disconnected = True
            raise
        finally:
            try:
                close = getattr(source, "aclose", None)
                if callable(close):
                    await close()
            finally:
                try:
                    if result.stream_cleanup is not None:
                        await result.stream_cleanup()
                finally:
                    if on_done:
                        on_done()
            if prepared and not client_disconnected:
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
        ("reasoning", "reasoning"),
        ("prompt_cache_key", "prompt_cache_key"),
        (SPARTAN_CACHE_LINEAGE_FIELD, SPARTAN_CACHE_LINEAGE_FIELD),
        (SPARTAN_CACHE_LINEAGE_REASON_FIELD, SPARTAN_CACHE_LINEAGE_REASON_FIELD),
        ("spartan_chain_id", "spartan_chain_id"),
        ("spartan_expected_realm", "spartan_expected_realm"),
        ("spartan_binding_mac", "spartan_binding_mac"),
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
    failed_error: dict[str, Any] | None = None
    async for _event, data, _raw in iter_sse_payloads(source):
        if data == "[DONE]":
            continue
        parsed = parse_json_bytes(data.encode("utf-8"))
        if not isinstance(parsed, dict):
            continue
        if parsed.get("error"):
            failed_error = parsed["error"] if isinstance(parsed["error"], dict) else {"message": str(parsed["error"]), "type": "upstream_error", "code": "codex_error"}
            yield sse({"error": failed_error}, "error")
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
    if failed_error is not None:
        yield sse({
            "id": response_id,
            "object": "response",
            "created_at": created,
            "model": model,
            "status": "failed",
            "output": [],
            "error": failed_error,
        }, "response.failed")
        return
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
    started = time.monotonic()
    prompt_cache_key = request_prompt_cache_key(body)
    session_id = session_id_from_request(body, prompt_cache_key)
    effective_prompt_cache_key = request_effective_prompt_cache_key(body, session_id, prompt_cache_key)
    turn_id = resolve_turn_id(body, session_id, request_id)
    policy = state.routing_policy
    try:
        route = route_request(state.config, body, policy.tier_models if policy else None)
    except InvalidContextVariant:
        return response_json_error(f"The model '{body['model']}' does not exist", 404, "model_not_found", "invalid_request_error")
    result = await execute_chat(state, body, route, bool(body.get("stream")), session_id, effective_prompt_cache_key, request_id)
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
    started = time.monotonic()
    prompt_cache_key = request_prompt_cache_key(chat_body)
    session_id = session_id_from_request(chat_body, prompt_cache_key)
    effective_prompt_cache_key = request_effective_prompt_cache_key(chat_body, session_id, prompt_cache_key)
    turn_id = resolve_turn_id(chat_body, session_id, request_id)
    policy = state.routing_policy
    try:
        route = route_request(state.config, chat_body, policy.tier_models if policy else None)
    except InvalidContextVariant:
        return response_json_error(f"The model '{body['model']}' does not exist", 404, "model_not_found", "invalid_request_error")
    wants_stream = bool(body.get("stream"))
    preflight_error = codex_responses_capability_error(body) if route.provider == "codex" else None
    binding_error = None if preflight_error is not None else reasoning_binding_error(
        state, body, reasoning_realm_for_route(route)
    )
    probe = None
    probe_header = request.headers.get(PROBE_HEADER)
    if probe_header is not None:
        try:
            if preflight_error or binding_error or route.provider != "codex" or state.codex is None:
                raise ProbeRejected("probe_unavailable")
            probe = state.cache_experiments.admit(probe_header, body, route.actual_model)
        except ProbeRejected:
            return response_json_error("Cache probe rejected; inspect the private run report", 409, "cache_probe_rejected")
    try:
        result = preflight_error or binding_error or await execute_responses(
            state, body, route, wants_stream, session_id, effective_prompt_cache_key, probe
        )
    except BaseException:
        if probe is not None:
            state.cache_experiments.finish(probe, success=False)
        raise
    if probe is not None and result.stream is None:
        state.cache_experiments.finish(probe, success=False, sent=False)

    def done() -> None:
        log_request(state, chat_body, route, result, started, request_id, session_id, turn_id, "responses")

    return await write_execution_response(request, result, on_done=done, content_type="text/event-stream" if wants_stream else None)


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
    if state.config.debug and endpoint == "/images/generations":
        print(f"[images] forwarding JSON {endpoint} {stable_json(summarize_image_json_payload(payload, state.config.log_content))}", flush=True)
    async with session.post(
        f"{provider_base_url('openai')}/{endpoint.lstrip('/')}",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=ClientTimeout(total=180),
    ) as response:
        data = await response.read()
        return ExecutionResult(response.status, body=data, headers={"Content-Type": response.headers.get("Content-Type", "application/json")}, error=sanitize_preview(data.decode("utf-8", "replace")) if response.status >= 400 else None)


GPT_IMAGE_MIN_PIXELS = 655_360
GPT_IMAGE_MAX_PIXELS = 8_294_400
GPT_IMAGE_MAX_EDGE = 3840
GPT_IMAGE_MAX_RATIO = 3.0


def parse_image_size(size: Any) -> tuple[int, int] | str:
    if size is None:
        return "default"
    text = str(size).strip()
    if text == "auto":
        return "auto"
    match = re.fullmatch(r"(\d+)x(\d+)", text)
    if not match:
        return "invalid"
    return int(match.group(1)), int(match.group(2))


def codex_image_size_error(size: Any) -> web.Response | None:
    parsed = parse_image_size(size)
    if parsed in {"default", "auto"}:
        return None
    if parsed == "invalid":
        return response_json_error(f'size must be "auto" or WIDTHxHEIGHT; received {size}', 400, "invalid_image_size", "invalid_request_error")
    width, height = parsed
    if width % 16 != 0 or height % 16 != 0:
        return response_json_error(f"size edges must be multiples of 16 for gpt-image-2; received {size}", 400, "invalid_image_size", "invalid_request_error")
    if width > GPT_IMAGE_MAX_EDGE or height > GPT_IMAGE_MAX_EDGE:
        return response_json_error(f"size edge must be <= {GPT_IMAGE_MAX_EDGE}px for gpt-image-2; received {size}", 400, "invalid_image_size", "invalid_request_error")
    ratio = max(width, height) / max(1, min(width, height))
    if ratio > GPT_IMAGE_MAX_RATIO:
        return response_json_error(f"size aspect ratio must not exceed 3:1 for gpt-image-2; received {size}", 400, "invalid_image_size", "invalid_request_error")
    pixels = width * height
    if pixels < GPT_IMAGE_MIN_PIXELS or pixels > GPT_IMAGE_MAX_PIXELS:
        return response_json_error(
            f"size total pixels must be between {GPT_IMAGE_MIN_PIXELS} and {GPT_IMAGE_MAX_PIXELS} for gpt-image-2; received {size}",
            400,
            "invalid_image_size",
            "invalid_request_error",
        )
    return None


def summarize_prompt(value: Any, log_content: bool) -> Any:
    if not isinstance(value, str):
        return value
    return value if log_content else f"[redacted {len(value)} chars]"


def summarize_image_json_payload(payload: dict[str, Any], log_content: bool) -> dict[str, Any]:
    summary = dict(payload)
    if "prompt" in summary:
        summary["prompt"] = summarize_prompt(summary["prompt"], log_content)
    return summary


def summarize_image_multipart_payload(fields: Any, log_content: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    image_fields: list[str] = []
    for name, value in fields.items():
        if isinstance(value, FileField):
            if name in {"image", "image[]"}:
                image_fields.append(name)
            summary[name] = {"filename": value.filename, "content_type": value.content_type}
        elif name == "prompt":
            summary[name] = summarize_prompt(str(value), log_content)
        else:
            summary[name] = str(value)
    summary["image_count"] = len(image_fields)
    summary["image_fields"] = image_fields
    return summary


async def image_generations(request: web.Request) -> web.StreamResponse:
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    state = get_state(request)
    model = normalize_model_id(body.get("model") or "gpt-image-2")
    if model.startswith("codex/") or not state.config.api_key("openai"):
        size_error = codex_image_size_error(body.get("size"))
        if size_error is not None:
            return size_error
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
    if state.config.debug and endpoint == "/images/edits":
        print(f"[images] forwarding multipart {endpoint} {stable_json(summarize_image_multipart_payload(fields, state.config.log_content))}", flush=True)
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
    size_error = codex_image_size_error(fields.get("size"))
    if size_error is not None:
        return size_error
    input_fidelity = fields.get("input_fidelity")
    if input_fidelity is not None:
        input_fidelity = str(input_fidelity).strip().lower()
        if input_fidelity not in {"low", "high"}:
            return response_json_error(
                "input_fidelity must be low or high.",
                400,
                "invalid_input_fidelity",
                "invalid_request_error",
            )
    mask_field = fields.get("mask")
    if mask_field is not None and not isinstance(mask_field, FileField):
        return response_json_error("mask must be an uploaded image file.", 400, "invalid_mask", "invalid_request_error")
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
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
    if isinstance(mask_field, FileField):
        mask_field.file.seek(0)
        payload["mask"] = data_url_from_file(mask_field)
    if input_fidelity is not None:
        payload["input_fidelity"] = input_fidelity
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
    return web.json_response({
        "object": "list",
        "data": [
            auto,
            {**auto, "id": "custom-1/clawroute/auto"},
            {**auto, "id": "spartan-route/clawroute/auto"},
            *[model_payload(m) for m in state.config.models.values() if m.enabled],
            *[
                model_payload(replace(m, id=f"{m.id}{CONTEXT_VARIANT_SUFFIX}", max_context=CONTEXT_VARIANT_TOKENS))
                for m in state.config.models.values()
                if context_variant_eligible(m) and f"{m.id}{CONTEXT_VARIANT_SUFFIX}" not in state.config.models
            ],
            *([realtime_model()] if request.app[RT_KEY].config.enabled else []),
        ],
    })


async def model_detail(request: web.Request) -> web.Response:
    state = get_state(request)
    model_id = normalize_model_id(request.match_info["model_id"])
    if model_id == request.app[RT_KEY].config.model and request.app[RT_KEY].config.enabled:
        return web.json_response(realtime_model())
    if model_id in {"clawroute/auto", "auto"}:
        return web.json_response({"id": request.match_info["model_id"], "object": "model", "owned_by": "spartan-route", "max_context": 1_000_000, "tool_capable": True, "multimodal": True})
    entry = state.config.models.get(model_id)
    variant = None if entry is not None else context_variant_entry(state.config, model_id)
    entry = entry or variant
    if not entry:
        return response_json_error(f"The model '{request.match_info['model_id']}' does not exist", 404, "model_not_found", "invalid_request_error")
    payload = {"id": entry.id, "object": "model", "owned_by": entry.provider, "max_context": entry.max_context, "context_length": entry.max_context, "max_model_len": entry.max_context, "tool_capable": entry.tool_capable, "multimodal": entry.multimodal}
    return web.json_response(payload)


ROUTING_TIER_PRESENTATION = {
    "heartbeat": ("Heartbeat", "Small health-check-sized requests"),
    "simple": ("Simple", "Small requests up to about 4,000 estimated tokens"),
    "moderate": ("Moderate", "Medium-context requests"),
    "complex": ("Complex", "Tools, large context, or engineering-heavy prompts"),
}
CONFIG_BODY_MAX_BYTES = 16 * 1024
MODEL_ID_MAX_CHARS = 256


def effective_routing_policy(state: SpartanRouteState) -> RoutingPolicySnapshot:
    if state.routing_policy is None:
        state.routing_policy = build_routing_policy_snapshot(state.config, state.telemetry)
    return state.routing_policy


def model_readiness(state: SpartanRouteState, entry: ModelEntry) -> str:
    if entry.provider == "codex":
        if not state.codex or not getattr(state.codex, "slots", []):
            return "no_codex_slots"
        if state.codex_catalog is not None and state.codex_catalog.known_unavailable(entry.id):
            return "model_unavailable"
        return "configured"
    if entry.provider == "ollama":
        return "configured"
    if entry.provider in {"openai", "openrouter"}:
        return "configured" if state.config.api_key(entry.provider) else "missing_credentials"
    return "provider_not_supported"


def routing_config_document(state: SpartanRouteState) -> dict[str, Any]:
    policy = effective_routing_policy(state)
    file_defaults = file_routing_models(state.config)
    return {
        "editableTiers": [
            {
                "id": tier,
                "label": ROUTING_TIER_PRESENTATION[tier][0],
                "rule": ROUTING_TIER_PRESENTATION[tier][1],
            }
            for tier in AUTO_ROUTING_TIERS
        ],
        "fileDefaults": file_defaults,
        "overrides": dict(policy.overrides),
        "effective": dict(policy.tier_models),
        "sources": {
            tier: "runtime_override" if tier in policy.overrides else "file"
            for tier in AUTO_ROUTING_TIERS
        },
        "updatedAt": policy.updated_at,
        "warning": policy.warning,
        "etag": policy.etag,
        "enabled": state.config.enabled,
        "dryRun": state.config.dry_run,
        "limitations": [
            "Fallback models are not executed by Spartan Route v1.",
            "Frontier tiers are configured but are not emitted by the v1 classifier.",
        ],
        "models": [
            {
                "id": entry.id,
                "provider": entry.provider,
                "maxContext": entry.max_context,
                "toolCapable": entry.tool_capable,
                "multimodal": entry.multimodal,
                "enabled": entry.enabled,
                "eligible": model_is_routing_eligible(state.config, entry.id),
                "readiness": model_readiness(state, entry),
            }
            for entry in state.config.models.values()
        ],
    }


def routing_config_response(state: SpartanRouteState, status: int = 200) -> web.Response:
    policy = effective_routing_policy(state)
    return web.json_response(
        routing_config_document(state),
        status=status,
        headers={"ETag": policy.etag, "Cache-Control": "no-store"},
    )


def routing_precondition_error(
    state: SpartanRouteState,
    message: str,
    status: int,
    code: str,
) -> web.Response:
    response = response_json_error(message, status, code, "conflict_error")
    response.headers["ETag"] = effective_routing_policy(state).etag
    response.headers["Cache-Control"] = "no-store"
    return response


def routing_persistence_error(
    state: SpartanRouteState,
    message: str,
) -> web.Response:
    response = response_json_error(
        message,
        503,
        "config_persistence_failed",
    )
    response.headers["ETag"] = effective_routing_policy(state).etag
    response.headers["Cache-Control"] = "no-store"
    return response


async def read_bounded_json_object(
    request: web.Request,
    max_bytes: int = CONFIG_BODY_MAX_BYTES,
) -> dict[str, Any] | web.Response:
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        return response_json_error(
            "Configuration request is too large.",
            413,
            "config_payload_too_large",
            "invalid_request_error",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await request.content.read(min(8192, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return response_json_error(
                "Configuration request is too large.",
                413,
                "config_payload_too_large",
                "invalid_request_error",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        body = json.loads(data)
    except Exception:
        return response_json_error(
            "Invalid JSON body.",
            400,
            "invalid_json",
            "invalid_request_error",
        )
    if not isinstance(body, dict):
        return response_json_error(
            "JSON body must be an object.",
            400,
            "invalid_request",
            "invalid_request_error",
        )
    return body


def validate_routing_config_body(
    config: SpartanRouteConfig,
    body: dict[str, Any],
) -> tuple[dict[str, str] | None, web.Response | None]:
    if set(body) != {"tiers"} or not isinstance(body.get("tiers"), dict):
        return None, response_json_error(
            "Configuration must contain exactly one tiers object.",
            400,
            "invalid_routing_config_shape",
            "invalid_request_error",
        )
    tiers = body["tiers"]
    if set(tiers) != set(AUTO_ROUTING_TIERS):
        return None, response_json_error(
            "Configuration must include exactly the editable routing tiers.",
            400,
            "invalid_routing_tier_set",
            "invalid_request_error",
        )
    validated: dict[str, str] = {}
    for tier in AUTO_ROUTING_TIERS:
        model_id = tiers.get(tier)
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or len(model_id) > MODEL_ID_MAX_CHARS
        ):
            return None, response_json_error(
                "Each routing tier must contain a bounded model id.",
                400,
                "invalid_routing_model_id",
                "invalid_request_error",
            )
        if normalize_model_id(model_id) in {"auto", "clawroute/auto"}:
            return None, response_json_error(
                "Auto aliases cannot be used as routing targets.",
                400,
                "recursive_routing_model",
                "invalid_request_error",
            )
        if not model_is_routing_eligible(config, model_id):
            return None, response_json_error(
                "One or more routing targets are not eligible.",
                400,
                "routing_model_not_eligible",
                "invalid_request_error",
            )
        validated[tier] = model_id
    return validated, None


async def api_config_routing_get(request: web.Request) -> web.Response:
    return routing_config_response(get_state(request))


async def api_config_routing_put(request: web.Request) -> web.Response:
    state = get_state(request)
    if_match = request.headers.get("If-Match")
    if not if_match:
        return routing_precondition_error(
            state,
            "If-Match is required for configuration updates.",
            428,
            "config_precondition_required",
        )
    body = await read_bounded_json_object(request)
    if isinstance(body, web.Response):
        return body
    mapping, error = validate_routing_config_body(state.config, body)
    if error is not None or mapping is None:
        return error or response_json_error(
            "Invalid routing configuration.",
            400,
            "invalid_routing_config",
            "invalid_request_error",
        )

    async with state.routing_policy_lock:
        current = effective_routing_policy(state)
        if if_match != current.etag:
            return routing_precondition_error(
                state,
                "Routing configuration changed; reload before saving.",
                412,
                "config_revision_conflict",
            )
        try:
            updated_at = state.telemetry.replace_routing_model_overrides(mapping)
        except Exception as exc:
            record_telemetry_failure(state, "write", "routing_config", exc)
            return routing_persistence_error(
                state,
                "Routing configuration could not be persisted.",
            )
        state.telemetry.record_health_success("write", "routing_config")
        state.routing_policy = make_routing_policy_snapshot(
            state.config,
            mapping,
            updated_at=updated_at,
        )
    return routing_config_response(state)


async def api_config_routing_delete(request: web.Request) -> web.Response:
    state = get_state(request)
    if_match = request.headers.get("If-Match")
    if not if_match:
        return routing_precondition_error(
            state,
            "If-Match is required for configuration reset.",
            428,
            "config_precondition_required",
        )

    async with state.routing_policy_lock:
        current = effective_routing_policy(state)
        if if_match != current.etag:
            return routing_precondition_error(
                state,
                "Routing configuration changed; reload before resetting.",
                412,
                "config_revision_conflict",
            )
        try:
            state.telemetry.delete_routing_model_overrides()
        except Exception as exc:
            record_telemetry_failure(state, "write", "routing_config", exc)
            return routing_persistence_error(
                state,
                "Routing configuration could not be reset.",
            )
        state.telemetry.record_health_success("write", "routing_config")
        state.routing_policy = make_routing_policy_snapshot(state.config)
    return routing_config_response(state)


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


def record_telemetry_failure(
    state: SpartanRouteState,
    category: str,
    operation: str,
    exc: Exception,
) -> None:
    state.telemetry.record_health_failure(category, operation)
    print(
        f"[spartan-route] telemetry {operation} {category} failed ({type(exc).__name__})",
        file=sys.stderr,
    )


def telemetry_error(operation: str, code: str = "telemetry_read_failed") -> dict[str, str]:
    return {"code": code, "operation": operation}


def add_telemetry_error(payload: dict[str, Any], error: dict[str, str]) -> None:
    errors = payload.get("telemetryErrors")
    if not isinstance(errors, list):
        errors = []
        payload["telemetryErrors"] = errors
    if error not in errors:
        errors.append(error)
    payload["partial"] = True


def telemetry_unavailable_response() -> web.Response:
    return response_json_error(
        "Telemetry is temporarily unavailable.",
        503,
        "telemetry_unavailable",
    )


def read_daily_cache_rows(
    state: SpartanRouteState,
) -> tuple[list[dict[str, Any]] | None, dict[str, str] | None]:
    try:
        rows = state.telemetry.daily_cache_rows()
    except Exception as exc:
        record_telemetry_failure(state, "read", "daily_cache", exc)
        return None, telemetry_error("daily_cache")
    state.telemetry.record_health_success("read", "daily_cache")
    return rows, None


async def health(request: web.Request) -> web.Response:
    state = get_state(request)
    codex_slots = []
    if state.codex:
        try:
            codex_slots = state.codex.slot_status()
        except Exception as exc:
            record_telemetry_failure(state, "read", "slot_status", exc)
        else:
            state.telemetry.record_health_success("read", "slot_status")
    telemetry_status = state.queue.status()
    database_status = state.telemetry.health_status()
    telemetry_status["database"] = database_status
    return web.json_response({
        "ok": True,
        "degraded": not database_status["healthy"],
        "service": "spartan-route",
        "version": "0.1.0",
        "enabled": state.config.enabled,
        "telemetry": telemetry_status,
        "codexSlots": codex_slots,
    })


async def stats(request: web.Request) -> web.Response:
    state = get_state(request)
    rows, error = read_daily_cache_rows(state)
    if error is not None or rows is None:
        return telemetry_unavailable_response()
    try:
        today = state.telemetry.today_summary()
    except Exception as exc:
        record_telemetry_failure(state, "read", "today_summary", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("read", "today_summary")
    totals = {
        "requests": sum(int(row["requests"] or 0) for row in rows),
        "inputTokens": sum(int(row["input_tokens"] or 0) for row in rows),
        "cachedInputTokens": sum(int(row["cached_input_tokens"] or 0) for row in rows),
        "cacheLikelyRequests": sum(int(row["cache_likely_requests"] or 0) for row in rows),
        "cacheLikelyInputTokens": sum(int(row["cache_likely_input_tokens"] or 0) for row in rows),
        "cacheLikelyCachedInputTokens": sum(int(row["cache_likely_cached_input_tokens"] or 0) for row in rows),
        "cacheLowRequests": sum(int(row["cache_low_requests"] or 0) for row in rows),
        "cacheLowInputTokens": sum(int(row["cache_low_input_tokens"] or 0) for row in rows),
        "errors": sum(int(row["errors"] or 0) for row in rows),
    }
    totals["cacheHitPercent"] = (totals["cachedInputTokens"] / totals["inputTokens"] * 100) if totals["inputTokens"] else 0
    totals["cacheLikelyHitPercent"] = (
        totals["cacheLikelyCachedInputTokens"] / totals["cacheLikelyInputTokens"] * 100
        if totals["cacheLikelyInputTokens"]
        else 0
    )
    return web.json_response({
        "service": "spartan-route",
        "totals": totals,
        "today": today,
        "latencyToday": today["latencyMs"],
        "firstResponseLatencyToday": today["firstResponseLatencyMs"],
        "dailyCache": rows,
        "codexSlots": state.codex.slot_status(include_paths=True) if state.codex else [],
    })


def attach_routing_usage_units(
    state: SpartanRouteState,
    payload: dict[str, Any],
) -> None:
    estimate: dict[str, Any] = {
        "available": False,
        "unavailableReason": "codex_unavailable",
        "quotaUnitsPerMillionTokens": None,
    }
    if state.codex is not None:
        try:
            account_keys = {
                str(slot.get("accountKey"))
                for slot in state.codex.slot_status()
                if isinstance(slot, Mapping) and slot.get("accountKey")
            }
            estimate = state.telemetry.codex_usage_estimate(account_keys)
        except Exception as exc:
            record_telemetry_failure(state, "read", "routing_usage_estimate", exc)
            estimate = {
                "available": False,
                "unavailableReason": "telemetry_unavailable",
                "quotaUnitsPerMillionTokens": None,
            }
        else:
            state.telemetry.record_health_success("read", "routing_usage_estimate")
    raw_rate = estimate.get("quotaUnitsPerMillionTokens")
    rate = float(raw_rate) if isinstance(raw_rate, (int, float)) and raw_rate > 0 else None
    for session in payload.get("sessions", []):
        if not isinstance(session, dict):
            continue
        items = [session, *(session.get("turns") or [])]
        for item in items:
            if not isinstance(item, dict):
                continue
            metrics = item.get("metrics")
            if not isinstance(metrics, dict):
                continue
            codex_tokens = int(metrics.get("codexInputTokens") or 0) + int(
                metrics.get("codexOutputTokens") or 0
            )
            metrics["estimatedUsageUnits"] = (
                codex_tokens / 1_000_000 * rate
                if rate is not None and int(metrics.get("codexUsageSeenRequests") or 0) > 0
                else None
            )
    payload["usageUnitEstimate"] = {
        "available": rate is not None,
        "source": estimate.get("source", "calibrated_total_tokens"),
        "unavailableReason": estimate.get("unavailableReason"),
        "quotaUnitsPerMillionTokens": rate,
        "sampleTokens": int(estimate.get("sampleTokens") or 0),
        "observedAt": estimate.get("observedAt"),
    }


async def api_routing_live(request: web.Request) -> web.Response:
    state = get_state(request)
    limit = parse_int(request.query.get("limit"), 30, 1, 100)
    session_limit = parse_int(request.query.get("sessionLimit"), limit, 1, 100)
    turn_limit = parse_int(request.query.get("turnLimit"), 20, 1, 100)
    filters = {
        "model": (request.query.get("model") or "").strip(),
        "reasoning": (request.query.get("reasoning") or "").strip().lower(),
        "prompt": (request.query.get("prompt") or "").strip(),
    }
    for name, maximum in (("model", 160), ("reasoning", 64), ("prompt", 300)):
        if len(filters[name]) > maximum:
            return response_json_error(
                f"{name} filter is too long",
                400,
                "invalid_routing_filter",
                "invalid_request_error",
            )
    try:
        payload = state.telemetry.live_sessions(
            limit=limit,
            session_limit=session_limit,
            turn_limit=turn_limit,
            **filters,
        )
    except Exception as exc:
        record_telemetry_failure(state, "read", "routing_live", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("read", "routing_live")
    payload["cacheDiagnostics"] = {"version": 2, "epoch": state.native_cache.epoch, "startedAt": state.native_cache.started_at}
    attach_routing_usage_units(state, payload)
    return web.json_response(payload)


def valid_turn_id(value: str) -> bool:
    if not 1 <= len(value) <= 128:
        return False
    if "/" in value or "\\" in value:
        return False
    return not any(ord(char) < 32 or ord(char) == 127 for char in value)


async def api_routing_turn(request: web.Request) -> web.Response:
    state = get_state(request)
    turn_id = request.match_info["turn_id"]
    if not valid_turn_id(turn_id):
        return response_json_error("Invalid turn id", 400, "invalid_turn_id", "invalid_request_error")
    try:
        payload = state.telemetry.turn_requests(turn_id)
    except Exception as exc:
        record_telemetry_failure(state, "read", "routing_turn", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("read", "routing_turn")
    return web.json_response(payload)


async def api_codex_cache_day(request: web.Request) -> web.Response:
    state = get_state(request)
    rows, error = read_daily_cache_rows(state)
    if error is not None or rows is None:
        return telemetry_unavailable_response()
    return web.json_response({"days": rows})


def classify_codex_slot_health(
    slot: Mapping[str, Any],
    *,
    slot_errors: list[dict[str, Any]],
    reset_credit_errors: list[dict[str, Any]],
    cached: bool,
    now: float | None = None,
) -> dict[str, Any]:
    slot_index = int(slot.get("slotIndex") or 0)
    now_value = time.time() if now is None else now

    def matches(error: Mapping[str, Any]) -> bool:
        try:
            return int(error.get("slotIndex")) == slot_index
        except (TypeError, ValueError):
            return False

    has_slot_error = any(matches(error) for error in slot_errors)
    has_reset_error = any(matches(error) for error in reset_credit_errors)
    has_check_error = has_slot_error or has_reset_error
    cached_error = bool(cached and has_check_error)
    rate_limited = parse_float(slot.get("rateLimitedUntil"), 0.0) > now_value
    transient = parse_float(slot.get("transientUntil"), 0.0) > now_value

    if not bool(slot.get("routingEnabled", True)):
        status = "disabled"
        reasons = ["Routing disabled by operator"]
    elif has_check_error and not cached:
        status = "failed"
        reasons = []
        if has_slot_error:
            reasons.append("Usage check failed")
        if has_reset_error:
            reasons.append("Reset-credit check failed")
    elif rate_limited or transient:
        status = "unavailable"
        reasons = []
        if rate_limited:
            reasons.append("Rate-limit cooldown active")
        if transient:
            reasons.append("Transient error cooldown active")
    elif not bool(slot.get("authAvailable")):
        status = "failed"
        reasons = ["Authentication unavailable"]
    elif cached_error:
        status = "history"
        reasons = []
        if has_slot_error:
            reasons.append("Previous usage check failed")
        if has_reset_error:
            reasons.append("Previous reset-credit check failed")
    else:
        status = "healthy"
        reasons = ["Ready"]

    return {
        "healthStatus": status,
        "healthReasons": reasons,
        "healthCached": cached_error,
    }


def codex_slots_with_health(
    payload: Mapping[str, Any],
    slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slot_errors = payload.get("slotErrors")
    reset_errors = payload.get("resetCreditErrors")
    accounts = payload.get("accounts")
    progress = payload.get("refreshProgress")
    safe_slot_errors = slot_errors if isinstance(slot_errors, list) else []
    safe_reset_errors = reset_errors if isinstance(reset_errors, list) else []
    safe_accounts = accounts if isinstance(accounts, list) else []
    pending = {
        int(index)
        for index in (
            progress.get("pendingSlotIndexes", [])
            if isinstance(progress, Mapping)
            else []
        )
        if isinstance(index, int)
    }

    rows = []
    for slot in slots:
        slot_index = int(slot.get("slotIndex") or 0)

        def matches(error: Mapping[str, Any]) -> bool:
            try:
                return int(error.get("slotIndex")) == slot_index
            except (TypeError, ValueError):
                return False

        slot_specific_errors = [error for error in safe_slot_errors if matches(error)]
        reset_specific_errors = [error for error in safe_reset_errors if matches(error)]
        has_live_account = any(
            account.get("source") == "live"
            and slot_index in (account.get("slotIndexes") or [])
            for account in safe_accounts
            if isinstance(account, Mapping)
        )
        has_live_error = any(
            error.get("source") != "persisted"
            for error in [*slot_specific_errors, *reset_specific_errors]
            if isinstance(error, Mapping)
        )
        if slot_index in pending:
            data_source = "pending"
            health_slot_errors: list[dict[str, Any]] = []
            health_reset_errors: list[dict[str, Any]] = []
        elif has_live_account or has_live_error:
            data_source = "live"
            health_slot_errors = slot_specific_errors
            health_reset_errors = reset_specific_errors
        else:
            data_source = "persisted"
            health_slot_errors = slot_specific_errors
            health_reset_errors = reset_specific_errors
        health_slot = (
            {**slot, "authAvailable": True}
            if data_source == "pending"
            else slot
        )
        health = classify_codex_slot_health(
            health_slot,
            slot_errors=health_slot_errors,
            reset_credit_errors=health_reset_errors,
            cached=data_source == "persisted",
        )
        if (
            data_source == "pending"
            and not bool(slot.get("authAvailable"))
            and health["healthStatus"] == "healthy"
        ):
            health.update({
                "healthStatus": "history",
                "healthReasons": ["Awaiting live authentication"],
            })
        rows.append({
            **slot,
            "healthDataSource": data_source,
            **health,
        })
    return rows


def attach_codex_telemetry(
    state: SpartanRouteState,
    payload: dict[str, Any],
) -> None:
    rows, error = read_daily_cache_rows(state)
    if error is not None or rows is None:
        payload["cacheDaily"] = []
        add_telemetry_error(payload, error or telemetry_error("daily_cache"))
    else:
        payload["cacheDaily"] = rows

    assert state.codex is not None
    try:
        balancer = state.codex.balancer_status(include_paths=True)
        raw_slots = balancer.get("slots")
        slot_rows = raw_slots if isinstance(raw_slots, list) else []
    except Exception as exc:
        record_telemetry_failure(state, "read", "slot_status", exc)
        add_telemetry_error(payload, telemetry_error("slot_status"))
        balancer = {
            "mode": "minimal",
            "slots": [],
            "activeLeases": state.codex.lease_status(),
        }
        slot_rows = []
    else:
        state.telemetry.record_health_success("read", "slot_status")
    slots = codex_slots_with_health(payload, slot_rows)
    account_keys = {
        str(account.get("accountKey"))
        for account in payload.get("accounts", [])
        if isinstance(account, Mapping) and account.get("accountKey")
    }
    account_keys.update(
        str(slot.get("accountKey"))
        for slot in slots
        if isinstance(slot, Mapping) and slot.get("accountKey")
    )
    try:
        multipliers = {
            account_key: state.telemetry.codex_capacity_multiplier(account_key)
            for account_key in account_keys
        }
        estimate = state.telemetry.codex_usage_estimate(account_keys)
    except Exception as exc:
        record_telemetry_failure(state, "read", "codex_usage_estimate", exc)
        add_telemetry_error(payload, telemetry_error("codex_usage_estimate"))
        multipliers = {account_key: 1 for account_key in account_keys}
        estimate = {
            "available": False,
            "source": "calibrated_total_tokens",
            "unavailableReason": "telemetry_unavailable",
            "models": [],
        }
    else:
        state.telemetry.record_health_success("read", "codex_usage_estimate")
    for account in payload.get("accounts", []):
        if not isinstance(account, dict):
            continue
        account_key = str(account.get("accountKey") or "")
        multiplier = multipliers.get(account_key, 1)
        account["capacityMultiplier"] = multiplier
        for window_name in ("weekly", "fiveHour"):
            window = account.get(window_name)
            if not isinstance(window, dict):
                continue
            used_percent = max(0.0, min(100.0, parse_float(window.get("usedPercent"), 0.0)))
            window["capacityMultiplier"] = multiplier
            window["capacityUnits"] = 100 * multiplier
            window["usedUnits"] = used_percent * multiplier
            window["remainingUnits"] = max(0.0, 100.0 - used_percent) * multiplier
    for slot in slots:
        account_key = str(slot.get("accountKey") or "")
        slot["capacityMultiplier"] = multipliers.get(account_key, 1)
    balancer["slots"] = slots
    payload["slots"] = slots
    payload["balancer"] = balancer
    payload["weeklyModelUsage"] = estimate


async def api_codex_usage(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    payload = await state.codex.usage_snapshot(force=request.query.get("force") == "true")
    attach_codex_telemetry(state, payload)
    return web.json_response(payload)


async def api_codex_usage_refresh(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    payload = state.codex.trigger_usage_refresh()
    attach_codex_telemetry(state, payload)
    return web.json_response(payload, status=202)


async def api_codex_usage_slot(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    slot_index = int(request.match_info["slot_index"])
    if not any(slot.index == slot_index for slot in state.codex.slots):
        return response_json_error("Codex slot not found", 404, "slot_not_found", "invalid_request_error")
    payload = await state.codex.usage_snapshot(force=True, slot_indexes={slot_index})
    attach_codex_telemetry(state, payload)
    return web.json_response(payload)


async def api_codex_balancer(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    try:
        payload = state.codex.balancer_status(include_paths=True)
    except Exception as exc:
        record_telemetry_failure(state, "read", "slot_status", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("read", "slot_status")
    return web.json_response(payload)


async def api_codex_balancer_slot(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    if "routingEnabled" in body:
        value = body["routingEnabled"]
    elif "enabled" in body:
        value = body["enabled"]
    else:
        return response_json_error("routingEnabled is required", 400, "missing_routing_enabled", "invalid_request_error")
    if not isinstance(value, bool):
        return response_json_error("routingEnabled must be boolean", 400, "invalid_routing_enabled", "invalid_request_error")
    slot_index = int(request.match_info["slot_index"])
    try:
        slot = await state.codex.set_slot_routing_enabled(slot_index, value)
    except IndexError:
        return response_json_error("Codex slot not found", 404, "slot_not_found", "invalid_request_error")
    except Exception as exc:
        record_telemetry_failure(state, "write", "slot_routing_override", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("write", "slot_routing_override")
    try:
        balancer = state.codex.balancer_status(include_paths=True)
    except Exception as exc:
        record_telemetry_failure(state, "read", "slot_status", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("read", "slot_status")
    return web.json_response({"slot": slot, "balancer": balancer})


async def api_codex_account_capacity(request: web.Request) -> web.Response:
    state = get_state(request)
    if state.codex is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    account_key = request.match_info["account_key"]
    if re.fullmatch(r"[0-9a-f]{16}", account_key) is None:
        return response_json_error(
            "Invalid Codex account key",
            400,
            "invalid_account_key",
            "invalid_request_error",
        )
    body = await read_json_object(request)
    if isinstance(body, web.Response):
        return body
    if set(body) != {"capacityMultiplier"}:
        return response_json_error(
            "capacityMultiplier is required and must be the only field",
            400,
            "invalid_capacity_multiplier",
            "invalid_request_error",
        )
    multiplier = body["capacityMultiplier"]
    if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier not in {1, 5}:
        return response_json_error(
            "capacityMultiplier must be 1 or 5",
            400,
            "invalid_capacity_multiplier",
            "invalid_request_error",
        )
    try:
        current_keys = {
            str(slot.get("accountKey"))
            for slot in state.codex.slot_status()
            if slot.get("accountKey")
        }
    except Exception as exc:
        record_telemetry_failure(state, "read", "slot_status", exc)
        return telemetry_unavailable_response()
    if account_key not in current_keys:
        return response_json_error(
            "Codex account is not currently configured",
            404,
            "account_not_found",
            "invalid_request_error",
        )
    try:
        state.telemetry.set_codex_capacity_multiplier(account_key, multiplier)
    except Exception as exc:
        record_telemetry_failure(state, "write", "capacity_multiplier", exc)
        return telemetry_unavailable_response()
    state.telemetry.record_health_success("write", "capacity_multiplier")
    payload = await state.codex.usage_snapshot()
    attach_codex_telemetry(state, payload)
    return web.json_response(payload)


async def dashboard_file(request: web.Request) -> web.StreamResponse:
    state = get_state(request)
    name = {
        "/dashboard": "dashboard.html",
        "/dashboard-codex": "dashboard-codex.html",
        "/dashboard-config": "dashboard-config.html",
    }.get(request.path)
    if name is None:
        return response_json_error("Dashboard not found", 404, "dashboard_not_found")
    path = (state.config.web_dir / name).resolve()
    if not path.exists() or not path.is_file():
        return response_json_error(f"Dashboard file not found: {name}", 404, "dashboard_not_found")
    return web.FileResponse(path)


async def unsupported(request: web.Request) -> web.Response:
    del request
    return response_json_error("Endpoint is not supported by spartan-route v1.", 400, "unsupported_endpoint", "invalid_request_error")


@web.middleware
async def auth_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    if request.path in VOICE_PATHS:
        return await voice_dispatch(request, handler)
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
    if candidate and hmac.compare_digest(candidate, token):
        return await handler(request)
    return web.json_response({"error": {"message": "Unauthorized. Provide Bearer token in Authorization header.", "type": "authentication_error", "code": "unauthorized"}}, status=401)


async def startup(app: web.Application) -> None:
    state: SpartanRouteState = app[STATE_KEY]
    state.session = create_client_session()
    state.control_session = create_client_session(limit=16)
    state.codex = CodexRouter(state.session, state.telemetry, control_session=state.control_session, transport_mode=state.config.codex_transport)
    bundled_models = dict(state.config.models)
    try:
        contexts = state.telemetry.codex_model_contexts()
        state.telemetry.record_health_success("read", "codex_model_contexts")
    except Exception as exc:
        contexts = {}
        record_telemetry_failure(state, "read", "codex_model_contexts", exc)

    def with_cached_contexts(entries: dict[str, ModelEntry]) -> dict[str, ModelEntry]:
        # Context metadata must neither resurrect a removed model nor grant
        # account eligibility. Overlay only entries already in this catalog.
        return {
            model_id: replace(entry, max_context=contexts[model_id])
            if entry.provider == "codex" and model_id in contexts else entry
            for model_id, entry in entries.items()
        }

    state.config = replace(state.config, models=with_cached_contexts(bundled_models))

    async def publish_catalog(slots: dict[int, SlotCatalog], discovered: dict[str, ModelEntry]) -> None:
        # Publish the catalog and its persisted-override validation together.
        async with state.routing_policy_lock:
            observed = {model_id: entry.max_context for model_id, entry in discovered.items()}
            contexts.update(observed)
            candidate = replace(state.config, models=with_cached_contexts({**bundled_models, **discovered}))
            policy = build_routing_policy_snapshot(candidate, state.telemetry)
            catalog = state.codex_catalog
            if catalog is not None:
                catalog.install_snapshot(slots, discovered)
            state.config = candidate
            state.routing_policy = policy
        try:
            state.telemetry.save_codex_model_contexts(observed)
            state.telemetry.record_health_success("write", "codex_model_contexts")
        except Exception as exc:
            record_telemetry_failure(state, "write", "codex_model_contexts", exc)

    state.codex_catalog = CodexModelDiscovery(state.codex, publish_catalog, set(bundled_models))
    state.codex.model_catalog = state.codex_catalog
    state.codex_catalog.start()
    await app[RT_KEY].start(state.codex)
    state.queue.start()


async def shutdown_realtime(app: web.Application) -> None:
    await app[RT_KEY].shutdown()


async def cleanup(app: web.Application) -> None:
    state: SpartanRouteState = app[STATE_KEY]
    if state.codex_catalog is not None:
        await state.codex_catalog.stop()
    codex_cleanup = getattr(state.codex, "cleanup", None)
    if codex_cleanup:
        await codex_cleanup()
    telemetry_drained = await state.queue.stop()
    if state.session:
        await state.session.close()
    if state.control_session:
        await state.control_session.close()
    if telemetry_drained:
        state.telemetry.close()


def create_app(config: SpartanRouteConfig | None = None, telemetry: TelemetryStore | None = None) -> web.Application:
    config = config or load_config()
    telemetry = telemetry or TelemetryStore(config.db_path)
    app = web.Application(middlewares=[auth_middleware], client_max_size=64 * 1024 * 1024)
    app.router.add_get("/dashboard-config", dashboard_file)
    app.router.add_get("/api/config/routing", api_config_routing_get)
    app.router.add_put("/api/config/routing", api_config_routing_put)
    app.router.add_delete("/api/config/routing", api_config_routing_delete)
    app[STATE_KEY] = SpartanRouteState(
        config=config,
        telemetry=telemetry,
        queue=TelemetryQueue(
            telemetry,
            capacity=config.telemetry_queue_capacity,
            retention_batch_size=config.telemetry_retention_batch_size,
            shutdown_timeout=config.telemetry_shutdown_timeout_ms / 1000,
        ),
        routing_policy=build_routing_policy_snapshot(config, telemetry),
    )
    register_realtime(app, RealtimeConfig.from_env(config.token))
    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown_realtime)
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
    app.router.add_post("/api/codex/usage/refresh", api_codex_usage_refresh)
    app.router.add_post(r"/api/codex/usage/slots/{slot_index:\d+}", api_codex_usage_slot)
    app.router.add_get("/api/codex/balancer", api_codex_balancer)
    app.router.add_patch(r"/api/codex/balancer/slots/{slot_index:\d+}", api_codex_balancer_slot)
    app.router.add_patch(
        r"/api/codex/balancer/accounts/{account_key}/capacity",
        api_codex_account_capacity,
    )
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
