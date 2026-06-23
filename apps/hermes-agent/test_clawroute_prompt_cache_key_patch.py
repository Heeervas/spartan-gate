import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / "patches" / "patch_clawroute_prompt_cache_key.py"


def load_module():
    spec = importlib.util.spec_from_file_location("patch_clawroute_prompt_cache_key", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ClawroutePromptCacheKeyPatchTests(unittest.TestCase):
    def test_injects_cache_key_into_profile_and_legacy_paths(self):
        module = load_module()
        source = "\n".join([
            "import os",
            "",
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        """Build kwargs."""',
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            return self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "        api_kwargs = dict(params)",
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            "        return {}",
            "",
        ])
        patched, changed = module.patch_source(source)
        self.assertTrue(changed)
        self.assertIn('extra_body.setdefault("prompt_cache_key", f"hermes:{session_id}")', patched)
        self.assertIn(
            "return _spartan_add_clawroute_prompt_cache_key(profile_kwargs, params)",
            patched,
        )
        self.assertIn(
            "return _spartan_add_clawroute_prompt_cache_key(api_kwargs, params)",
            patched,
        )

        again, changed_again = module.patch_source(patched)
        self.assertFalse(changed_again)
        self.assertEqual(patched, again)

    def test_upgrades_legacy_patch(self):
        module = load_module()
        source = "\n".join([
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            return self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "        api_kwargs = dict(params)",
            "        # spartan-gate: clawroute prompt cache key",
            '        session_id = params.get("session_id")',
            '        request_base_url = str(api_kwargs.get("base_url") or params.get("base_url") or "")',
            '        clawroute_base_url = __import__("os").environ.get("OPENAI_BASE_URL", "")',
            "        if session_id and clawroute_base_url:",
            '            extra_body = dict(api_kwargs.get("extra_body") or {})',
            '            extra_body.setdefault("prompt_cache_key", f"hermes:{session_id}")',
            '            api_kwargs["extra_body"] = extra_body',
            "",
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            "        return {}",
        ])

        patched, changed = module.patch_source(source)

        self.assertTrue(changed)
        self.assertIn(module.MARKER, patched)
        self.assertEqual(
            patched.count(
                'extra_body.setdefault("prompt_cache_key", f"hermes:{session_id}")'
            ),
            1,
        )
        self.assertNotIn(
            "        # spartan-gate: clawroute prompt cache key\n",
            patched,
        )


if __name__ == "__main__":
    unittest.main()
