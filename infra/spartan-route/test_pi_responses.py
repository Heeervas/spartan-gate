"""Pi native Responses signing, discovery, and accounting boundaries."""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route.app import STATE_KEY, ExecutionResult, create_app
from spartan_route.config import ModelEntry
from spartan_route.pi_usage import CURRENT_REQUEST
from spartan_route.pi_usage_store import COUNTERS
from spartan_route.telemetry import TelemetryStore
from spartan_route.native_responses import execute_codex_responses
from test_app import test_config
import test_codex as fixtures

SECRET = "41" * 32
SCOPE = hashlib.sha256(b"synthetic-pi-home").hexdigest()


class PiResponsesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET=SECRET)
        self.env.start()
        self.store = TelemetryStore(":memory:")
        config = test_config()
        config = replace(config, models={**config.models, "openai/test": ModelEntry("openai/test", "openai")})
        self.app = create_app(config, self.store)
        self.app.on_startup.clear()
        self.state = self.app[STATE_KEY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer secret"}
        self.document = {"scope": SCOPE, "sessionId": str(uuid.uuid4()), "realm": "codex_subscription"}

    async def asyncTearDown(self):
        await self.client.close()
        self.env.stop()

    async def binding(self, **changes):
        return await self.client.post("/api/pi/reasoning-binding", headers=self.headers,
                                      json={**self.document, **changes})

    async def test_binding_is_authenticated_domain_separated_stable_and_scope_specific(self):
        denied = await self.client.post("/api/pi/reasoning-binding", json=self.document)
        self.assertEqual(denied.status, 401)
        first = await self.binding()
        self.assertEqual(first.status, 200)
        payload = await first.json()
        binding = payload["binding"]
        expected = hmac.new(bytes.fromhex(SECRET), f"pi\0{SCOPE}\0{self.document['sessionId']}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(binding["spartan_chain_id"], expected)
        mac = hmac.new(bytes.fromhex(SECRET), f"{expected}\0codex_subscription".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(binding["spartan_binding_mac"], mac)
        self.assertEqual((await (await self.binding()).json())["binding"], binding)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        for change in ({"scope": "a" * 64}, {"sessionId": str(uuid.uuid4())}):
            other = (await (await self.binding(**change)).json())["binding"]
            self.assertNotEqual(other["spartan_chain_id"], expected)
        self.assertNotIn(SECRET, str(payload))

    async def test_malformed_scope_session_realm_and_caller_chosen_chain_cannot_be_signed(self):
        for change in ({"scope": ""}, {"scope": "private/path"}, {"sessionId": "invalid"},
                       {"realm": "other"}, {"spartan_chain_id": "a" * 64}):
            response = await self.binding(**change)
            self.assertEqual(response.status, 400, change)
            self.assertNotIn("binding", await response.json())
        with patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET=""):
            response = await self.binding()
            self.assertEqual(response.status, 503)
        with patch.object(self.store, "bind_reasoning_chain", side_effect=RuntimeError("private-detail")):
            response = await self.binding()
            self.assertEqual(response.status, 503)
            self.assertNotIn("private-detail", await response.text())

    async def test_chain_realm_cannot_change_and_foreign_mac_is_rejected_before_execution(self):
        binding = (await (await self.binding()).json())["binding"]
        response = await self.binding(realm="openai_api")
        self.assertEqual(response.status, 409)
        body = {"model": "codex/gpt-5.4", "input": [{"type": "reasoning", "encrypted_content": "opaque"}],
                **binding, "spartan_binding_mac": "00" * 32}
        with patch("spartan_route.app.execute_responses", new_callable=AsyncMock) as execute:
            response = await self.client.post("/v1/responses", headers=self.headers, json=body)
            self.assertEqual(response.status, 400)
            self.assertEqual((await response.json())["error"]["code"], "reasoning_binding_invalid")
            execute.assert_not_awaited()


    async def test_catalog_advertises_native_protocol_and_realm_without_affecting_other_models(self):
        response = await self.client.get("/v1/models", headers=self.headers)
        models = {row["id"]: row for row in (await response.json())["data"]}
        codex = next(row for row in models.values() if row["owned_by"] == "codex")
        self.assertIn("responses", codex["api_modes"])
        self.assertEqual(codex["reasoning_realm"], "codex_subscription")
        self.assertEqual(models["openai/test"]["reasoning_realm"], "openai_api")
        for row in models.values():
            if row["owned_by"] not in {"codex", "openai", "spartan-route"}:
                self.assertNotIn("responses", row["api_modes"])
        detail = await self.client.get("/v1/models/" + codex["id"], headers=self.headers)
        self.assertEqual((await detail.json())["api_modes"], codex["api_modes"])

    async def test_auto_has_native_transport_only_when_routing_choices_share_a_realm(self):
        self.state.routing_policy = None
        tiers = {tier: {"primary": "codex/gpt-5.4"} for tier in self.state.config.tier_models}
        self.state.config = replace(self.state.config, tier_models=tiers, baseline_model="codex/gpt-5.4")
        response = await self.client.get("/v1/models", headers=self.headers)
        auto = (await response.json())["data"][0]
        self.assertEqual(auto["reasoning_realm"], "codex_subscription")
        self.assertIn("responses", auto["api_modes"])
        self.assertEqual(auto["reasoning_efforts"], [])
        supported = {"codex/gpt-5.4": {"low", "medium", "high"}, "codex/gpt-5.5": {"low", "medium"}}
        self.state.codex_catalog = SimpleNamespace(supports_effort=lambda model, effort: effort in supported.get(model, set()), stop=AsyncMock())
        self.state.config = replace(self.state.config, baseline_model="codex/gpt-5.5")
        response = await self.client.get("/v1/models", headers=self.headers)
        self.assertEqual((await response.json())["data"][0]["reasoning_efforts"], ["low", "medium"])
        self.state.config = replace(self.state.config, tier_models={**tiers, "simple": {"primary": "openai/test"}})
        response = await self.client.get("/v1/models", headers=self.headers)
        self.assertNotIn("responses", (await response.json())["data"][0]["api_modes"])

    async def test_openai_native_requests_retain_pi_usage_correlation(self):
        session_id = self.document["sessionId"]
        registered, _ = self.store.pi_usage.register(session_id, dict.fromkeys(COUNTERS, 0))
        headers = {**self.headers, "X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": session_id,
                   "X-Spartan-Pi-Origin": registered["originId"]}
        seen = []

        async def execute(*args):
            seen.append(CURRENT_REQUEST.get())
            return ExecutionResult(status=200, body={"output": []}, usage_seen=True,
                                   input_tokens=12, output_tokens=3)

        with patch("spartan_route.app.execute_responses", side_effect=execute):
            response = await self.client.post("/v1/responses", headers=headers,
                                              json={"model": "openai/test", "input": "hello"})
            self.assertEqual(response.status, 200)
            await response.read()
        self.assertEqual(seen[0].session_id, session_id)
        stored = self.store.pi_usage.get(session_id)
        self.assertEqual(stored["totals"]["totalTokens"], 15)
        self.assertEqual(stored["coverage"]["attempts"], 1)
        headers["X-Spartan-Pi-Origin"] = str(uuid.uuid4())
        with patch("spartan_route.app.execute_responses", new_callable=AsyncMock) as execute:
            response = await self.client.post("/v1/responses", headers=headers,
                                              json={"model": "openai/test", "input": "hello"})
            self.assertEqual(response.status, 409)
            execute.assert_not_awaited()


class NativePiAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_retry_and_stream_cleanup_record_each_actual_attempt_once(self):
        events = [("response.completed", {"response": {"status": "completed", "output": [],
                    "usage": {"input_tokens": 10, "output_tokens": 3}}})]
        for stream in (False, True):
            with self.subTest(stream=stream):
                store = TelemetryStore(":memory:")
                self.addCleanup(store.close)
                from spartan_route.pi_usage import PiUsage
                service = PiUsage(store.pi_usage, store)
                session = str(uuid.uuid4())
                row, _ = store.pi_usage.register(session, dict.fromkeys(COUNTERS, 0))
                headers = {"X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": session,
                           "X-Spartan-Pi-Origin": row["originId"]}
                correlation = service.correlate(headers)
                router = fixtures.NativeResponsesTests.router([fixtures.FakeResponse(status=500, text="retry"),
                                                      fixtures.FakeResponse(events=events)])
                token = CURRENT_REQUEST.set(correlation)
                try:
                    result = await execute_codex_responses(router, {"input": "hello"},
                                                          "codex/gpt-5.6-sol", stream, session, None)
                finally:
                    CURRENT_REQUEST.reset(token)
                if result.stream:
                    async for _ in result.stream:
                        pass
                    await result.stream_cleanup()
                observed = store.pi_usage.get(session)
                self.assertEqual(observed["coverage"]["attempts"], 2)
                self.assertEqual(observed["totals"]["totalTokens"], 13)
                self.assertEqual(observed["totals"]["codexTokens"], 13)
                self.assertTrue(observed["coverage"]["trackingIncomplete"] or observed["coverage"]["incompleteAttempts"])
                self.assertEqual(correlation.service.affinities[session][1], session)
