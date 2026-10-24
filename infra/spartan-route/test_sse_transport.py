import asyncio
import unittest

from spartan_route.codex import parse_sse_response
from test_codex import FakeResponse
import test_codex as fixtures
from spartan_route.native_responses import execute_codex_responses


class SSETransportTests(unittest.TestCase):
    def test_public_metadata_filter_preserves_raw_usage_unknowns_tools_and_terminal_errors(self):
        metadata = {"type": "response.metadata", "usage": {"input_tokens": 9, "output_tokens": 3}}
        tool = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "answer", "arguments": "{}"}
        for terminal in ("completed", "failed", "incomplete", "cancelled", "error"):
            with self.subTest(terminal=terminal):
                name = "error" if terminal == "error" else "response." + terminal
                ending = {"type": name, "response": {"status": terminal, "output": [tool]}}
                if terminal != "completed":
                    ending["error"] = {"code": "fixture_error", "message": "synthetic failure"}
                router = fixtures.NativeResponsesTests.router([FakeResponse(events=[
                    ("response.metadata", metadata),  # Precommit path, even before created.
                    ("response.created", {"type": "response.created", "response": {"output": []}}),
                    ("response.future_event", {"type": "response.future_event", "value": "preserved"}),
                    ("response.metadata", metadata),  # Already committed path.
                    (name, ending),
                ])])

                async def collect():
                    result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-sol", True, None, None)
                    return result, b"".join([chunk async for chunk in result.stream]).decode()

                result, raw = asyncio.run(collect())
                self.assertNotIn("response.metadata", raw)
                self.assertIn("response.future_event", raw)
                self.assertIn('"type":"' + name + '"', raw)
                self.assertEqual((result.input_tokens, result.output_tokens), (9, 3))
                self.assertTrue(result.usage_seen)
                self.assertEqual(result.completed, terminal == "completed")
                self.assertEqual(len(router.session.calls), 1)
                if terminal == "completed":
                    self.assertEqual(result.actual_tool_names, ["answer"])
                    self.assertTrue(result.tool_telemetry_complete)
                else:
                    self.assertIn("fixture_error", raw)
                    self.assertIsNotNone(result.error)

    def test_crlf_multiline_and_multiple_frames_in_one_chunk(self):
        raw = b'event: response.created\r\ndata: {"a":\r\ndata: 1}\r\n\r\ndata: [DONE]\n\n'
        async def collect():
            return [item async for item in parse_sse_response(FakeResponse(chunks=[bytes([byte]) for byte in raw]))]
        self.assertEqual(asyncio.run(collect()), [("response.created", '{"a":\n1}'), (None, "[DONE]")])

    def test_utf8_character_split_between_network_chunks(self):
        raw = 'event: response.output_text.delta\ndata: {"delta":"España"}\n\n'.encode()
        split = raw.index('ñ'.encode()) + 1

        async def collect():
            return [item async for item in parse_sse_response(FakeResponse(chunks=[raw[:split], raw[split:]]))]

        self.assertEqual(asyncio.run(collect()), [('response.output_text.delta', '{"delta":"España"}')])
