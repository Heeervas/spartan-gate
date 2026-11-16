"""Synthetic Codex tool streams; no captured arguments or inference."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from spartan_route.codex import CodexCallResult
from spartan_route.native_responses import execute_codex_responses
from test_codex import FakeResponse, NativeResponsesTests


def tool_events(parts):
    yield "response.output_item.added", {"item": {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "edit", "arguments": ""}}
    for part in parts:
        yield "response.function_call_arguments.delta", {"item_id": "fc_test", "delta": part}
    yield "response.completed", {"response": {"status": "completed", "output": []}}


class DegenerateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, parts, native=True):
        upstream = FakeResponse(events=list(tool_events(parts)))
        router = NativeResponsesTests.router([upstream])
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
                self.assertIn("model=", logs.output[0])
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
