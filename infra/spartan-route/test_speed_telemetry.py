"""Passive speed observations, never inferred from request success or latency."""
import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_app as app_fixtures
import test_codex as codex_fixtures
from spartan_route.app import ExecutionResult, apply_usage, apply_responses_telemetry, copy_codex_telemetry, execute_chat, execute_responses, log_request, route_request
from spartan_route.codex import CodexCallResult
from spartan_route.native_responses import execute_codex_responses
from spartan_route.telemetry import TelemetryStore
from spartan_route.speed import speed_context


class SpeedTelemetryTests(unittest.TestCase):
    def test_fast_wire_alias_is_observed_as_priority_without_mutating_request(self):
        body = {"service_tier": "fast"}
        self.assertEqual(speed_context(body, "priority"), {"requested": "priority", "served": "priority"})
        self.assertEqual(speed_context(body, None), {"requested": "priority", "served": "unknown"})
        self.assertEqual(body, {"service_tier": "fast"})

    def test_native_and_converted_codex_capture_terminal_tier_in_both_modes(self):
        async def run(native, stream, served):
            events = [
                ("response.created", {"response": {"service_tier": "priority"}}),
                ("response.completed", {"response": {"status": "completed", "service_tier": served,
                    "output": [{"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "hello"}]}]}}),
            ]
            response = codex_fixtures.FakeResponse(events=events)
            router = codex_fixtures.NativeResponsesTests.router([response])
            if native:
                result = await execute_codex_responses(router, {"input": "hello", "service_tier": "priority"},
                    "codex/gpt-6-astra", stream, "session", None)
            elif stream:
                result = CodexCallResult(status=200)
                result.stream = router._codex_stream_to_chat(response, "gpt-6-astra", result, router.slots[0])
            else:
                result = await router._collect_codex_response(response, "gpt-6-astra", router.slots[0])
            if result.stream:
                async for _ in result.stream:
                    pass
            execution = ExecutionResult(200)
            copy_codex_telemetry(result, execution)
            return execution

        for native in (False, True):
            for stream in (False, True):
                for served in ("default", "priority", "ultrafast", None):
                    with self.subTest(native=native, stream=stream, served=served):
                        result = asyncio.run(run(native, stream, served))
                        self.assertEqual(result.served_service_tier, served)

    def test_openai_execution_captures_downgrade_without_changing_wire_tier(self):
        async def run(native, stream):
            fixture = app_fixtures.AppCacheDiagnosticsTests()
            state = fixture.state()
            try:
                payload = {"object": "response", "status": "completed", "service_tier": "default", "output": []}
                if stream:
                    events = [("response.created", {"response": {"service_tier": "priority"}}),
                              ("response.completed", {"response": payload})] if native else [
                                  (None, {"choices": [], "service_tier": "default"})]
                    response = codex_fixtures.FakeResponse(events=events)
                    response.headers = {"Content-Type": "text/event-stream"}
                    state.session = codex_fixtures.NativeResponsesTests.SequenceSession([response])
                else:
                    state.session = app_fixtures.FakeOpenAISession(body=payload)
                body = {"model": "openai/gpt-test", "service_tier": "priority", "stream": stream,
                        **({"input": []} if native else {"messages": []})}
                route = route_request(state.config, body)
                if native:
                    result = await execute_responses(state, body, route, stream, "session", None)
                else:
                    result = await execute_chat(state, body, route, stream, "session", None, "request")
                if result.stream:
                    async for _ in result.stream:
                        pass
                self.assertEqual(result.served_service_tier, "default")
                self.assertEqual(state.session.calls[0]["json"]["service_tier"], "priority")
                self.assertEqual(body["service_tier"], "priority")
            finally:
                state.telemetry.close()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"}):
            for native in (False, True):
                for stream in (False, True):
                    with self.subTest(native=native, stream=stream):
                        asyncio.run(run(native, stream))

    def test_openai_metadata_does_not_confirm_created_or_unknown_tiers(self):
        result = ExecutionResult(200)
        apply_responses_telemetry(result, {"response": {"service_tier": "priority"}}, "response.created")
        self.assertIsNone(result.served_service_tier)
        apply_responses_telemetry(result, {"response": {"status": "completed", "service_tier": "default"}}, "response.completed")
        self.assertEqual(result.served_service_tier, "default")
        for tier in ("ultrafast", "priority", "default", "auto", "flex", "scale", "<private>", {}, "x" * 10000):
            with self.subTest(tier=tier):
                chat = ExecutionResult(200)
                apply_usage(chat, {"service_tier": tier})
                self.assertEqual(chat.served_service_tier, tier if isinstance(tier, str) and tier in {
                    "ultrafast", "priority", "default", "auto", "flex", "scale"} else None)

    def test_logged_downgrade_mixed_and_history_aggregate_without_false_confirmation(self):
        fixture = app_fixtures.AppCacheDiagnosticsTests()
        state = fixture.state()
        self.addCleanup(state.telemetry.close)
        store = TelemetryStore(":memory:")
        self.addCleanup(store.close)
        cases = [({"service_tier": "priority"}, "default"),
                 ({"service_tier": "ultrafast"}, "ultrafast"), ({}, None),
                 ({"service_tier": "<private>"}, None)]
        for index, (body, served) in enumerate(cases):
            result = ExecutionResult(200)
            apply_usage(result, {"service_tier": served})
            log_request(state, body, fixture.route(), result, 0, str(index), "session", "turn", "chat_completions")
            entry = state.queue.entries[-1]
            if index == 3:
                self.assertNotIn("<private>", entry.context_info)
            store.log_routing(entry)
        historical = replace(state.queue.entries[-1], request_id="historical", context_info="{}")
        store.log_routing(historical)
        session = store.live_sessions()["sessions"][0]
        expected = {"requested": {"priority": 1, "ultrafast": 1, "unspecified": 1, "unknown": 2},
                    "served": {"default": 1, "ultrafast": 1, "unknown": 3}}
        self.assertEqual(session["metrics"]["speed"], expected)
        self.assertEqual(session["turns"][0]["metrics"]["speed"], expected)
        context = json.loads(state.queue.entries[0].context_info)
        self.assertEqual(context["speed"], {"requested": "priority", "served": "default"})


if __name__ == "__main__":
    unittest.main()
