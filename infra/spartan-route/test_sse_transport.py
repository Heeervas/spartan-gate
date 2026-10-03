import asyncio
import unittest

from spartan_route.codex import parse_sse_response
from test_codex import FakeResponse


class SSETransportTests(unittest.TestCase):
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
