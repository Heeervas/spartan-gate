from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest
from unittest.mock import patch


PLUGIN_PATH = pathlib.Path(__file__).parent / "spartan-cache-lineage" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("spartan_cache_lineage", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module._reset_for_tests()
    return module


class FakeContext:
    def __init__(self) -> None:
        self.middleware: list[tuple[str, object]] = []

    def register_middleware(self, kind: str, callback: object) -> None:
        self.middleware.append((kind, callback))


class SpartanCacheLineagePluginTests(unittest.TestCase):
    def test_registers_llm_request_middleware(self) -> None:
        plugin = load_plugin()
        ctx = FakeContext()

        plugin.register(ctx)

        self.assertEqual(len(ctx.middleware), 1)
        self.assertEqual(ctx.middleware[0][0], "llm_request")

    def test_injects_cache_key_lineage_and_reasoning_for_spartan_route(self) -> None:
        plugin = load_plugin()
        request = {
            "model": "custom-1/clawroute/auto",
            "messages": [{"role": "user", "content": "hello"}],
            "extra_body": {"reasoning": {"effort": "minimal"}},
        }

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request=request,
                session_id="session-123",
                base_url="http://spartan-route:18790/v1",
                api_mode="chat_completions",
            )

        self.assertIsInstance(result, dict)
        updated = result["request"]
        self.assertEqual(updated["extra_body"]["prompt_cache_key"], "hermes:session-123")
        self.assertEqual(updated["extra_body"]["spartan_cache_lineage"], 0)
        self.assertEqual(updated["extra_body"]["spartan_cache_lineage_reason"], "baseline")
        self.assertEqual(updated["reasoning_effort"], "low")

    def test_tracks_retries_prefixes_rewrites_and_tool_changes(self) -> None:
        plugin = load_plugin()

        def lineage(messages, tools=None):
            with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
                result = plugin._llm_request_middleware(
                    request={
                        "messages": messages,
                        "tools": tools or [{"type": "function", "function": {"name": "read_file"}}],
                    },
                    session_id="session",
                    base_url="http://spartan-route:18790/v1",
                    api_mode="chat_completions",
                )
            return (
                result["request"]["extra_body"]["spartan_cache_lineage"],
                result["request"]["extra_body"]["spartan_cache_lineage_reason"],
            )

        base = [{"role": "system", "content": "stable"}, {"role": "user", "content": "first"}]

        self.assertEqual(lineage(base), (0, "baseline"))
        self.assertEqual(lineage([*base, {"role": "assistant", "content": "answer"}]), (0, "prefix"))
        self.assertEqual(lineage([*base, {"role": "assistant", "content": "answer"}]), (0, "retry"))
        self.assertEqual(lineage([{"role": "system", "content": "changed"}, {"role": "user", "content": "first"}]), (1, "history_rewrite"))
        self.assertEqual(
            lineage(
                [{"role": "system", "content": "changed"}, {"role": "user", "content": "first"}],
                tools=[{"type": "function", "function": {"name": "write_file"}}],
            ),
            (2, "tool_schema_changed"),
        )

    def test_preserves_existing_prompt_cache_key_and_does_not_store_raw_key(self) -> None:
        plugin = load_plugin()
        request = {
            "messages": [{"role": "user", "content": "secret prompt"}],
            "extra_body": {"prompt_cache_key": "raw-secret-cache-key"},
        }

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request=request,
                session_id="session",
                base_url="http://spartan-route:18790/v1",
                api_mode="chat_completions",
            )

        self.assertEqual(result["request"]["extra_body"]["prompt_cache_key"], "raw-secret-cache-key")
        serialized_state = repr(plugin._state)
        self.assertNotIn("raw-secret-cache-key", serialized_state)
        self.assertNotIn("secret prompt", serialized_state)

    def test_ignores_direct_provider_requests(self) -> None:
        plugin = load_plugin()

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request={"messages": [{"role": "user", "content": "hello"}]},
                session_id="session",
                base_url="https://api.openai.com/v1",
                api_mode="chat_completions",
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
