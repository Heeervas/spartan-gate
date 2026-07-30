from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest
from types import SimpleNamespace
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
        self.hooks: list[tuple[str, object]] = []

    def register_middleware(self, kind: str, callback: object) -> None:
        self.middleware.append((kind, callback))

    def register_hook(self, kind: str, callback: object) -> None:
        self.hooks.append((kind, callback))


class SpartanCacheLineagePluginTests(unittest.TestCase):
    def test_registers_llm_request_middleware(self) -> None:
        plugin = load_plugin()
        ctx = FakeContext()

        plugin.register(ctx)

        self.assertEqual(len(ctx.middleware), 1)
        self.assertEqual(ctx.middleware[0][0], "llm_request")
        self.assertEqual(ctx.hooks[0][0], "post_api_request")

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

    def test_codex_responses_normalizes_legacy_reasoning_effort_before_adapter(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "10" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data"):
            result = plugin._llm_request_middleware(
                request={"input": [], "reasoning_effort": "HIGH"},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )

        request = result["request"]
        self.assertNotIn("reasoning_effort", request)
        self.assertEqual(request["reasoning"], {"effort": "high"})

    def test_same_session_id_uses_distinct_chain_per_active_profile(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "11" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }

        def chain_for(home: str) -> str:
            with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value=home):
                result = plugin._llm_request_middleware(
                    request={"input": [{"role": "user", "content": "hello"}]},
                    session_id="shared-session",
                    base_url=env["OPENAI_BASE_URL"],
                    api_mode="codex_responses",
                )
            return result["request"]["extra_body"]["spartan_chain_id"]

        self.assertNotEqual(chain_for("/opt/data/profiles/work"), chain_for("/opt/data/profiles/reviewer"))

    def test_profile_switch_drops_foreign_reasoning_and_assistant_item_id(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "22" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/work"):
            foreign = plugin._reasoning_binding("same-session")
        reasoning = {
            "type": "reasoning",
            "encrypted_content": "opaque",
            "_spartan_chain_id": foreign["spartan_chain_id"],
            "_spartan_reasoning_realm": foreign["spartan_expected_realm"],
            "_spartan_binding_mac": foreign["spartan_binding_mac"],
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/reviewer"):
            result = plugin._llm_request_middleware(
                request={"input": [reasoning, {"type": "message", "id": "msg_old", "role": "assistant", "content": []}]},
                session_id="same-session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        self.assertEqual(result["reason"], "reasoning_chain_reset")
        self.assertEqual(result["request"]["input"], [{"type": "message", "role": "assistant", "content": []}])

    def test_post_hook_stamps_and_next_request_strips_private_metadata(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "33" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        class ReadOnlyResponse:
            def __init__(self) -> None:
                self.provider_data = {
                    "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "cipher"}],
                }

            @property
            def codex_reasoning_items(self):
                return self.provider_data["codex_reasoning_items"]

        message = ReadOnlyResponse()
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/work"):
            plugin._post_api_request(
                assistant_message=message,
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
            self.assertIn("_spartan_chain_id", message.codex_reasoning_items[0])
            result = plugin._llm_request_middleware(
                request={"input": message.codex_reasoning_items},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        replay = result["request"]["input"][0]
        self.assertEqual(replay, {"type": "reasoning", "encrypted_content": "cipher"})
        self.assertEqual(result["request"]["extra_body"]["spartan_expected_realm"], "codex_subscription")

    def test_missing_secret_injects_fail_closed_binding(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "",
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False):
            result = plugin._llm_request_middleware(
                request={"input": [{"role": "user", "content": "hello"}]},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        self.assertEqual(result["request"]["extra_body"]["spartan_chain_id"], "invalid")

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
