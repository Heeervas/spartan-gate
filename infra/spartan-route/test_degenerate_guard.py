"""Synthetic Codex tool streams; no captured arguments or inference."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from spartan_route.codex import CodexCallResult
from spartan_route.native_responses import execute_codex_responses
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from spartan_route.codex import parse_sse_response
from spartan_route.tool_guard import DegenerateToolArguments
import test_codex as fixtures


def tool_events(parts):
    yield "response.output_item.added", {"item": {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "edit", "arguments": ""}}
    for part in parts:
        yield "response.function_call_arguments.delta", {"item_id": "fc_test", "delta": part}
    yield "response.completed", {"response": {"status": "completed", "output": []}}


class DegenerateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, parts, native=True):
        upstream = fixtures.FakeResponse(events=list(tool_events(parts)))
        upstream.headers = {"x-request-id": "synthetic-request"}
        router = fixtures.NativeResponsesTests.router([upstream])
        if native:
            result = await execute_codex_responses(router, {"input": "synthetic"}, "codex/gpt-6.1-sol", True, None, None)
            stream = result.stream
        else:
            result = CodexCallResult(status=200)
            stream = router._codex_stream_to_chat(upstream, "codex/gpt-6.1-sol", result, router.slots[0])
        chunks = [chunk async for chunk in stream]
        if result.stream_cleanup:
            await result.stream_cleanup()
        return upstream, result, b"".join(chunks).decode()

    async def test_degenerate_tail_fails_before_completion_and_releases_once_logged(self):
        for native in (True, False):
            with self.subTest(native=native), patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES="1024"):
                with self.assertLogs("spartan_route", level="WARNING") as logs:
                    upstream, result, text = await asyncio.wait_for(self.run_stream(['{"secret":"never-log-this"', *(['   \n'] * 1000)], native), 2)
                    self.assertEqual(result.error, "codex_tool_arguments_degenerate")
                self.assertFalse(result.completed)
                self.assertTrue(upstream.released)
                self.assertLess(upstream.content.index, 270)
                self.assertIn("server_error", text)
                self.assertIn("tool argument whitespace", text)
                self.assertNotIn("response.completed", text)
                self.assertNotIn('"finish_reason":"tool_calls"', text)
                self.assertEqual(len(logs.output), 1)
                self.assertIn("whitespace_bytes=1025", logs.output[0])
                self.assertIn("duration_ms=", logs.output[0])
                self.assertIn("gpt-6.1-sol", logs.output[0])
                self.assertIn('request_id="synthetic-request"', logs.output[0])
                self.assertNotIn("never-log-this", logs.output[0])
                self.assertNotIn("fc_test", logs.output[0])

    async def test_fragmented_escaped_strings_and_long_legitimate_spaces_complete(self):
        arguments = json.dumps({"code": 'quote " slash \\ unicode ☃ ' + ' ' * 5000 + '\nend'})
        with patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES="256"):
            for native in (True, False):
                with self.subTest(native=native):
                    upstream, result, text = await self.run_stream(list(arguments), native)
                    self.assertTrue(upstream.released)
                    self.assertIsNone(result.error)
                    self.assertTrue(result.completed)
                    self.assertNotIn("codex_tool_arguments_degenerate", text)

    async def test_exact_limit_progress_reset_initial_arguments_and_call_isolation(self):
        events = [("response.output_item.added", {"item": {"type": "function_call", "id": "a", "arguments": '{"code":"'}})]
        events += [("response.function_call_arguments.delta", {"item_id": "b", "delta": ' ' * 256})]
        events += [("response.function_call_arguments.delta", {"item_id": "a", "delta": ' ' * 600 + '\\"\\\\' + ' ' * 600 + '"'})]
        events += [("response.function_call_arguments.delta", {"item_id": "b", "delta": ','})]
        for item in ("a", "b", "a", "b"):
            events.append(("response.function_call_arguments.delta", {"item_id": item, "delta": ' ' * 256 + ','}))
        upstream = fixtures.FakeResponse(events=events)
        with patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES="256"):
            observed = [event async for event in parse_sse_response(upstream, "synthetic")]
        self.assertEqual(len(observed), len(events))
        upstream = fixtures.FakeResponse(events=[
            ("response.output_item.added", {"item": {"type": "function_call", "id": "a", "arguments": '{"v":"'}}),
            ("response.function_call_arguments.delta", {"item_id": "b", "delta": ' ' * 257}),
        ])
        with patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES="256"), self.assertLogs("spartan_route", level="WARNING"):
            with self.assertRaises(DegenerateToolArguments):
                _ = [event async for event in parse_sse_response(upstream)]
        self.assertTrue(upstream.released)

    async def test_default_invalid_and_configured_limits_on_direct_event_transport(self):
        class DirectResponse:
            released = False

            def release(self):
                self.released = True

            async def iter_events(self):
                for event, payload in tool_events([' ' * 1024, ' ']):
                    yield event, json.dumps(payload)

        for configured, consumed in (("invalid", 2), ("1024", 2), ("2048", 4)):
            with self.subTest(configured=configured), patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES=configured):
                response = DirectResponse()
                seen = []
                if configured == "2048":
                    seen = [event async for event in parse_sse_response(response)]
                    self.assertFalse(response.released)
                else:
                    with self.assertLogs("spartan_route", level="WARNING"), self.assertRaises(DegenerateToolArguments):
                        async for event in parse_sse_response(response):
                            seen.append(event)
                    self.assertTrue(response.released)
                self.assertEqual(len(seen), consumed)

    async def test_live_synthetic_http_connection_closes_without_reading_the_tail(self):
        disconnected = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            try:
                for event, payload in tool_events(['{"key":"private"', *([' ' * 64] * 1000)]):
                    await response.write(fixtures.sse_event(event, payload))
                    await asyncio.sleep(0.001)
            except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
                disconnected.set()
            return response

        app = web.Application()
        app.router.add_get("/", upstream)
        with patch.dict(os.environ, SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES="256"):
            async with TestServer(app) as server, ClientSession() as client:
                response = await client.get(server.make_url("/"))
                with self.assertLogs("spartan_route", level="WARNING") as logs:
                    with self.assertRaises(DegenerateToolArguments):
                        async with asyncio.timeout(2):
                            _ = [event async for event in parse_sse_response(response, "synthetic")]
                self.assertTrue(response.closed)
                await asyncio.wait_for(disconnected.wait(), 2)
                self.assertEqual(len(logs.output), 1)
                self.assertNotIn("private", logs.output[0])
