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
    build_codex_responses_headers,
    codex_request_timeout_ms,
    parse_codex_error,
    parse_sse_response,
    resolve_affinity_key,
    resolve_prompt_cache_key,
    should_retry,
    sse,
)
from .util import stable_json, token_count_estimate
from .native_cache import NativeCacheDiagnostics, provider_cache_fields, request_diagnostics, cache_read_known
from .cache_experiments import CacheExperiments, ProbeTicket, ProbeRejected
from .transport import TransportError
from .attempt import CodexAttempt, apply_outcome
from .pi_usage import CURRENT_REQUEST

CODEX_RESPONSES_REALM = "codex_subscription"
CODEX_RESPONSES_MAX_ATTEMPTS = 3
CODEX_RESPONSES_PRECOMMIT_MAX_EVENTS = 64
CODEX_RESPONSES_PRECOMMIT_MAX_BYTES = 256 * 1024


@dataclass
class OpenedResponse:
    response: Any
    response_time_ms: int | None


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
    # Events are immutable after parsing; only the response metadata needs copying.
    updated = dict(payload)
    if isinstance(payload.get("response"), dict):
        updated["response"] = dict(payload["response"])
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
    if result.first_output_at is None and event_name in {"response.output_text.delta", "response.function_call_arguments.delta", "response.custom_tool_call_input.delta"} and isinstance(payload.get("delta"), str) and payload["delta"]:
        result.first_output_at = time.monotonic()
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
        result.cache_read_known = cache_read_known(usage)
        if not result.cache_read_known:
            result.cached_input_tokens = 0  # Compatibility counter; v2 marks this unknown.
        result.usage_event_type = event_name
    response = response_object(payload)
    if response is not None and isinstance(response.get("id"), str) and len(response["id"]) <= 256:
        result.cache_response_id = response["id"]
    output = response.get("output") if response is not None else None
    if response is not None and "output" in response and not isinstance(output, list):
        result._native_tool_invalid = True
    if isinstance(output, list):
        for index, item in enumerate(output):
            capture_tool_item(result, item, index)
    if event_name in {"response.output_item.added", "response.output_item.done"}:
        capture_tool_item(result, payload.get("item"), payload.get("output_index"))
    if event_name in TERMINAL_CODEX_EVENTS:
        result.error = terminal_error(payload, event_name)
        result.completed = bool(event_name == "response.completed" and response is not None and response.get("status") == "completed")
        try:
            result.cache_write_tokens, result.cache_provider_diagnostics = provider_cache_fields(payload)
        except Exception:
            pass
        result.tool_telemetry_complete = bool(
            event_name == "response.completed" and response is not None
            and response.get("status") == "completed" and not result._native_tool_invalid
        )
        if result.tool_telemetry_complete:
            result.actual_tool_call_count = len(result._native_tool_names)
            result.actual_tool_names = sorted(set(result._native_tool_names.values()))


def terminal_error(payload: dict[str, Any], event_name: str) -> str | None:
    response = response_object(payload) or {}
    if event_name == "response.completed" and response.get("status") == "completed":
        return None
    error = response.get("error") or payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or error.get("type") or "codex_error")
    return "codex_incomplete_response"


def missing_terminal(result: CodexCallResult) -> bytes:
    result.completed = False
    result.error = "codex_incomplete_response"
    return sse({"type": "response.failed", "response": {"status": "failed", "error": {
        "code": result.error, "type": "upstream_error", "message": "Upstream ended before a terminal response"}}}, "response.failed")


def sanitized_error_code(text: str) -> str:
    error = parse_codex_error(text)
    return str(error.get("code") or error.get("type") or "codex_error")


async def open_response(
    router: CodexRouter,
    body: dict[str, Any],
    auth: CodexAuth,
    scope: str | None = None,
) -> OpenedResponse | CodexCallResult:
    headers = build_codex_responses_headers(body, auth)
    started = time.monotonic()
    try:
        response = await router.open_inference(body, headers, auth, scope)
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
    elapsed = None if getattr(response, "is_websocket", False) else max(0, int((time.monotonic() - started) * 1000))
    if response.status != 200:
        text = await response.text()
        response.release()
        return CodexCallResult(
            status=response.status,
            body=text.encode(),
            headers={"Content-Type": "application/json"},
            error=sanitized_error_code(text),
            upstream_response_time_ms=elapsed,
            transport_details=getattr(response, "transport_details", None),
            retry_allowed=getattr(response, "retry_allowed", True),
        )
    return OpenedResponse(response, elapsed)


async def collect_response(
    router: CodexRouter,
    opened: OpenedResponse,
    slot: CodexSlot,
    realm: str,
) -> CodexCallResult:
    result = CodexCallResult(
        status=200, upstream_response_time_ms=opened.response_time_ms,
        transport_details=getattr(opened.response, "transport_details", None),
        retry_allowed=getattr(opened.response, "retry_allowed", True),
    )
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
    except TransportError as exc:
        result.status, result.error, result.retry_allowed = exc.status, exc.code, exc.retry_allowed
        client_error = router._websocket_stream_error(slot, exc)
        result.body = {"error": exc.error if exc.retry_allowed else client_error}
        return result
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
    if status in {"failed", "cancelled", "incomplete"}:
        error = terminal.get("error")
        result.error = router._error_code(error) if isinstance(error, dict) else f"response_{status}"
    return result


async def buffer_precommit(events: Any, result: CodexCallResult, realm: str) -> tuple[list[bytes], bool]:
    buffered = []
    size = 0
    async for event, data in events:
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except (ValueError, TypeError):
            result._native_tool_invalid = True
            continue
        if not isinstance(payload, dict):
            result._native_tool_invalid = True
            continue
        name = response_event_name(event, payload)
        encoded = sse(annotate_realm(payload, realm), name)
        buffered.append(encoded)
        size += len(encoded)
        capture_telemetry(result, payload, name)
        if name == "error":
            result.error = sanitized_error_code(data)
        if len(buffered) > CODEX_RESPONSES_PRECOMMIT_MAX_EVENTS or size > CODEX_RESPONSES_PRECOMMIT_MAX_BYTES or name not in {"response.created", "response.in_progress"}:
            return buffered, name in TERMINAL_CODEX_EVENTS or name == "error"
    return [], False


async def prepare_stream(
    router: CodexRouter,
    opened: OpenedResponse,
    result: CodexCallResult,
    slot: CodexSlot,
    realm: str,
) -> bool:
    events = parse_sse_response(opened.response)
    try:
        buffered, first_terminal = await buffer_precommit(events, result, realm)
        if not buffered:
            opened.response.release()
            return False
    except TransportError as exc:
        result.status, result.error, result.retry_allowed = exc.status, exc.code, exc.retry_allowed
        result.body = {"error": exc.error}
        opened.response.release()
        return False
    except Exception:
        opened.response.release()
        return False

    async def stream() -> AsyncIterator[bytes]:
        terminal_seen = first_terminal
        try:
            for chunk in buffered:
                yield chunk
            if terminal_seen:
                return
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
                if event_name in TERMINAL_CODEX_EVENTS:
                    terminal_seen = True
                elif event_name == "error":
                    terminal_seen = True
                    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
                    result.error = router._error_code(error)
                yield sse(annotate_realm(payload, realm), event_name)
                if terminal_seen:
                    return
            if not terminal_seen:
                yield missing_terminal(result)
        except TransportError as exc:
            error = router._websocket_stream_error(slot, exc)
            result.error = router._error_code(error)
            result.completed = False
            yield sse({"error": error}, "error")
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
    diagnostics: NativeCacheDiagnostics | None = None,
    experiments: CacheExperiments | None = None,
    probe: ProbeTicket | None = None,
) -> CodexCallResult:
    model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    upstream_cache_key = resolve_prompt_cache_key(request, session_id, prompt_cache_key)
    affinity_key = str(request.get("spartan_chain_id") or "").strip() or resolve_affinity_key(request, session_id, prompt_cache_key)
    pi_request = CURRENT_REQUEST.get()
    if pi_request is not None:
        pi_request.affinity(affinity_key)
    base_body = build_codex_responses_body(request, model_name, upstream_cache_key)
    reasoning = base_body.get("reasoning")
    reasoning_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    diagnostics = diagnostics or getattr(router, "native_cache", None) or NativeCacheDiagnostics()
    router.native_cache = diagnostics
    attempts: list[dict[str, Any]] = []
    max_attempts = 1 if probe is not None else min(CODEX_RESPONSES_MAX_ATTEMPTS, max(1, len(router.slots)))
    excluded_slots: set[int] = set()
    excluded_accounts: set[str] = set()
    last_result: CodexCallResult | None = None
    selection_diagnostics: dict[str, Any] = {}

    if probe is None:
        initial_state = await router.slot_availability()
        if initial_state.eligible == 0 and initial_state.rate_limited:
            await router._probe_rate_limit_recovery()

    for _attempt in range(max_attempts):
        if probe is not None:
            if experiments is None:
                raise ProbeRejected("probe_unavailable")
            try:
                chosen = await experiments.choose(router, probe)
            except BaseException as exc:
                experiments.finish(probe, success=False, sent=False)
                if not isinstance(exc, Exception):
                    raise
                return CodexCallResult(status=503, body={"error": {"code": "probe_account_unavailable"}}, error="probe_account_unavailable")
        else:
            chosen = await router.choose_slot_with_catalog_recovery(
                affinity_key, excluded_slots, excluded_accounts, model_id, reasoning_effort,
                diagnostics=selection_diagnostics,
            )
        if not chosen:
            break
        slot, auth = chosen
        attempt = CodexAttempt(slot, auth, slot.auth_fingerprint)
        catalog = getattr(router, "model_catalog", None)
        if probe is not None and catalog is not None and not catalog.supports(slot, auth, model_id, reasoning_effort):
            experiments.finish(probe, success=False, sent=False)
            return CodexCallResult(status=503, body={"error": {"code": "probe_account_unavailable"}}, error="probe_account_unavailable")
        body, experiment = base_body, probe.metadata() if probe else None
        if experiments is not None and probe is None:
            body, experiment = experiments.cohort_body(
                base_body, model=model_id, account=auth.account_key,
                session_scope=diagnostics.session_scope(CODEX_RESPONSES_REALM, str(request.get("spartan_chain_id") or session_id or "") or None),
                candidate_signature=lambda: diagnostics.candidate_signature(base_body, upstream_cache_key),
            )
        cohort_attempt_id = None
        if experiment and experiment.get("kind") == "cohort" and not experiment.get("skipped"):
            cohort_attempt_id = experiments.reserve_cohort(experiment, auth.account_key)
            if cohort_attempt_id is None:
                body, experiment = base_body, {**experiment, "skipped": True}
            else:
                experiment = {**experiment, "attempt_id": cohort_attempt_id}
        observation = diagnostics.begin(
            body, endpoint=CODEX_RESPONSES_URL, realm=CODEX_RESPONSES_REALM,
            model=model_name, account=auth.account_key, slot=slot.index,
            chain=str(request.get("spartan_chain_id") or session_id or "") or None,
            cache_key=upstream_cache_key,
        )

        if experiment is not None:
            observation.metadata["experiment"] = experiment

        def finish_probe(success: bool, response_id: str | None = None) -> None:
            if probe is not None and experiments is not None:
                experiments.finish(probe, success=success, response_id=response_id)
            if cohort_attempt_id is not None and experiments is not None:
                experiments.finish_cohort(cohort_attempt_id, success)

        async def finish_cache(current: CodexCallResult, *, delivered: bool = True) -> None:
            # Only result objects carry usage. OpenedResponse is never accepted.
            if not isinstance(current, CodexCallResult):
                return
            attempts.append(diagnostics.finish(
                observation, success=delivered and current.completed and not current.error,
                cache_write_tokens=current.cache_write_tokens,
                cache_read_known=current.cache_read_known, status=current.status, error=current.error,
                first_output_at=current.first_output_at,
                provider_diagnostics=current.cache_provider_diagnostics,
            ))
            current.native_cache = request_diagnostics(attempts)
            finish_probe(delivered and current.completed and not current.error and current.usage_seen, current.cache_response_id)
            if pi_attempt is not None:
                await pi_attempt.finish(current, incomplete=not delivered or not current.completed or bool(current.error))

        slot.in_flight += 1
        slot.last_selected_at = time.time()
        pi_attempt = pi_request.start("codex") if pi_request is not None else None
        try:
            opened = await open_response(router, body, auth, affinity_key)
        except BaseException:
            diagnostics.finish(observation, success=False, error="request_aborted")
            finish_probe(False)
            slot.in_flight = max(0, slot.in_flight - 1)
            if pi_attempt is not None:
                await pi_attempt.finish(incomplete=True)
            raise
        if isinstance(opened, CodexCallResult):
            slot.in_flight = max(0, slot.in_flight - 1)
            opened.selection_diagnostics = selection_diagnostics or None
            opened.slot_index = slot.index
            opened.account_key = auth.account_key
            await finish_cache(opened)
            last_result = opened
            text = opened.body.decode("utf-8", errors="replace") if isinstance(opened.body, bytes) else stable_json(opened.body)
            if not opened.retry_allowed or not should_retry(opened.status, text):
                return opened
            excluded_slots.add(slot.index)
            if auth.account_key:
                excluded_accounts.add(auth.account_key)
            apply_outcome(router, attempt, opened.status, text)
            continue

        result = CodexCallResult(
            status=200,
            selection_diagnostics=selection_diagnostics or None,
            slot_index=slot.index,
            account_key=auth.account_key,
            input_tokens=token_count_estimate(request),
            upstream_response_time_ms=opened.response_time_ms,
            transport_details=getattr(opened.response, "transport_details", None),
            retry_allowed=getattr(opened.response, "retry_allowed", True),
        )
        if wants_stream:
            try:
                committed = await prepare_stream(router, opened, result, attempt, CODEX_RESPONSES_REALM)
            except BaseException:
                diagnostics.finish(observation, success=False, error="request_aborted")
                finish_probe(False)
                slot.in_flight = max(0, slot.in_flight - 1)
                opened.response.release()
                if pi_attempt is not None:
                    await pi_attempt.finish(incomplete=True)
                raise
            if committed:
                source = result.stream
                drained = False
                cleaned = False

                async def cleanup_stream() -> None:
                    nonlocal cleaned
                    if cleaned:
                        return
                    cleaned = True
                    try:
                        if source is not None:
                            await source.aclose()
                    finally:
                        opened.response.release()
                        slot.in_flight = max(0, slot.in_flight - 1)
                        await finish_cache(result, delivered=drained)

                async def guarded_stream() -> AsyncIterator[bytes]:
                    nonlocal drained
                    try:
                        if source is not None:
                            async for chunk in source:
                                yield chunk
                        drained = True
                    finally:
                        await cleanup_stream()
                        if result.completed and not result.error and affinity_key:
                            router._remember_lease(affinity_key, attempt)

                result.stream_cleanup = cleanup_stream
                result.stream = guarded_stream()
                return result
            slot.in_flight = max(0, slot.in_flight - 1)
            if not result.retry_allowed:
                if result.body is None:
                    result.status = 502
                    result.error = "codex_stream_error"
                    result.body = {"error": {"code": result.error, "type": "upstream_error", "message": "Codex stream ended before output"}}
                await finish_cache(result)
                return result
            if result.status >= 400 and result.body is not None:
                await finish_cache(result)
                text = stable_json(result.body)
                if not should_retry(result.status, text):
                    return result
                excluded_slots.add(slot.index)
                if auth.account_key:
                    excluded_accounts.add(auth.account_key)
                if result.status == 401:
                    router._quarantine_auth_failure(attempt)
                elif result.status == 429:
                    router.mark_rate_limited(attempt, text)
                else:
                    router.mark_transient(attempt)
                last_result = result
                continue
            excluded_slots.add(slot.index)
            if auth.account_key:
                excluded_accounts.add(auth.account_key)
            router.mark_transient(attempt)
            last_result = CodexCallResult(
                status=502,
                body={"error": {"message": "Codex Responses stream ended before commit", "type": "upstream_error", "code": "codex_stream_error"}},
                error="codex_stream_error",
                slot_index=slot.index,
                account_key=auth.account_key,
            )
            await finish_cache(last_result)
            continue

        try:
            collected = await collect_response(router, opened, attempt, CODEX_RESPONSES_REALM)
        except BaseException:
            diagnostics.finish(observation, success=False, error="request_aborted")
            finish_probe(False)
            if pi_attempt is not None:
                await pi_attempt.finish(incomplete=True)
            raise
        finally:
            slot.in_flight = max(0, slot.in_flight - 1)
        collected.selection_diagnostics = selection_diagnostics or None
        collected.slot_index = slot.index
        collected.account_key = auth.account_key
        await finish_cache(collected)
        if not collected.retry_allowed or collected.status < 400 or not should_retry(collected.status, stable_json(collected.body)):
            if collected.completed and not collected.error and affinity_key:
                router._remember_lease(affinity_key, attempt)
            return collected
        excluded_slots.add(slot.index)
        if auth.account_key:
            excluded_accounts.add(auth.account_key)
        error_text = stable_json(collected.body)
        if collected.status == 401:
            router._quarantine_auth_failure(attempt)
        elif collected.status == 429:
            router.mark_rate_limited(attempt, error_text)
        else:
            router.mark_transient(attempt)
        last_result = collected

    if selection_diagnostics.get("operational_eligible", 0) > 0 and selection_diagnostics.get("model_eligible") == 0:
        unavailable = await router._unavailable_result(selection_diagnostics)
        if last_result is not None:
            unavailable.native_cache = last_result.native_cache
        return unavailable
    if last_result is not None:
        last_result.selection_diagnostics = selection_diagnostics or None
        return last_result
    return await router._unavailable_result(selection_diagnostics)
