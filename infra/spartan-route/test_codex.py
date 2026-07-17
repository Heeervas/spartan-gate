from __future__ import annotations

import asyncio
import base64
import os
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import (
    CodexAuth,
    CodexCallResult,
    CodexRouter,
    CodexSlot,
    build_codex_request_body,
    build_content_prompt_cache_key,
    chat_messages_to_responses_input,
    normalize_tool_choice,
    normalize_reset_credits,
    resolve_auth_paths,
    resolve_prompt_cache_key,
)


def sse_event(event: str | None, data: dict[str, Any]) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data_json(data)}\n\n".encode("utf-8")


def data_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


class FakeContent:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.index = 0

    async def iter_chunked(self, _size: int):
        while self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            yield chunk


class FailingContent:
    def __init__(self, exc: BaseException, chunks: list[bytes] | None = None):
        self.exc = exc
        self.chunks = chunks or []
        self.index = 0

    async def iter_chunked(self, _size: int):
        while self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            yield chunk
        raise self.exc


class FakeResponse:
    def __init__(
        self,
        events: list[tuple[str | None, dict[str, Any]]] | None = None,
        chunks: list[bytes] | None = None,
        status: int = 200,
        text: str = "",
    ):
        self.content = FakeContent(chunks if chunks is not None else [sse_event(event, data) for event, data in events or []])
        self.released = False
        self.status = status
        self._text = text

    def release(self) -> None:
        self.released = True

    async def text(self) -> str:
        return self._text


class FailingResponse(FakeResponse):
    def __init__(self, exc: BaseException, chunks: list[bytes] | None = None):
        self.content = FailingContent(exc, chunks)
        self.released = False
        self.status = 200
        self._text = ""


class CapturePostSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.timeout = None

    async def post(self, *_args, **kwargs):
        self.timeout = kwargs.get("timeout")
        return self.response


class FakeJsonContextResponse:
    def __init__(self, status: int, payload: dict[str, Any]):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None

    async def json(self):
        return self.payload


class FakeUsageSession:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.urls.append(url)
        if "rate-limit-reset-credits" in url:
            return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
        return FakeJsonContextResponse(200, {
            "primary_window": {"used_percent": 25, "reset_at": "2026-06-27T10:00:00Z"},
            "secondary_window": {"used_percent": 40, "reset_at": "2026-07-01T10:00:00Z"},
        })


class RetryRouter(CodexRouter):
    def __init__(self):
        self.slots = [
            CodexSlot(0, auth=CodexAuth("token-a", account_id="acct-a")),
            CodexSlot(1, auth=CodexAuth("token-b", account_id="acct-b")),
        ]
        self.leases = {}
        self.calls: list[int] = []

    async def choose_slot(self, _affinity_key, excluded_slots, excluded_accounts):
        for slot in self.slots:
            auth = slot.auth
            if not auth:
                continue
            if slot.index in excluded_slots or auth.account_key in excluded_accounts:
                continue
            return slot, auth
        return None

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, slot, _auth):
        self.calls.append(slot.index)
        if slot.index == 0:
            return CodexCallResult(
                status=502,
                body={"error": {"type": "upstream_error", "code": "codex_empty_response"}},
                error="codex_empty_response",
            )
        return CodexCallResult(status=200, body={"ok": True}, output_tokens=1, completed=True)


class StreamingCloseRouter(CodexRouter):
    def __init__(self):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.leases = {}
        self.refresh_locks = {}

    async def choose_slot(self, _affinity_key, _excluded_slots, _excluded_accounts):
        return self.slots[0], self.slots[0].auth

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, _slot, _auth):
        result = CodexCallResult(status=200)

        async def stream():
            try:
                yield b"data: {}\n\n"
            finally:
                result.input_tokens = 1000
                result.cached_input_tokens = 512
                result.output_tokens = 7
                result.usage_seen = True
                result.usage_event_type = "response.completed"
                result.completed = True

        result.stream = stream()
        return result


class CaptureModelNameRouter(CodexRouter):
    def __init__(self):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.leases = {}
        self.refresh_locks = {}
        self.telemetry = None
        self.captured: dict[str, Any] | None = None

    async def choose_slot(self, _affinity_key, _excluded_slots, _excluded_accounts):
        return self.slots[0], self.slots[0].auth

    async def _single_chat_call(self, body, model_id, model_name, _wants_stream, _slot, _auth):
        self.captured = {"body": body, "model_id": model_id, "model_name": model_name}
        return CodexCallResult(status=200, body={"ok": True}, completed=True)


class RecoveringRouter(CodexRouter):
    def __init__(self, *, transient_until: float = 0, rate_limited_until: float = 0):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.slots[0].transient_until = transient_until
        self.slots[0].rate_limited_until = rate_limited_until
        self.leases = {}
        self.refresh_locks = {}
        self.telemetry = None
        self._usage_cache = None
        self._availability_event = None
        self._availability_event_loop = None
        self.calls = 0

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, _slot, _auth):
        self.calls += 1
        return CodexCallResult(status=200, body={"ok": True}, output_tokens=1, completed=True)

    async def _single_image_call(self, _request, _slot, _auth):
        self.calls += 1
        return CodexCallResult(status=200, body={"data": [{"b64_json": "abc"}]}, completed=True)


class CodexTests(unittest.TestCase):
    def test_content_prompt_cache_key_is_stable_for_tool_order(self) -> None:
        base = {
            "messages": [{"role": "system", "content": "stable system"}],
            "tools": [
                {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
                {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
            ],
        }
        reordered = {
            **base,
            "tools": list(reversed(base["tools"])),
        }
        self.assertEqual(build_content_prompt_cache_key(base), build_content_prompt_cache_key(reordered))

    def test_explicit_prompt_cache_key_is_namespaced_and_hashed(self) -> None:
        key = resolve_prompt_cache_key({"prompt_cache_key": "session-1"}, None, None)
        self.assertIsNotNone(key)
        self.assertTrue(key.startswith("clawroute:"))
        self.assertNotIn("session-1", key)

    def test_resolve_auth_paths_matches_clawroute_contract(self) -> None:
        env = {"OPENAI_CODEX_AUTH_PATHS": "/a,/b/auth.json"}
        self.assertEqual(resolve_auth_paths(env), ["/a/auth.json", "/b/auth.json"])
        self.assertEqual(resolve_auth_paths({"OPENAI_CODEX_AUTH_PATH": "/c"}), ["/c/auth.json"])
        self.assertEqual(resolve_auth_paths({"OPENAI_CODEX_TOKEN": "tok"}), [])
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(resolve_auth_paths()[0].endswith(".codex/auth.json"))

    def image_router(self, response: FakeResponse) -> CodexRouter:
        router = CodexRouter.__new__(CodexRouter)
        router.session = CapturePostSession(response)
        router.telemetry = None
        router.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        router.leases = {}
        router.refresh_locks = {}
        return router

    def png_b64(self, width: int, height: int) -> str:
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + (b"\x00" * 160)
        return base64.b64encode(raw).decode("ascii")

    def test_codex_image_payload_uses_minimal_builtin_tool_shape(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        body = router._build_codex_image_body({"prompt": "draw a gate", "size": "1536x1024", "quality": "high"})

        self.assertEqual(body["model"], "gpt-5.5")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["input"][0]["type"], "message")
        tool = body["tools"][0]
        self.assertEqual(tool, {"type": "image_generation", "output_format": "png", "size": "1536x1024"})
        prompt_text = body["input"][0]["content"][-1]["text"]
        self.assertIn("Size: 1536x1024", prompt_text)
        self.assertIn("Quality: high", prompt_text)

    def test_codex_image_passes_through_non_exact_portrait_results(self) -> None:
        for width, height in ((864, 1821), (941, 1672)):
            with self.subTest(actual=f"{width}x{height}"):
                image = self.png_b64(width, height)
                router = self.image_router(FakeResponse([
                    ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
                ]))

                result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

                self.assertEqual(result.status, 200)
                self.assertEqual(result.body["data"][0]["b64_json"], image)
                self.assertEqual(result.body["data"][0]["actual_size"], f"{width}x{height}")

    def test_codex_image_passes_through_wrong_orientation_result(self) -> None:
        image = self.png_b64(1536, 1024)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a portrait gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)
        self.assertEqual(result.body["data"][0]["actual_size"], "1536x1024")

    def test_codex_image_passes_through_when_dimensions_are_unknown(self) -> None:
        image = base64.b64encode(b"not an image header").decode("ascii")
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)
        self.assertNotIn("actual_size", result.body["data"][0])

    def test_codex_image_empty_result_is_still_an_error(self) -> None:
        router = self.image_router(FakeResponse([
            ("response.completed", {"response": {"status": "completed", "output": []}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_image_empty")

    def test_codex_image_allows_matching_non_square_result(self) -> None:
        image = self.png_b64(1536, 1024)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1536x1024"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)

    def test_codex_image_allows_same_aspect_scaled_square_result(self) -> None:
        image = self.png_b64(1254, 1254)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1024"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["actual_size"], "1254x1254")

    def test_codex_image_http_error_empty_body_is_non_empty(self) -> None:
        router = self.image_router(FakeResponse(status=500, text=""))

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1024"}))

        self.assertEqual(result.status, 500)
        self.assertEqual(result.body["error"]["code"], "codex_500")
        self.assertEqual(result.body["error"]["message"], "Codex API error (500)")

    def test_chat_messages_to_responses_input_preserves_tools_and_images(self) -> None:
        output = chat_messages_to_responses_input([
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
            {"role": "assistant", "content": "done", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ])
        self.assertEqual(output[0]["role"], "user")
        self.assertEqual(output[1]["type"], "message")
        self.assertEqual(output[2]["type"], "function_call")
        self.assertEqual(output[3]["type"], "function_call_output")

    def test_build_codex_request_body_normalizes_chat_tool_choice(self) -> None:
        request = {
            "messages": [{"role": "user", "content": "use lookup"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
            "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        }
        body = build_codex_request_body(request, "gpt-5.4-mini", "cache")

        self.assertEqual(normalize_tool_choice(request["tool_choice"]), {"type": "function", "name": "lookup"})
        self.assertEqual(body["tool_choice"], {"type": "function", "name": "lookup"})

    def test_gpt56_max_is_sent_as_exact_reasoning_effort(self) -> None:
        body = build_codex_request_body(
            {
                "messages": [{"role": "user", "content": "solve"}],
                "reasoning_effort": " MAX ",
            },
            "gpt-5.6-sol",
            None,
        )

        self.assertEqual(body["reasoning"], {"effort": "max"})

    def test_normalize_reset_credits_builds_api_and_db_shapes(self) -> None:
        payload = {
            "available_count": 1,
            "credits": [
                {
                    "id": "credit-1",
                    "status": "available",
                    "type": "weekly",
                    "title": "Reset",
                    "expires_at": "2026-06-30T00:00:00Z",
                }
            ],
        }
        normalized = normalize_reset_credits(payload, "acct", 2)
        self.assertEqual(normalized["api"]["availableCount"], 1)
        self.assertEqual(normalized["api"]["slotIndex"], 2)
        self.assertEqual(normalized["api"]["credits"][0]["status"], "available")
        self.assertEqual(normalized["db"]["account_key"], "acct")
        self.assertEqual(normalized["db"]["credits"][0]["reset_type"], "weekly")

    def test_streaming_rate_limit_error_marks_slot_unavailable(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        router._mark_streaming_error(slot, {"type": "usage_limit_reached", "resets_in_seconds": 60})
        self.assertGreater(slot.rate_limited_until, time.time())

    def test_codex_event_parser_collects_done_text_without_delta(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        text_parts: list[str] = []
        chunks = router._apply_codex_event(
            "response.output_text.done",
            {"item_id": "msg_1", "text": "final"},
            text_parts,
            [],
            {},
            {},
            {},
            set(),
            set(),
            stream=True,
        )

        self.assertEqual(text_parts, ["final"])
        self.assertEqual(chunks, [{"content": "final"}])

    def test_codex_event_parser_maps_item_id_argument_done_to_call_id(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        tool_calls: dict[str, dict] = {}
        item_to_call: dict[str, str] = {}
        arg_delta_ids: set[str] = set()

        router._apply_codex_event(
            "response.output_item.added",
            {"item": {"id": "item_1", "type": "function_call", "call_id": "call_1", "name": "lookup"}},
            [],
            [],
            tool_calls,
            {},
            item_to_call,
            set(),
            arg_delta_ids,
        )
        router._apply_codex_event(
            "response.function_call_arguments.done",
            {"item_id": "item_1", "name": "lookup", "arguments": "{\"q\":\"x\"}"},
            [],
            [],
            tool_calls,
            {},
            item_to_call,
            set(),
            arg_delta_ids,
        )

        self.assertEqual(item_to_call["item_1"], "call_1")
        self.assertEqual(tool_calls["call_1"]["function"]["name"], "lookup")
        self.assertEqual(tool_calls["call_1"]["function"]["arguments"], "{\"q\":\"x\"}")

    async def collect(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        return await router._collect_codex_response(FakeResponse(events), "codex/gpt-5.5", CodexSlot(2))

    async def collect_stream(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        chunks = []
        async for chunk in router._codex_stream_to_chat(FakeResponse(events), "codex/gpt-5.5", result, slot):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks), result, slot

    async def collect_stream_response(self, response: FakeResponse, slot: CodexSlot | None = None):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        selected_slot = slot or CodexSlot(0)
        chunks = []
        async for chunk in router._codex_stream_to_chat(response, "codex/gpt-5.5", result, selected_slot):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks), result, selected_slot, response

    async def close_stream_early(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        response = FakeResponse(events)
        stream = router._codex_stream_to_chat(response, "codex/gpt-5.5", result, slot)
        await stream.__anext__()
        await stream.aclose()
        return result, response

    async def close_stream_after_chunk_buffered_terminal(self):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        response = FakeResponse(chunks=[b"".join([
            sse_event("response.output_text.delta", {"item_id": "msg_1", "delta": "visible"}),
            sse_event("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 120000,
                        "input_tokens_details": {"cached_tokens": 90000},
                        "output_tokens": 20,
                    },
                },
            }),
        ])])
        stream = router._codex_stream_to_chat(response, "codex/gpt-5.5", result, slot)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        return result, response

    def test_collects_output_item_done_message_without_delta(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.done", {
                "item": {
                    "id": "msg_1",
                    "type": "message",
                    "content": [{"type": "output_text", "text": "fallback text"}],
                },
            }),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 3}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["message"]["content"], "fallback text")
        self.assertEqual(result.output_tokens, 3)

    def test_eventless_completed_payload_uses_type_and_usage(self) -> None:
        result = asyncio_run(self.collect([
            (None, {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{
                        "id": "msg_1",
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }],
                    "usage": {
                        "input_tokens": 100,
                        "input_tokens_details": {"cached_tokens": 80},
                        "output_tokens": 12,
                    },
                },
            }),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.input_tokens, 100)
        self.assertEqual(result.cached_input_tokens, 80)
        self.assertEqual(result.output_tokens, 12)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.usage_event_type, "response.completed")

    def test_top_level_usage_is_collected_before_terminal_event(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_text.done", {
                "item_id": "msg_1",
                "text": "done",
                "usage": {
                    "input_tokens": 90,
                    "input_tokens_details": {"cached_tokens": 45},
                    "output_tokens": 6,
                },
            }),
            ("response.completed", {"response": {"status": "completed"}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.input_tokens, 90)
        self.assertEqual(result.cached_input_tokens, 45)
        self.assertEqual(result.output_tokens, 6)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.usage_event_type, "response.output_text.done")

    def test_collects_reasoning_text_done(self) -> None:
        result = asyncio_run(self.collect([
            ("response.reasoning_text.done", {"item_id": "rs_1", "content_index": 0, "text": "thinking"}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 9, "output_tokens": 2}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["message"]["reasoning_content"], "thinking")

    def test_raw_reasoning_only_gets_placeholder(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.done", {"item": {"id": "rs_raw_1", "type": "reasoning", "encrypted_content": "opaque"}}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 9, "output_tokens": 2}}}),
        ]))

        message = result.body["choices"][0]["message"]
        self.assertEqual(result.status, 200)
        self.assertEqual(message["content"], "<think> </think>")
        self.assertEqual(message["reasoning_content"], " ")

    def test_incomplete_maps_to_length_finish_reason(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_text.delta", {"item_id": "msg_1", "delta": "partial"}),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 7}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["finish_reason"], "length")
        self.assertEqual(result.body["choices"][0]["message"]["content"], "partial")

    def test_incomplete_tool_call_is_retryable_502(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ]))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_incomplete_response")
        self.assertEqual(result.error, "codex_incomplete_response")

    def test_streaming_buffers_incomplete_tool_call_until_terminal_success(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.function_call_arguments.delta", {"item_id": "fc_1", "delta": "{\"q\":\"unterminated"}),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ]))

        self.assertNotIn("tool_calls", body)
        self.assertIn("event: error", body)
        self.assertIn("codex_incomplete_response", body)
        self.assertEqual(result.error, "codex_incomplete_response")
        self.assertGreater(slot.transient_until, time.time())

    def test_streaming_emits_completed_tool_call_after_terminal_success(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.function_call_arguments.delta", {"item_id": "fc_1", "delta": "{\"q\":\"ok\"}"}),
            ("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 50000,
                        "input_tokens_details": {"cached_tokens": 30000},
                        "output_tokens": 3,
                    },
                },
            }),
        ]))

        self.assertIn("tool_calls", body)
        self.assertIn("\"arguments\":\"{\\\"q\\\":\\\"ok\\\"}\"", body)
        self.assertIn("data: [DONE]", body)
        self.assertIsNone(result.error)
        self.assertEqual(result.input_tokens, 50000)
        self.assertEqual(result.cached_input_tokens, 30000)
        self.assertEqual(result.output_tokens, 3)
        self.assertTrue(result.usage_seen)
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_drains_usage_after_client_stops_reading(self) -> None:
        result, response = asyncio_run(self.close_stream_early([
            ("response.output_text.delta", {"item_id": "msg_1", "delta": "visible"}),
            ("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 90000,
                        "input_tokens_details": {"cached_tokens": 60000},
                        "output_tokens": 10,
                    },
                },
            }),
        ]))

        self.assertTrue(response.released)
        self.assertEqual(result.input_tokens, 90000)
        self.assertEqual(result.cached_input_tokens, 60000)
        self.assertEqual(result.output_tokens, 10)
        self.assertTrue(result.usage_seen)
        self.assertTrue(result.completed)

    def test_streaming_drains_buffered_terminal_usage_after_client_stops_reading(self) -> None:
        result, response = asyncio_run(self.close_stream_after_chunk_buffered_terminal())

        self.assertTrue(response.released)
        self.assertEqual(result.input_tokens, 120000)
        self.assertEqual(result.cached_input_tokens, 90000)
        self.assertEqual(result.output_tokens, 20)
        self.assertTrue(result.usage_seen)
        self.assertTrue(result.completed)

    def test_assistant_reasoning_is_not_replayed_upstream_by_default(self) -> None:
        output = chat_messages_to_responses_input([
            {"role": "assistant", "content": "visible", "reasoning_content": "volatile reasoning"},
        ])

        self.assertEqual(output, [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "visible"}],
        }])

    def test_assistant_reasoning_replay_can_be_enabled_for_rollback(self) -> None:
        with patch.dict(os.environ, {"SPARTAN_ROUTE_REPLAY_ASSISTANT_REASONING": "1"}):
            output = chat_messages_to_responses_input([
                {"role": "assistant", "content": "visible", "reasoning_content": "reasoning"},
            ])

        self.assertEqual(output[0], {"type": "reasoning", "summary": [{"type": "summary_text", "text": "reasoning"}]})
        self.assertEqual(output[1]["type"], "message")

    def test_empty_successful_codex_response_is_retryable_502(self) -> None:
        result = asyncio_run(self.collect([
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 155000, "output_tokens": 0}}}),
        ]))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_empty_response")
        self.assertEqual(result.error, "codex_empty_response")

    def test_terminal_error_event_maps_to_non_200(self) -> None:
        result = asyncio_run(self.collect([
            ("error", {"error": {"type": "usage_limit_reached", "code": "usage_limit_reached", "message": "limit"}}),
        ]))

        self.assertEqual(result.status, 429)
        self.assertIn("error", result.body)

    def test_non_streaming_read_timeout_is_retryable_transport_error(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(2)

        result = asyncio_run(router._collect_codex_response(FailingResponse(asyncio.TimeoutError()), "codex/gpt-5.5", slot))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_stream_timeout")
        self.assertEqual(result.error, "codex_stream_timeout")
        self.assertFalse(result.usage_seen)
        self.assertEqual(slot.transient_until, 0)

    def test_non_streaming_read_exception_is_retryable_transport_error(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(2)

        result = asyncio_run(router._collect_codex_response(FailingResponse(RuntimeError("socket closed")), "codex/gpt-5.5", slot))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_stream_error")
        self.assertEqual(result.error, "codex_stream_error")
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_read_timeout_emits_error_without_marking_slot(self) -> None:
        body, result, slot, response = asyncio_run(self.collect_stream_response(FailingResponse(asyncio.TimeoutError())))

        self.assertIn("event: error", body)
        self.assertIn("codex_stream_timeout", body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(result.error, "codex_stream_timeout")
        self.assertEqual(result.output_tokens, 0)
        self.assertFalse(result.usage_seen)
        self.assertTrue(response.released)
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_read_exception_emits_error_without_marking_slot(self) -> None:
        body, result, slot, response = asyncio_run(self.collect_stream_response(FailingResponse(RuntimeError("socket closed"))))

        self.assertIn("event: error", body)
        self.assertIn("codex_stream_error", body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(result.error, "codex_stream_error")
        self.assertTrue(response.released)
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_terminal_server_error_marks_slot_transient(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("error", {"error": {"message": "upstream failed", "type": "server_error", "code": "server_error"}}),
        ]))

        self.assertIn("event: error", body)
        self.assertEqual(result.error, "server_error")
        self.assertGreater(slot.transient_until, time.time())

    def test_execute_chat_strips_codex_prefix_for_gpt56_variant(self) -> None:
        router = CaptureModelNameRouter()

        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "user", "content": "hi"}]},
            "codex/gpt-5.6-luna",
            False,
            "session",
            None,
            "request",
        ))

        self.assertEqual(result.status, 200)
        self.assertIsNotNone(router.captured)
        self.assertEqual(router.captured["model_id"], "codex/gpt-5.6-luna")
        self.assertEqual(router.captured["model_name"], "gpt-5.6-luna")
        self.assertEqual(router.captured["body"]["model"], "gpt-5.6-luna")

    def test_single_chat_call_uses_header_timeout_without_body_total_timeout(self) -> None:
        response = FakeResponse([
            ("response.output_text.done", {"item_id": "msg_1", "text": "done"}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ])
        router = CodexRouter.__new__(CodexRouter)
        router.session = CapturePostSession(response)

        result = asyncio_run(router._single_chat_call(
            {"input": [{"role": "user", "content": "hi"}], "stream": True},
            "codex/gpt-5.5",
            "gpt-5.5",
            False,
            CodexSlot(0),
            CodexAuth("token", account_id="acct"),
        ))

        self.assertEqual(result.status, 200)
        self.assertIsNotNone(router.session.timeout)
        self.assertIsNone(router.session.timeout.total)

    def test_all_transient_slots_return_retry_metadata(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        slot.transient_until = time.time() + 60
        router.slots = [slot]
        router.leases = {}
        router.telemetry = None

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 503)
        self.assertEqual(result.body["error"]["code"], "codex_transient_unavailable")
        self.assertGreater(result.body["error"]["retry_after_seconds"], 0)
        self.assertEqual(result.body["error"]["slot_diagnostics"]["transient_5xx"], 1)
        self.assertIn("Retry-After", result.headers)

    def test_all_transient_slots_wait_then_retry(self) -> None:
        router = RecoveringRouter(transient_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 200)
        self.assertEqual(router.calls, 1)

    def test_all_rate_limited_slots_return_429_metadata_when_wait_disabled(self) -> None:
        router = RecoveringRouter(rate_limited_until=time.time() + 60)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 429)
        self.assertEqual(result.body["error"]["code"], "codex_429")
        self.assertGreater(result.body["error"]["resets_in_seconds"], 0)
        self.assertEqual(result.body["error"]["slot_diagnostics"]["rate_limited"], 1)
        self.assertIn("Retry-After", result.headers)

    def test_all_rate_limited_slots_wait_then_retry(self) -> None:
        router = RecoveringRouter(rate_limited_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 200)
        self.assertEqual(router.calls, 1)

    def test_image_generation_uses_recovery_wait(self) -> None:
        router = RecoveringRouter(transient_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_image({"prompt": "draw a gate"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(router.calls, 1)

    def test_codex_empty_response_is_retryable(self) -> None:
        from spartan_route.codex import should_retry

        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_empty_response"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_incomplete_response"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_stream_timeout"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_stream_error"}})))
        self.assertFalse(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_image_empty"}})))

    def test_execute_chat_retries_empty_response_and_remembers_successful_slot(self) -> None:
        router = RetryRouter()
        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}]},
            "codex/gpt-5.5",
            False,
            "session",
            None,
            "request",
        ))

        self.assertEqual(router.calls, [0, 1])
        self.assertEqual(result.status, 200)
        self.assertEqual(result.slot_index, 1)
        self.assertEqual(len(router.leases), 1)
        self.assertEqual(next(iter(router.leases.values())).slot_index, 1)
        self.assertGreater(router.slots[0].transient_until, time.time())

    def test_execute_chat_stream_close_preserves_usage_from_inner_stream(self) -> None:
        router = StreamingCloseRouter()
        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "user", "content": "hi"}]},
            "codex/gpt-5.4-mini",
            True,
            "session",
            "cache",
            "request",
        ))

        async def close_after_first_chunk():
            stream = result.stream
            self.assertIsNotNone(stream)
            await stream.__anext__()
            await stream.aclose()

        asyncio_run(close_after_first_chunk())

        self.assertEqual(result.input_tokens, 1000)
        self.assertEqual(result.cached_input_tokens, 512)
        self.assertEqual(result.output_tokens, 7)
        self.assertTrue(result.usage_seen)
        self.assertEqual(router.slots[0].in_flight, 0)
        self.assertEqual(next(iter(router.leases.values())).slot_index, 0)

    def test_disabled_slot_is_not_selected(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.session = None
            router.telemetry = store
            router.slots = [
                CodexSlot(0, auth=CodexAuth("token-a", account_id="acct-a")),
                CodexSlot(1, auth=CodexAuth("token-b", account_id="acct-b")),
            ]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            asyncio_run(router.set_slot_routing_enabled(0, False))
            chosen = asyncio_run(router.choose_slot(None, set(), set()))
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen[0].index, 1)
        finally:
            store.close()

    def test_usage_snapshot_uses_short_lived_cache(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            first = asyncio_run(router.usage_snapshot())
            second = asyncio_run(router.usage_snapshot())
            self.assertFalse(first["cached"])
            self.assertEqual(first["accounts"][0]["slotPaths"], ["/codex-a/auth.json"])
            self.assertTrue(second["cached"])
            self.assertEqual(second["accounts"][0]["slotPaths"], ["/codex-a/auth.json"])
            self.assertEqual(len(session.urls), 2)

            forced = asyncio_run(router.usage_snapshot(force=True))
            self.assertFalse(forced["cached"])
            self.assertEqual(len(session.urls), 4)
        finally:
            store.close()

    def test_forced_slot_usage_refresh_clears_recovered_rate_limit(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))]
            router.slots[0].rate_limited_until = time.time() + 60
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None
            router._availability_event = None
            router._availability_event_loop = None

            payload = asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))

            self.assertFalse(payload["partial"])
            self.assertEqual(router.slots[0].rate_limited_until, 0)
        finally:
            store.close()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
