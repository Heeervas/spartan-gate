from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import (
    CodexAuth,
    CodexRouter,
    CodexSlot,
    build_content_prompt_cache_key,
    chat_messages_to_responses_input,
    normalize_reset_credits,
    resolve_auth_paths,
    resolve_prompt_cache_key,
)


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


if __name__ == "__main__":
    unittest.main()
