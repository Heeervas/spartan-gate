from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from spartan_route.app import STATE_KEY, create_app
from spartan_route.telemetry import RoutingLogEntry, TelemetryStore
import test_app


class DashboardQueryTests(unittest.TestCase):
    def test_live_sessions_materializes_only_selected_whole_turns(self) -> None:
        store = TelemetryStore(":memory:")
        self.addCleanup(store.close)
        for turn in range(30):
            for request in range(2):
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-10-20T12:00:00Z", original_model="auto",
                    routed_model="codex/test", actual_model="codex/test", tier="complex",
                    session_id="session", turn_id=f"turn-{turn}",
                    request_id=f"request-{turn}-{request}", context_info='{"usage_seen":true}',
                ))
        detail_rows = 0

        def factory(cursor, row):
            nonlocal detail_rows
            if "input_tokens" in [column[0] for column in cursor.description]:
                detail_rows += 1
            return sqlite3.Row(cursor, row)

        store._conn.row_factory = factory
        result = store.live_sessions(session_limit=1, turn_limit=2)
        self.assertEqual(result["matchingTurns"], 30)
        self.assertEqual(result["sessions"][0]["metrics"]["requests"], 4)
        self.assertEqual(detail_rows, 4)

    def test_capacity_multipliers_are_one_query_with_defaults(self) -> None:
        store = TelemetryStore(":memory:")
        self.addCleanup(store.close)
        store.set_codex_capacity_multiplier("a" * 16, 5)
        queries = []
        store._conn.set_trace_callback(queries.append)
        self.assertEqual(store.codex_capacity_multipliers({"a" * 16, "b" * 16}),
                         {"a" * 16: 5, "b" * 16: 1})
        self.assertEqual(len(queries), 1)

    def test_today_summary_cache_invalidates_writes_retention_and_day(self) -> None:
        store = TelemetryStore(":memory:", routing_row_cap=1)
        self.addCleanup(store.close)

        def log(day):
            store.log_routing(RoutingLogEntry(
                timestamp=day + "T12:00:00Z", original_model="auto",
                routed_model="codex/test", actual_model="codex/test", tier="complex",
                input_tokens=100, context_info='{"usage_seen":true}',
            ))

        log("2026-10-19")
        first = store.today_summary("2026-10-19")
        first["latencyMs"]["avg"] = -1
        with patch.object(store, "_row_context", side_effect=AssertionError("cache miss")):
            cached = store.today_summary("2026-10-19")
        self.assertNotEqual(cached["latencyMs"]["avg"], -1)
        log("2026-10-20")
        self.assertEqual(store.today_summary("2026-10-20")["loggedRequests"], 1)
        log("2026-10-20")
        self.assertEqual(store.today_summary("2026-10-20")["loggedRequests"], 2)
        store.enforce_routing_retention()
        self.assertEqual(store.today_summary("2026-10-20")["loggedRequests"], 1)
        self.assertEqual(store.today_summary("2026-10-19")["loggedRequests"], 0)


class DashboardEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        assets = root / "assets"
        assets.mkdir()
        (assets / "dashboard.css").write_text("body { color: black; }")
        (assets / "secret.txt").write_text("never serve")
        (root / "dashboard.html").write_text("dashboard")
        self.store = TelemetryStore(":memory:")
        self.app = create_app(replace(test_app.test_config(), web_dir=root), self.store)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.auth = {"Authorization": "Bearer secret"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_assets_match_html_auth_and_revalidate_without_exposing_other_files(self):
        for path in ("/dashboard", "/dashboard-assets/dashboard.css"):
            denied = await self.client.get(path)
            self.assertEqual(denied.status, 401)
            response = await self.client.get(path, headers=self.auth)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
            await response.read()
            cached = await self.client.get(path, headers={**self.auth, "If-None-Match": response.headers["ETag"]})
            self.assertEqual(cached.status, 304)
        for name in ("secret.txt", "%2e%2e%2fsecret.txt", "missing.js"):
            response = await self.client.get("/dashboard-assets/" + name, headers=self.auth)
            self.assertEqual(response.status, 404)
        outside = Path(self.directory.name) / "outside.js"
        outside.write_text("never serve")
        (Path(self.directory.name) / "assets" / "dashboard.js").symlink_to(outside)
        response = await self.client.get("/dashboard-assets/dashboard.js", headers=self.auth)
        self.assertEqual(response.status, 404)

    async def test_slow_telemetry_reads_leave_event_loop_responsive(self):
        entered = threading.Event()
        released = threading.Event()
        original = self.store.live_sessions

        def blocked(**kwargs):
            entered.set()
            if not released.wait(2):
                raise AssertionError("event loop blocked during database read")
            return original(**kwargs)

        with patch.object(self.store, "live_sessions", side_effect=blocked):
            task = asyncio.create_task(self.client.get("/api/routing/live", headers=self.auth))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                health = await asyncio.wait_for(self.client.get("/health"), .5)
                self.assertEqual(health.status, 200)
            finally:
                released.set()
            response = await task
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_telemetry_failure_stays_sanitized_and_uncacheable(self):
        with patch.object(self.store, "live_sessions", side_effect=RuntimeError("private detail")):
            response = await self.client.get("/api/routing/live", headers=self.auth)
        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("private detail", await response.text())
