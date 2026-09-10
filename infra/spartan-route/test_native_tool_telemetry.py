from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import CodexCallResult
from spartan_route.native_responses import capture_telemetry, execute_codex_responses
from spartan_route.telemetry import RoutingLogEntry, TelemetryStore
import test_codex as fixtures
import test_app as app_fixtures
from spartan_route.app import ExecutionResult, apply_responses_telemetry, log_request, sync_codex_stream_result


class NativeToolTelemetryTests(unittest.TestCase):
    def events(self, terminal_output=None):
        item = {"type": "function_call", "id": "item-a", "call_id": "call-a", "name": "read_file"}
        return [
            ("response.created", {"response": {"output": []}}),
            ("response.output_item.added", {"output_index": 0, "item": item}),
            ("response.output_item.done", {"output_index": 0, "item": item}),
            ("response.completed", {"response": {"status": "completed", "output": terminal_output or []}}),
        ]

    def test_item_events_survive_empty_terminal_output_in_both_modes(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                router = fixtures.NativeResponsesTests.router([fixtures.FakeResponse(events=self.events())])

                async def run():
                    result = await execute_codex_responses(
                        router, {"input": "hello"}, "codex/gpt-6-astra", stream, "session", None,
                    )
                    if result.stream:
                        async for _ in result.stream:
                            pass
                    return result

                result = asyncio.run(run())
                self.assertEqual(result.actual_tool_call_count, 1)
                self.assertEqual(result.actual_tool_names, ["read_file"])
                self.assertTrue(result.tool_telemetry_complete)

    def test_malformed_stream_remains_partial_after_terminal_success(self):
        chunks = [fixtures.sse_event(event, payload) for event, payload in self.events()]
        chunks.insert(2, b"event: response.output_item.done\ndata: {broken\n\n")
        for stream in (False, True):
            with self.subTest(stream=stream):
                router = fixtures.NativeResponsesTests.router([fixtures.FakeResponse(chunks=chunks)])

                async def run():
                    result = await execute_codex_responses(router, {"input": "hello"},
                                                          "codex/gpt-6-astra", stream, "session", None)
                    if result.stream:
                        async for _ in result.stream:
                            pass
                    return result

                result = asyncio.run(run())
                self.assertEqual(result.actual_tool_call_count, 1)
                self.assertFalse(result.tool_telemetry_complete)

    def test_repeated_items_and_terminal_output_are_deduplicated(self):
        output = [
            {"type": "function_call", "id": "item-a", "call_id": "call-a", "name": "read_file"},
            {"type": "function_call", "id": "item-b", "call_id": "call-b", "name": "read_file"},
            {"type": "custom_tool_call", "id": "item-c", "name": "patch"},
            {"type": "web_search_call", "id": "item-d"},
        ]
        result = CodexCallResult(status=200)
        for event, payload in self.events(output):
            capture_telemetry(result, payload, event)
        self.assertEqual(result.actual_tool_call_count, 4)
        self.assertEqual(result.actual_tool_names, ["patch", "read_file", "web_search"])

    def test_zero_requires_completion_and_interrupted_calls_are_partial(self):
        result = CodexCallResult(status=200)
        capture_telemetry(result, {"response": {"output": []}}, "response.created")
        self.assertIsNone(result.actual_tool_call_count)
        self.assertFalse(result.tool_telemetry_complete)
        capture_telemetry(result, {"response": {"status": "completed", "output": []}}, "response.completed")
        self.assertEqual(result.actual_tool_call_count, 0)
        self.assertTrue(result.tool_telemetry_complete)
        interrupted = CodexCallResult(status=200)
        for event, payload in self.events()[:-1]:
            capture_telemetry(interrupted, payload, event)
        self.assertEqual(interrupted.actual_tool_call_count, 1)
        self.assertFalse(interrupted.tool_telemetry_complete)

    def test_stream_finalization_persists_tool_completeness(self):
        source = CodexCallResult(status=200)
        target = ExecutionResult(status=200)

        async def stream():
            for event, payload in self.events():
                capture_telemetry(source, payload, event)
                yield b"event"

        async def run():
            source.stream = stream()
            async for _ in sync_codex_stream_result(source, target):
                pass

        asyncio.run(run())
        fixture = app_fixtures.AppCacheDiagnosticsTests()
        state = fixture.state()
        self.addCleanup(state.telemetry.close)
        log_request(state, {"input": "hello"}, fixture.route(), target, time.monotonic(),
                    "request", "session", "turn", "responses")
        entry = state.queue.entries[0]
        self.assertEqual(entry.actual_tool_call_count, 1)
        self.assertTrue(json.loads(entry.context_info)["tool_telemetry_complete"])
        state.telemetry.log_routing(entry)
        self.assertEqual(state.telemetry.live_sessions()["sessions"][0]["metrics"]["actualToolCalls"], 1)

    def test_api_responses_share_item_counting_and_usage_capture(self):
        result = ExecutionResult(status=200)
        for event, payload in self.events():
            apply_responses_telemetry(result, payload, event)
        self.assertEqual(result.actual_tool_call_count, 1)
        self.assertTrue(result.tool_telemetry_complete)
        complete = ExecutionResult(status=200)
        apply_responses_telemetry(complete, {
            "object": "response", "status": "completed",
            "output": [{"type": "function_call", "id": "a", "name": "lookup"}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }, "response.completed")
        self.assertEqual(complete.actual_tool_names, ["lookup"])
        self.assertTrue(complete.usage_seen)

    def test_unidentifiable_call_cannot_produce_verified_zero(self):
        result = CodexCallResult(status=200)
        capture_telemetry(result, {"item": {"type": "function_call", "name": "lookup"}}, "response.output_item.done")
        capture_telemetry(result, {"response": {"status": "completed", "output": []}}, "response.completed")
        self.assertFalse(result.tool_telemetry_complete)
        self.assertIsNone(result.actual_tool_call_count)

    def test_historical_zeros_and_partial_counts_are_honest(self):
        store = TelemetryStore(":memory:")
        self.addCleanup(store.close)
        for n, (count, context) in enumerate([
            (0, {}),
            (0, {"tool_telemetry_version": 2, "tool_telemetry_complete": True}),
            (2, {"tool_telemetry_version": 2, "tool_telemetry_complete": False}),
            (1, {"tool_telemetry_version": 2, "tool_telemetry_complete": True}),
        ]):
            store.log_routing(RoutingLogEntry(
                timestamp=f"2026-09-10T09:00:0{n}Z", original_model="codex/gpt-6-astra",
                routed_model="codex/gpt-6-astra", actual_model="codex/gpt-6-astra", tier="complex",
                request_api_kind="responses", actual_tool_call_count=count, session_id="session",
                turn_id="turn", context_info=json.dumps(context),
            ))
        rows = store.turn_requests("turn")["requests"]
        self.assertIsNone(rows[0]["actualToolCallCount"])
        self.assertEqual(rows[1]["actualToolCallCount"], 0)
        self.assertFalse(rows[2]["toolTelemetryComplete"])
        metrics = store.live_sessions()["sessions"][0]["metrics"]
        self.assertEqual(metrics["actualToolCalls"], 3)
        self.assertEqual(metrics["toolTelemetryKnownRequests"], 2)
        self.assertEqual(metrics["toolTelemetryPartialRequests"], 1)
        self.assertEqual(metrics["toolTelemetryUnknownRequests"], 1)


if __name__ == "__main__":
    unittest.main()
