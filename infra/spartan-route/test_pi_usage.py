from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.app import STATE_KEY, ExecutionResult, apply_usage, create_app
from spartan_route.codex import (
    CacheLease,
    CodexAuth,
    CodexCallResult,
    CodexRouter,
    CodexSlot,
)
from spartan_route.pi_usage import CURRENT_REQUEST, PiUsage
from spartan_route.pi_usage_store import COUNTERS
from spartan_route.telemetry import TelemetryStore
from test_app import test_config
from test_codex import FakeJsonContextResponse, FakeResponse


def baseline(**changes):
    return dict(dict.fromkeys(COUNTERS, 0), **changes)


class PiStoreTests(unittest.TestCase):
    def test_origin_is_atomic_immutable_and_survives_retention_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "route.db")
            first, second = TelemetryStore(path), TelemetryStore(path)
            session_id = str(uuid.uuid4())
            original = baseline(inputTokens=10, outputTokens=2, totalTokens=12, codexTokens=12)
            row, created = first.pi_usage.register(session_id, original)
            replay, replay_created = second.pi_usage.register(session_id, baseline(), True)
            self.assertTrue(created)
            self.assertFalse(replay_created)
            self.assertEqual(replay["originId"], row["originId"])
            self.assertEqual(replay["baseline"], original)
            first.pi_usage.record(session_id, row["originId"], original, True, False)
            first.enforce_routing_retention(force_rebuild=True)
            first.close()
            second.close()
            reopened = TelemetryStore(path)
            try:
                result = reopened.pi_usage.get(session_id)
                self.assertEqual(result["totals"]["totalTokens"], 24)
                self.assertTrue(result["coverage"]["trackingIncomplete"])
                self.assertEqual(result["coverage"]["attempts"], 1)
            finally:
                reopened.close()

    def test_reported_zero_replaces_previous_provider_usage(self):
        result = ExecutionResult(status=200, input_tokens=99, output_tokens=33)
        for empty in ({"usage": {}}, {"usage": {"other": 0}}, {"usage": {"input_tokens": True}}, {"choices": []}):
            apply_usage(result, empty)
            self.assertFalse(result.usage_seen)
            self.assertEqual((result.input_tokens, result.output_tokens), (99, 33))
        apply_usage(result, {"usage": {"prompt_tokens": 0, "completion_tokens": 0}})
        apply_usage(result, {"usage": {"completion_tokens": 5}})
        apply_usage(result, {"choices": [{"delta": {"content": "hi"}}]})
        self.assertTrue(result.usage_seen)
        self.assertEqual((result.input_tokens, result.output_tokens), (0, 5))


class PiApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(":memory:")
        self.app = create_app(test_config(), self.store)
        self.app.on_startup.clear()
        self.state = self.app[STATE_KEY]
        self.router = CodexRouter(None, self.store)
        self.router.slots = [CodexSlot(0, auth=CodexAuth("token-a", account_id="private-a")),
                             CodexSlot(1, auth=CodexAuth("token-b", account_id="private-b")),
                             CodexSlot(2, auth=CodexAuth("token-a", account_id="private-a"))]
        self.state.codex = self.router
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer secret"}
        self.session_id = str(uuid.uuid4())

    async def asyncTearDown(self):
        await self.client.close()

    async def register(self, value=None, **extra):
        response = await self.client.post("/api/pi/sessions", headers=self.headers,
                                          json={"sessionId": self.session_id, "baseline": baseline() if value is None else value, **extra})
        return response, await response.json()

    async def snapshot(self):
        response = await self.client.get(f"/api/pi/status?sessionId={self.session_id}", headers=self.headers)
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_registration_canonical_replay_validation_auth_and_recovery_gap(self):
        unauthorized = await self.client.post("/api/pi/sessions", json={})
        self.assertEqual(unauthorized.status, 401)
        missing = await self.snapshot()
        self.assertEqual((missing["status"], missing["session"]), ("not_registered", None))
        invalid, _ = await self.register(baseline(inputTokens=True))
        self.assertEqual(invalid.status, 400)
        original = baseline(inputTokens=10, cachedInputTokens=4, outputTokens=2, totalTokens=12, codexTokens=12)
        response, first = await self.register(original)
        self.assertEqual(response.status, 201)
        response, second = await self.register(trackingIncomplete=True)
        self.assertEqual(response.status, 200)
        self.assertTrue(second["baselineConflict"])
        self.assertEqual(second["session"]["originId"], first["session"]["originId"])
        self.assertEqual(second["session"]["baseline"], original)
        self.assertTrue(second["session"]["coverage"]["trackingIncomplete"])

    async def test_status_is_passive_sanitized_and_weights_unique_accounts(self):
        await self.register()
        key_a, key_b = (slot.account_key() for slot in self.router.slots[:2])
        self.store.set_codex_capacity_multiplier(key_b, 5)
        self.store.upsert_usage_snapshots([
            {"account_key": key_a, "slot_index": 0, "window": "weekly", "used_percent": 25,
             "reset_at": "2099-01-01T00:00:00Z", "window_minutes": 10080},
            {"account_key": key_b, "slot_index": 1, "window": "weekly", "used_percent": 60,
             "reset_at": "2099-01-01T00:00:00Z", "window_minutes": 10080},
        ])
        forbidden = AssertionError("passive GET attempted routing/auth/refresh")
        with patch.object(self.router, "load_auth", side_effect=forbidden), \
             patch.object(self.router, "lease_status", side_effect=forbidden), \
             patch.object(self.router, "choose_slot", side_effect=forbidden), \
             patch.object(self.router, "usage_snapshot", side_effect=forbidden), \
             patch.object(self.router, "trigger_usage_refresh", side_effect=forbidden):
            result = await self.snapshot()
        self.assertEqual(len(result["accounts"]), 2)
        self.assertEqual(result["accounts"][0]["slotIndexes"], [0, 2])
        self.assertEqual(result["pool"]["weekly"]["capacityUnits"], 600)
        self.assertEqual(result["pool"]["weekly"]["usedUnits"], 325)
        self.assertEqual(result["pool"]["weekly"]["remainingUnits"], 275)
        self.assertEqual(result["pool"]["fiveHour"]["unknownAccounts"], 2)
        self.assertIsNone(result["pool"]["fiveHour"]["remainingPercent"])
        serialized = json.dumps(result)
        for private in (key_a, key_b, "private-a", "private-b", "slotPaths", "access_token", "accountKey"):
            self.assertNotIn(private, serialized)

    async def test_session_affinity_latest_started_and_lease_peek_never_touches_state(self):
        _, registration = await self.register()
        headers = {"X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                   "X-Spartan-Pi-Origin": registration["session"]["originId"]}
        older, newer = self.state.pi_usage.correlate(headers), self.state.pi_usage.correlate(headers)
        newer.affinity("new-private-key")
        older.affinity("old-private-key")
        now = time.time()
        lease = CacheLease("new-private-key", self.router.slots[0].account_key(), 0, now - 30, now - 5, now - 1, now + 500)
        self.router.leases["new-private-key"] = lease
        original = replace(lease)
        result = await self.snapshot()
        self.assertEqual(result["lease"]["state"], "grace")
        self.assertEqual(result["lease"]["slotIndex"], 0)
        self.assertGreater(result["lease"]["remainingSeconds"], 490)
        self.assertEqual(lease, original)
        lease.max_expires_at = now - 1
        expired = await self.snapshot()
        self.assertEqual(expired["lease"]["state"], "expired")
        self.assertIn("new-private-key", self.router.leases)

    async def test_global_lease_requires_observed_session_binding_after_registration_or_restart(self):
        _, registration = await self.register()
        now = time.time()
        self.router.leases["__global__"] = CacheLease("private-affinity", self.router.slots[0].account_key(), 0,
                                                     now, now, now + 300, now + 600)
        with patch.dict("os.environ", {"CODEX_CACHE_LEASE_MODE": "global"}):
            initial = await self.snapshot()
            self.assertEqual(initial["lease"]["scope"], "global")
            self.assertIsNone(initial["lease"]["slotIndex"])
            headers = {"X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                       "X-Spartan-Pi-Origin": registration["session"]["originId"]}
            self.state.pi_usage.correlate(headers).affinity("private-affinity")
            observed = await self.snapshot()
            self.assertEqual(observed["lease"]["slotIndex"], 0)
            self.state.pi_usage = PiUsage(self.store.pi_usage, self.store)
            restarted = await self.snapshot()
            self.assertIsNone(restarted["lease"]["slotIndex"])
            self.assertIsNone(restarted["lease"]["remainingSeconds"])

    async def test_expired_windows_and_unresolved_identity_are_unknown_without_shrinking_pool(self):
        await self.register()
        key = self.router.slots[0].account_key()
        self.router.slots[1].rate_limited_until = time.time() + 500
        self.router.slots.append(CodexSlot(3, path="/private/auth.json"))
        self.store.upsert_usage_snapshots([
            {"account_key": key, "slot_index": 0, "window": "weekly", "used_percent": 25,
             "reset_at": "2000-01-01T00:00:00Z", "window_minutes": 10080},
        ])
        result = await self.snapshot()
        self.assertIsNone(result["accounts"][0]["weekly"])
        self.assertEqual(result["accounts"][1]["state"], "rate_limited")
        self.assertEqual(result["pool"]["weekly"]["capacityUnits"], 200)
        self.assertTrue(result["pool"]["weekly"]["capacityIncomplete"])
        self.assertEqual(result["unresolvedSlots"], [{"slotIndex": 3, "state": "identity_unknown"}])
        self.assertIsNone(result["pool"]["weekly"]["remainingPercent"])

    async def test_observations_older_than_one_hour_are_unknown(self):
        await self.register()
        key = self.router.slots[0].account_key()
        self.store.upsert_usage_snapshots([
            {"account_key": key, "slot_index": 0, "window": "weekly", "used_percent": 25,
             "reset_at": "2099-01-01T00:00:00Z", "window_minutes": 10080},
        ])
        current = await self.snapshot()
        self.assertEqual(current["accounts"][0]["weekly"]["usedPercent"], 25)
        with patch("spartan_route.pi_usage.time.time", return_value=time.time() + 3601):
            stale = await self.snapshot()
        self.assertIsNone(stale["accounts"][0]["weekly"])
        self.assertIsNone(stale["pool"]["weekly"]["remainingPercent"])

    async def test_refresh_shared_singleflight_due_gate_and_session_response(self):
        await self.register()
        release = asyncio.Event()

        async def wait_refresh():
            await release.wait()
            return {}

        def trigger():
            self.router._usage_refresh_task = asyncio.create_task(wait_refresh())

        try:
            with patch.object(self.router, "trigger_usage_refresh", side_effect=trigger) as refresh:
                for _ in range(2):
                    response = await self.client.post("/api/pi/usage/refresh", headers=self.headers, json={"sessionId": self.session_id})
                    self.assertEqual(response.status, 202)
                    self.assertEqual((await response.json())["session"]["sessionId"], self.session_id)
                release.set()
                await self.router._usage_refresh_task
                response = await self.client.post("/api/pi/usage/refresh", headers=self.headers, json={})
                self.assertEqual(response.status, 200)
                self.assertEqual(refresh.call_count, 1)
        finally:
            release.set()

    async def test_normal_controller_account_failures_backoff_and_preserve_last_good_windows(self):
        await self.register()
        key = self.router.slots[0].account_key()
        self.store.upsert_usage_snapshots([
            {"account_key": key, "slot_index": 0, "window": "weekly", "used_percent": 25,
             "reset_at": "2099-01-01T00:00:00Z", "window_minutes": 10080},
        ])
        calls = []

        class ControlSession:
            def get(self, url, **_kwargs):
                calls.append(url)
                return FakeJsonContextResponse(429 if url.endswith("/usage") else 200, {})

        self.router.control_session = ControlSession()
        self.router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        response = await self.client.post("/api/pi/usage/refresh", headers=self.headers, json={})
        self.assertEqual(response.status, 202)
        task = self.router._usage_refresh_task
        completed = await task
        self.assertEqual(completed["refreshProgress"]["status"], "complete")
        self.assertTrue(completed["slotErrors"])
        result = await self.snapshot()
        self.assertEqual(result["refresh"]["state"], "failed")
        self.assertEqual(result["accounts"][0]["weekly"]["remainingPercent"], 75)
        self.assertGreater(self.state.pi_usage.next_due - time.time(), 119)
        before = len(calls)
        await self.client.post("/api/pi/usage/refresh", headers=self.headers, json={})
        self.assertIs(self.router._usage_refresh_task, task)
        self.assertEqual(len(calls), before)
        self.assertEqual(self.state.pi_usage.failure_count, 1)

    async def test_persisted_errors_without_new_live_cohort_do_not_trigger_backoff(self):
        await self.register()

        async def persisted():
            return {"source": "persisted", "slotErrors": [{"slotIndex": 0}],
                    "refreshProgress": {"status": "complete"}}

        self.router._usage_refresh_task = asyncio.create_task(persisted())
        await self.router._usage_refresh_task
        self.router._usage_progress_snapshot = {"source": "live", "slotErrors": [{"slotIndex": 0}],
                                                 "refreshProgress": {"status": "failed"}}
        result = await self.snapshot()
        self.assertEqual(result["refresh"]["state"], "idle")
        self.assertEqual(self.state.pi_usage.failure_count, 0)
        self.assertIsNone(result["refresh"]["nextDueAt"])

    async def test_chat_correlation_validation_marker_only_and_provider_observation(self):
        _, registered = await self.register()
        valid = {**self.headers, "X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                 "X-Spartan-Pi-Origin": registered["session"]["originId"]}
        body = {"model": "openai/gpt-test", "messages": [{"role": "user", "content": "test"}]}
        observed = ExecutionResult(200, body={"choices": []}, usage_seen=True, input_tokens=0, output_tokens=7)
        with patch("spartan_route.app.execute_chat", new=AsyncMock(return_value=observed)) as execute:
            bad = await self.client.post("/v1/chat/completions", headers={**valid, "X-Spartan-Pi-Origin": str(uuid.uuid4())}, json=body)
            self.assertEqual(bad.status, 409)
            execute.assert_not_awaited()
            untracked = await self.client.post("/v1/chat/completions", headers={**self.headers, "X-Spartan-Pi-Client": "1"}, json=body)
            self.assertEqual(untracked.status, 200)
            response = await self.client.post("/v1/chat/completions", headers=valid, json=body)
            self.assertEqual(response.status, 200)
        session = (await self.snapshot())["session"]
        self.assertEqual(session["observed"]["totalTokens"], 7)
        self.assertEqual(session["observed"]["codexTokens"], 0)
        self.assertEqual(session["coverage"]["attempts"], 1)

    async def test_store_write_failure_does_not_replace_successful_chat(self):
        _, registered = await self.register()
        headers = {**self.headers, "X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                   "X-Spartan-Pi-Origin": registered["session"]["originId"]}
        observed = ExecutionResult(200, body={"choices": []}, usage_seen=True, input_tokens=10)
        with patch("spartan_route.app.execute_chat", new=AsyncMock(return_value=observed)), \
             patch.object(self.store.pi_usage, "record", side_effect=OSError("private storage failure")):
            response = await self.client.post("/v1/chat/completions", headers=headers,
                                               json={"model": "openai/gpt-test", "messages": []})
            self.assertEqual(response.status, 200)
        result = await self.snapshot()
        self.assertTrue(result["session"]["coverage"]["trackingIncomplete"])

    async def test_client_disconnect_finalizes_observed_partial_provider_stream(self):
        _, registered = await self.register()
        headers = {**self.headers, "X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                   "X-Spartan-Pi-Origin": registered["session"]["originId"]}
        release, finalized = asyncio.Event(), asyncio.Event()
        observed = ExecutionResult(200, usage_seen=True, input_tokens=8, output_tokens=2)

        async def stream():
            yield b"data: first\n\n"
            await release.wait()
            yield b"data: second\n\n"

        observed.stream = stream()
        record = self.store.pi_usage.record

        def finished(*args):
            record(*args)
            finalized.set()

        with patch("spartan_route.app.execute_chat", new=AsyncMock(return_value=observed)), \
             patch.object(self.store.pi_usage, "record", side_effect=finished):
            response = await self.client.post("/v1/chat/completions", headers=headers,
                                               json={"model": "openai/gpt-test", "stream": True, "messages": []})
            await response.content.readuntil(b"\n\n")
            response.close()
            release.set()
            await asyncio.wait_for(finalized.wait(), 2)
        session = (await self.snapshot())["session"]
        self.assertEqual(session["observed"]["totalTokens"], 10)
        self.assertEqual(session["coverage"]["incompleteAttempts"], 1)
        self.assertEqual(session["coverage"]["attempts"], 1)


class PiTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(":memory:")
        self.service = PiUsage(self.store.pi_usage, self.store)
        self.session_id = str(uuid.uuid4())
        registration, _ = self.store.pi_usage.register(self.session_id, baseline())
        self.correlation = self.service.correlate({"X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": self.session_id,
                                                   "X-Spartan-Pi-Origin": registration["originId"]})
        self.router = CodexRouter(None, self.store)
        self.router.slots = [CodexSlot(index, auth=CodexAuth("token", account_id=f"test-{index}")) for index in range(2)]
        self.router.slot_availability = AsyncMock(return_value=type("Available", (), {"eligible": 2})())
        self.token = CURRENT_REQUEST.set(self.correlation)

    async def asyncTearDown(self):
        CURRENT_REQUEST.reset(self.token)
        self.store.close()

    async def test_retried_attempts_observed_once_and_zero_never_estimated(self):
        results = [CodexCallResult(502, body={"error": {"code": "codex_empty_response"}}, usage_seen=True,
                                   input_tokens=3, output_tokens=2, error="upstream"),
                   CodexCallResult(200, body={"choices": []}, usage_seen=True, input_tokens=0, output_tokens=7, completed=True)]
        chosen = [(slot, slot.auth) for slot in self.router.slots]
        self.router.choose_slot_with_catalog_recovery = AsyncMock(side_effect=chosen)
        self.router._single_chat_call = AsyncMock(side_effect=results)
        result = await self.router.execute_chat({"messages": [{"role": "user", "content": "long enough to estimate"}]},
                                                "codex/gpt-5.5", False, "session", "key", "request")
        self.assertEqual(result.input_tokens, 0)
        session = self.store.pi_usage.get(self.session_id)
        self.assertEqual(session["observed"]["codexTokens"], 12)
        self.assertEqual(session["coverage"]["attempts"], 2)
        self.assertEqual(session["coverage"]["usageSeenAttempts"], 2)
        self.assertEqual(session["coverage"]["incompleteAttempts"], 1)

    async def test_stream_abort_keeps_observed_usage_and_cleanup_is_idempotent(self):
        result = CodexCallResult(200, usage_seen=True, input_tokens=8, cached_input_tokens=4, output_tokens=2)

        async def stream():
            yield b"data: partial\n\n"
            result.completed = True

        result.stream = stream()
        self.router.choose_slot_with_catalog_recovery = AsyncMock(return_value=(self.router.slots[0], self.router.slots[0].auth))
        self.router._single_chat_call = AsyncMock(return_value=result)
        response = await self.router.execute_chat({"messages": []}, "codex/gpt-5.5", True, "session", "key", "request")
        await anext(response.stream)
        await response.stream.aclose()
        await response.stream_cleanup()
        session = self.store.pi_usage.get(self.session_id)
        self.assertEqual(session["observed"]["totalTokens"], 10)
        self.assertEqual(session["observed"]["cachedInputTokens"], 4)
        self.assertEqual(session["coverage"]["attempts"], 1)
        self.assertEqual(session["coverage"]["incompleteAttempts"], 1)
        self.assertEqual(self.router.slots[0].in_flight, 0)

    async def test_missing_usage_has_coverage_without_estimated_tokens(self):
        result = CodexCallResult(200, body={"choices": []}, completed=True)
        self.router.choose_slot_with_catalog_recovery = AsyncMock(return_value=(self.router.slots[0], self.router.slots[0].auth))
        self.router._single_chat_call = AsyncMock(return_value=result)
        await self.router.execute_chat({"messages": [{"role": "user", "content": "input with estimates"}]},
                                       "codex/gpt-5.5", False, "session", "key", "request")
        session = self.store.pi_usage.get(self.session_id)
        self.assertEqual(session["observed"]["totalTokens"], 0)
        self.assertEqual(session["coverage"]["usageMissingAttempts"], 1)
        self.assertEqual(session["coverage"]["incompleteAttempts"], 1)

    async def test_cancelled_attempt_preserves_missing_coverage_and_releases_inflight(self):
        self.router.choose_slot_with_catalog_recovery = AsyncMock(return_value=(self.router.slots[0], self.router.slots[0].auth))
        self.router._single_chat_call = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.router.execute_chat({"messages": []}, "codex/gpt-5.5", False, "session", "key", "request")
        session = self.store.pi_usage.get(self.session_id)
        self.assertEqual(session["coverage"]["usageMissingAttempts"], 1)
        self.assertEqual(session["observed"]["totalTokens"], 0)
        self.assertEqual(self.router.slots[0].in_flight, 0)

    async def test_real_chat_collector_distinguishes_empty_usage_from_reported_zero(self):
        self.router.choose_slot_with_catalog_recovery = AsyncMock(return_value=(self.router.slots[0], self.router.slots[0].auth))
        for usage in ({}, {"input_tokens": 0, "output_tokens": 2}):
            response = FakeResponse([
                ("response.output_text.delta", {"delta": "hello"}),
                ("response.completed", {"response": {"usage": usage}}),
            ])
            self.router.open_inference = AsyncMock(return_value=response)
            result = await self.router.execute_chat({"messages": [{"role": "user", "content": "nonzero input estimate"}]},
                                                    "codex/gpt-5.5", False, "session", "key", "request")
            self.assertEqual(result.usage_seen, bool(usage))
            if usage:
                self.assertEqual(result.input_tokens, 0)
        session = self.store.pi_usage.get(self.session_id)
        self.assertEqual(session["coverage"]["attempts"], 2)
        self.assertEqual(session["coverage"]["usageMissingAttempts"], 1)
        self.assertEqual(session["observed"]["totalTokens"], 2)
