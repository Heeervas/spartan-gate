from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.codex_catalog import (
    ACCOUNT_GATED_BUNDLED_MODELS,
    CATALOG_CLIENT_VERSION,
    MAX_AGE_SECONDS,
    MAX_RESPONSE_BYTES,
    CodexModelDiscovery,
    SlotCatalog,
    cached_client_version,
    parse_catalog,
)
from spartan_route.telemetry import TelemetryStore


def row(slug: str, *, visibility: str = "list", version: str = "0.155.0", efforts: tuple[str, ...] = ("low", "medium", "max")) -> dict:
    return {
        "slug": slug,
        "visibility": visibility,
        "supported_in_api": True,
        "minimal_client_version": version,
        "input_modalities": ["text", "image"],
        "max_context_window": 872000,
        "supported_reasoning_levels": [{"effort": effort} for effort in efforts],
        "tool_mode": "code_mode_only",
    }


class CatalogParserTests(unittest.TestCase):
    def test_gpt_6_1_sol_uses_subscription_maximum_context(self) -> None:
        catalog = parse_catalog({"models": [{
            **row("gpt-6.1-sol", version="0.153.0"),
            "context_window": 272000,
            "multi_agent_version": "v2",
        }]})
        self.assertEqual(catalog["codex/gpt-6.1-sol"].entry.max_context, 872000)

    def test_visible_compatible_models_only(self) -> None:
        catalog = parse_catalog({"models": [
            row("gpt-6-sol"), row("gpt-6-luna", efforts=("low", "medium")),
            row("gpt-reserve", visibility="hide"),
            row("future-model", version="0.156.0"),
            {**row("not-api"), "supported_in_api": False},
            {**row("audio-only"), "input_modalities": ["audio"]},
        ]})
        self.assertEqual(set(catalog), {"codex/gpt-6-sol", "codex/gpt-6-luna"})
        self.assertEqual(catalog["codex/gpt-6-sol"].entry.max_context, 872000)
        self.assertTrue(catalog["codex/gpt-6-sol"].entry.multimodal)
        self.assertNotIn("max", catalog["codex/gpt-6-luna"].efforts)

    def test_malformed_visible_catalog_fails_closed(self) -> None:
        for payload in (
            {"models": "not-a-list"},
            {"models": [{**row("gpt-6-sol"), "max_context_window": -1}]},
            {"models": [row("gpt-6-sol"), row("gpt-6-sol")]},
            {"models": [{**row("gpt-6-sol"), "minimal_client_version": "bad"}]},
            {"models": [{**row("gpt-6-sol"), "slug": "../../escape"}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_catalog(payload)


class CatalogVersionTests(unittest.TestCase):
    def test_newest_enabled_cache_applies_to_all_accounts_without_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            slots = []
            for index, version in enumerate(("0.148.0", "0.160.0", "0.999.0")):
                home = Path(directory) / str(index)
                home.mkdir()
                (home / "models_cache.json").write_text(json.dumps({"client_version": version}))
                slots.append(SimpleNamespace(path=str(home / "auth.json"), enabled=index != 2))
            router = SimpleNamespace(slots=slots, slot_routing_status=lambda slot: {"routingEnabled": slot.enabled})
            catalog = CodexModelDiscovery(router, AsyncMock(), set())
            self.assertEqual(catalog.catalog_request_version(), (0, 160, 0))
            Path(slots[1].path).with_name("models_cache.json").write_text('{"client_version":"0.148.0"}')
            self.assertEqual(catalog.catalog_request_version(), (0, 160, 0))
            router.slots = []
            self.assertEqual(CodexModelDiscovery(router, AsyncMock(), set()).catalog_request_version(), CATALOG_CLIENT_VERSION)

    def test_invalid_missing_oversized_and_nonregular_caches_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_path = str(Path(directory) / "auth.json")
            cache = Path(directory) / "models_cache.json"
            self.assertIsNone(cached_client_version(auth_path))
            self.assertIsNone(cached_client_version(None))
            self.assertIsNone(cached_client_version("/"))
            for raw in (
                b'{}', b'[]', b'not json', b'{"client_version":true}',
                b'{"client_version":"0.159"}', b'{"client_version":"99999.1.0"}',
                b'{"client_version":"0.159.0-secret"}', b'\xff',
                b' ' * (MAX_RESPONSE_BYTES + 1),
            ):
                with self.subTest(raw=raw[:50]):
                    cache.write_bytes(raw)
                    self.assertIsNone(cached_client_version(auth_path))
            cache.unlink()
            os.mkfifo(cache)
            self.assertIsNone(cached_client_version(auth_path))


class CatalogFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_reads_complete_bounded_body_without_redirects(self) -> None:
        class Content:
            def __init__(self, chunks):
                self.chunks = iter(chunks)

            async def read(self, _size):
                return next(self.chunks, b"")

        class Response:
            status = 200

            def __init__(self, chunks):
                self.content = Content(chunks)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            def __init__(self, chunks):
                self.chunks = chunks
                self.request = None

            def get(self, url, **kwargs):
                self.request = (url, kwargs)
                return Response(self.chunks)

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter(None, store)
            async def publish(_slots, _models):
                pass
            catalog = CodexModelDiscovery(router, publish, set())
            raw = json.dumps({"models": [row("gpt-6-sol")]}).encode()
            session = Session([raw[:13], raw[13:]])
            router.session = session
            fetched = await catalog._fetch(CodexAuth("secret", account_id="account"))
            self.assertIn("codex/gpt-6-sol", fetched)
            self.assertEqual(session.request[1]["params"], {"client_version": "0.159.0"})
            self.assertFalse(session.request[1]["allow_redirects"])
            router.session = Session([raw])
            await catalog._fetch(CodexAuth("secret", account_id="account"), client_version=(0, 160, 0))
            self.assertEqual(router.session.request[1]["params"], {"client_version": "0.160.0"})
            router.session = Session([b"x" * MAX_RESPONSE_BYTES, b"y"])
            with self.assertRaises(ValueError):
                await catalog._fetch(CodexAuth("secret", account_id="account"))
        finally:
            store.close()


class CatalogRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TelemetryStore(":memory:")
        self.router = CodexRouter(None, self.store)
        self.router.slots = [
            CodexSlot(0, token="token-a", auth=CodexAuth("token-a", account_id="account-a")),
            CodexSlot(1, token="token-b", auth=CodexAuth("token-b", account_id="account-b")),
        ]
        self.publications: list[dict] = []

        async def publish(slots, models):
            self.publications.append(models)
            self.catalog.install_snapshot(slots, models)

        self.catalog = CodexModelDiscovery(self.router, publish, set(ACCOUNT_GATED_BUNDLED_MODELS))
        self.router.model_catalog = self.catalog

        async def fetch(auth, **_kwargs):
            slug = "gpt-6-sol" if auth.account_id == "account-a" else "gpt-6-luna"
            return parse_catalog({"models": [row(slug)]})

        self.catalog._fetch = fetch
        self.assertTrue(self.catalog.known_unavailable("codex/gpt-6-sol"))
        self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol"))
        await self.catalog.refresh()

    async def asyncTearDown(self) -> None:
        self.store.close()

    async def test_partial_rollout_filters_accounts_and_affinity(self) -> None:
        self.assertEqual(set(self.publications[-1]), {"codex/gpt-6-sol", "codex/gpt-6-luna"})
        sol = await self.router.choose_slot("session", set(), set(), "codex/gpt-6-sol", "max")
        self.assertIsNotNone(sol)
        self.assertEqual(sol[0].index, 0)
        self.router._remember_lease("session", self.router.slots[1])
        sol_again = await self.router.choose_slot("session", set(), set(), "codex/gpt-6-sol", "max")
        self.assertEqual(sol_again[0].index, 0)
        luna = await self.router.choose_slot(None, set(), set(), "codex/gpt-6-luna", "max")
        self.assertEqual(luna[0].index, 1)

    async def test_newer_request_version_retries_baseline_for_each_account(self) -> None:
        models = parse_catalog({"models": [row("gpt-6.1-sol", version="0.153.0")]})
        self.catalog._fetch = AsyncMock(side_effect=[ValueError("catalog_http_400"), models] * 2)
        with patch.object(self.catalog, "catalog_request_version", return_value=(0, 160, 0)):
            await self.catalog.refresh()
        self.assertEqual(self.catalog._fetch.await_args_list, [
            call(self.router.slots[0].auth, client_version=(0, 160, 0)),
            call(self.router.slots[0].auth, client_version=CATALOG_CLIENT_VERSION),
            call(self.router.slots[1].auth, client_version=(0, 160, 0)),
            call(self.router.slots[1].auth, client_version=CATALOG_CLIENT_VERSION),
        ])
        self.assertIn("codex/gpt-6.1-sol", self.publications[-1])

    async def test_failed_hint_and_fallback_keep_previous_account_catalog(self) -> None:
        self.catalog._fetch = AsyncMock(side_effect=RuntimeError("unavailable"))
        with patch.object(self.catalog, "catalog_request_version", return_value=(0, 160, 0)):
            await self.catalog.refresh()
        self.assertEqual(self.catalog._fetch.await_count, 4)
        self.assertFalse(self.catalog.known_unavailable("codex/gpt-6-sol"))

    async def test_cancelled_hint_is_not_retried(self) -> None:
        self.catalog._fetch = AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(self.catalog, "catalog_request_version", return_value=(0, 160, 0)):
            with self.assertRaises(asyncio.CancelledError):
                await self.catalog.refresh()
        self.assertEqual(self.catalog._fetch.await_count, 1)

    async def test_context_uses_smallest_offering_account_and_updates_after_refresh(self) -> None:
        async def fetch(auth, **_kwargs):
            context = 900000 if auth.account_id == "account-a" else 400000
            return parse_catalog({"models": [{**row("gpt-6.1-sol"), "max_context_window": context}]})
        self.catalog._fetch = fetch
        await self.catalog.refresh()
        self.assertEqual(self.publications[-1]["codex/gpt-6.1-sol"].max_context, 400000)
        await self.router.set_slot_routing_enabled(1, False)
        await self.catalog.refresh()
        self.assertEqual(self.publications[-1]["codex/gpt-6.1-sol"].max_context, 900000)

    async def test_account_change_and_disabled_slot_fail_closed(self) -> None:
        self.router.slots[0].auth = CodexAuth("new-token", account_id="replacement")
        self.assertTrue(self.catalog.known_unavailable("codex/gpt-6-sol"))
        self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol"))
        self.router.slots[0].auth = CodexAuth("token-a", account_id="account-a")
        await self.router.set_slot_routing_enabled(0, False)
        self.assertTrue(self.catalog.known_unavailable("codex/gpt-6-sol"))
        self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol"))

    async def test_failed_refresh_expires_validated_catalog(self) -> None:
        self.catalog._fetch = AsyncMock(side_effect=RuntimeError("unavailable"))
        await self.catalog.refresh()
        self.assertIn(0, self.catalog.slots)
        self.catalog.slots[0] = replace(self.catalog.slots[0], fetched_at=time.monotonic() - MAX_AGE_SECONDS - 1)
        await self.catalog.refresh()
        self.assertNotIn(0, self.catalog.slots)
        self.assertTrue(self.catalog.known_unavailable("codex/gpt-6-sol"))

    async def test_healthy_account_rotation_refreshes_even_without_previous_catalog(self) -> None:
        from test_codex import CodexTests, NoCallOAuthSession

        fixtures = CodexTests()

        async def fetch(auth, **_kwargs):
            slug = "gpt-6-sol" if auth.account_id == "account-a" else "gpt-6-luna"
            return parse_catalog({"models": [row(slug, efforts=("low", "medium", "high", "max"))]})

        self.catalog._fetch = fetch
        for populated in (True, False):
            with self.subTest(populated=populated), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                auth_file = root / "auth.json"
                expiry = time.time() + 3600
                fixtures.write_auth(auth_file, fixtures.jwt(expiry), account_id="account-a")
                self.router.auth_store_root = root / "codex-auth"
                self.router.session = NoCallOAuthSession()
                self.router._persist_refresh(str(auth_file), {
                    "access_token": fixtures.jwt(expiry + 7200),
                    "refresh_token": "managed-a",
                    "account_id": "account-a",
                })
                slot = CodexSlot(0, path=str(auth_file))
                self.router.slots = [slot]
                await self.catalog.refresh()
                self.assertEqual(slot.auth_state_source, "managed")
                self.assertTrue(self.catalog.supports(slot, slot.auth, "codex/gpt-6-sol", "max"))
                if not populated:
                    self.catalog.slots = {}
                self.catalog._wake.clear()
                fixtures.write_auth(auth_file, fixtures.jwt(expiry + 1800), account_id="account-b")

                auth = await self.router.load_auth(slot)

                self.assertEqual(auth.account_id, "account-b")
                self.assertNotIn(slot.index, self.catalog.slots)
                self.assertTrue(self.catalog._wake.is_set())
                self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol", "max"))
                self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-luna", "high"))
                await self.catalog.refresh()
                selected = await self.router.choose_slot(None, set(), set(), "codex/gpt-6-luna", "high")
                self.assertIsNotNone(selected)
                self.assertEqual(selected[1].account_id, "account-b")
                self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol", "max"))

    async def test_invalid_bootstrap_with_managed_fallback_revokes_catalog_eligibility(self) -> None:
        from test_codex import CodexTests, NoCallOAuthSession

        fixtures = CodexTests()
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                auth_file = root / "auth.json"
                expiry = time.time() + 3600
                fixtures.write_auth(auth_file, fixtures.jwt(expiry), account_id="account-a")
                self.router.auth_store_root = root / "codex-auth"
                self.router.session = NoCallOAuthSession()
                self.router._persist_refresh(str(auth_file), {
                    "access_token": fixtures.jwt(expiry + 3600),
                    "refresh_token": "managed-a",
                    "account_id": "account-a",
                })
                slot = CodexSlot(0, path=str(auth_file))
                self.router.slots = [slot]
                await self.catalog.refresh()
                self.assertTrue(self.catalog.supports(slot, slot.auth, "codex/gpt-6-sol", "max"))
                if missing:
                    auth_file.unlink()
                else:
                    auth_file.write_text("not json")

                auth = await self.router.load_auth(slot)

                self.assertIsNotNone(auth)
                self.assertEqual(slot.auth_state_source, "managed")
                self.assertNotIn(slot.index, self.catalog.slots)
                await self.catalog.refresh()
                self.assertNotIn(slot.index, self.catalog.slots)
                self.assertIsNone(await self.router.choose_slot(None, set(), set(), "codex/gpt-6-sol", "max"))

    async def test_bootstrap_file_change_revokes_old_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / "auth.json"
            auth_file.write_text(json.dumps({"tokens": {"account_id": "account-a", "access_token": "token-a"}}))
            slot = self.router.slots[0]
            slot.path = str(auth_file)
            source, _ = self.catalog._source(slot)
            old = self.catalog.slots[0]
            self.catalog.slots[0] = SlotCatalog(old.account_key, source, old.fetched_at, old.models)
            self.assertTrue(self.catalog.supports(slot, slot.auth, "codex/gpt-6-sol"))
            auth_file.write_text(json.dumps({"tokens": {"account_id": "replacement", "access_token": "new-token"}}))
            self.assertFalse(self.catalog.supports(slot, slot.auth, "codex/gpt-6-sol"))
            self.assertTrue(self.catalog.known_unavailable("codex/gpt-6-sol"))


if __name__ == "__main__":
    unittest.main()
