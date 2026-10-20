"""Contract tests for the shared MASTER Hermes plugin."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_PATH = Path(__file__).parent / "shared-master" / "__init__.py"
spec = importlib.util.spec_from_file_location("shared_master_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class FakeContext:
    def __init__(self):
        self.section = None
        self.hooks = {}
        self.middleware = {}

    def register_system_prompt_section(self, id, content, *, position, max_chars):
        self.section = (id, content, position, max_chars)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback


class SharedMasterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "MASTER.md"
        self.path.write_text("One shared rule.\nSecond rule.", encoding="utf-8")
        path_patch = patch.object(plugin, "MASTER_PATH", self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def context(self):
        ctx = FakeContext()
        plugin.register(ctx)
        self.assertEqual(ctx.section[0], "shared-master")
        self.assertEqual(ctx.section[2:], ("after_memory", 4000))
        return ctx

    def request(self, ctx, payload, mode="chat_completions", profile="work"):
        with patch.object(plugin, "_active_profile", return_value=profile):
            result = ctx.middleware["llm_request"](request=payload, api_mode=mode)
        return result["request"] if result else payload

    def test_native_section_includes_full_text_only_for_eligible_profiles(self):
        section = self.context().section[1]
        self.assertEqual(section({"profile_name": "default"}), "One shared rule.\nSecond rule.")
        self.assertEqual(section({"profile_name": "builder"}), "One shared rule.\nSecond rule.")
        for profile in ("empty", "workstation", "ssr", "ssr_synthetic_consumer", "ssr-test"):
            with self.subTest(profile=profile):
                self.assertEqual(section({"profile_name": profile}), "")

    def test_native_section_is_not_doubled_on_request_or_retry(self):
        ctx = self.context()
        native = (
            "Identity\n\n<!-- hermes-plugin-sections:start -->\n"
            + plugin._native_block("One shared rule.\nSecond rule.")
            + "\n<!-- hermes-plugin-sections:end -->\n\nConversation started: today"
        )
        payload = {"messages": [{"role": "system", "content": native}, {"role": "user", "content": "Hi"}]}
        self.assertIs(self.request(ctx, payload), payload)
        self.assertIs(self.request(ctx, payload), payload)

    def test_native_section_after_another_plugin_is_not_doubled(self):
        ctx = self.context()
        other = plugin._native_block("Other rule.").replace("shared-master", "other-plugin")
        native = plugin._native_block("One shared rule.\nSecond rule.")
        system = (
            "<!-- hermes-plugin-sections:start -->\n" + other + "\n\n" + native
            + "\n<!-- hermes-plugin-sections:end -->"
        )
        payload = {"messages": [{"role": "system", "content": system}]}
        self.assertIs(self.request(ctx, payload), payload)

    def test_copied_native_section_inside_another_plugin_does_not_suppress_policy(self):
        ctx = self.context()
        native = plugin._native_block("One shared rule.\nSecond rule.")
        quoted = plugin._native_block("Page quote:\n" + native).replace("shared-master", "other-plugin", 1)
        system = (
            "<!-- hermes-plugin-sections:start -->\n" + quoted
            + "\n<!-- hermes-plugin-sections:end -->"
        )
        amended = self.request(ctx, {"messages": [{"role": "system", "content": system}]})
        self.assertIn(plugin.FALLBACK_MARKER, amended["messages"][0]["content"])

    def test_untrusted_quote_or_incomplete_native_block_does_not_suppress_policy(self):
        ctx = self.context()
        full = plugin._native_block("One shared rule.\nSecond rule.")
        for messages in (
            [{"role": "system", "content": "Identity"}, {"role": "user", "content": full}],
            [{"role": "system", "content": "Identity"}, {"role": "tool", "content": full}],
            [{"role": "system", "content": "Identity\n\n" + full[:-4]}],
            [{"role": "system", "content": "Identity\n\n" + full.replace("Second rule.", "Altered rule.")}],
            [{"role": "system", "content": "Quoted page text:\n" + full}],
            [{"role": "system", "content": "Identity\n\n" + plugin.FALLBACK_MARKER + "One shared rule."}],
        ):
            with self.subTest(messages=messages):
                amended = self.request(ctx, {"messages": messages})
                self.assertIn("One shared rule.\nSecond rule.", amended["messages"][0]["content"])
                self.assertIn(plugin.FALLBACK_MARKER, amended["messages"][0]["content"])

    def test_chat_fallback_preserves_roles_and_is_idempotent(self):
        ctx = self.context()
        original = {"messages": [{"role": "developer", "content": "Developer rule"}, {"role": "user", "content": "Hi"}], "model": "x"}
        amended = self.request(ctx, original)
        self.assertEqual(original["messages"][0]["role"], "developer")
        self.assertEqual(amended["messages"][0]["role"], "system")
        self.assertEqual(amended["messages"][1:], original["messages"])
        self.assertEqual(amended["messages"][0]["content"].count("One shared rule."), 1)
        self.assertIs(self.request(ctx, amended), amended)

    def test_responses_fallback_preserves_existing_instructions(self):
        ctx = self.context()
        payload = {"instructions": "Existing instruction", "input": [{"role": "user", "content": "Hi"}]}
        amended = self.request(ctx, payload, "codex_responses")
        self.assertTrue(amended["instructions"].startswith("Existing instruction\n\n"))
        self.assertTrue(amended["instructions"].endswith("One shared rule.\nSecond rule."))
        self.assertIs(amended["input"], payload["input"])
        self.assertIs(self.request(ctx, amended, "codex_responses"), amended)

    def test_anthropic_and_bedrock_blocks_preserve_existing_system(self):
        ctx = self.context()
        for mode, block in (("anthropic_messages", {"type": "text", "text": "Existing"}),
                            ("bedrock_converse", {"text": "Existing"})):
            with self.subTest(mode=mode):
                original = {"system": [block], "messages": [{"role": "user", "content": "Hi"}]}
                amended = self.request(ctx, original, mode)
                self.assertEqual(amended["system"][0], block)
                self.assertIn("One shared rule.", amended["system"][1]["text"])
                self.assertEqual(len(original["system"]), 1)
                self.assertIs(self.request(ctx, amended, mode), amended)

    def test_oversized_master_kept_complete_via_middleware(self):
        content = "x" * 8200
        self.path.write_text(content, encoding="utf-8")
        ctx = self.context()
        self.assertEqual(ctx.section[1]({"profile_name": "work"}), "")
        payload = {"instructions": "Existing", "input": []}
        amended = self.request(ctx, payload, "codex_responses")
        self.assertIn(content, amended["instructions"])
        self.assertEqual(amended["instructions"].count(content), 1)
        self.assertIn("Mention this once at session start", amended["instructions"])

    def test_exact_4000_character_boundary_needs_no_advisory(self):
        content = "x" * 4000
        self.path.write_text(content, encoding="utf-8")
        ctx = self.context()
        self.assertEqual(ctx.section[1]({"profile_name": "work"}), content)
        with patch.object(plugin, "_active_profile", return_value="work"):
            ctx.hooks["on_session_start"](session_id="s1")

    def test_4001_character_boundary_uses_full_fallback_and_advisory(self):
        content = "x" * 4001
        self.path.write_text(content, encoding="utf-8")
        ctx = self.context()
        self.assertEqual(ctx.section[1]({"profile_name": "work"}), "")
        amended = self.request(ctx, {"instructions": "Existing", "input": []}, "codex_responses")
        self.assertIn(content, amended["instructions"])
        self.assertIn("Mention this once at session start", amended["instructions"])

    def test_excluded_profile_never_gets_fallback(self):
        ctx = self.context()
        payload = {"messages": [{"role": "system", "content": "Existing"}]}
        self.assertIs(self.request(ctx, payload, profile="ssr_synthetic_consumer"), payload)

    def test_missing_file_reports_error_and_keeps_callbacks_active(self):
        self.path.unlink()
        with self.assertLogs(plugin.logger, level="ERROR") as logs:
            ctx = self.context()
            with patch.object(plugin, "_active_profile", return_value="work"):
                ctx.hooks["on_session_start"](session_id="s1")
        self.assertIn("unavailable", " ".join(logs.output))
        self.assertIn("missing for profile work", " ".join(logs.output))
        self.assertIn("could not be loaded", ctx.section[1]({"profile_name": "work"}))

    def test_size_advisory_at_session_start(self):
        self.path.write_text("x" * 4100, encoding="utf-8")
        ctx = self.context()
        with patch.object(plugin, "_active_profile", return_value="work"):
            with self.assertLogs(plugin.logger, level="WARNING") as logs:
                ctx.hooks["on_session_start"](session_id="s1")
        self.assertIn("4100 characters", " ".join(logs.output))
        self.assertEqual(ctx.section[1]({"profile_name": "work"}), "")


if __name__ == "__main__":
    unittest.main()
