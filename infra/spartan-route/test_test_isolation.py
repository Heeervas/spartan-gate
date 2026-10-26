"""One boundary regression: no ambient auth/egress, real loopback catalog works."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from run_tests import isolated_route_tests


class RouteTestIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_network_seal_preserves_real_catalog(self):
        await self.assert_auth_and_egress_sealed()
        await self.assert_catalog_roundtrip()

    async def assert_auth_and_egress_sealed(self):
        # RED control never calls an actual resolver/connect or reads host auth.
        # Only synthetic home/env values and recording substitutes are involved.
        with tempfile.TemporaryDirectory(prefix="route-poison-", dir="/tmp") as directory:
            poison = {"HOME": directory, "OPENAI_CODEX_TOKEN": "synthetic-poison",
                      "HTTP_PROXY": "http://egress.invalid:8080", "OPENAI_API_KEY": "synthetic-key"}
            resolver, connect, connect_ex = Mock(return_value=[]), Mock(), Mock(return_value=0)
            with patch.dict(os.environ, poison, clear=True), patch.object(socket, "getaddrinfo", resolver), \
                    patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex):
                from spartan_route import codex
                from spartan_route.telemetry import TelemetryStore
                store = TelemetryStore(":memory:")
                try:
                    unsealed = codex.CodexRouter(None, store)
                    self.assertEqual(unsealed.slots[0].token, "synthetic-poison")
                    socket.getaddrinfo("egress.invalid", 443)
                    with socket.socket() as sock:
                        sock.connect(("203.0.113.10", 443))
                        sock.connect_ex(("203.0.113.10", 443))
                    self.assertEqual((resolver.call_count, connect.call_count, connect_ex.call_count), (1, 1, 1))
                    resolver.reset_mock(); connect.reset_mock(); connect_ex.reset_mock()
                    with isolated_route_tests() as root:
                        router = codex.CodexRouter(None, store)
                        self.assertIsNone(router.slots[0].token)
                        self.assertTrue(Path(router.slots[0].path).is_relative_to(root))
                        self.assertIsNone(await router.load_auth(router.slots[0]))
                        self.assertEqual(router.auth_store_root, root / "managed-auth")
                        explicit = codex.CodexRouter(None, store, auth_store_root=root / "explicit")
                        self.assertEqual(explicit.auth_store_root, root / "explicit")
                        for key in poison.keys() - {"HOME"}:
                            self.assertNotIn(key, os.environ)
                        self.assertEqual(Path.home(), root / "home")
                        # Existing tests may clear the environment themselves.
                        with patch.dict(os.environ, {}, clear=True):
                            self.assertTrue(Path(codex.resolve_auth_paths()[0]).is_relative_to(root))
                        self.assert_no_nonloopback_attempts(resolver, connect, connect_ex)
                finally:
                    store.close()

    def assert_no_nonloopback_attempts(self, resolver, connect, connect_ex):
        with self.assertRaises(PermissionError):
            socket.getaddrinfo("egress.invalid", 443)
        for family, address in [(socket.AF_INET, ("203.0.113.10", 443)),
                                (socket.AF_INET6, ("2001:db8::1", 443))]:
            with socket.socket(family) as sock:
                for method in (sock.connect, sock.connect_ex):
                    with self.assertRaises(PermissionError):
                        method(address)
        resolver.assert_not_called(); connect.assert_not_called(); connect_ex.assert_not_called()

    async def assert_catalog_roundtrip(self):
        # GREEN uses the actual catalog startup/load_auth/_fetch/parser/publisher
        # against a real local TCP HTTP service; no catalog transport mocks.
        with isolated_route_tests():
            from spartan_route import codex
            from spartan_route.telemetry import TelemetryStore
            from aiohttp import ClientSession, web
            from aiohttp.test_utils import TestServer
            from spartan_route import codex_catalog
            requests = []

            async def models(request):
                requests.append((request.headers.get("Authorization"), request.headers.get("chatgpt-account-id")))
                return web.json_response({"models": [{
                    "slug": "fixture-model", "visibility": "list", "supported_in_api": True,
                    "minimal_client_version": "0.155.0", "input_modalities": ["text"],
                    "max_context_window": 4096, "supported_reasoning_levels": [{"effort": "low"}],
                    "tool_mode": "code_mode_only",
                }]})

            app = web.Application()
            app.router.add_get("/models", models)
            async with TestServer(app, host="127.0.0.1") as server, ClientSession() as session:
                store = TelemetryStore(":memory:")
                router = codex.CodexRouter(session, store)
                router.slots = [codex.CodexSlot(0, token="synthetic-fixture", auth=codex.CodexAuth("synthetic-fixture", account_id="fixture-account"))]
                published = asyncio.Event()
                publication = {}

                async def publish(slots, models):
                    publication.update(models)
                    catalog.install_snapshot(slots, models)
                    published.set()

                catalog = codex_catalog.CodexModelDiscovery(router, publish, set())
                try:
                    with patch.object(codex_catalog, "CODEX_CATALOG_URL", str(server.make_url("/models"))):
                        catalog.start()
                        await asyncio.wait_for(published.wait(), timeout=3)
                    self.assertEqual(requests, [("Bearer synthetic-fixture", "fixture-account")])
                    self.assertIn("codex/fixture-model", publication)
                    self.assertEqual(publication["codex/fixture-model"].max_context, 4096)
                    self.assertIn("codex/fixture-model", catalog.discovered_ids)
                finally:
                    await catalog.stop()
                    await router.cleanup()
                    store.close()
