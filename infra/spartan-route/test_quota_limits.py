from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from spartan_route.app import STATE_KEY, classify_codex_slot_health, create_app
from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.quota_limits import dispatch_admission, evaluate
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
            self.assertEqual(store.quota_limits.limits("account"), {"weekly": None, "fiveHour": None})
            store.quota_limits.set("account", {"weekly": 70, "fiveHour": 50})
            store._conn.execute("""CREATE TRIGGER reject_limits BEFORE UPDATE ON codex_account_quota_limits
                BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
            store._conn.commit()
            with self.assertRaisesRegex(Exception, "fixture failure"):
                store.quota_limits.set("account", {"weekly": None, "fiveHour": None})
            self.assertEqual(store.quota_limits.limits("account"), {"weekly": 70, "fiveHour": 50})
            store.close()
            restored = TelemetryStore(path)
            self.assertEqual(restored.quota_limits.limits("account"), {"weekly": 70, "fiveHour": 50})
            restored._conn.execute("DROP TRIGGER reject_limits")
            restored._conn.execute("PRAGMA ignore_check_constraints=ON")
            restored._conn.execute("UPDATE codex_account_quota_limits SET weekly='invalid'")
            restored._conn.commit()
            restored.close()
            with self.assertRaises(SlotPolicyUnavailable):
                TelemetryStore(path)


class QuotaRoutingTests(unittest.IsolatedAsyncioTestCase):
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
            self.assertEqual(router.quota.store.limits(fresh_identity.account_key), {"weekly": None, "fiveHour": None})
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
            self.assertEqual(payload["quotaLimits"], limits)
            self.assertTrue(payload["quotaPolicy"]["paused"])
            self.assertEqual(store.quota_limits.limits(auth.account_key), limits)
            slot = router.slot_status()[0]
            health = classify_codex_slot_health(slot, slot_errors=[], reset_credit_errors=[], cached=False)
            self.assertEqual(health["healthStatus"], "paused")
            health = classify_codex_slot_health({**slot, "routingEnabled": False}, slot_errors=[], reset_credit_errors=[], cached=False)
            self.assertEqual(health["healthStatus"], "disabled")
            cleared = await client.patch(path, headers=headers, json={"weekly": None, "fiveHour": None})
            self.assertFalse((await cleared.json())["quotaPolicy"]["paused"])
