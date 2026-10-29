from __future__ import annotations

import asyncio
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from test_app import CaptureChatCodex, FakeImageCodex, test_config
from spartan_route.app import STATE_KEY, create_app, make_routing_policy_snapshot
from spartan_route.config import ModelEntry
from spartan_route.model_variants import VariantPolicy
from spartan_route.provider_policy import ProviderPolicy, SUPPORTED, initialize_store
from spartan_route.telemetry import TelemetryStore

AUTH = {"Authorization": "Bearer secret"}
PATH = "/api/config/providers"


def rule(**changes):
    return {"enabled": True, "rejectEndpoints": [], "rejectModels": [], **changes}


class StoredPolicyTests(unittest.TestCase):
    def test_absent_restrictions_restart_clear_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "policy.db")
            store = TelemetryStore(path)
            initialize_store(store)
            self.assertIsNone(ProviderPolicy.load(store.provider_settings).providers)
            store.provider_settings.replace({"openai": rule(rejectModels=["gpt-6-luna"])})
            store.close()
            store = TelemetryStore(path)
            initialize_store(store)
            policy = ProviderPolicy.load(store.provider_settings)
            self.assertEqual(policy.denial("openai", "/v1/responses", "gpt-6-luna"), "provider_model_rejected")
            self.assertIsNone(policy.denial("openai", "/v1/responses", "gpt-6-luna-plus"))
            self.assertIsNone(policy.denial("codex", "/v1/responses", "codex/gpt-6-luna"))
            store.provider_settings.replace({"openai": rule()})
            self.assertIsNone(ProviderPolicy.load(store.provider_settings).denial("openai", "/v1/responses", "gpt-6-luna"))
            for raw in ("{", "null", '{"version":1,"providers":{},"updatedAt":null}'):
                store._conn.execute("UPDATE provider_config SET document=?", (raw,))
                store._conn.commit()
                broken = ProviderPolicy.load(store.provider_settings)
                self.assertTrue(broken.unavailable)
                self.assertEqual(broken.denial("codex", "/v1/responses", "gpt-6-luna"), "provider_policy_unavailable")
                self.assertEqual(store._conn.execute("SELECT document FROM provider_config").fetchone()[0], raw)
            store.close()

    def test_transaction_failure_keeps_stored_restrictions(self):
        store = TelemetryStore(":memory:")
        initialize_store(store)
        store.provider_settings.replace({"openai": rule(enabled=False)})
        before = store.provider_settings.read()
        store._conn.execute("CREATE TRIGGER refuse_provider BEFORE INSERT ON provider_config BEGIN SELECT RAISE(FAIL, 'fixture write failure'); END")
        store._conn.commit()
        with self.assertRaises(Exception):
            store.provider_settings.replace({"openai": rule()})
        self.assertEqual(store.provider_settings.read(), before)
        store.close()


class ProviderFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.response_status = 200
        self.response_body = None
        self.delay = 0
        self.dispatched = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

        async def upstream(request):
            body = await request.json() if request.content_type == "application/json" else dict(await request.post())
            self.calls.append((request.path, body, request.headers.get("Authorization")))
            self.dispatched.set()
            if self.block:
                await self.release.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            payload = self.response_body
            if payload is None:
                payload = ({"id": "response-fixture", "status": "completed", "output": [], "usage": {"input_tokens": 12, "output_tokens": 0}}
                           if request.path.endswith("/responses") else {"choices": [], "data": [{"b64_json": "ZmFrZQ=="}]})
            return web.json_response(payload, status=self.response_status)

        upstream_app = web.Application()
        upstream_app.router.add_post("/v1/{tail:.*}", upstream)
        self.upstream = TestServer(upstream_app)
        await self.upstream.start_server()
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-openai-key", "OPENROUTER_API_KEY": "synthetic-openrouter-key",
                            "OPENAI_BASE_URL": str(self.upstream.make_url("/v1")), "OPENROUTER_BASE_URL": str(self.upstream.make_url("/v1")),
                            "OLLAMA_ENDPOINT": str(self.upstream.make_url("/")), "SPARTAN_REASONING_BINDING_SECRET": "41" * 32})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = TelemetryStore(":memory:")
        config = test_config()
        config.models["openrouter/openai/gpt-test"] = ModelEntry("openrouter/openai/gpt-test", "openrouter")
        self.app = create_app(config, self.store)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.state = self.app[STATE_KEY]
        await self.state.codex_catalog.stop()

    async def asyncTearDown(self):
        self.release.set()
        await self.client.close()
        await self.upstream.close()

    async def get(self):
        response = await self.client.get(PATH, headers=AUTH)
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(response.headers["ETag"], data["etag"])
        return data

    async def save(self, providers, etag=None):
        etag = etag or (await self.get())["etag"]
        return await self.client.put(PATH, headers={**AUTH, "If-Match": etag}, json={"providers": providers})

    async def post(self, endpoint="/v1/chat/completions", model="openai/gpt-test", **fields):
        payload = {"input": "hello"} if endpoint == "/v1/responses" else {"messages": []}
        return await self.client.post(endpoint, headers=AUTH, json={"model": model, **payload, **fields})


class ProviderRealtimeTests(ProviderFixture):
    async def asyncSetUp(self):
        from spartan_route.realtime import RealtimeConfig, VoiceClient, digest
        self.voice_token = "fixture-voice-secret-" + "a" * 32
        voice = RealtimeConfig(enabled=True, clients=(VoiceClient("fixture", digest(self.voice_token), 0),))
        with patch("spartan_route.app.RealtimeConfig.from_env", return_value=voice):
            await super().asyncSetUp()

    async def test_realtime_mint_uses_codex_policy_and_preserves_voice_auth_without_api_key(self):
        from spartan_route import realtime as rt
        from spartan_route.codex import CodexAuth, CodexSlot
        auth = CodexAuth("fixture-oauth", account_id="fixture-account")
        slot = CodexSlot(0, path="/fixture/auth.json", auth=auth)
        router = SimpleNamespace(slots=[slot], load_auth=AsyncMock(return_value=auth),
                                 slot_routing_status=lambda s: {"routingEnabled": True, "requestedRoutingEnabled": True})
        service = self.app[rt.RT_KEY]
        service.codex = router
        self.response_body = {"value": "fixture-upstream-ticket", "expires_at": time.time() + 60}
        payload = {"session": {"type": "realtime", "model": rt.MODEL}}
        headers = {"Authorization": "Bearer " + self.voice_token}
        # A successful native mint establishes the baseline before each denial.
        with patch.dict("os.environ", OPENAI_API_KEY=""), patch.object(rt, "MINT_URL", str(self.upstream.make_url("/v1/realtime/client_secrets"))), patch.object(rt, "checked_proxy", return_value={}):
            result = await self.client.post("/v1/realtime/client_secrets", headers=headers, json=payload)
            self.assertEqual(result.status, 200)
            self.assertEqual(self.calls[-1][2], "Bearer fixture-oauth")
            for reservation in list(service.pool.entries.values()):
                service.pool.release(reservation)
            for setting in (rule(enabled=False), rule(rejectEndpoints=["/v1/realtime"]),
                            rule(rejectEndpoints=["/v1/realtime/client_secrets"]), rule(rejectModels=["codex/" + rt.MODEL])):
                await self.save({"codex": setting})
                before = len(self.calls)
                self.assertEqual((await self.client.post("/v1/realtime/client_secrets", headers=headers, json=payload)).status, 403)
                self.assertEqual(len(self.calls), before)
                self.assertEqual((await self.client.post("/v1/realtime/client_secrets", headers=AUTH, json=payload)).status, 401)
            await self.save({"codex": rule(), "openai": rule(enabled=False)})
            self.assertEqual((await self.client.post("/v1/realtime/client_secrets", headers=headers, json=payload)).status, 200)


class ProviderApiTests(ProviderFixture):
    async def test_contract_auth_defaults_partial_merge_and_preconditions(self):
        self.assertEqual((await self.client.get(PATH)).status, 401)
        self.assertEqual((await self.client.put(PATH, json={"providers": {}})).status, 401)
        data = await self.get()
        self.assertEqual(set(data), {"providers", "etag", "configured", "updatedAt"})
        self.assertFalse(data["configured"])
        self.assertEqual(set(data["providers"]), set(SUPPORTED))
        for provider in data["providers"].values():
            self.assertEqual(set(provider), {"enabled", "rejectEndpoints", "rejectModels", "models", "supportedEndpoints", "credentialStatus"})
            self.assertTrue(provider["enabled"])
            self.assertEqual(provider["rejectEndpoints"], [])
            self.assertEqual(provider["rejectModels"], [])
        self.assertNotIn("synthetic", json.dumps(data))
        self.assertEqual((await self.client.put(PATH, headers=AUTH, json={"providers": {}})).status, 428)
        self.assertEqual((await self.save({"openai": rule(enabled=False)})).status, 200)
        self.assertEqual((await self.save({"codex": rule(rejectEndpoints=["/v1/responses"])})).status, 200)
        saved = await self.get()
        self.assertFalse(saved["providers"]["openai"]["enabled"])
        self.assertTrue(saved["configured"])
        self.assertIsNotNone(saved["updatedAt"])
        self.assertEqual((await self.save({}, data["etag"])).status, 412)
        self.state.provider_policy = ProviderPolicy.load(self.store.provider_settings)
        self.assertEqual(await self.get(), saved)

    async def test_invalid_write_concurrent_save_and_persistence_failure_are_atomic(self):
        before = await self.get()
        bad = [{"unknown": rule()}, {"openai": {**rule(), "secret": "not allowed"}},
               {"openai": rule(enabled=1)}, {"openai": rule(rejectEndpoints=["/v1/embeddings"])},
               {"openai": rule(rejectModels=["gpt-*"])}, {"openai": rule(rejectModels=["codex/gpt-test"])}]
        for providers in bad:
            self.assertEqual((await self.save(providers)).status, 400)
            self.assertEqual(await self.get(), before)
        oversized = await self.client.put(PATH, headers={**AUTH, "If-Match": before["etag"]}, data=b" " * 16385)
        self.assertEqual(oversized.status, 413)
        results = await asyncio.gather(self.save({"openai": rule(enabled=False)}, before["etag"]), self.save({"codex": rule(enabled=False)}, before["etag"]))
        self.assertEqual(sorted(r.status for r in results), [200, 412])
        before = await self.get()
        with patch.object(self.store.provider_settings, "replace", side_effect=RuntimeError("synthetic failure")):
            self.assertEqual((await self.save({"openai": rule()})).status, 503)
        self.assertEqual(await self.get(), before)

    async def test_real_dispatch_defaults_speed_passthrough_and_same_path_isolation(self):
        self.assertEqual((await self.post(service_tier="priority", speed="ultrafast")).status, 200)
        self.assertEqual(self.calls[-1][1]["model"], "gpt-test")
        self.assertEqual(self.calls[-1][1]["service_tier"], "priority")
        self.assertEqual(self.calls[-1][1]["speed"], "ultrafast")
        self.assertEqual(self.calls[-1][2], "Bearer synthetic-openai-key")
        self.assertEqual((await self.post("/v1/responses", service_tier="priority", speed="fast")).status, 200)
        self.assertEqual(self.calls[-1][1]["speed"], "fast")
        await self.save({"openai": rule(rejectEndpoints=["/v1/chat/completions"])})
        before = len(self.calls)
        denied = await self.post()
        self.assertEqual(denied.status, 403)
        self.assertEqual((await denied.json())["error"]["code"], "provider_endpoint_rejected")
        self.assertEqual(len(self.calls), before)
        self.assertEqual((await self.post(model="openrouter/openai/gpt-test")).status, 200)
        self.assertEqual(self.calls[-1][2], "Bearer synthetic-openrouter-key")
        self.state.codex = CaptureChatCodex()
        self.state.codex_catalog = None
        self.assertEqual((await self.post(model="codex/gpt-6-luna")).status, 200)
        self.assertEqual((await self.post("/v1/responses")).status, 200)
        await self.save({"openai": rule()})
        self.assertEqual((await self.post()).status, 200)

    async def test_alias_exact_model_auto_and_catalog_are_policy_bound(self):
        self.state.model_variants = VariantPolicy((("friendly", "openai/gpt-test", 100000),))
        self.assertEqual((await self.post(model="friendly")).status, 200)
        await self.save({"openai": rule(rejectModels=["gpt-test"])})
        for model in ("gpt-test", "openai/gpt-test", "custom-1/openai/gpt-test", "friendly"):
            self.assertEqual((await self.post(model=model)).status, 403)
        self.assertEqual((await self.post(model="openai/gpt-test-plus")).status, 200)
        self.state.routing_policy = make_routing_policy_snapshot(self.state.config, {tier: "openai/gpt-test" for tier in ("heartbeat", "simple", "moderate", "complex")})
        denied = await self.post(model="auto")
        self.assertEqual(denied.status, 503)
        self.assertEqual((await denied.json())["error"]["code"], "auto_target_not_eligible")
        catalog = await (await self.client.get("/v1/models")).json()
        for model in ("openai/gpt-test", "friendly"):
            entry = next(e for e in catalog["data"] if e["id"] == model)
            self.assertFalse(entry["enabled"])
            self.assertEqual(entry["api_modes"], [])
        routing = await (await self.client.get("/api/config/routing", headers=AUTH)).json()
        self.assertFalse(next(e for e in routing["models"] if e["id"] == "openai/gpt-test")["eligible"])

    async def test_images_enforce_actual_provider_and_selected_codex_host(self):
        fake = FakeImageCodex()
        self.state.codex = fake
        self.assertEqual((await self.post("/v1/images/generations", "gpt-image-2", prompt="inert")).status, 200)
        self.assertEqual(self.calls[-1][0], "/v1/images/generations")
        self.assertEqual((await self.post("/v1/images/generations", "codex/gpt-image-2", prompt="inert")).status, 200)
        await self.save({"openai": rule(rejectEndpoints=["/v1/images/generations"])})
        self.assertEqual((await self.post("/v1/images/generations", "gpt-image-2")).status, 403)
        self.assertEqual((await self.post("/v1/images/generations", "codex/gpt-image-2")).status, 200)
        with patch.dict("os.environ", CODEX_IMAGE_HOST_MODEL="gpt-6-luna"):
            await self.save({"codex": rule(rejectModels=["codex/gpt-6-luna"])})
            before = len(fake.requests)
            self.assertEqual((await self.post("/v1/images/generations", "codex/gpt-image-2")).status, 403)
            form = FormData()
            form.add_field("model", "codex/gpt-image-2")
            form.add_field("image", b"fake", filename="fixture.png", content_type="image/png")
            self.assertEqual((await self.client.post("/v1/images/edits", headers=AUTH, data=form)).status, 403)
            self.assertEqual(len(fake.requests), before)
        await self.save({"codex": rule(rejectModels=["codex/gpt-image-2"])})
        with patch.dict("os.environ", OPENAI_API_KEY=""):
            self.assertEqual((await self.post("/v1/images/generations", "gpt-image-2")).status, 403)

    async def test_policy_save_does_not_cancel_an_admitted_upstream_request(self):
        self.block = True
        request = asyncio.create_task(self.post())
        await self.dispatched.wait()
        await self.save({"openai": rule(enabled=False)})
        self.assertEqual((await self.post()).status, 403)
        self.release.set()
        self.assertEqual((await request).status, 200)

    async def test_corruption_and_read_failure_do_not_admit_or_overwrite(self):
        await self.save({"openai": rule(enabled=False)})
        self.store._conn.execute("UPDATE provider_config SET document='{' ")
        self.store._conn.commit()
        self.state.provider_policy = ProviderPolicy.load(self.store.provider_settings)
        self.assertEqual((await self.client.get(PATH, headers=AUTH)).status, 503)
        self.assertEqual((await self.client.put(PATH, headers={**AUTH, "If-Match": "anything"}, json={"providers": {}})).status, 503)
        self.assertEqual((await self.post()).status, 503)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store._conn.execute("SELECT document FROM provider_config").fetchone()[0], "{")
        with patch.object(self.store.provider_settings, "read", side_effect=RuntimeError("fixture read failure")):
            self.assertTrue(ProviderPolicy.load(self.store.provider_settings).unavailable)
