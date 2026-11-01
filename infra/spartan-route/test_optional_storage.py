"""Synthetic contingency boundaries; no production state or upstream access."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from spartan_route.app import STATE_KEY, create_app
from spartan_route.codex import CodexAuth, CodexCallResult, CodexRouter, CodexSlot
from spartan_route.config import load_config
from spartan_route.pi_usage import CorrelationError, PiUsage
from spartan_route.telemetry import OptionalTelemetryUnavailable, TelemetryQueue, TelemetryStore
from test_app import test_config
from test_codex import FakeJsonContextResponse
from test_telemetry import routing_entry

REQUIRED = {"provider_config", "codex_slot_overrides", "codex_account_quota_limits", "routing_model_overrides",
            "model_variant_config", "reasoning_chain_bindings", "cache_probe_reservations",
            "cache_experiment_reservations"}


class GuardedConnection(sqlite3.Connection):
    """Reject data access outside the named required controls on BOTH connections."""
    violations: list = []
    opened: list = []
    deny_required = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bootstrap = False
        self.opened.append(self)
        self.set_authorizer(self.authorize)

    def authorize(self, action, table, _column, _database, _source):
        if action not in {sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
            return sqlite3.SQLITE_OK
        if table in {"sqlite_master", "sqlite_sequence"} or self.bootstrap:
            return sqlite3.SQLITE_OK
        if table in REQUIRED and not self.deny_required:
            return sqlite3.SQLITE_OK
        self.violations.append((action, table))
        return sqlite3.SQLITE_DENY

    def execute(self, sql, parameters=()):
        # Two separate schema owners bootstrap compact control/Pi tables.
        ddl = sql.strip().startswith(("CREATE TABLE IF NOT EXISTS pi_usage_sessions", "CREATE TABLE IF NOT EXISTS codex_account_quota_limits"))
        self.bootstrap = ddl
        try:
            return super().execute(sql, parameters)
        finally:
            self.bootstrap = False

    def executescript(self, script):
        # Only the existing schema bootstrap receives an allowance, not startup.
        import re
        statements = re.sub(r"--[^\n]*", "", script).split(";")
        assert all(not statement.strip() or statement.strip().split()[0] in {"CREATE", "PRAGMA"} for statement in statements)
        self.bootstrap = True
        try:
            return super().executescript(script)
        finally:
            self.bootstrap = False


class SyntheticUsage:
    def __init__(self):
        self.calls = []
        self.failed = set()
        self.invalid_five_hour = False

    def get(self, url, **kwargs):
        account = kwargs["headers"].get("chatgpt-account-id")
        self.calls.append((account, url))
        if account in self.failed:
            return FakeJsonContextResponse(503, {})
        if "reset-credits" in url:
            return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
        return FakeJsonContextResponse(200, {
            "primary_window": {"used_percent": None if self.invalid_five_hour else 12, "reset_at": time.time() + 18000},
            "secondary_window": {"used_percent": 34, "reset_at": time.time() + 604800},
        })


class ResetFailureUsage(SyntheticUsage):
    fail_reset = False

    def get(self, url, **kwargs):
        response = super().get(url, **kwargs)
        if self.fail_reset and "reset-credits" in url:
            return FakeJsonContextResponse(503, {})
        return response


class UsageRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        self.upstream = ResetFailureUsage()
        self.router = CodexRouter(self.upstream, self.store)
        self.router.slots = [CodexSlot(0, auth=CodexAuth("synthetic", account_id="one"))]
        self.router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)

    async def asyncTearDown(self):
        await self.router.cleanup()
        self.store.close()

    async def test_reset_failure_then_success_starts_fresh_cohort_with_last_good_windows(self):
        self.upstream.fail_reset = True
        failed = await self.router.usage_snapshot(force=True)
        self.assertEqual(len(failed["resetCreditErrors"]), 1)
        self.assertEqual((await self.router.usage_snapshot())["resetCreditErrors"], failed["resetCreditErrors"])
        previous = failed["accounts"][0]["weekly"]
        self.upstream.fail_reset = False
        pending = self.router.trigger_usage_refresh()
        self.assertEqual(pending["resetCreditErrors"], [])
        self.assertEqual(pending["accounts"][0]["weekly"]["updatedAt"], previous["updatedAt"])
        self.assertEqual(pending["accounts"][0]["weekly"]["source"], previous["source"])
        recovered = await asyncio.shield(self.router._usage_refresh_task)
        self.assertEqual(recovered["resetCreditErrors"], [])
        self.assertEqual(recovered["accounts"][0]["resetCredits"]["availableCount"], 1)
        self.assertFalse(recovered.get("refreshError"))
        self.assertEqual((await self.router.usage_snapshot())["resetCreditErrors"], [])

    async def test_controller_failure_then_success_retires_only_previous_error(self):
        first = await self.router.usage_snapshot(force=True)
        with patch.object(self.router, "load_auth", new=AsyncMock(side_effect=RuntimeError("synthetic controller failure"))):
            failed = await self.router.usage_snapshot(force=True)
        self.assertEqual(failed["refreshProgress"]["status"], "failed")
        self.assertEqual(failed["refreshError"]["code"], "usage_refresh_failed")
        self.assertEqual((await self.router.usage_snapshot())["refreshError"], failed["refreshError"])
        self.assertEqual(failed["accounts"][0]["weekly"]["updatedAt"], first["accounts"][0]["weekly"]["updatedAt"])
        recovered = await self.router.usage_snapshot(force=True)
        self.assertEqual(recovered["refreshProgress"]["status"], "complete")
        self.assertNotIn("refreshError", recovered)
        self.assertNotIn("refreshError", await self.router.usage_snapshot())

    async def test_quota_triggered_reset_failures_are_bounded_to_current_cohort(self):
        slot = self.router.slots[0]
        self.store.quota_limits.set(slot.account_key(), {"weekly": 80, "fiveHour": 80})
        self.upstream.fail_reset = True
        for _cohort in range(3):
            # Missing observations require the existing quota-admission refresh path.
            self.router.quota.observations.clear()
            self.router.quota.last_attempt = -float("inf")
            self.assertTrue(await self.router.quota.admit(slot, slot.auth))
            current = await self.router.usage_snapshot()
            self.assertEqual(len(current["resetCreditErrors"]), 1)
            self.assertEqual(current["resetCreditErrors"][0]["slotIndex"], 0)
            self.assertEqual(current["refreshProgress"]["status"], "complete")
            self.assertEqual(current["accounts"][0]["weekly"]["usedPercent"], 34)


class OptionalStorageTests(unittest.IsolatedAsyncioTestCase):
    def test_flag_defaults_on_and_off_precedes_retention(self):
        self.assertTrue(load_config({"CLAWROUTE_TOKEN": "synthetic"}).optional_telemetry_enabled)
        self.assertFalse(load_config({"SPARTAN_ROUTE_OPTIONAL_TELEMETRY_ENABLED": "false"}).optional_telemetry_enabled)
        with patch("spartan_route.telemetry.RetentionMaintenance.initialize") as maintenance:
            store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
            try:
                maintenance.assert_not_called()
                self.assertFalse(store.health_status()["healthy"])
            finally:
                store.close()

    def test_guards_precede_database_mutex_and_never_invent_origins(self):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        try:
            with patch.object(store, "_lock") as lock, patch.object(store.pi_usage, "lock") as pi_lock:
                lock.__enter__.side_effect = AssertionError("optional DB mutex")
                pi_lock.__enter__.side_effect = AssertionError("optional Pi mutex")
                store.upsert_usage_snapshots([])
                store.upsert_reset_credits([])
                store.save_codex_model_contexts({"codex/test": 123})
                self.assertEqual(store.codex_model_contexts(), {})
                self.assertEqual(store.usage_snapshots(), [])
                self.assertEqual(store.reset_credit_snapshots(), {})
                store.enforce_routing_retention()
                for read in (store.daily_cache_rows, store.native_cache_daily_rows, store.today_summary,
                             store.routing_filter_options, store.live_sessions,
                             lambda: store.turn_requests("turn"), lambda: store.codex_usage_estimate(set()),
                             lambda: store.codex_capacity_multipliers(set())):
                    with self.assertRaises(OptionalTelemetryUnavailable):
                        read()
                with self.assertRaises(OptionalTelemetryUnavailable):
                    store.pi_usage.register("synthetic", {})
                service = PiUsage(store.pi_usage, store)
                with self.assertRaises(CorrelationError):
                    service.correlate({"X-Spartan-Pi-Session": "malformed"}, False)
                self.assertIsNone(service.correlate({"X-Spartan-Pi-Client": "1",
                    "X-Spartan-Pi-Session": "a7becb62-8311-466d-89ca-e8b215d0a700",
                    "X-Spartan-Pi-Origin": "b7becb62-8311-466d-89ca-e8b215d0a700"}, False))
                queue = TelemetryQueue(store)
                self.assertFalse(queue.log(routing_entry("2026-01-01T00:00:00Z", "omitted")))
                self.assertEqual(queue.queue.qsize(), 0)
                self.assertEqual(queue.drops, 0)
        finally:
            store.close()

    async def test_both_connections_reject_optional_sql_through_api_lifecycle(self):
        GuardedConnection.violations, GuardedConnection.opened = [], []
        original = sqlite3.connect
        def connect(*args, **kwargs):
            return original(*args, **kwargs, factory=GuardedConnection)
        with tempfile.TemporaryDirectory() as directory, patch("spartan_route.telemetry.sqlite3.connect", connect):
            config = replace(test_config(), db_path=str(Path(directory) / "synthetic.db"), optional_telemetry_enabled=False)
            with patch("spartan_route.app.CodexModelDiscovery.start"), patch("spartan_route.app.CodexModelDiscovery.stop", new_callable=AsyncMock):
                client = TestClient(TestServer(create_app(config)))
                await client.start_server()
                try:
                    state = client.app[STATE_KEY]
                    state.codex.control_session = SyntheticUsage()
                    state.codex.slots = [CodexSlot(0, auth=CodexAuth("synthetic", account_id="one"))]
                    state.codex.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
                    await self.exercise_endpoints(client, state)
                    # Prove the separate dashboard connection is guarded, not just unused.
                    with state.telemetry._dashboard_connection() as connection:
                        with self.assertRaises(sqlite3.DatabaseError):
                            connection.execute("SELECT * FROM routing_log").fetchall()
                    self.assertEqual(GuardedConnection.violations, [(sqlite3.SQLITE_READ, "routing_log")])
                    GuardedConnection.violations.clear()
                finally:
                    await asyncio.wait_for(client.close(), 2)
        self.assertEqual(GuardedConnection.violations, [])
        self.assertEqual(len(GuardedConnection.opened), 2)

    async def exercise_endpoints(self, client, state):
        auth = {"Authorization": "Bearer secret"}
        self.assertEqual((await client.get("/api/codex/usage")).status, 401)
        for path in ("/api/codex/usage", "/api/codex/usage?force=true"):
            response = await client.get(path, headers=auth)
            payload = await response.json()
            self.assertEqual((response.status, payload["accounts"]), (200, []))
        self.assertEqual(state.codex.control_session.calls, [])
        for path in ("/stats", "/api/routing/live", "/api/routing/turns/t", "/api/codex/cache/day", "/api/pi/status"):
            self.assertEqual((await client.get(path, headers=auth)).status, 503, path)
        for path in ("/api/pi/usage/refresh", "/api/pi/sessions"):
            body = {"sessionId": "a7becb62-8311-466d-89ca-e8b215d0a700", "baseline": {"totalTokens": 0}}
            self.assertEqual((await client.post(path, json=body, headers=auth)).status, 503)
        response = await client.post("/api/codex/usage/refresh", headers=auth)
        self.assertEqual(response.status, 202)
        await asyncio.shield(state.codex._usage_refresh_task)
        response = await client.get("/api/codex/usage", headers=auth)
        payload = await response.json()
        self.assertEqual(payload["accounts"][0]["weekly"]["usedPercent"], 34)
        self.assertIsNone(payload["accounts"][0]["capacityMultiplier"])
        self.assertIsNone(payload["cacheDaily"])
        state.codex.execute_chat = AsyncMock(return_value=CodexCallResult(status=200, body={"choices": []}))
        headers = {**auth, "X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": "a7becb62-8311-466d-89ca-e8b215d0a700",
                   "X-Spartan-Pi-Origin": "b7becb62-8311-466d-89ca-e8b215d0a700"}
        response = await client.post("/v1/chat/completions", headers=headers, json={"model": "codex/gpt-5.5", "messages": [{"role": "user", "content": "synthetic"}]})
        self.assertEqual(response.status, 200)
        self.assertIsNone(state.queue.task)
        health = await (await client.get("/health")).json()
        self.assertTrue(health["degraded"])
        self.assertGreater(health["telemetry"]["optionalOmissions"], 0)
        self.assertEqual(health["telemetry"]["database"]["readFailures"], 0)

    def test_required_policy_and_durability_failures_are_not_silenced(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(test_config(), db_path=str(Path(directory) / "control.db"), optional_telemetry_enabled=False)
            with patch("spartan_route.telemetry.load_policy", side_effect=RuntimeError("required unavailable")):
                with self.assertRaisesRegex(RuntimeError, "required unavailable"):
                    create_app(config)
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        try:
            store._conn.set_authorizer(lambda *_args: sqlite3.SQLITE_DENY)
            with self.assertRaises(sqlite3.DatabaseError):
                store.bind_reasoning_chain("chain", "realm")
            with self.assertRaises(sqlite3.DatabaseError):
                store.reserve_cache_probe("run", 1, "account", time.time(), 1)
            with self.assertRaises(sqlite3.DatabaseError):
                store.quota_limits.set("account", {"weekly": 50, "fiveHour": None})
            self.assertIsNone(store.quota_limits.limits("account")["weekly"])
        finally:
            store.close()

    async def test_retired_targeted_read_cannot_publish_display_or_admission(self):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        entered, release = asyncio.Event(), asyncio.Event()
        class BlockingUsage(SyntheticUsage):
            def get(self, url, **kwargs):
                response = super().get(url, **kwargs)
                if "reset-credits" not in url:
                    original = response.json
                    async def delayed():
                        entered.set()
                        await release.wait()
                        return await original()
                    response.json = delayed
                return response
        router = CodexRouter(BlockingUsage(), store)
        router.slots = [CodexSlot(0, auth=CodexAuth("synthetic", account_id="one"))]
        router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        pending = asyncio.create_task(router.usage_snapshot(force=True, slot_indexes={0}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            router.reset_state()
            release.set()
            await asyncio.wait_for(pending, 1)
            self.assertEqual(router.quota.observations, {})
            self.assertIsNone(router._usage_progress_snapshot)
            self.assertEqual((await router.usage_snapshot())["accounts"], [])
        finally:
            release.set()
            await router.cleanup()
            store.close()

    async def test_partial_live_display_never_reenters_admission_and_targeting_is_scoped(self):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        upstream = SyntheticUsage()
        router = CodexRouter(upstream, store)
        router.slots = [CodexSlot(index, auth=CodexAuth("synthetic", account_id=key)) for index, key in enumerate(("one", "two"))]
        router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        key = router.slots[0].account_key()
        store.quota_limits.set(key, {"weekly": 80, "fiveHour": 80})
        self.assertTrue(router.quota.status(key)["paused"])
        try:
            first = await router.usage_snapshot(force=True)
            observed = first["accounts"][0]["fiveHour"]["updatedAt"]
            self.assertFalse(router.quota.status(key)["paused"])
            upstream.failed = {"two"}
            upstream.invalid_five_hour = True
            second = await router.usage_snapshot(force=True)
            row = second["accounts"][0]
            self.assertEqual(row["fiveHour"]["usedPercent"], 12)
            self.assertEqual(row["fiveHour"]["updatedAt"], observed)
            self.assertTrue(row["fiveHour"]["stale"])
            self.assertFalse(row["weekly"]["stale"])
            self.assertTrue(second["accounts"][1]["weekly"]["stale"])
            self.assertIsNone(router.quota.observations[key]["fiveHour"])
            self.assertTrue(router.quota.status(key)["paused"])
            upstream.calls.clear()
            await router.usage_snapshot(force=True, slot_indexes={0})
            self.assertEqual({account for account, _url in upstream.calls}, {"one"})
            with patch("spartan_route.usage_display.time.time", return_value=time.time() + 120):
                passive = await router.usage_snapshot()
            self.assertTrue(passive["accounts"][0]["weekly"]["stale"])
            self.assertTrue(router.quota.status(key)["paused"])
        finally:
            await router.cleanup()
            store.close()
