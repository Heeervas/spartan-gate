from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    apply_tool_call_telemetry,
    build_request_shape_diagnostics,
    build_routing_policy_snapshot,
    classify_codex_slot_health,
    codex_slots_with_health,
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
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.body = body

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeUpstreamResponse(body=self.body)

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


    def test_discovering_pending_auth_does_not_look_failed(self) -> None:
        payload = {
            "cached": True,
            "source": "persisted",
            "accounts": [],
            "refreshProgress": {
                "status": "discovering",
                "completedAccounts": 0,
                "totalAccounts": None,
                "pendingSlotIndexes": [0, 1, 2],
            },
            "slotErrors": [],
            "resetCreditErrors": [],
        }
        pending = {
            "authAvailable": False,
            "rateLimitedUntil": 0,
            "transientUntil": 0,
        }

        slots = codex_slots_with_health(
            payload,
            [
                {**pending, "slotIndex": 0, "routingEnabled": True},
                {**pending, "slotIndex": 1, "routingEnabled": False},
                {
                    **pending,
                    "slotIndex": 2,
                    "routingEnabled": True,
                    "rateLimitedUntil": 9_999_999_999,
                },
            ],
        )

        self.assertEqual(
            [
                (slot["healthDataSource"], slot["healthStatus"])
                for slot in slots
            ],
            [
                ("pending", "history"),
                ("pending", "disabled"),
                ("pending", "unavailable"),
            ],
        )

    def test_slot_health_uses_per_slot_data_source_not_global_cache(self) -> None:
        healthy = {
            "routingEnabled": True,
            "authAvailable": True,
            "rateLimitedUntil": 0,
            "transientUntil": 0,
        }
        payload = {
            "cached": False,
            "source": "mixed",
            "accounts": [{
                "accountKey": "complete-account",
                "slotIndexes": [1],
                "source": "live",
                "stale": False,
            }],
            "refreshProgress": {
                "status": "running",
                "completedAccounts": 1,
                "totalAccounts": 3,
                "pendingSlotIndexes": [0],
            },
            "slotErrors": [
                {"slotIndex": 1, "source": "none", "message": "current failure"},
                {"slotIndex": 2, "source": "persisted", "message": "old failure"},
            ],
            "resetCreditErrors": [],
        }

        slots = codex_slots_with_health(
            payload,
            [
                {**healthy, "slotIndex": 0, "accountKey": "pending-account"},
                {**healthy, "slotIndex": 1, "accountKey": "complete-account"},
                {**healthy, "slotIndex": 2, "accountKey": "history-account"},
            ],
        )

        self.assertEqual(
            [
                (slot["healthDataSource"], slot["healthStatus"])
                for slot in slots
            ],
            [
                ("pending", "healthy"),
                ("live", "failed"),
                ("persisted", "history"),
            ],
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
        self.assertFalse(health_payload["degraded"])
        self.assertEqual(
            health_payload["telemetry"]["database"],
            {
                "healthy": True,
                "readHealthy": True,
                "writeHealthy": True,
                "maintenanceHealthy": True,
                "readFailures": 0,
                "writeFailures": 0,
                "maintenanceFailures": 0,
                "lastFailureCode": None,
            },
        )
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        payload = await models.json()
        model_ids = [item["id"] for item in payload["data"]]
        self.assertIn("custom-1/clawroute/auto", model_ids)
        self.assertIn("spartan-route/clawroute/auto", model_ids)
        for model_id in ("codex/gpt-5.6", "codex/gpt-5.6-sol", "codex/gpt-5.6-terra", "codex/gpt-5.6-luna"):
            self.assertIn(model_id, model_ids)

    async def test_astra_discovery_exposes_subscription_metadata(self) -> None:
        from spartan_route.config import load_config

        model_id = "codex/gpt-6-astra"
        self.app[STATE_KEY].config.models.update(load_config({"SPARTAN_ROUTE_CONFIG_DIR": "/missing"}).models)
        response = await self.client.get("/v1/models")
        self.assertEqual(response.status, 200)
        entries = {entry["id"]: entry for entry in (await response.json())["data"]}
        self.assertIn(model_id, entries)
        astra = entries[model_id]
        for field in ("max_context", "context_length", "max_model_len"):
            self.assertEqual(astra[field], 872_000)
        response = await self.client.get(f"/v1/models/{model_id}")
        self.assertEqual(response.status, 200)
        for entry in (astra, await response.json()):
            self.assertEqual(entry["owned_by"], "codex")
            self.assertTrue(entry["tool_capable"])
            self.assertTrue(entry["multimodal"])
            self.assertEqual(entry["max_context"], 872_000)

    async def test_astra_reasoning_for_both_apis_and_stream_modes(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = CaptureChatCodex()
        state.codex = fake_codex
        auth = {"Authorization": "Bearer secret"}
        for stream in (False, True):
            for endpoint, input_body in (
                ("/v1/chat/completions", {"messages": [{"role": "user", "content": "solve"}]}),
                ("/v1/responses", {"input": "solve"}),
            ):
                for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
                    with self.subTest(endpoint=endpoint, stream=stream, effort=effort):
                        fake_codex.calls.clear()
                        with patch("spartan_route.app.execute_codex_responses", return_value=CodexCallResult(
                            status=200, body={"id": "resp_test", "object": "response", "status": "completed", "output": []}, completed=True,
                        )) as native:
                            response = await self.client.post(endpoint, headers=auth, json={
                                "model": "codex/gpt-6-astra", "stream": stream, **input_body,
                                **({"reasoning": {"effort": effort}} if endpoint.endswith("responses") else {"reasoning_effort": effort}),
                            })
                        await response.read()
                        if effort == "ultra":
                            self.assertEqual(response.status, 400)
                            self.assertEqual((await response.json())["error"]["code"], "codex_ultra_requires_app_server")
                            self.assertEqual(fake_codex.calls, [])
                            native.assert_not_called()
                        else:
                            self.assertEqual(response.status, 200)
                            if endpoint.endswith("responses"):
                                native.assert_called_once()
                                self.assertEqual(native.call_args.args[1]["reasoning"]["effort"], effort)
                                self.assertEqual(native.call_args.args[2:4], ("codex/gpt-6-astra", stream))
                            else:
                                self.assertEqual(len(fake_codex.calls), 1)
                                self.assertEqual(fake_codex.calls[0]["model_id"], "codex/gpt-6-astra")
                                self.assertEqual(fake_codex.calls[0]["wants_stream"], stream)
                                self.assertEqual(fake_codex.calls[0]["request"]["reasoning_effort"], effort)

    async def test_api_requires_bearer_token(self) -> None:
        missing = await self.client.get("/api/routing/live")
        self.assertEqual(missing.status, 401)
        query_token = await self.client.get("/api/routing/live?token=secret")
        self.assertEqual(query_token.status, 401)
        ok = await self.client.get("/api/routing/live", headers={"Authorization": "Bearer secret"})
        self.assertEqual(ok.status, 200)

    async def test_routing_live_attaches_estimated_units_to_sessions_and_turns(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        auth = CodexAuth("token", account_id="usage-account")
        state.codex.slots = [CodexSlot(0, path="/tmp/auth.json", auth=auth)]
        account_key = auth.account_key
        self.assertIsNotNone(account_key)
        timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.store.log_routing(RoutingLogEntry(
            timestamp=timestamp.isoformat(),
            original_model="clawroute/auto",
            routed_model="codex/gpt-5.6-sol",
            actual_model="codex/gpt-5.6-sol",
            tier="complex",
            input_tokens=900_000,
            output_tokens=100_000,
            selected_codex_account_key=account_key,
            selected_codex_slot_index=0,
            session_id="unit-session",
            turn_id="unit-turn",
            request_id="unit-request",
            prompt_preview="Show usage units",
            context_info='{"usage_seen":true}',
        ))
        self.store.upsert_usage_snapshots([{
            "account_key": account_key,
            "slot_index": 0,
            "window": "weekly",
            "used_percent": 10,
            "reset_at": (timestamp + timedelta(days=7)).isoformat(),
            "window_minutes": 10080,
        }], current_account_keys={account_key})

        response = await self.client.get(
            "/api/routing/live",
            headers={"Authorization": "Bearer secret"},
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["usageUnitEstimate"]["available"])
        self.assertAlmostEqual(payload["usageUnitEstimate"]["quotaUnitsPerMillionTokens"], 10.0)
        session = payload["sessions"][0]
        self.assertAlmostEqual(session["metrics"]["estimatedUsageUnits"], 10.0)
        self.assertAlmostEqual(session["turns"][0]["metrics"]["estimatedUsageUnits"], 10.0)

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

    async def test_usage_refresh_post_is_authenticated_202_and_nonblocking(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        auth = CodexAuth("token", account_id="acct")
        state.codex.slots = [
            CodexSlot(0, path="/codex-a/auth.json", auth=auth)
        ]
        gate = asyncio.Event()
        original_load_auth = state.codex.load_auth

        async def slow_load_auth(_slot: CodexSlot) -> CodexAuth:
            await gate.wait()
            return auth

        state.codex.load_auth = slow_load_auth
        try:
            unauthorized = await self.client.post("/api/codex/usage/refresh")
            self.assertEqual(unauthorized.status, 401)

            response = await asyncio.wait_for(
                self.client.post(
                    "/api/codex/usage/refresh",
                    headers={"Authorization": "Bearer secret"},
                ),
                timeout=1,
            )
            self.assertEqual(response.status, 202)
            payload = await response.json()
            self.assertTrue(payload["refreshing"])
            self.assertEqual(
                payload["refreshProgress"],
                {
                    "status": "discovering",
                    "completedAccounts": 0,
                    "totalAccounts": None,
                    "pendingSlotIndexes": [0],
                },
            )
            self.assertIn("cacheDaily", payload)
            self.assertIn("slots", payload)
            self.assertIn("balancer", payload)
        finally:
            gate.set()
            task = getattr(state.codex, "_usage_refresh_task", None)
            if task is not None:
                await asyncio.shield(task)
            state.codex.load_auth = original_load_auth

    async def test_usage_api_rollup_read_never_queries_routing_log(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        state.codex.slots = []
        for index in range(32):
            self.store.log_routing(RoutingLogEntry(
                timestamp=f"2026-07-19T{index % 24:02d}:00:00Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=100,
                cached_input_tokens=25,
                request_id=f"request-{index}",
                context_info='{"usage_seen":true}',
            ))

        traced: list[str] = []
        self.store._conn.set_trace_callback(traced.append)
        try:
            response = await self.client.get(
                "/api/codex/usage?force=true",
                headers={"Authorization": "Bearer secret"},
            )
        finally:
            self.store._conn.set_trace_callback(None)

        self.assertEqual(response.status, 200)
        self.assertFalse(
            any("FROM ROUTING_LOG" in statement.upper() for statement in traced),
            traced,
        )

    async def test_usage_stays_available_when_daily_cache_read_fails(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        state.codex.slots = []
        auth = {"Authorization": "Bearer secret"}

        with patch.object(
            self.store,
            "daily_cache_rows",
            side_effect=RuntimeError("private sqlite path and detail"),
        ):
            response = await self.client.get(
                "/api/codex/usage?force=true",
                headers=auth,
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["cacheDaily"], [])
        self.assertEqual(
            payload["telemetryErrors"],
            [
                {
                    "code": "telemetry_read_failed",
                    "operation": "daily_cache",
                }
            ],
        )
        self.assertNotIn("private sqlite", json.dumps(payload))

        health = await self.client.get("/health")
        self.assertEqual(health.status, 200)
        health_payload = await health.json()
        self.assertTrue(health_payload["ok"])
        self.assertTrue(health_payload["degraded"])
        self.assertFalse(health_payload["telemetry"]["database"]["readHealthy"])

    async def test_health_stays_ready_when_slot_telemetry_read_fails(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        with patch.object(
            state.codex,
            "slot_status",
            side_effect=RuntimeError("private slot database detail"),
        ):
            response = await self.client.get("/health")

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["codexSlots"], [])
        self.assertFalse(payload["telemetry"]["database"]["readHealthy"])
        self.assertNotIn("private slot", json.dumps(payload))

    async def test_telemetry_only_apis_return_sanitized_503_on_read_failure(self) -> None:
        auth = {"Authorization": "Bearer secret"}
        with patch.object(
            self.store,
            "daily_cache_rows",
            side_effect=RuntimeError("private sqlite path and detail"),
        ):
            for path in ("/stats", "/api/codex/cache/day"):
                with self.subTest(path=path):
                    response = await self.client.get(path, headers=auth)
                    self.assertEqual(response.status, 503)
                    payload = await response.json()
                    self.assertEqual(payload["error"]["code"], "telemetry_unavailable")
                    self.assertNotIn("private sqlite", json.dumps(payload))

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

    async def test_codex_capacity_patch_is_strict_persistent_and_attached(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        codex_auth = CodexAuth("token", account_id="acct")
        account_key = codex_auth.account_key
        self.assertIsNotNone(account_key)
        state.codex.slots = [CodexSlot(0, path="/tmp/auth.json", auth=codex_auth)]
        with patch("spartan_route.telemetry.now_iso", return_value="2026-07-07T00:00:00Z"):
            self.store.upsert_usage_snapshots([{
                "account_key": account_key,
                "slot_index": 0,
                "window": "weekly",
                "used_percent": 20,
                "reset_at": "2026-07-08T00:00:00Z",
                "window_minutes": 10080,
            }], current_account_keys={account_key})
        path = f"/api/codex/balancer/accounts/{account_key}/capacity"
        auth = {"Authorization": "Bearer secret"}

        unauthorized = await self.client.patch(path, json={"capacityMultiplier": 5})
        self.assertEqual(unauthorized.status, 401)
        invalid = await self.client.patch(
            path,
            headers=auth,
            json={"capacityMultiplier": 3, "extra": True},
        )
        self.assertEqual(invalid.status, 400)
        unknown = await self.client.patch(
            "/api/codex/balancer/accounts/0000000000000000/capacity",
            headers=auth,
            json={"capacityMultiplier": 5},
        )
        self.assertEqual(unknown.status, 404)

        response = await self.client.patch(path, headers=auth, json={"capacityMultiplier": 5})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["accounts"][0]["capacityMultiplier"], 5)
        self.assertEqual(payload["accounts"][0]["weekly"]["usedUnits"], 100)
        self.assertEqual(payload["accounts"][0]["weekly"]["capacityUnits"], 500)
        self.assertEqual(self.store.codex_capacity_multiplier(account_key), 5)

        response = await self.client.patch(path, headers=auth, json={"capacityMultiplier": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.store.codex_capacity_multiplier(account_key), 1)

    async def test_usage_poll_attaches_estimate_without_querying_routing_log(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.codex)
        codex_auth = CodexAuth("token", account_id="acct")
        account_key = codex_auth.account_key
        self.assertIsNotNone(account_key)
        state.codex.slots = [CodexSlot(0, path="/tmp/auth.json", auth=codex_auth)]
        self.store.log_routing(RoutingLogEntry(
            timestamp="2026-07-06T12:00:00Z",
            original_model="clawroute/auto",
            routed_model="codex/gpt-5.6-sol",
            actual_model="codex/gpt-5.6-sol",
            tier="complex",
            input_tokens=100,
            output_tokens=10,
            selected_codex_account_key=account_key,
            selected_codex_slot_index=0,
            request_id="usage",
            context_info='{"usage_seen":true}',
        ))
        with patch("spartan_route.telemetry.now_iso", return_value="2026-07-07T00:00:00Z"):
            self.store.upsert_usage_snapshots([{
                "account_key": account_key,
                "slot_index": 0,
                "window": "weekly",
                "used_percent": 10,
                "reset_at": "2026-07-08T00:00:00Z",
                "window_minutes": 10080,
            }], current_account_keys={account_key})

        traced: list[str] = []
        self.store._conn.set_trace_callback(traced.append)
        try:
            response = await self.client.get(
                "/api/codex/usage",
                headers={"Authorization": "Bearer secret"},
            )
        finally:
            self.store._conn.set_trace_callback(None)

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["weeklyModelUsage"]["available"])
        self.assertEqual(payload["weeklyModelUsage"]["models"][0]["model"], "codex/gpt-5.6-sol")
        self.assertEqual(payload["weeklyModelUsage"]["efficiency"][0]["reasoning"], "default")
        self.assertFalse(
            any("FROM ROUTING_LOG" in statement.upper() for statement in traced),
            traced,
        )

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

    async def test_responses_endpoint_preserves_native_reasoning_items(self) -> None:
        captured: list[dict[str, Any]] = []
        reasoning_item = {"type": "reasoning", "encrypted_content": "opaque-input"}
        secret_hex = "55" * 32
        chain_id = "66" * 32
        realm = "codex_subscription"
        binding_mac = hmac.new(bytes.fromhex(secret_hex), f"{chain_id}\0{realm}".encode(), hashlib.sha256).hexdigest()

        async def fake_execute(_router, request, model_id, wants_stream, session_id, prompt_cache_key):
            captured.append({
                "request": request,
                "model_id": model_id,
                "wants_stream": wants_stream,
                "session_id": session_id,
                "prompt_cache_key": prompt_cache_key,
            })
            return CodexCallResult(
                status=200,
                body={
                    "id": "resp_native",
                    "object": "response",
                    "status": "completed",
                    "output": [{"type": "reasoning", "encrypted_content": "opaque-output"}],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
                input_tokens=10,
                output_tokens=4,
                reasoning_tokens=3,
                completed=True,
                usage_seen=True,
            )

        with patch.dict(os.environ, {"SPARTAN_REASONING_BINDING_SECRET": secret_hex}, clear=False), patch("spartan_route.app.execute_codex_responses", side_effect=fake_execute):
            response = await self.client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer secret"},
                json={
                    "model": "codex/gpt-5.6-sol",
                    "input": [reasoning_item, {"role": "user", "content": "continue"}],
                    "reasoning": {"effort": "high"},
                    "spartan_chain_id": chain_id,
                    "spartan_expected_realm": realm,
                    "spartan_binding_mac": binding_mac,
                },
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["output"][0]["encrypted_content"], "opaque-output")
        self.assertEqual(captured[0]["request"]["input"][0], reasoning_item)
        self.assertEqual(captured[0]["request"]["reasoning"], {"effort": "high"})

    async def test_codex_encrypted_replay_requires_signed_binding(self) -> None:
        with patch("spartan_route.app.execute_codex_responses") as execute:
            response = await self.client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer secret"},
                json={
                    "model": "codex/gpt-5.6-sol",
                    "input": [{"type": "reasoning", "encrypted_content": "opaque"}],
                },
            )

        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"]["code"], "reasoning_binding_required")
        execute.assert_not_called()

    async def test_codex_responses_rejects_unreplayable_capabilities_before_egress(self) -> None:
        cases = (
            ({"store": True}, "codex_responses_store_not_supported"),
            ({"previous_response_id": "resp_previous"}, "codex_previous_response_not_supported"),
            ({"context_management": [{"type": "compaction"}]}, "codex_compaction_not_supported"),
            ({"input": [{"type": "compaction", "encrypted_content": "opaque"}]}, "codex_compaction_not_supported"),
            ({"reasoning": {"mode": "summary"}}, "codex_reasoning_mode_not_supported"),
            ({"tools": [{"type": "web_search_preview"}]}, "codex_tool_type_not_supported"),
        )

        with patch("spartan_route.app.execute_codex_responses") as execute:
            for extra, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    request_body = {
                        "model": "codex/gpt-5.6-sol",
                        "input": "hello",
                        **extra,
                    }
                    response = await self.client.post(
                        "/v1/responses",
                        headers={"Authorization": "Bearer secret"},
                        json=request_body,
                    )
                    self.assertEqual(response.status, 400)
                    self.assertEqual((await response.json())["error"]["code"], expected_code)

        execute.assert_not_called()

    async def test_openai_api_responses_uses_responses_endpoint_without_chat_conversion(self) -> None:
        state = self.app[STATE_KEY]
        if state.session is not None:
            await state.session.close()
        fake_session = FakeOpenAISession({
            "id": "resp_api",
            "object": "response",
            "status": "completed",
            "output": [{"type": "reasoning", "encrypted_content": "api-cipher"}],
            "usage": {"input_tokens": 8, "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 2}},
        })
        state.session = fake_session

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            response = await self.client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer secret"},
                json={"model": "openai/gpt-test", "input": "hello", "reasoning": {"effort": "medium"}},
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["output"][0]["encrypted_content"], "api-cipher")
        self.assertEqual(payload["metadata"]["spartan_reasoning_realm"], "openai_api")
        self.assertTrue(fake_session.calls[0]["url"].endswith("/responses"))
        self.assertEqual(fake_session.calls[0]["json"]["input"], "hello")
        self.assertNotIn("messages", fake_session.calls[0]["json"] )
        self.assertFalse(fake_session.calls[0]["json"]["store"] )

    async def test_reasoning_chain_allows_subscription_model_switch_and_rejects_api_realm(self) -> None:
        secret_hex = "44" * 32
        chain_id = hmac.new(bytes.fromhex(secret_hex), b"/opt/data/profiles/work\0shared", hashlib.sha256).hexdigest()
        realm = "codex_subscription"
        binding_mac = hmac.new(bytes.fromhex(secret_hex), f"{chain_id}\0{realm}".encode(), hashlib.sha256).hexdigest()
        binding = {
            "spartan_chain_id": chain_id,
            "spartan_expected_realm": realm,
            "spartan_binding_mac": binding_mac,
        }

        async def fake_execute(*_args, **_kwargs):
            return CodexCallResult(status=200, body={"id": "resp", "object": "response", "status": "completed", "output": []}, completed=True)

        with patch.dict(os.environ, {"SPARTAN_REASONING_BINDING_SECRET": secret_hex}, clear=False), patch("spartan_route.app.execute_codex_responses", side_effect=fake_execute):
            for model in ("codex/gpt-5.6-sol", "codex/gpt-5.6-terra"):
                accepted = await self.client.post(
                    "/v1/responses",
                    headers={"Authorization": "Bearer secret"},
                    json={"model": model, "input": "continue", **binding},
                )
                self.assertEqual(accepted.status, 200)
            rejected = await self.client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer secret"},
                json={"model": "openai/gpt-test", "input": "continue", **binding},
            )

        self.assertEqual(rejected.status, 409)
        self.assertEqual((await rejected.json())["error"]["code"], "reasoning_realm_mismatch")
        self.assertEqual(self.store.reasoning_realm_for_chain(chain_id), "codex_subscription")

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

    async def test_openai_image_edit_passthrough_preserves_mask_and_fidelity(self) -> None:
        state = self.app[STATE_KEY]
        if state.session is not None:
            await state.session.close()
        fake_session = FakeOpenAISession()
        state.session = fake_session
        form = FormData()
        form.add_field("model", "gpt-image-2")
        form.add_field("prompt", "repair the grip")
        form.add_field("image", b"source", filename="source.png", content_type="image/png")
        form.add_field("mask", b"mask", filename="mask.png", content_type="image/png")
        form.add_field("input_fidelity", "high")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            response = await self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret"},
                data=form,
            )

        self.assertEqual(response.status, 200)
        self.assertTrue(fake_session.calls[0]["url"].endswith("/images/edits"))
        upstream_fields = {
            field_options["name"]: value
            for field_options, _headers, value in fake_session.calls[0]["data"]._fields
        }
        self.assertEqual(upstream_fields["mask"], b"mask")
        self.assertEqual(upstream_fields["input_fidelity"], "high")

    async def test_codex_image_edits_allow_non_square_size(self) -> None:
        state = self.app[STATE_KEY]
        fake_codex = FakeImageCodex()
        state.codex = fake_codex

        form = FormData()
        form.add_field("model", "gpt-image-2")
        form.add_field("prompt", "make the red square landscape")
        form.add_field("size", "1024x1536")
        form.add_field("image", b"\x89PNG\r\n", filename="red.png", content_type="image/png")
        form.add_field("mask", b"\x89PNG\r\nmask", filename="mask.png", content_type="image/png")
        form.add_field("input_fidelity", "high")

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret"},
                data=form,
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_codex.requests[0]["size"], "1024x1536")
        self.assertEqual(len(fake_codex.requests[0]["images"]), 1)
        self.assertTrue(fake_codex.requests[0]["mask"].startswith("data:image/png;base64,"))
        self.assertEqual(fake_codex.requests[0]["input_fidelity"], "high")

    async def test_codex_image_edits_reject_invalid_input_fidelity(self) -> None:
        form = FormData()
        form.add_field("model", "gpt-image-2")
        form.add_field("prompt", "repair the grip")
        form.add_field("image", b"\x89PNG\r\n", filename="source.png", content_type="image/png")
        form.add_field("input_fidelity", "maximum")

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret"},
                data=form,
            )

        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"]["code"], "invalid_input_fidelity")


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
                result.actual_tool_call_count = 1
                result.actual_tool_names = ["lookup"]
                result.usage_seen = True
                result.usage_event_type = "response.completed"

        result.stream = stream()
        return result


class AppStreamingTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_tool_telemetry_uses_protocol_choice_indexes(self) -> None:
        result = ExecutionResult(status=200)
        apply_tool_call_telemetry(
            result,
            {"choices": [{"index": 2, "delta": {"tool_calls": [{"index": 0, "function": {"name": "lookup"}}]}}]},
        )
        apply_tool_call_telemetry(
            result,
            {"choices": [{"index": 1, "delta": {"tool_calls": [{"index": 0, "function": {"name": "search"}}]}}]},
        )

        self.assertEqual(result.actual_tool_call_count, 2)
        self.assertEqual(result.actual_tool_names, ["lookup", "search"])

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
        self.assertEqual(result.actual_tool_call_count, 1)
        self.assertEqual(result.actual_tool_names, ["lookup"])
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
        self.assertIn("function reasoningSummary", html)
        self.assertIn("function toolSummary", html)
        self.assertIn("function usageUnitSummary", html)
        self.assertIn("estimatedUsageUnits", html)
        self.assertIn("fact('Est. usage'", html)
        self.assertIn('id="model-filter"', html)
        self.assertIn('id="reasoning-filter"', html)
        self.assertIn('id="prompt-filter"', html)
        self.assertIn('id="filter-status"', html)
        self.assertIn("new AbortController()", html)
        self.assertIn("actualToolCalls || 0)} calls", html)
        self.assertIn("called ${esc(toolNames.join(', '))}", html)
        self.assertIn('class="activity-title"', html)
        self.assertIn("actualToolCallCount", html)
        self.assertIn("Technical identifiers", html)
        self.assertIn("Copy session ID", html)

    def test_codex_dashboard_superseded_load_restores_both_refresh_controls(self) -> None:
        html = (
            Path(__file__).resolve().parent / "web" / "dashboard-codex.html"
        ).read_text(encoding="utf-8")

        helper_start = html.find("function restoreLoadControls")
        self.assertGreaterEqual(helper_start, 0)
        helper_end = html.find("\n    function ", helper_start + 1)
        helper = html[helper_start:helper_end]
        self.assertIn("refresh", helper)
        self.assertIn("force", helper)
        self.assertIn("disabled = false", helper)
        self.assertIn("textContent", helper)

        load_start = html.find("async function load")
        load_end = html.find("\n    setInterval", load_start)
        load = html[load_start:load_end]
        self.assertIn("activeLoadController.abort()", load)
        finally_start = load.find("finally")
        self.assertGreaterEqual(finally_start, 0)
        self.assertIn("restoreLoadControls()", load[finally_start:])

    def test_codex_dashboard_uses_progressive_post_abort_and_bounded_polling(self) -> None:
        html = (
            Path(__file__).resolve().parent / "web" / "dashboard-codex.html"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/codex/usage/refresh", html)
        self.assertIn("method: 'POST'", html)
        self.assertNotIn("?force=true", html)
        self.assertIn("AbortController", html)
        self.assertIn("signal:", html)
        self.assertIn("5000", html)
        self.assertIn("45000", html)
        self.assertIn("1000", html)
        self.assertIn("refreshProgress", html)
        self.assertIn("completedAccounts", html)
        self.assertIn("totalAccounts", html)
        self.assertIn("pendingSlotIndexes", html)
        self.assertIn("discovering", html)
        self.assertIn("running", html)
        self.assertIn("complete", html)
        self.assertIn("failed", html)
        self.assertIn("Discovering accounts", html)
        self.assertIn("healthDataSource", html)
        self.assertIn("server refresh may still be running", html)

    def test_codex_dashboard_uses_threshold_colored_rings(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn("function balanceTone", html)
        self.assertIn("value >= 90", html)
        self.assertIn("value >= 70", html)
        self.assertIn("--tone", html)
        self.assertIn("Weekly balance", html)
        self.assertIn("Five-hour balance", html)
        self.assertIn("account.fiveHour", html)
        self.assertIn("row.usedPercent ?? (100 - Number(row.remainingPercent || 0))", html)
        self.assertIn("used * 3.6", html)
        self.assertIn("row.usedUnits", html)
        self.assertIn("row.capacityUnits", html)
        self.assertIn("% raw · x", html)
        self.assertIn("slotPaths", html)
        self.assertIn("likelyUsageCacheText", html)
        self.assertIn("refreshing in background", html)
        self.assertIn("function pollRefresh", html)

    def test_codex_dashboard_supports_capacity_and_model_estimates(self) -> None:
        html = (Path(__file__).resolve().parent / "web" / "dashboard-codex.html").read_text(encoding="utf-8")

        self.assertIn('data-action="capacity"', html)
        self.assertIn('value="1"', html)
        self.assertIn('value="5"', html)
        self.assertIn("/api/codex/balancer/accounts/", html)
        self.assertIn("encodeURIComponent(accountKey)", html)
        self.assertIn("capacityMultiplier", html)
        capacity_handler = html[html.index("accountsEl.addEventListener('change'"):]
        self.assertIn("++loadGeneration", capacity_handler)
        self.assertIn("activeLoadController.abort()", capacity_handler)
        self.assertIn("controller.signal", capacity_handler)
        self.assertIn("generation === loadGeneration", capacity_handler)
        self.assertIn('class="expected-table"', html)
        self.assertIn('id="model-usage"', html)
        self.assertIn(".expected-table th:nth-child(4)", html)
        self.assertIn("weeklyModelUsage", html)
        self.assertIn("expectedUnits", html)
        self.assertIn("x1EquivalentPercent", html)
        self.assertIn("x5EquivalentPercent", html)
        self.assertIn("excludedAccountRequests", html)
        self.assertIn("unattributedRequests", html)
        self.assertIn('class="efficiency-table"', html)
        self.assertIn('id="efficiency"', html)
        self.assertIn("function renderEfficiency", html)
        self.assertIn("averageMillionTokensPerSession", html)
        self.assertIn("percentPerMillionTokens", html)
        self.assertIn(".efficiency-table th:nth-child(5)", html)

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
        self.assertIn("function authLifecycle", html)
        self.assertIn("authStateSource", html)
        self.assertIn("authExpiresAt", html)
        self.assertIn("authPersistenceHealthy", html)
        self.assertIn("authErrorCode", html)
        self.assertIn("stale disable ignored", html)
        self.assertIn("identity mismatch", html)
        self.assertNotIn("identity mismatch, override ignored", html)
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
        self.assertIn("telemetryErrors", html)
        self.assertIn("Telemetry degraded", html)


if __name__ == "__main__":
    unittest.main()
