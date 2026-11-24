from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from types import SimpleNamespace
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from spartan_route.app import STATE_KEY, classify_codex_slot_health, create_app
from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.quota_limits import QuotaAdmission, QuotaLimitStore, daily_slice, dispatch_admission, evaluate, validate_limits
from spartan_route.pi_usage import PiUsage
from spartan_route.slot_policy import SlotPolicyUnavailable
from spartan_route.telemetry import TelemetryStore
import test_app
import test_codex


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def window(used=20, age=0, reset=3600):
    return {"usedPercent": used, "updatedAt": iso(time.time() - age), "resetAt": iso(time.time() + reset)}


def provider(used):
    return {"secondary_window": {"used_percent": used, "reset_at": iso(time.time() + 3600)}}


class QuotaPolicyTests(unittest.TestCase):
    def test_configured_windows_require_valid_fresh_observations_and_strict_boundary(self):
        limits = {"weekly": 70, "fiveHour": None}
        cases = [({"weekly": window(69.9)}, False), ({"weekly": window(70)}, True),
                 ({"weekly": window(71)}, True), ({"weekly": window(float("nan"))}, True),
                 ({"weekly": window(True)}, True), ({"weekly": window(-1)}, True),
                 ({"weekly": window(20, age=61)}, True), ({"weekly": window(20, reset=-1)}, True),
                 ({}, True)]
        for observation, paused in cases:
            with self.subTest(observation=observation):
                self.assertEqual(evaluate(limits, observation, time.time())["paused"], paused)
        both = {"weekly": 70, "fiveHour": 50}
        self.assertTrue(evaluate(both, {"weekly": window(20)}, time.time())["paused"])
        self.assertFalse(evaluate(both, {"weekly": window(20), "fiveHour": window(49)}, time.time())["paused"])
        self.assertFalse(evaluate(dict.fromkeys(limits), {}, time.time())["paused"])

    def test_durable_atomic_limits_restart_failed_write_and_invalid_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "quota.db")
            store = TelemetryStore(path)
            self.assertEqual(store.quota_limits.limits("account"), {"weekly": None, "fiveHour": None, "daily": None})
            store.quota_limits.set("account", {"weekly": 70, "fiveHour": 50})
            store._conn.execute("""CREATE TRIGGER reject_limits BEFORE UPDATE ON codex_account_quota_limits
                BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
            store._conn.commit()
            with self.assertRaisesRegex(Exception, "fixture failure"):
                store.quota_limits.set("account", {"weekly": None, "fiveHour": None})
            self.assertEqual(store.quota_limits.limits("account"), {"weekly": 70, "fiveHour": 50, "daily": None})
            store.close()
            restored = TelemetryStore(path)
            self.assertEqual(restored.quota_limits.limits("account"), {"weekly": 70, "fiveHour": 50, "daily": None})
            restored._conn.execute("DROP TRIGGER reject_limits")
            restored._conn.execute("PRAGMA ignore_check_constraints=ON")
            restored._conn.execute("UPDATE codex_account_quota_limits SET weekly='invalid'")
            restored._conn.commit()
            restored.close()
            with self.assertRaises(SlotPolicyUnavailable):
                TelemetryStore(path)


class DailyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "daily.db")
        self.store = TelemetryStore(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.store.close())
        self.now = 1_800_000_000.0
        self.reset = self.now + 6 * 86400
        self.router = SimpleNamespace(telemetry=self.store, slots=[],
            _notify_availability_changed=lambda: None)
        self.quota = QuotaAdmission(self.router)
        self.clock = patch("spartan_route.quota_limits.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def observe(self, used, **extra):
        weekly = {"usedPercent": used, "updatedAt": iso(self.now), "resetAt": iso(self.reset), **extra}
        self.quota.observe("a", {"weekly": weekly})

    def budget(self):
        payload = {"accounts": [{"accountKey": "a"}]}
        self.quota.attach(payload)
        return payload["accounts"][0]["dailyBudget"]

    def test_validation_optional_daily_and_strict_numbers(self):
        base = {"weekly": None, "fiveHour": 50}
        self.assertEqual(validate_limits(base), base)
        for cap in (None, 0.1, 100):
            self.assertEqual(validate_limits({**base, "daily": cap})["daily"], cap)
        for field in ("weekly", "fiveHour", "daily"):
            for cap in (True, False, 0, -1, 100.1, float("nan"), float("inf"), "15"):
                with self.subTest(field=field, cap=cap), self.assertRaises(ValueError):
                    validate_limits({**base, field: cap})
        for invalid in ({"weekly": 40}, {**base, "extra": None}):
            with self.assertRaises(ValueError):
                validate_limits(invalid)

    def test_legacy_migration_preserves_limits_and_patch_daily_semantics(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE codex_account_quota_limits (account_key TEXT PRIMARY KEY, weekly REAL, five_hour REAL)")
        connection.execute("INSERT INTO codex_account_quota_limits VALUES ('a', 70, 50)")
        for _ in range(2):
            store = QuotaLimitStore(connection, threading.Lock())
            self.assertEqual(store.limits("a"), {"weekly": 70, "fiveHour": 50, "daily": None})
        store.set("a", {"weekly": 70, "fiveHour": 50, "daily": 15})
        self.assertEqual(store.set("a", {"weekly": 60, "fiveHour": None})["daily"], 15)
        self.assertIsNone(store.set("a", {"weekly": 60, "fiveHour": None, "daily": None})["daily"])
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE codex_account_quota_limits SET daily=101")

    def test_baseline_threshold_restart_and_no_redundant_write(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.observe(40)
        self.assertEqual(self.budget()["consumed"], 0)
        changes = self.store._conn.total_changes
        self.observe(40)
        self.assertEqual(self.store._conn.total_changes, changes)
        self.observe(54.9)
        self.assertFalse(self.quota.status("a")["paused"])
        self.observe(55)
        self.assertEqual(self.budget()["status"], "reached")
        self.assertIn("daily: daily budget reached", self.quota.status("a")["reasons"])
        self.store.close()
        self.store = TelemetryStore(self.path)
        self.router.telemetry = self.store
        self.quota = QuotaAdmission(self.router)
        self.observe(56)
        self.assertEqual(self.budget()["consumed"], 16)

    def test_reset_jitter_keeps_baseline_anchor_and_admission(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.observe(40)
        start = self.budget()["sliceStart"]
        anchor = self.reset
        self.now += 10
        for jitter in (1, -1, 300, 900):
            with self.subTest(jitter=jitter):
                self.reset = anchor + jitter
                weekly = {"usedPercent": 50, "updatedAt": iso(self.now), "resetAt": iso(self.reset)}
                # A fresh read must match even before its accounting write/publication.
                budget = self.store.quota_limits.daily_budget("a", weekly, self.now)
                self.assertEqual((budget["consumed"], budget["status"], budget["sliceStart"]), (10, "ok", start))
                self.observe(50)
                self.assertEqual(self.budget(), budget)
                self.assertFalse(self.quota.status("a")["paused"])
                self.assertFalse(self.quota.needs_refresh("a"))
                row = self.store._conn.execute("SELECT window_reset FROM codex_account_daily_budget WHERE account_key='a'").fetchone()
                self.assertEqual(row["window_reset"], anchor)

    def test_reset_jitter_keeps_persisted_anchor_after_restart(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.observe(40)
        start = self.budget()["sliceStart"]
        anchor = self.reset
        self.store.close()
        self.store = TelemetryStore(self.path)
        self.router.telemetry = self.store
        self.quota = QuotaAdmission(self.router)
        self.now += 10
        for jitter in (1, -1, 300):
            with self.subTest(jitter=jitter):
                self.reset = anchor + jitter
                self.observe(50)
                budget = self.budget()
                self.assertEqual((budget["consumed"], budget["status"], budget["sliceStart"]), (10, "ok", start))
                self.assertFalse(self.quota.status("a")["paused"])
                self.assertFalse(self.quota.needs_refresh("a"))
                row = self.store._conn.execute("SELECT window_reset FROM codex_account_daily_budget WHERE account_key='a'").fetchone()
                self.assertEqual(row["window_reset"], anchor)

    def test_new_weekly_window_starts_fresh_slice_without_carry(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        for offset in (901, -901, 7200, 7 * 86400):
            with self.subTest(offset=offset):
                self.observe(40)
                self.observe(50)
                self.assertEqual(self.budget()["consumed"], 10)
                self.reset += offset
                # Keep the computed slice start identical: window identity must also match.
                duration = 604800 + offset
                weekly = {"usedPercent": 2, "updatedAt": iso(self.now),
                          "resetAt": iso(self.reset), "windowSeconds": duration}
                self.assertEqual(self.store.quota_limits.daily_budget("a", weekly, self.now)["status"], "unknown")
                self.observe(2, windowSeconds=duration)
                self.assertEqual((self.budget()["consumed"], self.budget()["status"]), (0, "ok"))
                row = self.store._conn.execute("SELECT * FROM codex_account_daily_budget WHERE account_key='a'").fetchone()
                self.assertEqual((row["window_reset"], row["baseline"], row["carried"]), (self.reset, 2, 0))
                self.observe(3, windowSeconds=duration)
                self.assertEqual(self.budget()["consumed"], 1)
                # Give the next case a separate account window.
                self.reset += 7 * 86400
                self.now += 7 * 86400

    def test_slice_boundaries_and_partial_slice_use_stored_anchor(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        # A non-integral number of days yields a final partial slice.
        self.reset = self.now + 2.5 * 86400
        duration = 3.5 * 86400
        self.observe(40, windowSeconds=duration)
        self.assertEqual(self.budget()["sliceStart"], "2027-01-15T08:00:00.000Z")
        self.now += 86400 - 1
        self.observe(45, windowSeconds=duration)
        self.assertEqual(self.budget()["consumed"], 5)
        self.now += 1
        anchor = self.reset
        self.reset += 300
        self.observe(46, windowSeconds=duration)
        self.assertEqual(self.budget()["consumed"], 0)
        self.assertEqual(self.budget()["sliceStart"], "2027-01-16T08:00:00.000Z")
        self.now += 86400
        self.observe(50, windowSeconds=duration)
        self.assertEqual(self.budget()["sliceEnd"], iso(anchor).replace("+00:00", ".000Z"))
        self.observe(55, windowSeconds=duration)
        self.assertEqual(self.budget()["consumed"], 5)

    def test_slice_expiry_requires_new_baseline_and_invalid_readings_cannot_reset_it(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.now += 86400 - 1
        self.observe(40)
        self.now += 1
        self.assertTrue(self.quota.needs_refresh("a"))
        self.assertTrue(self.quota.status("a")["paused"])
        self.observe(50)
        self.assertEqual(self.budget()["consumed"], 0)
        changes = self.store._conn.total_changes
        for reading in ({}, {"weekly": {"usedPercent": True}},
                        {"weekly": {"usedPercent": 70, "updatedAt": iso(self.now - 61), "resetAt": iso(self.reset)}}):
            self.quota.observe("a", reading)
            self.assertTrue(self.quota.status("a")["paused"])
        self.assertEqual(self.store._conn.total_changes, changes)
        self.observe(55)
        self.assertEqual(self.budget()["consumed"], 5)

    def test_slice_math_default_duration_start_and_final_instant(self):
        weekly = {"resetAt": iso(self.reset)}
        start = self.reset - 604800
        self.assertEqual(daily_slice(weekly, start), (start, start + 86400))
        self.assertEqual(daily_slice(weekly, self.reset - 0.01), (self.reset - 86400, self.reset))
        for now in (start - 1, self.reset):
            with self.assertRaises(ValueError):
                daily_slice(weekly, now)
        for duration in (True, 0, -1, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                daily_slice({**weekly, "windowSeconds": duration}, self.now)

    def test_daily_missing_stale_invalid_and_disabled(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.assertTrue(self.quota.status("a")["paused"])
        self.assertTrue(self.quota.needs_refresh("a"))
        self.observe(40)
        self.now += 60
        self.assertEqual(self.budget()["status"], "unknown")
        self.assertIn("daily: usage stale", self.quota.status("a")["reasons"])
        self.observe(41, windowSeconds=True)
        self.assertTrue(self.quota.status("a")["paused"])
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": None})
        self.assertFalse(self.quota.status("a")["paused"])
        self.assertFalse(self.quota.needs_refresh("a"))
        self.assertEqual(self.budget()["status"], "off")

    def test_corrupted_daily_state_fails_closed_on_restart(self):
        self.observe(40)
        self.store._conn.execute("UPDATE codex_account_daily_budget SET baseline='invalid'")
        self.store._conn.commit()
        with self.assertRaises(SlotPolicyUnavailable):
            TelemetryStore(self.path)

    def test_failed_baseline_write_fails_closed_without_publishing(self):
        self.store.quota_limits.set("a", {"weekly": None, "fiveHour": None, "daily": 15})
        self.observe(40)
        self.store._conn.execute("""CREATE TRIGGER reject_daily BEFORE UPDATE ON codex_account_daily_budget
            BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with self.assertRaises(SlotPolicyUnavailable):
            self.observe(55)
        self.assertTrue(self.quota.status("a")["paused"])


class QuotaRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_only_live_refresh_persists_off_loop_and_blocks_dispatch(self):
        store = TelemetryStore(":memory:")
        session = test_codex.PayloadUsageSession(provider(40))
        router = CodexRouter(session, store)
        auth = CodexAuth("fixture", account_id="account")
        slot = CodexSlot(0, auth=auth)
        router.slots = [slot]
        router.load_auth = AsyncMock(return_value=auth)
        store.quota_limits.set(auth.account_key, {"weekly": None, "fiveHour": None, "daily": 15})
        loop_thread = threading.get_ident()
        write_threads = []
        original = store.quota_limits.observe_daily

        def persist(*args):
            write_threads.append(threading.get_ident())
            return original(*args)

        try:
            with patch.object(store.quota_limits, "observe_daily", side_effect=persist):
                self.assertIsNotNone(await router.choose_slot(None, set(), set()))
                session.payload["secondary_window"]["used_percent"] = 55
                await router._fetch_usage_account(auth.account_key, auth, [slot], None, True)
            self.assertTrue(write_threads)
            self.assertNotIn(loop_thread, write_threads)
            self.assertEqual(router.quota.daily_budget(auth.account_key)["consumed"], 15)
            self.assertTrue(router.quota.status(auth.account_key)["paused"])
            blocked = await dispatch_admission(router, auth, slot)
            self.assertEqual(blocked.status, 503)
        finally:
            await router.cleanup()
            store.close()

    async def test_dashboard_closed_shared_account_pause_recovery_and_manual_disable(self):
        store = TelemetryStore(":memory:")
        session = test_codex.PayloadUsageSession(provider(70))
        router = CodexRouter(session, store)
        auth = CodexAuth("fixture", account_id="account")
        router.slots = [CodexSlot(0, auth=auth), CodexSlot(1, auth=auth)]
        router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        key = auth.account_key
        store.quota_limits.set(key, {"weekly": 70, "fiveHour": None})
        router._remember_lease("affinity", router.slots[0])
        try:
            choices = await asyncio.gather(*(router.choose_slot("affinity", set(), set()) for _ in range(3)))
            self.assertEqual(choices, [None, None, None])
            self.assertEqual(len(session.urls), 2)  # One usage/reset fetch for both slots and all waiters.
            self.assertEqual(router.leases, {})
            self.assertEqual((await router._unavailable_error())["code"], "codex_quota_paused")
            self.assertEqual(PiUsage(store.pi_usage, store).accounts(router, time.time())[0][0]["state"], "quota_paused")
            self.assertTrue(router.slot_routing_status(router.slots[0])["routingEnabled"])
            await router.set_slot_routing_enabled(0, False)
            session.payload = provider(20)
            router.quota.observations[key]["weekly"]["updatedAt"] = iso(time.time() - 61)
            router.quota.last_attempt = -float("inf")
            chosen = await router.choose_slot("affinity", set(), set())
            self.assertEqual(chosen[0].index, 1)
            self.assertFalse(router.slot_routing_status(router.slots[0])["routingEnabled"])
            self.assertFalse(router.quota.status(key)["paused"])
            pi_account = PiUsage(store.pi_usage, store).accounts(router, time.time())[0][0]
            self.assertEqual((pi_account["state"], pi_account["slotIndexes"], pi_account["capacityMultiplier"]), ("available", [1], 1))
            # A cap changed after selection is enforced again at actual dispatch.
            store.quota_limits.set(key, {"weekly": 10, "fiveHour": None})
            blocked = await router.open_inference({}, {}, auth, slot=chosen[0])
            self.assertEqual(blocked.status, 503)
            self.assertIn('codex_quota_paused', await blocked.text())
            image = await router._single_image_call({"prompt": "fixture"}, chosen[0], auth)
            self.assertEqual(image.status, 503)
            self.assertEqual(image.error, "codex_quota_paused")
            fresh_identity = CodexAuth("other", account_id="other")
            self.assertEqual(router.quota.store.limits(fresh_identity.account_key), {"weekly": None, "fiveHour": None, "daily": None})
        finally:
            await router.cleanup()
            store.close()

    async def test_provider_duration_must_be_exact_integer_before_admission_or_recovery(self):
        store = TelemetryStore(":memory:")
        session = test_codex.PayloadUsageSession(provider(20))
        router = CodexRouter(session, store)
        auth = CodexAuth("fixture", account_id="account")
        slot = CodexSlot(0, auth=auth)
        router.slots = [slot]
        router.load_auth = AsyncMock(return_value=auth)
        key = auth.account_key
        store.quota_limits.set(key, {"weekly": 70, "fiveHour": None})
        cases = [(604800, None, False), (None, 75, False),
                 (604800.9, None, True), ("604800", None, True),
                 (604800.9, 75, True), ("604800", 75, True)]
        try:
            for duration, previous_usage, paused in cases:
                with self.subTest(duration=duration, previous_usage=previous_usage):
                    router.quota.observe(key, {} if previous_usage is None else {"weekly": window(previous_usage)})
                    router.quota.last_attempt = -float("inf")
                    session.payload = provider(20)
                    if duration is not None:
                        session.payload["secondary_window"]["limit_window_seconds"] = duration
                    await router._fetch_usage_account(key, auth, [slot], None, True)
                    blocked = await dispatch_admission(router, auth, slot)
                    self.assertEqual(blocked is not None, paused)
                    self.assertEqual(router.quota.status(key)["paused"], paused)
        finally:
            await router.cleanup()
            store.close()

    async def test_missing_window_failure_throttle_and_reset_never_self_recovers(self):
        store = TelemetryStore(":memory:")
        session = test_codex.PayloadUsageSession(provider(10))
        router = CodexRouter(session, store)
        auth = CodexAuth("fixture", account_id="account")
        slot = CodexSlot(0, auth=auth)
        router.slots = [slot]
        router.load_auth = AsyncMock(return_value=auth)
        store.quota_limits.set(auth.account_key, {"weekly": None, "fiveHour": 50})
        try:
            self.assertIsNone(await router.choose_slot(None, set(), set()))
            self.assertIn("usage missing", router.quota.status(auth.account_key)["reasons"][0])
            self.assertIsNone(await router.choose_slot(None, set(), set()))
            self.assertEqual(len(session.urls), 2)
            router.quota.observe(auth.account_key, {"fiveHour": window(10, reset=-1)})
            self.assertTrue(router.quota.status(auth.account_key)["paused"])
            # Removing a blocking cap is immediately effective without another provider read.
            store.quota_limits.set(auth.account_key, {"weekly": None, "fiveHour": None})
            self.assertIsNotNone(await router.choose_slot(None, set(), set()))
            self.assertEqual(len(session.urls), 2)
        finally:
            await router.cleanup()
            store.close()


@patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET="41" * 32)
class QuotaApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_patch_payload_preserve_clear_and_bound(self):
        store = TelemetryStore(":memory:")
        app = create_app(test_app.test_config(), store)
        async with TestClient(TestServer(app)) as client:
            router = app[STATE_KEY].codex
            auth = CodexAuth("fixture", account_id="account")
            router.slots = [CodexSlot(0, auth=auth)]
            path = f"/api/codex/balancer/accounts/{auth.account_key}/limits"
            headers = {"Authorization": "Bearer secret"}
            base = {"weekly": None, "fiveHour": None}
            router.quota.observe(auth.account_key, {"weekly": window(40)})
            response = await client.patch(path, headers=headers, json={**base, "daily": 15})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["dailyBudget"]["status"], "ok")
            # Keep resetAt fixed so this is the same aligned slice.
            weekly = {**router.quota.observations[auth.account_key]["weekly"], "usedPercent": 55}
            router.quota.observe(auth.account_key, {"weekly": weekly})
            response = await client.patch(path, headers=headers, json=base)
            payload = await response.json()
            self.assertEqual(payload["quotaLimits"]["daily"], 15)
            self.assertEqual(payload["dailyBudget"]["consumed"], 15)
            self.assertTrue(payload["quotaPolicy"]["paused"])
            response = await client.patch(path, headers=headers, json={**base, "daily": None})
            payload = await response.json()
            self.assertEqual(payload["dailyBudget"]["status"], "off")
            self.assertFalse(payload["quotaPolicy"]["paused"])
            response = await client.patch(path, headers=headers, data=b" " * 2049)
            self.assertEqual(response.status, 413)

    async def test_authenticated_strict_atomic_save_clear_and_health(self):
        store = TelemetryStore(":memory:")
        app = create_app(test_app.test_config(), store)
        async with TestClient(TestServer(app)) as client:
            router = app[STATE_KEY].codex
            auth = CodexAuth("fixture", account_id="account")
            router.slots = [CodexSlot(0, auth=auth)]
            path = f"/api/codex/balancer/accounts/{auth.account_key}/limits"
            headers = {"Authorization": "Bearer secret"}
            limits = {"weekly": 70, "fiveHour": 50}
            self.assertEqual((await client.patch(path, json=limits)).status, 401)
            for invalid in ({"weekly": 70}, {**limits, "extra": 1}, {**limits, "weekly": True},
                            {**limits, "weekly": 0}, {**limits, "weekly": 101}, {**limits, "weekly": "70"}):
                with self.subTest(invalid=invalid):
                    self.assertEqual((await client.patch(path, headers=headers, json=invalid)).status, 400)
            unknown = path.replace(auth.account_key, "0" * 16)
            self.assertEqual((await client.patch(unknown, headers=headers, json=limits)).status, 404)
            response = await client.patch(path, headers=headers, json=limits)
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual(payload["quotaLimits"], {**limits, "daily": None})
            self.assertEqual(payload["dailyBudget"]["status"], "off")
            self.assertTrue(payload["quotaPolicy"]["paused"])
            self.assertEqual(store.quota_limits.limits(auth.account_key), {**limits, "daily": None})
            slot = router.slot_status()[0]
            health = classify_codex_slot_health(slot, slot_errors=[], reset_credit_errors=[], cached=False)
            self.assertEqual(health["healthStatus"], "paused")
            health = classify_codex_slot_health({**slot, "routingEnabled": False}, slot_errors=[], reset_credit_errors=[], cached=False)
            self.assertEqual(health["healthStatus"], "disabled")
            cleared = await client.patch(path, headers=headers, json={"weekly": None, "fiveHour": None})
            self.assertFalse((await cleared.json())["quotaPolicy"]["paused"])
