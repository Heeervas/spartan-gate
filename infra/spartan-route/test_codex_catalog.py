from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.codex_catalog import (
    ACCOUNT_GATED_BUNDLED_MODELS,
    MAX_AGE_SECONDS,
    MAX_RESPONSE_BYTES,
    CodexModelDiscovery,
    SlotCatalog,
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
            self.assertEqual(session.request[1]["params"], {"client_version": "0.155.1"})
            self.assertFalse(session.request[1]["allow_redirects"])
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

        async def fetch(auth):
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
