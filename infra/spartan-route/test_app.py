from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.app import (
    ExecutionResult,
    RoutedRequest,
    SpartanRouteState,
    build_request_shape_diagnostics,
    compare_session_trace,
    create_app,
    log_request,
    request_prompt_cache_key_source,
    request_shape_snapshot,
)
from spartan_route.config import ModelEntry, SpartanRouteConfig
from spartan_route.telemetry import TelemetryStore


class CaptureQueue:
    def __init__(self) -> None:
        self.entries = []

    def log(self, entry) -> None:
        self.entries.append(entry)


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
        health = await self.client.get("/health")
        self.assertEqual(health.status, 200)
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        payload = await models.json()
        self.assertIn("custom-1/clawroute/auto", [item["id"] for item in payload["data"]])

    async def test_api_requires_bearer_token(self) -> None:
        missing = await self.client.get("/api/routing/live")
        self.assertEqual(missing.status, 401)
        ok = await self.client.get("/api/routing/live", headers={"Authorization": "Bearer secret"})
        self.assertEqual(ok.status, 200)

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


if __name__ == "__main__":
    unittest.main()
