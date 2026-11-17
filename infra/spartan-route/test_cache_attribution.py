"""Per-response attribution over real selection and synthetic upstreams only."""
import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from spartan_route.app import STATE_KEY, create_app
from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.native_responses import execute_codex_responses
from spartan_route.telemetry import TelemetryStore
from test_app import test_config
import test_codex as fixtures
import test_codex_cache_affinity as affinity


class CacheAttributionTests(unittest.IsolatedAsyncioTestCase):
    def router(self, responses):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        self.addCleanup(store.close)
        router = CodexRouter(fixtures.NativeResponsesTests.SequenceSession(responses), store)
        router.slots = [CodexSlot(i, auth=CodexAuth("token-" + name, account_id="acct-" + name))
                        for i, name in enumerate(("a", "b"))]
        router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        self.addAsyncCleanup(router.cleanup)
        return router

    async def call(self, router, native=True, stream=False, key="scope"):
        if native:
            return await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", stream, None, key)
        return await router.execute_chat({"messages": [{"role": "user", "content": "hello"}]},
                                         "codex/gpt-6.1-sol", stream, None, key, "request")

    async def drain(self, result):
        if result.stream:
            async for _ in result.stream:
                pass

    def assert_attribution(self, result, slot, lease):
        self.assertEqual(result.headers.get("x-spartan-route-slot"), str(slot))
        self.assertEqual(result.headers.get("x-spartan-route-lease"), lease)
        values = " ".join(value for key, value in result.headers.items() if key.startswith("x-spartan-route-"))
        for private in ("acct-a", "acct-b", "token-a", "token-b", CodexAuth("", account_id="acct-a").account_key):
            self.assertNotIn(private, values)

    async def test_completion_creates_lease_and_stream_headers_are_ready_before_drain(self):
        for native in (True, False):
            for stream in (True, False):
                with self.subTest(native=native, stream=stream):
                    router = self.router([affinity.CodexCacheAffinityTests.response() for _ in range(2)])
                    first = await self.call(router, native, stream)
                    self.assert_attribution(first, 0, "new")
                    if stream:
                        self.assertEqual(router.leases, {})
                    await self.drain(first)
                    self.assertTrue(first.completed)
                    second = await self.call(router, native, stream)
                    self.assert_attribution(second, 0, "hit")
                    await self.drain(second)
                    self.assert_attribution(first, 0, "new")

    async def test_no_key_and_disabled_leases_report_none(self):
        for native in (True, False):
            for mode, key in (("keyed", None), ("off", "scope")):
                with self.subTest(native=native, mode=mode):
                    router = self.router([affinity.CodexCacheAffinityTests.response()])
                    with patch.dict(os.environ, CODEX_CACHE_LEASE_MODE=mode):
                        result = await self.call(router, native, key=key)
                    self.assert_attribution(result, 0, "none")

    async def test_retry_reports_final_slot_and_not_failed_lease_hit(self):
        for native in (True, False):
            for stream in (True, False):
                with self.subTest(native=native, stream=stream):
                    router = self.router([fixtures.FakeResponse(status=500, text='{"error":{"code":"server_error"}}'),
                                          affinity.CodexCacheAffinityTests.response()])
                    router._remember_lease("scope", router.slots[0])
                    result = await self.call(router, native, stream)
                    self.assert_attribution(result, 1, "new")
                    await self.drain(result)
                    self.assertEqual(result.status, 200)
                    self.assertEqual(len(router.session.calls), 2)

    async def test_expired_and_global_leases_and_issuer_preference(self):
        router = self.router([affinity.CodexCacheAffinityTests.response() for _ in range(4)])
        router._remember_lease("scope", router.slots[0])
        router.leases["scope"].max_expires_at = time.time() - 1
        expired = await self.call(router)
        self.assert_attribution(expired, 0, "new")
        with patch.dict(os.environ, CODEX_CACHE_LEASE_MODE="global"):
            first = await self.call(router, key="one")
            second = await self.call(router, key="two")
            self.assert_attribution(first, 1, "new")
            self.assert_attribution(second, 1, "hit")
        router._remember_lease("scope", router.slots[1])
        with patch("spartan_route.codex.prefers_auth", side_effect=lambda auth: auth == router.slots[0].auth):
            preferred = await self.call(router)
        self.assert_attribution(preferred, 0, "new")
        self.assertEqual(router.leases["scope"].slot_index, 0)

    async def test_selected_error_and_exhausted_retry_keep_last_selection(self):
        for native in (True, False):
            for status in (400, 500):
                with self.subTest(native=native, status=status):
                    router = self.router([fixtures.FakeResponse(status=status, text='{"error":{"code":"server_error"}}')])
                    router.slots = router.slots[:1]
                    with patch.dict(os.environ, SPARTAN_ROUTE_RECOVERY_WAIT_MS="0"):
                        result = await self.call(router, native)
                    self.assertGreaterEqual(result.status, 400)
                    self.assert_attribution(result, 0, "new")

    async def test_no_selected_slot_omits_headers_even_after_previous_hit(self):
        router = self.router([affinity.CodexCacheAffinityTests.response()])
        router._remember_lease("scope", router.slots[0])
        self.assert_attribution(await self.call(router), 0, "hit")
        router.slots = []
        for native in (True, False):
            result = await self.call(router, native)
            self.assertNotIn("x-spartan-route-slot", result.headers)
            self.assertNotIn("x-spartan-route-lease", result.headers)

    async def test_image_retry_reports_final_slot_without_affinity(self):
        image = fixtures.CodexTests().png_b64(1024, 1024)
        router = self.router([
            fixtures.FakeResponse(status=500, text='{"error":{"code":"server_error"}}'),
            fixtures.FakeResponse(events=[("response.output_item.done", {"item": {
                "type": "image_generation_call", "result": image}})]),
        ])
        result = await router.execute_image({"prompt": "draw a gate"})
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)
        self.assert_attribution(result, 1, "none")
        router.slots = []
        unavailable = await router.execute_image({"prompt": "draw a gate"})
        self.assertNotIn("x-spartan-route-slot", unavailable.headers)
        self.assertNotIn("x-spartan-route-lease", unavailable.headers)

    async def test_interleaved_requests_do_not_share_mutable_selection_state(self):
        router = self.router([])
        router._remember_lease("warm", router.slots[0])
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_open(_body, _headers, _auth, *_args, **_kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return affinity.CodexCacheAffinityTests.response()

        router.open_inference = delayed_open
        warm = asyncio.create_task(self.call(router, key="warm"))
        await entered.wait()
        cold = await self.call(router, key="cold")
        release.set()
        self.assert_attribution(await warm, 0, "hit")
        self.assert_attribution(cold, 1, "new")


@patch.dict(os.environ, SPARTAN_ROUTE_OPTIONAL_TELEMETRY_ENABLED="false", SPARTAN_REASONING_BINDING_SECRET="41" * 32)
class CacheAttributionHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_and_chat_headers_reach_http_start_without_optional_storage(self):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        app = create_app(test_config(), store)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        await app[STATE_KEY].codex_catalog.stop()
        app[STATE_KEY].codex_catalog = None
        router = app[STATE_KEY].codex
        router.model_catalog = None
        router.session = fixtures.NativeResponsesTests.SequenceSession([affinity.CodexCacheAffinityTests.response() for _ in range(8)])
        router.transport.session = router.session
        router.slots = [CodexSlot(7, auth=CodexAuth("private-token", account_id="private-account"))]
        router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        self.assertFalse(store.optional_telemetry_enabled)
        for path in ("responses", "chat/completions"):
            for stream in (False, True):
                router.leases.clear()
                key = f"{path}-{stream}"
                for lease in ("new", "hit"):
                    body = {"model": "codex/gpt-6-sol", "stream": stream, "prompt_cache_key": key}
                    body.update({"input": "hello"} if path == "responses" else {"messages": [{"role": "user", "content": "hello"}]})
                    response = await client.post("/v1/" + path, headers={"Authorization": "Bearer secret"}, json=body)
                    self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
                    self.assertEqual(response.headers.get("x-spartan-route-slot"), "7")
                    self.assertEqual(response.headers.get("x-spartan-route-lease"), lease)
                    self.assertNotIn("private-account", str(response.headers))
                    self.assertNotIn("private-token", str(response.headers))
                    await response.read()
        health = await client.get("/health")
        self.assertNotIn("x-spartan-route-slot", health.headers)
        self.assertNotIn("x-spartan-route-lease", health.headers)
