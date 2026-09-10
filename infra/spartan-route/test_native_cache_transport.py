from __future__ import annotations

import asyncio
import copy
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_app as app_fixtures
import test_codex as fixtures
from spartan_route.app import execute_provider_responses, log_request, ExecutionResult
from spartan_route.native_cache import NativeCacheDiagnostics
from spartan_route.native_responses import execute_codex_responses
from spartan_route.telemetry import RoutingLogEntry, TelemetryStore


class NativeCacheTransportTests(unittest.TestCase):
    @staticmethod
    def response():
        return fixtures.FakeResponse(events=[
            ("response.created", {"type": "response.created", "response": {"status": "in_progress", "output": []}}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "OK"}),
            ("response.completed", {"type": "response.completed", "response": {
                "status": "completed", "output": [],
                "usage": {"input_tokens": 3000, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2000, "cache_write_tokens": 1000}},
                "prompt_cache_diagnostics": {"type": "cache_hit", "reason": "private text must not leak"},
            }}),
        ])

    def test_final_payloads_and_usage_reach_both_result_modes(self):
        for stream in (True, False):
            with self.subTest(stream=stream):
                router = fixtures.NativeResponsesTests.router([self.response(), self.response(), self.response()])
                cache = NativeCacheDiagnostics()
                body = {"instructions": "initial", "input": [{"role": "user", "content": "hello"}], "reasoning": {"effort": "high"}}

                async def run():
                    results = []
                    for instructions in ("initial", "initial", "changed"):
                        request = copy.deepcopy(body)
                        request["instructions"] = instructions
                        result = await execute_codex_responses(router, request, "codex/gpt-6-astra", stream, "session", "cache-key", cache)
                        if result.stream:
                            async for _ in result.stream:
                                pass
                        results.append(result)
                    return results

                results = asyncio.run(run())
                self.assertEqual([r.native_cache["comparison"] for r in results], ["no_predecessor", "replay", "changed"])
                self.assertEqual(results[-1].native_cache["changed_components"], ["instructions"])
                self.assertEqual(results[-1].native_cache["cache_write_tokens"], 1000)
                self.assertEqual(results[-1].native_cache["provider"]["type"], "cache_hit")
                self.assertNotIn("private text", json.dumps(results[-1].native_cache))
                self.assertEqual(router.session.calls[0]["json"]["reasoning"]["context"], "all_turns")
                self.assertEqual(router.session.calls[0]["json"]["instructions"], body["instructions"])
                self.assertEqual(body["reasoning"], {"effort": "high"})
                self.assertIsNotNone(results[-1].native_cache["ttft_ms"])
                self.assertTrue(results[-1].native_cache["cache_read_known"])

    def test_aborted_stream_does_not_publish_successful_predecessor(self):
        router = fixtures.NativeResponsesTests.router([self.response(), self.response()])
        cache = NativeCacheDiagnostics()

        async def run():
            first = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", True, "session", "cache", cache)
            await first.stream.__anext__()
            await first.stream.aclose()
            second = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", False, "session", "cache", cache)
            return first, second

        first, second = asyncio.run(run())
        self.assertFalse(first.native_cache["completed"])
        self.assertEqual(second.native_cache["comparison"], "no_predecessor")
        self.assertFalse(second.native_cache["overlap"])
        self.assertTrue(all(slot.in_flight == 0 for slot in router.slots))

    def test_failed_attempts_are_retained_without_cross_account_comparison(self):
        router = fixtures.NativeResponsesTests.router([
            fixtures.FakeResponse(status=500, text='{"error":{"code":"server_error"}}'), self.response(),
        ])
        result = asyncio.run(execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", False, "session", "cache"))
        self.assertEqual(result.status, 200)
        self.assertEqual(len(result.native_cache["attempts"]), 2)
        self.assertFalse(result.native_cache["attempts"][0]["completed"])
        self.assertEqual(result.native_cache["attempts"][0]["status"], 500)
        self.assertEqual(result.native_cache["attempts"][0]["error_code"], "server_error")
        self.assertNotEqual(result.native_cache["attempts"][0]["account_key"], result.native_cache["attempts"][1]["account_key"])
        self.assertEqual(result.native_cache["comparison"], "no_predecessor")

    def test_snapshot_failure_cannot_fail_inference(self):
        router = fixtures.NativeResponsesTests.router([self.response()])
        cache = NativeCacheDiagnostics()
        with patch.object(cache, "_shape", side_effect=RuntimeError("unavailable")):
            result = asyncio.run(execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", False, "session", "cache", cache))
        self.assertEqual(result.status, 200)
        self.assertEqual(result.native_cache["comparison"], "unknown")

    def test_api_credential_scope_and_legacy_log_are_separate(self):
        fixture = app_fixtures.AppCacheDiagnosticsTests()
        state = fixture.state()
        self.addCleanup(state.telemetry.close)
        state.session = app_fixtures.FakeOpenAISession({"object": "response", "status": "completed", "output": [], "usage": {"input_tokens": 10, "output_tokens": 1}})
        route = replace(fixture.route(), provider="openai", actual_model="openai/gpt-6-astra")
        body = {"model": "openai/gpt-6-astra", "input": "hi", "user": "session", "prompt_cache_key": "cache"}
        with patch.object(type(state.config), "api_key", return_value="private-key"), patch("spartan_route.app.provider_base_url", return_value="https://provider.invalid/v1"):
            first = asyncio.run(execute_provider_responses(state, body, route, False, "cache"))
            second = asyncio.run(execute_provider_responses(state, body, route, False, "cache"))
        self.assertEqual(second.native_cache["comparison"], "replay")
        self.assertFalse(first.native_cache["account_known"])
        self.assertFalse(first.native_cache["cache_read_known"])
        log_request(state, {"messages": [], "tools": []}, fixture.route(), second, time.monotonic(), "req", "session", "turn", "responses")
        context = json.loads(state.queue.entries[-1].context_info)
        self.assertEqual(context["native_cache"]["version"], 2)
        self.assertFalse(context["cache_likely"])
        self.assertIsNone(context["same_session_cache_trace"])
        self.assertEqual(state.recent_session_traces, {})
        self.assertNotIn("private-key", json.dumps(context))


class NativeCacheRollupTests(unittest.TestCase):
    def test_versions_rebuild_and_daily_reads_preserve_actual_usage(self):
        store = TelemetryStore(":memory:")
        self.addCleanup(store.close)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        for n, native in enumerate((None, {"version": 2, "comparison": "replay", "cache_write_tokens": 0, "cache_read_known": True}, {"version": 2, "comparison": "unknown"})):
            context = {"usage_seen": True, "cache_likely": native is None, "native_cache": native}
            store.log_routing(RoutingLogEntry(timestamp=f"{day}T09:00:0{n}Z", original_model="codex/gpt-6-astra", routed_model="codex/gpt-6-astra", actual_model="codex/gpt-6-astra", tier="complex", session_id="session", turn_id="turn", input_tokens=100, cached_input_tokens=50, context_info=json.dumps(context)))
        original = store.native_cache_daily_rows()
        self.assertEqual(original[0]["metrics"]["requests"], 2)
        self.assertEqual(original[0]["metrics"]["cacheWriteKnownRequests"], 1)
        self.assertEqual(original[0]["metrics"]["cacheWriteTokens"], 0)
        self.assertEqual(original[0]["metrics"]["inputTokens"], 200)
        self.assertEqual(original[0]["metrics"]["cacheReadInputTokens"], 100)
        self.assertEqual(original[0]["metrics"]["cacheReadMissingRequests"], 1)
        self.assertEqual(original[0]["metrics"]["cacheHitPercent"], 50.0)
        statements = []
        store._conn.set_trace_callback(statements.append)
        daily = store.daily_cache_rows()[0]
        store._conn.set_trace_callback(None)
        self.assertFalse(any("FROM ROUTING_LOG" in s.upper() for s in statements))
        self.assertEqual(daily["input_tokens"], 300)
        self.assertEqual(daily["cache_likely_requests"], 1)
        self.assertEqual(daily["nativeCache"]["requests"], 2)
        self.assertEqual(store.today_summary(day)["nativeCache"]["requests"], 2)
        self.assertEqual(store.live_sessions()["sessions"][0]["metrics"]["nativeCache"]["requests"], 2)
        store.enforce_routing_retention(force_rebuild=True)
        self.assertEqual(store.native_cache_daily_rows(), original)


if __name__ == "__main__":
    unittest.main()
