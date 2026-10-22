from __future__ import annotations

import asyncio
import contextlib
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
import test_codex
from spartan_route.codex import CodexAuth, CodexSlot


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

    def test_today_appends_parse_only_new_matching_rows_and_external_changes_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "telemetry.db")
            store = TelemetryStore(path)
            self.addCleanup(store.close)
            def log(timestamp, latency=37):
                store.log_routing(RoutingLogEntry(
                    timestamp=timestamp, original_model="auto", routed_model="codex/test",
                    actual_model="codex/test", tier="complex", input_tokens=100,
                    response_time_ms=latency, context_info='{"usage_seen":true}',
                ))
            log("2026-10-20T12:00:00Z")
            store.today_summary("2026-10-20")
            log("2026-10-20T00:30:00+01:00")  # Previous UTC day.
            log("2026-10-19T23:30:00-01:00", 127)  # Current UTC day.
            with patch.object(store, "_row_context", wraps=store._row_context) as parsed:
                incremental = store.today_summary("2026-10-20")
            self.assertEqual(parsed.call_count, 1)
            self.assertEqual(incremental["loggedRequests"], 2)
            store.set_codex_capacity_multiplier("a" * 16, 5)
            with patch.object(store, "_row_context", side_effect=AssertionError("quota write invalidated cache")):
                self.assertEqual(store.today_summary("2026-10-20"), incremental)
            store._today_summary_cache = None
            self.assertEqual(store.today_summary("2026-10-20"), incremental)
            with sqlite3.connect(path) as external:
                external.execute("UPDATE routing_log SET input_tokens = 109 WHERE id = 1")
            self.assertEqual(store.today_summary("2026-10-20")["inputTokens"], 209)
            with sqlite3.connect(path) as external:
                external.execute("DELETE FROM routing_log WHERE id = 1")
            self.assertEqual(store.today_summary("2026-10-20")["loggedRequests"], 1)
            with sqlite3.connect(path) as external:
                external.execute("""INSERT INTO routing_log (
                    id, timestamp, original_model, routed_model, actual_model, tier, input_tokens, context_info
                ) VALUES (1, '2026-10-20T12:00:00Z', 'auto', 'codex/test', 'codex/test',
                          'complex', 73, '{"usage_seen":true}')""")
            self.assertEqual(store.today_summary("2026-10-20")["inputTokens"], 173)


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
        self.store = TelemetryStore(str(root / "telemetry.db"))
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

    async def test_sqlite_dashboard_scan_does_not_block_health_with_active_slot(self):
        entered = threading.Event()
        released = threading.Event()
        original = self.store._dashboard_connection
        router = self.app[STATE_KEY].codex
        router.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="synthetic"))]

        @contextlib.contextmanager
        def blocked_connection():
            with original() as connection:
                def gate():
                    entered.set()
                    return 0 if released.wait(2) else 1
                connection.set_progress_handler(gate, 1)
                try:
                    yield connection
                finally:
                    connection.set_progress_handler(None, 0)

        with patch.object(self.store, "_dashboard_connection", blocked_connection):
            task = asyncio.create_task(self.client.get("/api/routing/live", headers=self.auth))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                health = await asyncio.wait_for(self.client.get("/health"), .5)
                self.assertEqual(health.status, 200)
                self.assertEqual(len((await health.json())["codexSlots"]), 1)
            finally:
                released.set()
            response = await task
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        with self.store._dashboard_connection() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM routing_log")

    async def test_telemetry_failure_stays_sanitized_and_uncacheable(self):
        with patch.object(self.store, "live_sessions", side_effect=RuntimeError("private detail")):
            response = await self.client.get("/api/routing/live", headers=self.auth)
        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("private detail", await response.text())


class DashboardRefreshRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(":memory:")
        self.addCleanup(self.store.close)
        self.fixture = test_codex.CodexTests()
        self.auth = CodexAuth("token", account_id="synthetic")
        self.router = self.fixture.usage_router(test_codex.FakeUsageSession(), self.store,
                                               [CodexSlot(0, auth=self.auth)])
        async def load_auth(slot):
            return slot.auth
        self.router.load_auth = load_auth

    async def test_slot_status_batches_identity_bound_overrides(self):
        self.router.slots.append(CodexSlot(1, auth=self.auth))
        self.store.set_slot_routing_enabled(0, False, account_key=self.auth.account_key)
        queries = []
        self.store._conn.set_trace_callback(queries.append)
        rows = self.router.slot_status()
        self.store._conn.set_trace_callback(None)
        self.assertFalse(rows[0]["routingEnabled"])
        self.assertTrue(rows[1]["routingEnabled"])
        self.assertEqual(len(queries), 0)  # Committed policy snapshot, not a telemetry query.

    async def test_target_waits_for_old_full_then_publishes_for_next_poll(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fetch(key, auth, slots, persisted, clear):
            nonlocal calls
            calls += 1
            reading = calls
            if reading == 1:
                entered.set()
                await release.wait()
            return self.fixture.live_usage_result(key, slots, used_percent=20 if reading == 1 else 80)

        self.router._fetch_usage_account = fetch
        self.router.trigger_usage_refresh()
        await asyncio.wait_for(entered.wait(), 1)
        full = self.router._usage_refresh_task
        cancelled = asyncio.create_task(self.router.usage_snapshot(force=True, slot_indexes={0}))
        await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertFalse(full.cancelled())
        target = asyncio.create_task(self.router.usage_snapshot(force=True, slot_indexes={0}))
        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        result = await asyncio.wait_for(target, 1)
        self.assertEqual(result["accounts"][0]["weekly"]["usedPercent"], 80)
        latest = await self.router.usage_snapshot()
        self.assertEqual(latest["accounts"][0]["weekly"]["usedPercent"], 80)
        self.assertEqual(self.store.usage_snapshots()[0]["used_percent"], 80)
        self.assertEqual(calls, 2)

    async def test_full_waits_for_in_progress_target(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fetch(key, auth, slots, persisted, clear):
            nonlocal calls
            calls += 1
            reading = calls
            if reading == 1:
                entered.set()
                await release.wait()
            return self.fixture.live_usage_result(key, slots, used_percent=20 if reading == 1 else 80)

        self.router._fetch_usage_account = fetch
        target = asyncio.create_task(self.router.usage_snapshot(force=True, slot_indexes={0}))
        await asyncio.wait_for(entered.wait(), 1)
        self.router.trigger_usage_refresh()
        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        target_result = await asyncio.wait_for(target, 1)
        self.assertEqual(target_result["accounts"][0]["weekly"]["usedPercent"], 20)
        await asyncio.wait_for(asyncio.shield(self.router._usage_refresh_task), 1)
        latest = await self.router.usage_snapshot()
        self.assertEqual(latest["accounts"][0]["weekly"]["usedPercent"], 80)
        self.assertEqual(calls, 2)

    async def test_target_replaces_failed_full_progress_in_next_poll(self):
        async def failing(slot):
            raise RuntimeError("synthetic failed full refresh")
        self.router.load_auth = failing
        self.router.trigger_usage_refresh()
        await asyncio.wait_for(asyncio.shield(self.router._usage_refresh_task), 1)
        self.assertEqual((await self.router.usage_snapshot())["refreshProgress"]["status"], "failed")
        async def restored(slot):
            return slot.auth
        async def fetch(key, auth, slots, persisted, clear):
            return self.fixture.live_usage_result(key, slots, used_percent=80)
        self.router.load_auth = restored
        self.router._fetch_usage_account = fetch
        await self.router.usage_snapshot(force=True, slot_indexes={0})
        latest = await self.router.usage_snapshot()
        self.assertNotIn("refreshError", latest)
        self.assertEqual(latest["accounts"][0]["weekly"]["usedPercent"], 80)

    async def test_reset_retires_targeted_persistence_before_new_full_completes(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fetch(key, auth, slots, persisted, clear):
            nonlocal calls
            calls += 1
            reading = calls
            if reading == 1:
                entered.set()
                await release.wait()
            result = self.fixture.live_usage_result(key, slots, used_percent=20 if reading == 1 else 80)
            result["resetRecord"] = {
                "account_key": key, "slot_index": 0, "available_count": reading,
                "details_available": False, "credits": [],
            }
            return result

        self.router._fetch_usage_account = fetch
        old_target = asyncio.create_task(self.router.usage_snapshot(force=True, slot_indexes={0}))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            with patch.object(self.router, "_init_slots", lambda: None):
                self.router.reset_state()
            self.router.slots = [CodexSlot(0, auth=self.auth)]
            self.router.trigger_usage_refresh()
            await asyncio.wait_for(asyncio.shield(self.router._usage_refresh_task), 1)
            self.assertEqual(self.store.usage_snapshots()[0]["used_percent"], 80)
        finally:
            release.set()
            await asyncio.wait_for(old_target, 1)
        self.assertEqual(self.store.usage_snapshots()[0]["used_percent"], 80)
        self.assertEqual(self.store.reset_credit_snapshots()[self.auth.account_key]["availableCount"], 2)
        latest = await self.router.usage_snapshot()
        self.assertEqual(latest["accounts"][0]["weekly"]["usedPercent"], 80)

    async def test_retired_account_fetch_does_not_clear_new_generation_cache(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original_get = self.router.session.get
        old_slot = self.router.slots[0]
        old_slot.rate_limited_until = 9999999999

        def delayed_get(url, **kwargs):
            response = original_get(url, **kwargs)
            original_json = response.json
            async def delayed_json():
                if "rate-limit-reset-credits" not in url:
                    entered.set()
                    await release.wait()
                return await original_json()
            response.json = delayed_json
            return response

        self.router.session.get = delayed_get
        task = asyncio.create_task(self.router._fetch_usage_account(
            self.auth.account_key, self.auth, [old_slot], None, True,
        ))
        await asyncio.wait_for(entered.wait(), 1)
        with patch.object(self.router, "_init_slots", lambda: None):
            self.router.reset_state()
        newer_cache = {"new-generation": True}
        self.router._usage_cache = newer_cache
        release.set()
        await asyncio.wait_for(task, 1)
        self.assertIs(self.router._usage_cache, newer_cache)
        self.assertEqual(old_slot.rate_limited_until, 9999999999)
