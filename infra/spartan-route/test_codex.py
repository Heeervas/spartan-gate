from __future__ import annotations

import os
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


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {data_json(data)}\n\n".encode("utf-8")


def data_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


class FakeContent:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, events: list[tuple[str, dict[str, Any]]]):
        self.content = FakeContent([sse_event(event, data) for event, data in events])
        self.released = False

    def release(self) -> None:
        self.released = True


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

    async def collect(self, events: list[tuple[str, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        return await router._collect_codex_response(FakeResponse(events), "codex/gpt-5.5", 2)

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

    def test_codex_empty_response_is_retryable(self) -> None:
        from spartan_route.codex import should_retry

        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_empty_response"}})))

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


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
