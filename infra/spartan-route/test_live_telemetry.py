from __future__ import annotations

import asyncio
import os
import sqlite3
import tracemalloc
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
from spartan_route.app import ExecutionResult, STATE_KEY, create_app, write_execution_response
from spartan_route.telemetry import TelemetryStore
from spartan_route.live_telemetry import LiveTelemetry, MAX_COUNTER
from test_app import test_config
from test_optional_storage import REQUIRED


class LiveTelemetryCounterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.live = LiveTelemetry(clock=lambda: self.now)

    def observe(self, input_tokens=100, cached=0, output=10, known=True, seen=True, **fields):
        self.live.observe(ExecutionResult(status=fields.pop("status", 200), input_tokens=input_tokens,
            cached_input_tokens=cached, output_tokens=output, usage_seen=seen,
            native_cache={"version": 2, "cache_read_known": known}, **fields), 50)
        return self.live.snapshot()["metrics"]

    def test_weighted_cache_unknown_zero_and_estimates_are_distinct(self):
        self.observe(900, 450)
        self.observe(100, 0)
        self.observe(1000000, 900000, known=False, seen=False)
        values = self.observe(100, 99, known=False)
        self.assertEqual(values["cacheHitPercent"], 45)
        self.assertEqual(values["inputTokens"], 1100)
        self.assertEqual(values["cachedInputTokens"], 450)
        self.assertEqual(values["cacheKnownRequests"], 2)
        self.assertEqual(values["usageKnownRequests"], 3)
        self.assertEqual(values["requests"], 4)
        zero = LiveTelemetry()
        zero.observe(ExecutionResult(200, usage_seen=True, input_tokens=10,
            native_cache={"version": 2, "cache_read_known": True}), 1)
        self.assertEqual(zero.snapshot()["metrics"]["cacheHitPercent"], 0)
        self.assertIsNone(LiveTelemetry().snapshot()["metrics"]["cacheHitPercent"])

    def test_invalid_cache_pairs_and_zero_denominator_remain_unknown(self):
        for input_tokens, cached in ((0, 0), (100, 101), (100, -1), (100, True)):
            with self.subTest(input_tokens=input_tokens, cached=cached):
                self.live = LiveTelemetry()
                self.assertIsNone(self.observe(input_tokens, cached)["cacheHitPercent"])

    def test_expiry_long_idle_restart_and_wall_time_do_not_change_window(self):
        self.observe()
        self.now = 899.9
        with patch("spartan_route.live_telemetry.timestamp", return_value="wall clock changed"):
            self.assertEqual(self.live.snapshot()["metrics"]["requests"], 1)
        self.now = 900
        self.assertEqual(self.live.snapshot()["metrics"]["requests"], 0)
        self.assertEqual(self.live.snapshot()["lastObservedAgeSeconds"], 900)
        self.now = 10000
        self.assertEqual(self.observe()["requests"], 1)
        restarted = LiveTelemetry()
        self.assertNotEqual(restarted.epoch, self.live.epoch)
        self.assertIsNone(restarted.snapshot()["lastObservedAt"])

    def test_overflow_suppresses_derived_ratios_and_counts_errors_not_disconnects(self):
        self.observe(MAX_COUNTER, MAX_COUNTER, output=MAX_COUNTER)
        values = self.observe(1, 0, status=502)
        self.assertEqual(values["inputTokens"], MAX_COUNTER)
        self.assertIsNone(values["cacheHitPercent"])
        self.assertIsNone(values["averageDurationMs"])
        self.assertTrue(self.live.snapshot()["saturated"])
        self.assertEqual(values["errors"], 1)
        self.observe(status=200, error="upstream_failed")
        self.assertEqual(self.observe(status=200)["errors"], 2)

    def test_storage_and_content_free_bounds_and_observer_failure_is_subordinate(self):
        for second in range(1200):
            self.now = second
            self.observe()
        self.assertEqual(len(self.live.buckets), 900)
        self.assertEqual(self.live.snapshot()["metrics"]["requests"], 900)
        self.live.observe(ExecutionResult(200, native_cache="invalid", usage_seen=True), 1)
        snapshot = self.live.snapshot()
        self.assertEqual(snapshot["observationFailures"], 1)
        self.assertEqual(snapshot["metrics"]["requests"], 900)
        self.assertNotIn("native_cache", str(snapshot))


    def test_memory_growth_stops_at_the_bucket_bound(self):
        result = ExecutionResult(200, usage_seen=True, input_tokens=100)
        tracemalloc.start()
        try:
            for second in range(2000):
                self.now = second
                self.live.observe(result, 1)
            before = tracemalloc.get_traced_memory()[0]
            for second in range(2000, 12000):
                self.now = second
                self.live.observe(result, 1)
            after = tracemalloc.get_traced_memory()[0]
            self.assertEqual(len(self.live.buckets), 900)
            self.assertLess(after - before, 100000)
        finally:
            tracemalloc.stop()


class LiveTelemetryApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_cleanup_precedes_one_observation_on_disconnect_prepare_failure_and_cancellation(self):
        for mode in ("complete", "disconnect", "prepare", "cancel"):
            with self.subTest(mode=mode):
                live = LiveTelemetry()
                async def source():
                    yield b"synthetic stream"
                result = ExecutionResult(200, stream=source())
                async def cleanup():
                    self.assertEqual(live.snapshot()["metrics"]["requests"], 0)
                    result.usage_seen, result.input_tokens = True, 10
                result.stream_cleanup = AsyncMock(side_effect=cleanup)
                response = MagicMock(prepare=AsyncMock(), write=AsyncMock(), write_eof=AsyncMock())
                if mode == "prepare":
                    response.prepare.side_effect = RuntimeError("synthetic prepare failure")
                elif mode == "disconnect":
                    response.write.side_effect = ConnectionResetError()
                elif mode == "cancel":
                    response.write.side_effect = asyncio.CancelledError()
                with patch("spartan_route.app.web.StreamResponse", return_value=response):
                    call = write_execution_response(MagicMock(), result, on_done=lambda: live.observe(result, 1))
                    if mode in {"prepare", "cancel"}:
                        with self.assertRaises(RuntimeError if mode == "prepare" else asyncio.CancelledError):
                            await call
                    else:
                        await call
                await result.stream.aclose()
                self.assertEqual(result.stream_cleanup.await_count, 1)
                self.assertEqual(live.snapshot()["metrics"]["requests"], 1)
                self.assertEqual(live.snapshot()["metrics"]["inputTokens"], 10)
                self.assertEqual(live.snapshot()["metrics"]["errors"], 0)


    async def test_disabled_storage_live_endpoint_and_real_request_finalization(self):
        store = TelemetryStore(":memory:", optional_telemetry_enabled=False)
        with patch("spartan_route.app.CodexModelDiscovery.start"), patch(
            "spartan_route.app.CodexModelDiscovery.stop", new_callable=AsyncMock,
        ):
            client = TestClient(TestServer(create_app(test_config(), telemetry=store)))
            await client.start_server()
            try:
                store._conn.set_authorizer(lambda *_args: sqlite3.SQLITE_DENY)
                self.assertEqual((await client.get("/api/telemetry/live")).status, 401)
                auth = {"Authorization": "Bearer secret"}
                response = await client.get("/api/telemetry/live", headers=auth)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                initial = await response.json()
                self.assertFalse(initial["historicalTelemetryEnabled"])
                self.assertEqual(initial["metrics"]["requests"], 0)
                self.assertIsNone(initial["metrics"]["cacheHitPercent"])
                observed = ExecutionResult(status=200, body={"choices": []}, input_tokens=100,
                    cached_input_tokens=25, output_tokens=12, usage_seen=True,
                    native_cache={"version": 2, "cache_read_known": True})
                with patch("spartan_route.app.execute_chat", new=AsyncMock(return_value=observed)):
                    response = await client.post("/v1/chat/completions", headers=auth, json={
                        "model": "codex/gpt-5.5", "messages": [{"role": "user", "content": "private synthetic prompt"}],
                    })
                    self.assertEqual(response.status, 200)
                    await response.read()
                optional_access = []
                def authorize(action, table, *_args):
                    data_actions = {sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                    if action in data_actions and table not in REQUIRED:
                        optional_access.append(table)
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                store._conn.set_authorizer(authorize)
                with patch.dict(os.environ, {"SPARTAN_REASONING_BINDING_SECRET": "a" * 64}), patch("spartan_route.app.execute_responses", new=AsyncMock(return_value=ExecutionResult(
                    502, body={"error": {"message": "synthetic failure"}}, error="upstream_failed", input_tokens=99999,
                ))):
                    response = await client.post("/v1/responses", headers=auth, json={
                        "model": "codex/gpt-5.5", "input": "second private synthetic prompt",
                    })
                    self.assertEqual(response.status, 502, await response.text())
                payload = await (await client.get("/api/telemetry/live", headers=auth)).json()
                self.assertEqual(payload["metrics"]["requests"], 2)
                self.assertEqual(payload["metrics"]["errors"], 1)
                self.assertEqual(payload["metrics"]["usageKnownRequests"], 1)
                self.assertEqual(payload["metrics"]["cacheHitPercent"], 25)
                self.assertEqual(payload["metrics"]["inputTokens"], 100)
                self.assertNotIn("private synthetic prompt", str(payload))
                self.assertIsNone(client.app[STATE_KEY].queue.task)
                self.assertEqual(optional_access, [])
            finally:
                store._conn.set_authorizer(None)
                await client.close()
