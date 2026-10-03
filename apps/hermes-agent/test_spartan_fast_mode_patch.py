import ast
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
SOURCE = Path(os.environ.get("HERMES_UPSTREAM_SOURCE", "/tmp/spartan-hermes-policy-integration-source"))


def load_patcher():
    spec = importlib.util.spec_from_file_location("fast_patcher", HERE / "patches/patch_spartan_fast_mode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless((SOURCE / "hermes_cli/models.py").is_file(), "set HERMES_UPSTREAM_SOURCE")
class FastModePatchTests(unittest.TestCase):
    def namespace(self, patched=True):
        source = (SOURCE / "hermes_cli/models.py").read_text()
        if patched and os.environ.get("SPARTAN_TEST_UNPATCHED") != "1":
            source = load_patcher().patch_source(source)
        names = {"_strip_vendor_prefix", "_is_openai_fast_model", "model_supports_fast_mode",
                 "_is_anthropic_fast_model", "_fast_mode_route_supported", "model_supports_ultrafast",
                 "resolve_fast_mode_overrides", "_spartan_fast_mode_route_supported"}
        nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = {"_OPENAI_FAST_MODE_PREFIXES": ("gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6"),
              "_OPENAI_ULTRAFAST_MODELS": {"gpt-6-astra"}, "normalize_provider": lambda p: p}
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(tree), "<fast-models>", "exec"), ns)
        return ns

    def setUp(self):
        metadata = types.ModuleType("agent.model_metadata")
        metadata.is_grok_46_family = lambda m: m.startswith("grok-4.6")
        metadata.is_anthropic_fast_mode_model = lambda m: "claude-opus-5" in m
        metadata.strip_codex_context_variant_suffix = lambda m: m.removesuffix("-282kcontext")
        self.modules = patch.dict(sys.modules, {"agent.model_metadata": metadata})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_trusted_explicit_models_receive_priority(self):
        resolve = self.namespace()["resolve_fast_mode_overrides"]
        for model in ("codex/gpt-6-astra", "codex/gpt-6-astra-282kcontext", "openai/gpt-5.6"):
            for url in ("http://spartan-route:18790/v1", "http://spartan-route:18790/v1/"):
                with self.subTest(model=model, url=url):
                    self.assertEqual(resolve(model, provider="openai-api", base_url=url), {"service_tier": "priority"})

    def test_other_routes_and_models_remain_denied(self):
        resolve = self.namespace()["resolve_fast_mode_overrides"]
        for url in ("http://other:18790/v1", "http://spartan-route:18790/v1?x=1",
                    "http://user@spartan-route:18790/v1", "http://spartan-route.evil:18790/v1",
                    "http://spartan-route:18790/v10", "http://spartan-route:18791/v1"):
            self.assertIsNone(resolve("codex/gpt-6-astra", provider="openai-api", base_url=url))
        for model in ("clawroute/auto", "gpt-6-astra", "openrouter/gpt-6-astra", "codex/gpt-5.3-codex", "anthropic/claude-opus-5", "xai/grok-4.6"):
            self.assertIsNone(resolve(model, provider="openai-api", base_url="http://spartan-route:18790/v1"))
        self.assertIsNone(resolve("codex/gpt-6-astra", provider="openrouter", base_url="http://spartan-route:18790/v1"))

    def test_first_party_and_ultrafast_eligibility_are_preserved(self):
        resolve = self.namespace()["resolve_fast_mode_overrides"]
        self.assertEqual(resolve("gpt-6-astra", provider="openai-api", base_url="https://api.openai.com/v1"), {"service_tier": "priority"})
        self.assertEqual(resolve("codex/gpt-6-astra-282kcontext", provider="openai-api", base_url="http://spartan-route:18790/v1", tier="ultrafast"), {"service_tier": "ultrafast"})
        self.assertIsNone(resolve("codex/gpt-5.6", provider="openai-api", base_url="http://spartan-route:18790/v1", tier="ultrafast"))

    def test_patch_is_idempotent_and_rejects_incompatible_input(self):
        patcher = load_patcher()
        source = (SOURCE / "hermes_cli/models.py").read_text()
        result = patcher.patch_source(source)
        self.assertEqual(patcher.patch_source(result), result)
        compile(result, "models.py", "exec")
        with self.assertRaises(ValueError):
            patcher.patch_source("def unrelated():\n    pass\n")
        with self.assertRaises(ValueError):
            patcher.patch_source(result.replace('"http://spartan-route:18790/v1"', '"http://other:18790/v1"', 1))

    def test_command_words_windows_expiry_and_model_switch(self):
        spec = importlib.util.spec_from_file_location("upstream_fast_mode", SOURCE / "agent/fast_mode.py")
        fast = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fast)
        models = types.ModuleType("hermes_cli.models")
        models.resolve_fast_mode_overrides = self.namespace()["resolve_fast_mode_overrides"]
        agent = types.SimpleNamespace(model="codex/gpt-6-astra", provider="openai-api",
            base_url="http://spartan-route:18790/v1", request_overrides={"stable": True},
            fast_auto_seconds=60)
        self.assertEqual(fast.parse_service_tier("fast"), "priority")
        self.assertIsNone(fast.parse_service_tier("normal"))
        with patch.dict(sys.modules, {"hermes_cli.models": models}), patch.object(fast.time, "monotonic", return_value=100):
            for word in ("auto", "cold"):
                agent.service_tier = fast.parse_service_tier(word)
                fast.begin_turn(agent, [])
                self.assertEqual(fast.effective_request_overrides(agent), {"stable": True, "service_tier": "priority"})
                with patch.object(fast.time, "monotonic", return_value=160):
                    self.assertEqual(fast.effective_request_overrides(agent), {"stable": True})
                agent.model = "clawroute/auto"
                self.assertEqual(fast.effective_request_overrides(agent), {"stable": True})
                agent.model = "codex/gpt-6-astra"
            fast.begin_turn(agent, [{"role": "user", "content": "previous turn"}])
            self.assertEqual(fast.effective_request_overrides(agent), {"stable": True})
            agent.service_tier = fast.parse_service_tier("normal")
            fast.begin_turn(agent, [])
            self.assertEqual(fast.effective_request_overrides(agent), {"stable": True})


if __name__ == "__main__":
    unittest.main()
