import importlib.util
import os
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
    def setUp(self):
        self._old_openai_base_url = os.environ.get("OPENAI_BASE_URL")
        os.environ["OPENAI_BASE_URL"] = "http://clawroute:18790/v1"

    def tearDown(self):
        if self._old_openai_base_url is None:
            os.environ.pop("OPENAI_BASE_URL", None)
        else:
            os.environ["OPENAI_BASE_URL"] = self._old_openai_base_url

    def test_injects_prompt_cache_key_and_reasoning_into_profile_and_legacy_paths(self):
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
        self.assertIn("def _spartan_add_clawroute_prompt_cache_key(", patched)
        self.assertIn('extra_body["prompt_cache_key"] = cache_key', patched)
        self.assertIn(
            "return _spartan_add_clawroute_prompt_cache_key(profile_kwargs, params, allow_missing_base_url=True)",
            patched,
        )
        self.assertIn(
            "return _spartan_add_clawroute_prompt_cache_key(api_kwargs, params)",
            patched,
        )
        self.assertIn("allow_missing_base_url=True", patched)

        again, changed_again = module.patch_source(patched)
        self.assertFalse(changed_again)
        self.assertEqual(patched, again)

    def test_profile_path_forwards_cache_key_and_reasoning_without_base_url(self):
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
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            '        return {"model": model, "messages": messages}',
            "",
        ])
        patched, changed = module.patch_source(source)
        self.assertTrue(changed)
        namespace = {}
        exec(patched, namespace)

        kwargs = namespace["ChatCompletionsTransport"]().build_kwargs(
            "custom-1/clawroute/auto",
            [{"role": "user", "content": "hello"}],
            provider_profile=object(),
            session_id="session-123",
            reasoning_config={"effort": "high"},
        )

        self.assertIn("extra_body", kwargs)
        self.assertEqual(kwargs["extra_body"]["prompt_cache_key"], "hermes:session-123")
        self.assertEqual(kwargs["reasoning_effort"], "high")

    def test_raw_ultra_overrides_upstream_gpt56_max_only_for_clawroute(self):
        module = load_module()
        source = "\n".join([
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            return self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "        api_kwargs = {}",
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            '        return {"model": model, "messages": messages, "reasoning_effort": "max"}',
            "",
        ])
        patched, changed = module.patch_source(source)
        self.assertTrue(changed)
        namespace = {}
        exec(patched, namespace)

        kwargs = namespace["ChatCompletionsTransport"]().build_kwargs(
            "gpt-5.6-sol",
            [{"role": "user", "content": "hello"}],
            provider_profile=object(),
            reasoning_config={"effort": "ultra"},
        )

        self.assertEqual(kwargs["reasoning_effort"], "ultra")

    def test_direct_provider_keeps_existing_reasoning_effort(self):
        module = load_module()
        source = "\n".join([
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            return self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "        api_kwargs = {}",
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            '        return {"base_url": "https://api.openai.com/v1", "reasoning_effort": "max"}',
            "",
        ])
        patched, changed = module.patch_source(source)
        self.assertTrue(changed)
        namespace = {}
        exec(patched, namespace)

        kwargs = namespace["ChatCompletionsTransport"]().build_kwargs(
            "gpt-5.6",
            [{"role": "user", "content": "hello"}],
            provider_profile=object(),
            reasoning_config={"effort": "ultra"},
        )

        self.assertEqual(kwargs["reasoning_effort"], "max")

    def test_legacy_path_does_not_inject_without_clawroute_base_url(self):
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
            "        return api_kwargs",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            "        return {}",
            "",
        ])
        patched, changed = module.patch_source(source)
        self.assertTrue(changed)
        namespace = {}
        exec(patched, namespace)

        kwargs = namespace["ChatCompletionsTransport"]().build_kwargs(
            "custom-1/clawroute/auto",
            [{"role": "user", "content": "hello"}],
            session_id="session-123",
            reasoning_config={"effort": "high"},
        )

        self.assertNotIn("extra_body", kwargs)
        self.assertNotIn("reasoning_effort", kwargs)

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
            '            extra_body["prompt_cache_key"] = cache_key',
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
        self.assertIn('extra_body["prompt_cache_key"] = cache_key', patched)
        self.assertNotIn("        # spartan-gate: clawroute prompt cache key\n", patched)

    def test_upgrades_v3_patch_and_preserves_cache_key_injection(self):
        module = load_module()
        source = "\n".join([
            "# spartan-gate: clawroute prompt cache key v3",
            "def _spartan_add_clawroute_prompt_cache_key(api_kwargs, params, allow_missing_base_url=False):",
            '    request_base_url = str(api_kwargs.get("base_url") or params.get("base_url") or "")',
            '    clawroute_base_url = __import__("os").environ.get("OPENAI_BASE_URL", "")',
            "    is_clawroute_request = bool(clawroute_base_url) and (",
            "        request_base_url.rstrip('/') == clawroute_base_url.rstrip('/')",
            "        or (allow_missing_base_url and not request_base_url)",
            "    )",
            "    if not is_clawroute_request:",
            "        return api_kwargs",
            '    session_id = params.get("session_id")',
            "    if session_id:",
            '        extra_body = dict(api_kwargs.get("extra_body") or {})',
            '        extra_body["prompt_cache_key"] = cache_key',
            '        api_kwargs["extra_body"] = extra_body',
            '    reasoning_config = params.get("reasoning_config")',
            '    if "reasoning_effort" not in api_kwargs and isinstance(reasoning_config, dict):',
            '        if reasoning_config.get("enabled") is not False:',
            '            effort = str(reasoning_config.get("effort") or "medium").strip().lower()',
            "            if effort:",
            '                api_kwargs["reasoning_effort"] = "low" if effort == "minimal" else effort',
            "    return api_kwargs",
            "",
            "",
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            profile_kwargs = self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "            return _spartan_add_clawroute_prompt_cache_key(profile_kwargs, params, allow_missing_base_url=True)",
            "        api_kwargs = dict(params)",
            "        return _spartan_add_clawroute_prompt_cache_key(api_kwargs, params)",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            "        return {}",
            "",
        ])

        patched, changed = module.patch_source(source)

        self.assertTrue(changed)
        self.assertIn(module.MARKER, patched)
        self.assertIn("_spartan_add_clawroute_prompt_cache_key", patched)
        self.assertIn('extra_body["prompt_cache_key"] = cache_key', patched)

    def test_upgrades_v4_reasoning_only_patch_and_restores_cache_key_injection(self):
        module = load_module()
        source = "\n".join([
            "# spartan-gate: clawroute reasoning effort v4",
            "def _spartan_add_clawroute_reasoning_effort(api_kwargs, params, allow_missing_base_url=False):",
            '    request_base_url = str(api_kwargs.get("base_url") or params.get("base_url") or "")',
            '    clawroute_base_url = __import__("os").environ.get("OPENAI_BASE_URL", "")',
            "    is_clawroute_request = bool(clawroute_base_url) and (",
            "        request_base_url.rstrip('/') == clawroute_base_url.rstrip('/')",
            "        or (allow_missing_base_url and not request_base_url)",
            "    )",
            "    if not is_clawroute_request:",
            "        return api_kwargs",
            '    reasoning_config = params.get("reasoning_config")',
            '    if "reasoning_effort" not in api_kwargs and isinstance(reasoning_config, dict):',
            '        if reasoning_config.get("enabled") is not False:',
            '            effort = str(reasoning_config.get("effort") or "medium").strip().lower()',
            "            if effort:",
            '                api_kwargs["reasoning_effort"] = "low" if effort == "minimal" else effort',
            "    return api_kwargs",
            "",
            "",
            "class ChatCompletionsTransport:",
            "    def build_kwargs(self, model, messages, **params):",
            '        profile = params.get("provider_profile")',
            "        if profile:",
            "            profile_kwargs = self._build_kwargs_from_profile(",
            "                profile, model, messages, None, params",
            "            )",
            "            return _spartan_add_clawroute_reasoning_effort(profile_kwargs, params, allow_missing_base_url=True)",
            "        api_kwargs = dict(params)",
            "        return _spartan_add_clawroute_reasoning_effort(api_kwargs, params)",
            "",
            "    def _build_kwargs_from_profile(self, profile, model, messages, tools, params):",
            "        return {}",
            "",
        ])

        patched, changed = module.patch_source(source)

        self.assertTrue(changed)
        self.assertIn(module.MARKER, patched)
        self.assertIn("_spartan_add_clawroute_prompt_cache_key", patched)
        self.assertIn('extra_body["prompt_cache_key"] = cache_key', patched)


if __name__ == "__main__":
    unittest.main()
