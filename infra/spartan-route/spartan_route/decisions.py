"""Native OpenAI Decisions. No chat conversion, Codex quota, or chat pricing."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from aiohttp import ClientError, ClientTimeout, web

from .provider_policy import DECISIONS, admission_error
from .telemetry import RoutingLogEntry
from .util import json_error, now_iso


def validate(body: dict) -> dict:
    """Check the native envelope; leave optional schema details to OpenAI.

    Contract: developers.openai.com/api/reference/resources/decisions/methods/create.
    Input items, multimodal instructions, unnamed questions and typed choice values
    must pass unchanged. The upstream owns validation of those native structures.
    """
    from .app import normalize_model_id
    if normalize_model_id(body.get("model")) not in {"openai/gpt-6-luna", "gpt-6-luna"}:
        raise ValueError("Decisions requires openai/gpt-6-luna or gpt-6-luna.")
    if not isinstance(body.get("input"), (str, list)):
        raise ValueError("input must be a string or an array of native input items.")
    questions = body.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 400:
        raise ValueError("questions must contain 1 to 400 questions.")
    for question in questions:
        if (not isinstance(question, dict) or question.get("type") not in ("predicate", "choice", "score")
                or not isinstance(question.get("instructions"), (str, list))):
            raise ValueError("Each question requires predicate, choice or score type and instructions.")
    return {**body, "model": "gpt-6-luna"}


async def execute(state, payload):
    from .app import ExecutionResult, apply_usage, provider_base_url
    if not state.config.api_key("openai"):
        return ExecutionResult(503, body=json_error("Missing OPENAI_API_KEY", 503, "provider_key_missing", "authentication_error")[0], error="provider_key_missing")
    if state.session is None:
        return ExecutionResult(503, body=json_error("HTTP session is not ready", 503, "session_unavailable")[0], error="session_unavailable")
    timeout = int(os.environ.get("SPARTAN_ROUTE_PROVIDER_TIMEOUT_MS") or "180000") / 1000
    try:
        async with state.session.post(
            provider_base_url("openai") + "/decisions", json=payload,
            headers={"Authorization": "Bearer " + state.config.api_key("openai"), "Content-Type": "application/json"},
            timeout=ClientTimeout(total=timeout),
        ) as response:
            raw = await response.read()
            result = ExecutionResult(response.status, body=raw,
                                     headers={"Content-Type": response.headers.get("Content-Type", "application/json")},
                                     error="provider_decisions_error" if response.status >= 400 else None)
            try:
                apply_usage(result, json.loads(raw))
            except (ValueError, TypeError):
                pass  # Native response bytes and status remain authoritative.
            return result
    except (asyncio.TimeoutError, ClientError):
        return ExecutionResult(502, body=json_error("Provider Decisions request failed", 502, "provider_request_failed", "upstream_error")[0], error="provider_request_failed")


def record(state, model: str, result, started: float) -> None:
    # Costs remain unknown (zero legacy numeric fields), never inferred from
    # chat model prices. request_api_kind keeps this out of subscription usage.
    entry = RoutingLogEntry(
        timestamp=now_iso(), original_model=model, routed_model="openai/gpt-6-luna",
        actual_model="openai/gpt-6-luna", tier="passthrough", classification_reason="native Decisions",
        input_tokens=result.input_tokens, cached_input_tokens=result.cached_input_tokens,
        output_tokens=result.output_tokens, reasoning_tokens=result.reasoning_tokens,
        response_time_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_api_kind="decisions", request_id=uuid.uuid4().hex[:12], error=result.error,
    )
    try:
        state.queue.log(entry)
    except Exception:
        # Telemetry is subordinate to the native response. Do not log input,
        # provider messages or credentials when a queue implementation fails.
        state.telemetry.record_health_failure("write", "decisions_telemetry")


def register(app: web.Application, get_state) -> None:
    async def decisions(request: web.Request):
        from .app import write_execution_response
        try:
            body = await request.json()
        except web.HTTPRequestEntityTooLarge:
            raise
        except (ValueError, UnicodeError):
            return web.json_response(json_error("Invalid JSON body", 400, "invalid_json")[0], status=400)
        try:
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object.")
            payload = validate(body)
        except ValueError as exc:
            return web.json_response(json_error(str(exc), 400, "invalid_decisions_request", "invalid_request_error")[0], status=400)
        state = get_state(request)
        error = admission_error(state, "openai", DECISIONS, payload["model"])
        if error is not None:
            return error
        started = time.monotonic()
        result = await execute(state, payload)
        record(state, body["model"], result, started)
        return await write_execution_response(request, result)

    app.router.add_post(DECISIONS, decisions)
