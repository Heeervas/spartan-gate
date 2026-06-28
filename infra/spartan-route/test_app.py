from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.app import (
    ExecutionResult,
    RoutedRequest,
    SpartanRouteState,
    STATE_KEY,
    build_request_shape_diagnostics,
    compare_session_trace,
    create_app,
    execute_chat,
    chat_sse_to_responses_sse,
    log_request,
    prompt_preview_from_request,
    request_prompt_cache_key_source,
    request_shape_snapshot,
)
from spartan_route.config import ModelEntry, SpartanRouteConfig
from spartan_route.codex import CodexAuth, CodexCallResult, CodexSlot, sse
from spartan_route.telemetry import RoutingLogEntry, TelemetryStore
from spartan_route.util import now_iso


class CaptureQueue:
    def __init__(self) -> None:
        self.entries = []

    def log(self, entry) -> None:
        self.entries.append(entry)


class FakeUpstreamResponse:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self._body = json.dumps(body or {"created": 1, "data": [{"b64_json": "ZmFrZQ=="}]}).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self) -> bytes:
        return self._body


class FakeOpenAISession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeUpstreamResponse()

    async def close(self) -> None:
        pass


class FakeImageCodex:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def execute_image(self, request: dict[str, Any]) -> CodexCallResult:
        self.requests.append(request)
        return CodexCallResult(status=200, body={"created": 1, "data": [{"b64_json": "ZmFrZQ=="}]})

    def slot_status(self):
        return []


def test_config() -> SpartanRouteConfig:
    tier_models = {
        "heartbeat": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
        "simple": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
        "moderate": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "complex": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "frontier-sonnet": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "frontier-opus": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
    }
    models = {
        "anthropic/test": ModelEntry("anthropic/test", "anthropic"),
        "codex/gpt-5.5": ModelEntry("codex/gpt-5.5", "codex"),
        "codex/gpt-5.4": ModelEntry("codex/gpt-5.4", "codex"),
        "codex/gpt-5.4-mini": ModelEntry("codex/gpt-5.4-mini", "codex"),
    }
    return SpartanRouteConfig(
        host="127.0.0.1",
        port=18790,
        token="secret",
        enabled=True,
        dry_run=False,
        debug=False,
        log_content=False,
        db_path=":memory:",
        config_dir=Path("."),
        web_dir=Path("infra/spartan-route/web"),
        provider_profile="codex",
        baseline_model="codex/gpt-5.5",
        tier_models=tier_models,
        models=models,
    )


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TelemetryStore(":memory:")
        self.app = create_app(test_config(), self.store)
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_health_and_models_do_not_require_auth(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        state.codex.slots = [CodexSlot(0, path="/private/codex/auth.json", auth=CodexAuth("token", account_id="acct"))]

        health = await self.client.get("/health")
        self.assertEqual(health.status, 200)
        health_payload = await health.json()
        self.assertNotIn("slotPath", health_payload["codexSlots"][0])
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        payload = await models.json()
        self.assertIn("custom-1/clawroute/auto", [item["id"] for item in payload["data"]])

    async def test_api_requires_bearer_token(self) -> None:
        missing = await self.client.get("/api/routing/live")
        self.assertEqual(missing.status, 401)
        query_token = await self.client.get("/api/routing/live?token=secret")
        self.assertEqual(query_token.status, 401)
        ok = await self.client.get("/api/routing/live", headers={"Authorization": "Bearer secret"})
        self.assertEqual(ok.status, 200)

    async def test_stats_include_today_latency(self) -> None:
        self.store.log_routing(RoutingLogEntry(
            timestamp=now_iso(),
            original_model="clawroute/auto",
            routed_model="codex/gpt-5.5",
            actual_model="codex/gpt-5.5",
            tier="complex",
            input_tokens=2000,
            cached_input_tokens=1000,
            output_tokens=50,
            response_time_ms=800,
            context_info='{"usage_seen":true}',
        ))
        response = await self.client.get("/stats", headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertIn("today", payload)
        self.assertIn("latencyToday", payload)
        self.assertGreaterEqual(payload["today"]["loggedRequests"], 1)
        self.assertGreaterEqual(payload["latencyToday"]["avg"], 0)

    async def test_invalid_turn_id_is_rejected_before_query(self) -> None:
        response = await self.client.get(
            f"/api/routing/turns/{'x' * 140}",
            headers={"Authorization": "Bearer secret"},
        )
        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"]["code"], "invalid_turn_id")

    async def test_codex_slot_routing_patch_persists_override(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        state.codex.slots = [CodexSlot(0, path="/tmp/auth.json", auth=CodexAuth("token", account_id="acct"))]
        response = await self.client.patch(
            "/api/codex/balancer/slots/0",
            headers={"Authorization": "Bearer secret"},
            json={"routingEnabled": False},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["slot"]["routingEnabled"])
        self.assertFalse(self.store.slot_routing_override(0)["routingEnabled"])

    async def test_codex_balancer_api_exposes_slot_path_after_auth(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        state.codex.slots = [CodexSlot(0, path="/tmp/auth.json", auth=CodexAuth("token", account_id="acct"))]

        response = await self.client.get("/api/codex/balancer", headers={"Authorization": "Bearer secret"})

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["slots"][0]["slotPath"], "/tmp/auth.json")

    async def test_unsupported_provider_returns_json_error(self) -> None:
        response = await self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={"model": "anthropic/test", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(response.status, 503)
        payload = await response.json()
        self.assertEqual(payload["error"]["code"], "provider_not_supported")

    async def test_request_shape_diagnostics_are_metadata_only(self) -> None:
        body = {
            "messages": [
                {"role": "system", "content": "private system prompt"},
                {"role": "user", "content": "private user text"},
            ],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        }
        snapshot = request_shape_snapshot(body)
        diagnostics = build_request_shape_diagnostics(snapshot)
        serialized = json.dumps(diagnostics)

        self.assertNotIn("private system prompt", serialized)
        self.assertNotIn("private user text", serialized)
        self.assertEqual(compare_session_trace(snapshot, None)["comparison"], "baseline")

        retry = request_shape_snapshot(body)
        self.assertEqual(compare_session_trace(retry, snapshot)["comparison"], "retry")

        appended = {**body, "messages": [*body["messages"], {"role": "assistant", "content": "new"}]}
        self.assertEqual(compare_session_trace(request_shape_snapshot(appended), snapshot)["comparison"], "prefix")

    async def test_codex_image_generation_rejects_non_square_size(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret"},
                json={"model": "gpt-image-2", "prompt": "draw a guarded gate", "size": "1536x1024"},
            )

        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["code"], "unsupported_image_size")
        self.assertEqual(
            payload["error"]["message"],
            "Requested image size 1536x1024 requires a non-square aspect ratio, but Spartan Route is using the Codex Auth image fallback, which currently cannot guarantee non-square output. Configure OPENAI_API_KEY for OpenAI Images passthrough or request square size 1024x1024.",
        )

    async def test_codex_image_generation_rejects_invalid_size(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret"},
                json={"model": "gpt-image-2", "prompt": "draw a guarded gate", "size": "bad"},
            )

        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["code"], "invalid_request")

    async def test_codex_image_generation_allows_square_size(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = FakeImageCodex()
        state.codex = fake_codex

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret"},
                json={"model": "gpt-image-2", "prompt": "draw a guarded gate", "size": "1024x1024"},
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_codex.requests[0]["size"], "1024x1024")

    async def test_openai_passthrough_preserves_non_square_generation_size(self) -> None:
        state = self.app[STATE_KEY]
        if state.session is not None:
            await state.session.close()
        fake_session = FakeOpenAISession()
        state.session = fake_session

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            response = await self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret"},
                json={"model": "gpt-image-2", "prompt": "draw a guarded gate", "size": "1536x1024"},
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_session.calls[0]["json"]["size"], "1536x1024")
        self.assertTrue(fake_session.calls[0]["url"].endswith("/images/generations"))

    async def test_codex_image_edits_reject_non_square_size(self) -> None:
        form = FormData()
        form.add_field("model", "gpt-image-2")
        form.add_field("prompt", "make the red square landscape")
        form.add_field("size", "1024x1536")
        form.add_field("image", b"\x89PNG\r\n", filename="red.png", content_type="image/png")

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret"},
                data=form,
            )

        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["code"], "unsupported_image_size")
        self.assertEqual(
            payload["error"]["message"],
            "Requested image size 1024x1536 requires a non-square aspect ratio, but Spartan Route is using the Codex Auth image fallback, which currently cannot guarantee non-square output. Configure OPENAI_API_KEY for OpenAI Images passthrough or request square size 1024x1024.",
        )


class AppCacheDiagnosticsTests(unittest.TestCase):
    def route(self) -> RoutedRequest:
        entry = ModelEntry("codex/gpt-5.5", "codex")
        return RoutedRequest(
            original_model="clawroute/auto",
            routed_model="codex/gpt-5.5",
            actual_model="codex/gpt-5.5",
            provider="codex",
            tier="complex",
            reason="test",
            entry=entry,
        )

    def state(self) -> SpartanRouteState:
        return SpartanRouteState(test_config(), TelemetryStore(":memory:"), CaptureQueue())

    def content_logging_state(self) -> SpartanRouteState:
        return SpartanRouteState(replace(test_config(), log_content=True), TelemetryStore(":memory:"), CaptureQueue())

    def test_prompt_cache_key_source_is_metadata_only(self) -> None:
        self.assertEqual(request_prompt_cache_key_source({"prompt_cache_key": "raw"}, None), "prompt_cache_key")
        self.assertEqual(request_prompt_cache_key_source({"user": "raw"}, None), "user")
        self.assertEqual(
            request_prompt_cache_key_source({"messages": [{"role": "system", "content": "stable"}]}, None),
            "content_prompt_cache_key",
        )
        self.assertEqual(request_prompt_cache_key_source({}, "session"), "session_id")
        self.assertEqual(request_prompt_cache_key_source({}, None), "none")

    def test_log_request_adds_cache_diagnostics_without_raw_key(self) -> None:
        state = self.state()
        body = {
            "model": "clawroute/auto",
            "prompt_cache_key": "raw-secret-cache-key",
            "messages": [{"role": "system", "content": "private system"}, {"role": "user", "content": "hello"}],
        }
        result = ExecutionResult(
            200,
            input_tokens=25000,
            cached_input_tokens=12000,
            output_tokens=300,
            usage_seen=True,
            usage_event_type="response.completed",
        )

        log_request(state, body, self.route(), result, 0, "req", "session", "turn", "chat_completions")

        context = json.loads(state.queue.entries[0].context_info)
        serialized = json.dumps(context)
        self.assertEqual(context["cache_key_source"], "prompt_cache_key")
        self.assertEqual(context["usage_seen"], True)
        self.assertEqual(context["usage_event_type"], "response.completed")
        self.assertAlmostEqual(context["cache_hit_ratio"], 0.48)
        self.assertNotIn("raw-secret-cache-key", serialized)
        self.assertNotIn("private system", serialized)

    def test_log_request_preview_uses_last_user_message_not_system_prompt(self) -> None:
        state = self.content_logging_state()
        body = {
            "model": "clawroute/auto",
            "messages": [
                {"role": "system", "content": "private system prompt must not be stored"},
                {"role": "user", "content": "[SYSTEM: noisy]\nSender (untrusted metadata):\n```json\n{\"sender\":\"x\"}\n```\nActual question token=super-secret"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{\"query\":\"secret\"}"}},
                ]},
                {"role": "tool", "tool_call_id": "call-1", "content": "result token=super-secret"},
            ],
        }
        result = ExecutionResult(
            200,
            input_tokens=25000,
            cached_input_tokens=12000,
            output_tokens=300,
            usage_seen=True,
            upstream_response_time_ms=123,
        )

        self.assertEqual(prompt_preview_from_request(body, True), "Actual question token=[REDACTED]")
        log_request(state, body, self.route(), result, 0, "req", "session", "turn", "chat_completions")

        entry = state.queue.entries[0]
        context = json.loads(entry.context_info)
        serialized = json.dumps(context)
        self.assertEqual(entry.prompt_preview, "Actual question token=[REDACTED]")
        self.assertEqual(entry.response_time_ms, 123)
        self.assertEqual(entry.upstream_response_time_ms, 123)
        self.assertGreaterEqual(entry.total_response_time_ms, 0)
        self.assertNotIn("private system prompt", serialized)
        self.assertEqual(context["request_trace"]["delta"]["items"][0]["role"], "user")
        self.assertEqual(context["request_trace"]["delta"]["items"][0]["preview"], "Actual question token=[REDACTED]")
        self.assertEqual(context["request_trace"]["delta"]["items"][1]["toolCalls"][0]["name"], "lookup")

    def test_cache_guard_marks_second_consecutive_expected_low_hit(self) -> None:
        state = self.state()
        base_messages = [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "first"},
        ]
        route = self.route()

        bodies = [
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": base_messages},
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": [*base_messages, {"role": "assistant", "content": "a"}]},
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": [*base_messages, {"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}]},
        ]
        for index, body in enumerate(bodies):
            result = ExecutionResult(
                200,
                slot_index=0,
                account_key="account",
                input_tokens=25000,
                cached_input_tokens=0,
                output_tokens=100,
                usage_seen=True,
                usage_event_type="response.completed",
            )
            log_request(state, body, route, result, 0, f"req-{index}", "session", f"turn-{index}", "chat_completions")

        contexts = [json.loads(entry.context_info) for entry in state.queue.entries]
        self.assertFalse(contexts[0]["expected_cache_hit"])
        self.assertTrue(contexts[1]["expected_cache_hit"])
        self.assertFalse(contexts[1]["cache_low"])
        self.assertTrue(contexts[2]["expected_cache_hit"])
        self.assertTrue(contexts[2]["cache_low"])

    def test_stream_stall_guard_records_diagnostic_without_blocking(self) -> None:
        state = self.state()
        route = self.route()
        bodies = [
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": [{"role": "system", "content": "stable"}]},
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "next"}]},
        ]

        for index, body in enumerate(bodies):
            result = ExecutionResult(
                200,
                slot_index=0,
                account_key="account",
                input_tokens=60000,
                cached_input_tokens=0,
                output_tokens=0,
                error="codex_stream_timeout",
                usage_seen=False,
                upstream_response_time_ms=1200,
                total_response_time_ms=180000,
            )
            log_request(state, body, route, result, 0, f"req-{index}", "session", f"turn-{index}", "chat_completions")

        contexts = [json.loads(entry.context_info) for entry in state.queue.entries]
        self.assertFalse(contexts[0]["cache_low"])
        self.assertFalse(contexts[1]["cache_low"])
        self.assertEqual(contexts[0]["cache_stall_diagnostic"]["consecutive_stream_failures"], 1)
        self.assertEqual(contexts[1]["cache_stall_diagnostic"]["consecutive_stream_failures"], 2)
        self.assertEqual(contexts[1]["cache_stall_diagnostic"]["last_error"], "codex_stream_timeout")
        self.assertTrue(contexts[1]["cache_stall_diagnostic"]["diagnostic_only"])
        self.assertEqual(state.queue.entries[1].error, "codex_stream_timeout")


class StreamingCodex:
    async def execute_chat(self, *_args):
        result = CodexCallResult(status=200)

        async def stream():
            try:
                yield b"data: {}\n\n"
            finally:
                result.input_tokens = 1000
                result.cached_input_tokens = 750
                result.output_tokens = 20
                result.usage_seen = True
                result.usage_event_type = "response.completed"

        result.stream = stream()
        return result


class AppStreamingTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_stream_updates_execution_result_before_logging(self) -> None:
        state = SpartanRouteState(test_config(), TelemetryStore(":memory:"), CaptureQueue())
        state.codex = StreamingCodex()
        route = AppCacheDiagnosticsTests().route()

        result = await execute_chat(
            state,
            {"messages": [{"role": "user", "content": "hi"}]},
            route,
            True,
            "session",
            "cache",
            "request",
        )

        stream = result.stream
        self.assertIsNotNone(stream)
        await stream.__anext__()
        await stream.aclose()

        self.assertEqual(result.input_tokens, 1000)
        self.assertEqual(result.cached_input_tokens, 750)
        self.assertEqual(result.output_tokens, 20)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.usage_event_type, "response.completed")

    async def test_responses_sse_error_emits_failed_not_completed(self) -> None:
        async def source():
            yield sse({"error": {"message": "timeout", "type": "upstream_error", "code": "codex_stream_timeout"}}, "error")
            yield b"data: [DONE]\n\n"

        chunks = []
        async for chunk in chat_sse_to_responses_sse(source(), "resp_test", "codex/gpt-5.5"):
            chunks.append(chunk.decode("utf-8"))
        body = "".join(chunks)

        self.assertIn("event: error", body)
        self.assertIn("event: response.failed", body)
        self.assertIn("codex_stream_timeout", body)
        self.assertNotIn("event: response.completed", body)


class DashboardStaticTests(unittest.TestCase):
    def test_routing_dashboard_uses_expandable_request_details(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard.html").read_text(encoding="utf-8")

        self.assertNotIn("<th>Content</th>", html)
        self.assertIn('class="request-list"', html)
        self.assertIn('class="request', html)
        self.assertIn('class="request-preview"', html)
        self.assertIn('class="trace-items"', html)
        self.assertIn("firstResponseLatencyMs", html)

    def test_codex_dashboard_uses_threshold_colored_rings(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn("function ringTone", html)
        self.assertIn("value >= 90", html)
        self.assertIn("value >= 70", html)
        self.assertIn("--tone", html)
        self.assertIn("slotPaths", html)

    def test_codex_dashboard_merges_usage_and_slot_cards_with_countdowns(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn("function renderAccountCards", html)
        self.assertIn("function latencyCell", html)
        self.assertIn("split_latency_requests", html)
        self.assertIn("function countdownText", html)
        self.assertIn("data-countdown", html)
        self.assertIn("resetTitle", html)
        self.assertIn("slot-only", html)
        self.assertIn("active-lease-strip", html)
        self.assertNotIn("<h2>Slots</h2>", html)


if __name__ == "__main__":
    unittest.main()
