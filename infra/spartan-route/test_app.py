from __future__ import annotations

import asyncio
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
    build_routing_policy_snapshot,
    classify_codex_slot_health,
    compare_session_trace,
    create_app,
    execute_chat,
    chat_sse_to_responses_sse,
    log_request,
    prompt_preview_from_request,
    request_prompt_cache_key_source,
    route_request,
    request_shape_snapshot,
)
from spartan_route.config import ModelEntry, SpartanRouteConfig
from spartan_route.codex import CodexAuth, CodexCallResult, CodexSlot, build_content_prompt_cache_key, sse
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

    def release(self) -> None:
        pass

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()


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


class CaptureChatCodex:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_chat(
        self,
        request: dict[str, Any],
        model_id: str,
        wants_stream: bool,
        session_id: str | None,
        prompt_cache_key: str | None,
        request_id: str,
    ) -> CodexCallResult:
        self.calls.append({
            "request": request,
            "model_id": model_id,
            "wants_stream": wants_stream,
            "session_id": session_id,
            "prompt_cache_key": prompt_cache_key,
            "request_id": request_id,
        })
        return CodexCallResult(status=200, body={"id": "chatcmpl_test", "choices": []}, usage_seen=False)

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
        "openai/gpt-test": ModelEntry("openai/gpt-test", "openai"),
        "codex/gpt-5.6": ModelEntry("codex/gpt-5.6", "codex", max_context=1_050_000, multimodal=True),
        "codex/gpt-5.6-sol": ModelEntry("codex/gpt-5.6-sol", "codex", max_context=1_050_000, multimodal=True),
        "codex/gpt-5.6-terra": ModelEntry("codex/gpt-5.6-terra", "codex", max_context=1_050_000, multimodal=True),
        "codex/gpt-5.6-luna": ModelEntry("codex/gpt-5.6-luna", "codex", max_context=1_050_000, multimodal=True),
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
        web_dir=Path(__file__).resolve().parent / "web",
        provider_profile="codex",
        baseline_model="codex/gpt-5.5",
        tier_models=tier_models,
        models=models,
    )


class RoutingPolicyTests(unittest.TestCase):
    def test_policy_uses_complete_valid_override_and_rejects_invalid_set(self) -> None:
        store = TelemetryStore(":memory:")
        config = test_config()
        override = {
            "heartbeat": "codex/gpt-5.6",
            "simple": "codex/gpt-5.6-sol",
            "moderate": "codex/gpt-5.6-terra",
            "complex": "codex/gpt-5.6-luna",
        }
        try:
            base = build_routing_policy_snapshot(config, store)
            self.assertEqual(base.source, "file")
            self.assertIsNone(base.warning)

            store.replace_routing_model_overrides(override)
            effective = build_routing_policy_snapshot(config, store)
            self.assertEqual(dict(effective.tier_models), override)
            self.assertEqual(effective.source, "runtime_override")
            self.assertNotEqual(base.etag, effective.etag)

            store._conn.execute(
                "UPDATE routing_model_overrides SET model_id = ? WHERE tier = ?",
                ("missing/model", "moderate"),
            )
            store._conn.commit()
            rejected = build_routing_policy_snapshot(config, store)
            self.assertEqual(rejected.source, "file")
            self.assertIsNotNone(rejected.warning)
            self.assertEqual(
                rejected.tier_models["moderate"],
                config.tier_models["moderate"]["primary"],
            )
        finally:
            store.close()

    def test_every_classifier_tier_uses_one_policy_snapshot(self) -> None:
        config = test_config()
        policy = {
            "heartbeat": "codex/gpt-5.4-mini",
            "simple": "codex/gpt-5.4",
            "moderate": "codex/gpt-5.5",
            "complex": "codex/gpt-5.6",
        }
        cases = [
            ("heartbeat", {"model": "clawroute/auto", "messages": [{"role": "user", "content": "ping"}]}),
            ("simple", {"model": "clawroute/auto", "messages": [{"role": "user", "content": "word " * 100}]}),
            ("moderate", {"model": "clawroute/auto", "messages": [{"role": "user", "content": "word " * 5000}]}),
            ("complex", {"model": "clawroute/auto", "messages": [{"role": "user", "content": "build"}], "tools": [{"type": "function"}]}),
        ]
        for expected_tier, body in cases:
            with self.subTest(tier=expected_tier):
                route = route_request(config, body, policy)
                self.assertEqual(route.tier, expected_tier)
                self.assertEqual(route.actual_model, policy[expected_tier])


class CodexHealthTests(unittest.TestCase):
    def test_slot_health_precedence(self) -> None:
        healthy = {
            "slotIndex": 2,
            "routingEnabled": True,
            "authAvailable": True,
            "rateLimitedUntil": 0,
            "transientUntil": 0,
        }
        cases = [
            ("disabled", {**healthy, "routingEnabled": False, "authAvailable": False}, [], [], False, "disabled"),
            ("usage failure", healthy, [{"slotIndex": 2, "message": "private upstream"}], [], False, "failed"),
            ("reset failure", healthy, [], [{"slotIndex": 2, "message": "private upstream"}], False, "failed"),
            ("auth failure", {**healthy, "authAvailable": False}, [], [], False, "failed"),
            ("rate cooldown", {**healthy, "authAvailable": False, "rateLimitedUntil": 1100}, [], [], False, "unavailable"),
            ("transient cooldown", {**healthy, "transientUntil": 1100}, [], [], False, "unavailable"),
            ("cached error", healthy, [{"slotIndex": 2, "message": "old"}], [], True, "history"),
            ("healthy", healthy, [], [], False, "healthy"),
        ]
        for label, slot, slot_errors, reset_errors, cached, expected in cases:
            with self.subTest(label=label):
                result = classify_codex_slot_health(
                    slot,
                    slot_errors=slot_errors,
                    reset_credit_errors=reset_errors,
                    cached=cached,
                    now=1000,
                )
                self.assertEqual(result["healthStatus"], expected)
                self.assertEqual(result["healthCached"], cached and bool(slot_errors or reset_errors))
                self.assertNotIn("private upstream", result["healthReasons"])


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
        self.assertEqual(
            {
                "depth": health_payload["telemetry"]["depth"],
                "capacity": health_payload["telemetry"]["capacity"],
                "drops": health_payload["telemetry"]["drops"],
                "writeFailures": health_payload["telemetry"]["writeFailures"],
            },
            {"depth": 0, "capacity": 1024, "drops": 0, "writeFailures": 0},
        )
        self.assertEqual(health_payload["telemetry"]["retention"]["retentionDays"], 30)
        self.assertEqual(health_payload["telemetry"]["retention"]["rowCap"], 50_000)
        self.assertNotIn(
            "oldestRetainedAt",
            health_payload["telemetry"]["retention"],
        )
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        payload = await models.json()
        model_ids = [item["id"] for item in payload["data"]]
        self.assertIn("custom-1/clawroute/auto", model_ids)
        self.assertIn("spartan-route/clawroute/auto", model_ids)
        for model_id in ("codex/gpt-5.6", "codex/gpt-5.6-sol", "codex/gpt-5.6-terra", "codex/gpt-5.6-luna"):
            self.assertIn(model_id, model_ids)

    async def test_api_requires_bearer_token(self) -> None:
        missing = await self.client.get("/api/routing/live")
        self.assertEqual(missing.status, 401)
        query_token = await self.client.get("/api/routing/live?token=secret")
        self.assertEqual(query_token.status, 401)
        ok = await self.client.get("/api/routing/live", headers={"Authorization": "Bearer secret"})
        self.assertEqual(ok.status, 200)

    async def test_config_api_is_authenticated_and_applies_immediately(self) -> None:
        unauthorized = await self.client.get("/api/config/routing")
        self.assertEqual(unauthorized.status, 401)

        auth = {"Authorization": "Bearer secret"}
        current = await self.client.get("/api/config/routing", headers=auth)
        self.assertEqual(current.status, 200)
        current_payload = await current.json()
        self.assertEqual(
            [tier["id"] for tier in current_payload["editableTiers"]],
            ["heartbeat", "simple", "moderate", "complex"],
        )
        etag = current.headers["ETag"]
        mapping = {
            "heartbeat": "codex/gpt-5.6",
            "simple": "codex/gpt-5.6-sol",
            "moderate": "codex/gpt-5.6-terra",
            "complex": "codex/gpt-5.6-luna",
        }
        updated = await self.client.put(
            "/api/config/routing",
            headers={**auth, "If-Match": etag},
            json={"tiers": mapping},
        )
        self.assertEqual(updated.status, 200)
        self.assertEqual((await updated.json())["effective"], mapping)
        self.assertNotEqual(updated.headers["ETag"], etag)

        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        routed = await self.client.post(
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": "clawroute/auto",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        self.assertEqual(routed.status, 200)
        self.assertEqual(fake_codex.calls[0]["model_id"], "codex/gpt-5.6")

        stale = await self.client.put(
            "/api/config/routing",
            headers={**auth, "If-Match": etag},
            json={"tiers": mapping},
        )
        self.assertEqual(stale.status, 412)
        self.assertEqual((await stale.json())["error"]["code"], "config_revision_conflict")

        reset = await self.client.delete(
            "/api/config/routing",
            headers={**auth, "If-Match": updated.headers["ETag"]},
        )
        self.assertEqual(reset.status, 200)
        self.assertEqual((await reset.json())["overrides"], {})

    async def test_config_api_rejects_ineligible_and_unbounded_updates(self) -> None:
        auth = {"Authorization": "Bearer secret"}
        current = await self.client.get("/api/config/routing", headers=auth)
        etag = current.headers["ETag"]

        missing_precondition = await self.client.put(
            "/api/config/routing",
            headers=auth,
            json={"tiers": {}},
        )
        self.assertEqual(missing_precondition.status, 428)

        invalid = await self.client.put(
            "/api/config/routing",
            headers={**auth, "If-Match": etag},
            json={"tiers": {tier: "anthropic/test" for tier in ("heartbeat", "simple", "moderate", "complex")}},
        )
        self.assertEqual(invalid.status, 400)
        self.assertEqual((await invalid.json())["error"]["code"], "routing_model_not_eligible")
        self.assertEqual(self.app[STATE_KEY].routing_policy.etag, etag)

        oversized = await self.client.put(
            "/api/config/routing",
            headers={**auth, "If-Match": etag, "Content-Type": "application/json"},
            data=b"{" + b"x" * 20000 + b"}",
        )
        self.assertEqual(oversized.status, 413)

        valid_body = json.dumps({
            "tiers": {
                tier: "codex/gpt-5.5"
                for tier in ("heartbeat", "simple", "moderate", "complex")
            }
        }).encode("utf-8")

        async def chunked_body():
            yield valid_body
            yield b" " * 20000

        chunked = await self.client.put(
            "/api/config/routing",
            headers={**auth, "If-Match": etag, "Content-Type": "application/json"},
            data=chunked_body(),
        )
        self.assertEqual(chunked.status, 413)
        self.assertEqual(self.app[STATE_KEY].routing_policy.etag, etag)

    async def test_config_mutations_serialize_and_publish_committed_state(self) -> None:
        auth = {"Authorization": "Bearer secret"}
        current = await self.client.get("/api/config/routing", headers=auth)
        etag = current.headers["ETag"]
        first_mapping = {
            "heartbeat": "codex/gpt-5.6",
            "simple": "codex/gpt-5.6-sol",
            "moderate": "codex/gpt-5.6-terra",
            "complex": "codex/gpt-5.6-luna",
        }
        second_mapping = {
            "heartbeat": "codex/gpt-5.4-mini",
            "simple": "codex/gpt-5.4",
            "moderate": "codex/gpt-5.5",
            "complex": "codex/gpt-5.6",
        }

        first, second = await asyncio.gather(
            self.client.put(
                "/api/config/routing",
                headers={**auth, "If-Match": etag},
                json={"tiers": first_mapping},
            ),
            self.client.put(
                "/api/config/routing",
                headers={**auth, "If-Match": etag},
                json={"tiers": second_mapping},
            ),
        )
        self.assertEqual(sorted((first.status, second.status)), [200, 412])
        await first.read()
        await second.read()

        published = await self.client.get("/api/config/routing", headers=auth)
        published_payload = await published.json()
        self.assertIn(published_payload["effective"], (first_mapping, second_mapping))
        self.assertEqual(
            {row["tier"]: row["model_id"] for row in self.store.routing_model_override_rows()},
            published_payload["effective"],
        )

        put_response, delete_response = await asyncio.gather(
            self.client.put(
                "/api/config/routing",
                headers={**auth, "If-Match": published.headers["ETag"]},
                json={"tiers": second_mapping},
            ),
            self.client.delete(
                "/api/config/routing",
                headers={**auth, "If-Match": published.headers["ETag"]},
            ),
        )
        self.assertEqual(sorted((put_response.status, delete_response.status)), [200, 412])
        await put_response.read()
        await delete_response.read()

        final = await self.client.get("/api/config/routing", headers=auth)
        final_payload = await final.json()
        persisted = {
            row["tier"]: row["model_id"]
            for row in self.store.routing_model_override_rows()
        }
        self.assertEqual(persisted, final_payload["overrides"])
        self.assertEqual(
            dict(self.app[STATE_KEY].routing_policy.tier_models),
            final_payload["effective"],
        )


    async def test_config_persistence_failures_preserve_policy_and_etag(self) -> None:
        auth = {"Authorization": "Bearer secret"}
        current = await self.client.get("/api/config/routing", headers=auth)
        etag = current.headers["ETag"]
        mapping = {
            "heartbeat": "codex/gpt-5.6",
            "simple": "codex/gpt-5.6-sol",
            "moderate": "codex/gpt-5.6-terra",
            "complex": "codex/gpt-5.6-luna",
        }

        with patch.object(
            self.store,
            "replace_routing_model_overrides",
            side_effect=RuntimeError("private sqlite detail"),
        ):
            failed_put = await self.client.put(
                "/api/config/routing",
                headers={**auth, "If-Match": etag},
                json={"tiers": mapping},
            )
        self.assertEqual(failed_put.status, 503)
        self.assertEqual(failed_put.headers["ETag"], etag)
        put_payload = await failed_put.json()
        self.assertEqual(put_payload["error"]["code"], "config_persistence_failed")
        self.assertNotIn("private sqlite detail", json.dumps(put_payload))
        self.assertEqual(self.app[STATE_KEY].routing_policy.etag, etag)

        with patch.object(
            self.store,
            "delete_routing_model_overrides",
            side_effect=RuntimeError("private sqlite detail"),
        ):
            failed_delete = await self.client.delete(
                "/api/config/routing",
                headers={**auth, "If-Match": etag},
            )
        self.assertEqual(failed_delete.status, 503)
        self.assertEqual(failed_delete.headers["ETag"], etag)
        self.assertEqual(self.app[STATE_KEY].routing_policy.etag, etag)


    async def test_config_dashboard_is_served_with_auth(self) -> None:
        unauthorized = await self.client.get("/dashboard-config")
        self.assertEqual(unauthorized.status, 401)
        response = await self.client.get(
            "/dashboard-config",
            headers={"Authorization": "Bearer secret"},
        )
        self.assertEqual(response.status, 200)
        self.assertIn("Spartan Route Config", await response.text())

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
            context_info='{"usage_seen":true,"cache_likely":true}',
        ))
        response = await self.client.get("/stats", headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertIn("today", payload)
        self.assertIn("latencyToday", payload)
        self.assertGreaterEqual(payload["today"]["loggedRequests"], 1)
        self.assertGreaterEqual(payload["latencyToday"]["avg"], 0)
        self.assertEqual(payload["today"]["cacheLikelyHitPercent"], 50.0)
        self.assertEqual(payload["totals"]["cacheLikelyHitPercent"], 50.0)
        self.assertEqual(payload["dailyCache"][0]["cache_likely_hit_percent"], 50.0)

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

    async def test_ultra_is_logged_and_rejected_before_codex_for_both_apis_and_stream_modes(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        original_queue = state.queue
        capture = CaptureQueue()
        state.queue = capture
        auth = {"Authorization": "Bearer secret"}

        try:
            scenarios = (
                ("/v1/chat/completions", False, {
                    "model": "codex/gpt-5.6-sol",
                    "messages": [{"role": "user", "content": "solve"}],
                }),
                ("/v1/chat/completions", True, {
                    "model": "codex/gpt-5.6-sol",
                    "messages": [{"role": "user", "content": "solve"}],
                }),
                ("/v1/responses", False, {
                    "model": "codex/gpt-5.6-sol",
                    "input": "solve",
                }),
                ("/v1/responses", True, {
                    "model": "codex/gpt-5.6-sol",
                    "input": "solve",
                }),
            )
            for endpoint, stream, base_body in scenarios:
                with self.subTest(endpoint=endpoint, stream=stream):
                    response = await self.client.post(
                        endpoint,
                        headers=auth,
                        json={**base_body, "stream": stream, "reasoning_effort": "ultra"},
                    )
                    self.assertEqual(response.status, 400)
                    payload = await response.json()
                    self.assertEqual(payload["error"]["type"], "invalid_request_error")
                    self.assertEqual(payload["error"]["code"], "codex_ultra_requires_app_server")
        finally:
            state.queue = original_queue

        self.assertEqual(fake_codex.calls, [])
        self.assertEqual(len(capture.entries), 4)
        for entry in capture.entries:
            self.assertEqual(entry.error, "codex_ultra_requires_app_server")
            self.assertEqual(entry.requested_reasoning_effort, "ultra")
            self.assertIsNone(entry.selected_codex_slot_index)
            self.assertIsNone(entry.selected_codex_account_key)

    async def test_max_rejects_non_gpt56_and_reaches_gpt56(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        auth = {"Authorization": "Bearer secret"}

        rejected = await self.client.post(
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": "codex/gpt-5.5",
                "messages": [{"role": "user", "content": "solve"}],
                "reasoning_effort": "max",
            },
        )
        self.assertEqual(rejected.status, 400)
        self.assertEqual((await rejected.json())["error"]["code"], "codex_max_requires_gpt_5_6")
        self.assertEqual(fake_codex.calls, [])

        accepted = await self.client.post(
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": "codex/gpt-5.6-sol",
                "messages": [{"role": "user", "content": "solve"}],
                "reasoning_effort": "max",
            },
        )
        self.assertEqual(accepted.status, 200)
        self.assertEqual(len(fake_codex.calls), 1)
        self.assertEqual(fake_codex.calls[0]["request"]["reasoning_effort"], "max")

    async def test_cache_lineage_uses_effective_key_and_stays_private(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        raw_key = "raw-secret-cache-key"

        response = await self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={
                "model": "clawroute/auto",
                "prompt_cache_key": raw_key,
                "spartan_cache_lineage": 2,
                "spartan_cache_lineage_reason": "history_rewrite",
                "messages": [{"role": "system", "content": "private system"}, {"role": "user", "content": "hello"}],
            },
        )

        self.assertEqual(response.status, 200)
        await response.json()
        self.assertEqual(len(fake_codex.calls), 1)
        call = fake_codex.calls[0]
        self.assertEqual(call["prompt_cache_key"], f"{raw_key}\0spartan-cache-lineage:2")
        self.assertNotIn("spartan_cache_lineage", call["request"])
        self.assertNotIn("spartan_cache_lineage_reason", call["request"])

    async def test_cache_lineage_applies_to_content_derived_key(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        body = {
            "model": "clawroute/auto",
            "spartan_cache_lineage": 3,
            "messages": [{"role": "system", "content": "stable policy"}, {"role": "user", "content": "hello"}],
        }

        response = await self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json=body,
        )

        self.assertEqual(response.status, 200)
        await response.json()
        content_key = build_content_prompt_cache_key(body)
        self.assertIsNotNone(content_key)
        self.assertEqual(fake_codex.calls[0]["prompt_cache_key"], f"{content_key}\0spartan-cache-lineage:3")

    async def test_openai_passthrough_uses_effective_prompt_cache_key(self) -> None:
        state = self.app[STATE_KEY]
        if state.session is not None:
            await state.session.close()
        fake_session = FakeOpenAISession()
        state.session = fake_session

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            response = await self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer secret"},
                json={
                    "model": "openai/gpt-test",
                    "prompt_cache_key": "session",
                    "spartan_cache_lineage": 4,
                    "spartan_cache_lineage_reason": "history_rewrite",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        self.assertEqual(response.status, 200)
        await response.json()
        upstream_body = fake_session.calls[0]["json"]
        self.assertEqual(upstream_body["prompt_cache_key"], "session\0spartan-cache-lineage:4")
        self.assertNotIn("spartan_cache_lineage", upstream_body)
        self.assertNotIn("spartan_cache_lineage_reason", upstream_body)

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

    async def test_codex_image_generation_allows_non_square_size(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = FakeImageCodex()
        state.codex = fake_codex

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret"},
                json={"model": "gpt-image-2", "prompt": "draw a guarded gate", "size": "1536x1024"},
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_codex.requests[0]["size"], "1536x1024")

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
        self.assertEqual(payload["error"]["code"], "invalid_image_size")

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

    async def test_codex_image_edits_allow_non_square_size(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = FakeImageCodex()
        state.codex = fake_codex

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

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_codex.requests[0]["size"], "1024x1536")
        self.assertEqual(len(fake_codex.requests[0]["images"]), 1)


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

    def test_log_request_records_lineage_effective_hash_without_raw_key(self) -> None:
        state = self.state()
        body = {
            "model": "clawroute/auto",
            "prompt_cache_key": "raw-secret-cache-key",
            "spartan_cache_lineage": "2",
            "spartan_cache_lineage_reason": "history_rewrite",
            "messages": [{"role": "system", "content": "private system"}, {"role": "user", "content": "hello"}],
        }
        result = ExecutionResult(200, input_tokens=25000, cached_input_tokens=0, output_tokens=300, usage_seen=True)

        log_request(state, body, self.route(), result, 0, "req", "session", "turn", "chat_completions")

        context = json.loads(state.queue.entries[0].context_info)
        serialized = json.dumps(context)
        self.assertEqual(context["cache_key_source"], "prompt_cache_key")
        self.assertEqual(context["cache_lineage"], "2")
        self.assertTrue(context["cache_lineage_present"])
        self.assertEqual(context["cache_lineage_reason"], "history_rewrite")
        self.assertNotEqual(context["cache_key_hash"], context["effective_cache_key_hash"])
        self.assertNotIn("raw-secret-cache-key", serialized)
        self.assertNotIn("private system", serialized)

    def test_invalid_cache_lineage_is_ignored(self) -> None:
        for lineage in (True, False, -1, 1_000_001, "bad", "-1", "1000001"):
            with self.subTest(lineage=lineage):
                state = self.state()
                body = {
                    "model": "clawroute/auto",
                    "prompt_cache_key": "session",
                    "spartan_cache_lineage": lineage,
                    "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "hello"}],
                }
                result = ExecutionResult(200, input_tokens=25000, cached_input_tokens=0, output_tokens=300, usage_seen=True)

                log_request(state, body, self.route(), result, 0, "req", "session", "turn", "chat_completions")

                context = json.loads(state.queue.entries[0].context_info)
                self.assertIsNone(context["cache_lineage"])
                self.assertFalse(context["cache_lineage_present"])
                self.assertEqual(context["cache_key_hash"], context["effective_cache_key_hash"])

    def test_lineage_change_starts_new_effective_cache_trace(self) -> None:
        state = self.state()
        route = self.route()
        base_messages = [{"role": "system", "content": "stable"}]
        base_messages.extend(
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
            for index in range(55)
        )
        bodies = [
            {
                "model": "clawroute/auto",
                "prompt_cache_key": "session",
                "spartan_cache_lineage": 0,
                "messages": base_messages,
            },
            {
                "model": "clawroute/auto",
                "prompt_cache_key": "session",
                "spartan_cache_lineage": 1,
                "spartan_cache_lineage_reason": "history_rewrite",
                "messages": [*base_messages, {"role": "assistant", "content": "a"}],
            },
        ]

        for index, body in enumerate(bodies):
            result = ExecutionResult(
                200,
                slot_index=0,
                account_key="account",
                input_tokens=80000,
                cached_input_tokens=0,
                output_tokens=100,
                usage_seen=True,
                usage_event_type="response.completed",
            )
            log_request(state, body, route, result, 0, f"req-{index}", "session", f"turn-{index}", "chat_completions")

        contexts = [json.loads(entry.context_info) for entry in state.queue.entries]
        self.assertEqual(contexts[1]["same_session_cache_trace"]["comparison"], "baseline")
        self.assertFalse(contexts[1]["expected_cache_hit"])
        self.assertFalse(contexts[1]["cache_prefix_candidate"])
        self.assertFalse(contexts[1]["cache_likely"])
        self.assertEqual(contexts[1]["cache_expectation_reason"], "baseline")
        self.assertFalse(contexts[1]["cache_low"])

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

    def test_cache_guard_does_not_mark_shallow_prefix_low_hit(self) -> None:
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
        self.assertTrue(contexts[1]["cache_prefix_candidate"])
        self.assertFalse(contexts[1]["cache_likely"])
        self.assertEqual(contexts[1]["cache_expectation_reason"], "shallow_prefix")
        self.assertFalse(contexts[1]["cache_low"])
        self.assertTrue(contexts[2]["expected_cache_hit"])
        self.assertTrue(contexts[2]["cache_prefix_candidate"])
        self.assertFalse(contexts[2]["cache_likely"])
        self.assertEqual(contexts[2]["cache_expectation_reason"], "shallow_prefix")
        self.assertFalse(contexts[2]["cache_low"])

    def test_cache_guard_marks_second_consecutive_mature_low_hit(self) -> None:
        state = self.state()
        route = self.route()
        base_messages = [{"role": "system", "content": "stable"}]
        base_messages.extend(
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
            for index in range(55)
        )
        bodies = [
            {"model": "clawroute/auto", "prompt_cache_key": "session", "messages": base_messages},
            {
                "model": "clawroute/auto",
                "prompt_cache_key": "session",
                "messages": [*base_messages, {"role": "assistant", "content": "a"}],
            },
            {
                "model": "clawroute/auto",
                "prompt_cache_key": "session",
                "messages": [
                    *base_messages,
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "b"},
                ],
            },
        ]

        for index, body in enumerate(bodies):
            result = ExecutionResult(
                200,
                slot_index=0,
                account_key="account",
                input_tokens=80000,
                cached_input_tokens=0,
                output_tokens=100,
                usage_seen=True,
                usage_event_type="response.completed",
            )
            log_request(state, body, route, result, 0, f"req-{index}", "session", f"turn-{index}", "chat_completions")

        contexts = [json.loads(entry.context_info) for entry in state.queue.entries]
        self.assertFalse(contexts[0]["expected_cache_hit"])
        self.assertTrue(contexts[1]["expected_cache_hit"])
        self.assertTrue(contexts[1]["cache_likely"])
        self.assertEqual(contexts[1]["cache_expectation_reason"], "mature_prefix")
        self.assertGreaterEqual(contexts[1]["same_session_cache_trace"]["commonPrefixMessages"], 50)
        self.assertGreaterEqual(contexts[1]["non_tool_input_tokens"], 50000)
        self.assertFalse(contexts[1]["cache_low"])
        self.assertTrue(contexts[2]["expected_cache_hit"])
        self.assertTrue(contexts[2]["cache_likely"])
        self.assertEqual(contexts[2]["cache_expectation_reason"], "mature_prefix")
        self.assertTrue(contexts[2]["cache_low"])

    def test_cache_retry_is_not_cache_likely(self) -> None:
        state = self.state()
        body = {
            "model": "clawroute/auto",
            "prompt_cache_key": "session",
            "messages": [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "first"},
            ],
        }
        route = self.route()

        for index in range(2):
            result = ExecutionResult(
                200,
                slot_index=0,
                account_key="account",
                input_tokens=80000,
                cached_input_tokens=0,
                output_tokens=100,
                usage_seen=True,
                usage_event_type="response.completed",
            )
            log_request(state, body, route, result, 0, f"req-{index}", "session", f"turn-{index}", "chat_completions")

        contexts = [json.loads(entry.context_info) for entry in state.queue.entries]
        self.assertEqual(contexts[1]["same_session_cache_trace"]["comparison"], "retry")
        self.assertTrue(contexts[1]["expected_cache_hit"])
        self.assertTrue(contexts[1]["cache_prefix_candidate"])
        self.assertFalse(contexts[1]["cache_likely"])
        self.assertEqual(contexts[1]["cache_expectation_reason"], "retry")
        self.assertFalse(contexts[1]["cache_low"])

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
        self.assertFalse(contexts[1]["cache_likely"])
        self.assertEqual(contexts[1]["cache_expectation_reason"], "error")
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
        self.assertIn("tokenCachePercent", html)
        self.assertIn("likelyUsageCacheText", html)

    def test_codex_dashboard_uses_threshold_colored_rings(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn("function ringTone", html)
        self.assertIn("value >= 90", html)
        self.assertIn("value >= 70", html)
        self.assertIn("--tone", html)
        self.assertIn("slotPaths", html)
        self.assertIn("likelyUsageCacheText", html)

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


    def test_dashboard_navigation_and_config_contract_are_consistent(self) -> None:
        web_dir = Path(__file__).resolve().parent / "web"
        routing = (web_dir / "dashboard.html").read_text(encoding="utf-8")
        codex = (web_dir / "dashboard-codex.html").read_text(encoding="utf-8")
        config = (web_dir / "dashboard-config.html").read_text(encoding="utf-8")

        for html in (routing, codex, config):
            self.assertIn('href="/dashboard-config"', html)
            self.assertIn("Bearer token", html)
            self.assertIn("Use token", html)
            self.assertIn('aria-current="page"', html)
        self.assertIn("/api/config/routing", config)
        self.assertIn("If-Match", config)
        self.assertIn("beforeunload", config)
        self.assertIn("textContent", config)
        self.assertNotIn("localStorage", config)
        self.assertIn("[mode, payload.warning].filter(Boolean)", config)

    def test_codex_dashboard_marks_only_backend_failed_health_red(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn(".account.health-failed", html)
        self.assertIn("function accountHealth", html)
        self.assertIn("healthStatus", html)
        self.assertIn("healthReasons", html)
        self.assertIn("min-width: 0;\n    }\n    .account.health-failed", html)
        self.assertNotIn('<div class="slot-row">\n      const tone', html)
        self.assertIn("const tone = slot.healthStatus", html)
        self.assertIn("health-badge", html)
        self.assertIn("account-health", html)
        self.assertIn("textContent = summary", html)


if __name__ == "__main__":
    unittest.main()
