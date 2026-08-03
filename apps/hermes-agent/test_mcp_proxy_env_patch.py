import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("patches").joinpath("patch_mcp_proxy_env.py")


def load_module():
    spec = importlib.util.spec_from_file_location("patch_mcp_proxy_env", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class McpProxyEnvPatchTests(unittest.TestCase):
    def test_adds_complete_proxy_allowlist_and_is_idempotent(self):
        module = load_module()
        source = module.OLD_BLOCK + "\n\n" + module.OLD_ENV_UPDATE
        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn(module.ENV_VALUE_MARKER, patched)
        for token in module.PROXY_TOKENS:
            self.assertIn(token, patched)
        self.assertEqual(module.patch_source(patched), patched)

    def test_normalizes_explicit_scalar_env_values_and_rejects_containers(self):
        module = load_module()
        source = (
            "from typing import Optional\n"
            + "import os\n"
            + "_SAFE_ENV_KEYS_CASE_INSENSITIVE = frozenset()\n"
            + module.OLD_BLOCK
            + "\n\n"
            + module.OLD_ENV_UPDATE.replace(
                "    if user_env:",
                "def _build_safe_env(user_env: Optional[dict]) -> dict:\n"
                "    env = {}\n"
                "    if user_env:",
                1,
            )
        )
        namespace = {}
        exec(module.patch_source(source), namespace)
        build_safe_env = namespace["_build_safe_env"]

        self.assertEqual(
            build_safe_env(
                {"TEXT": "value", "FLAG": False, "COUNT": 3, "RATE": 1.5}
            ),
            {"TEXT": "value", "FLAG": "false", "COUNT": "3", "RATE": "1.5"},
        )
        for invalid in (None, [], {}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError, "must be a string, boolean, or number"
                ):
                    build_safe_env({"INVALID": invalid})
        for invalid_key in (None, 1):
            with self.subTest(invalid_key=invalid_key):
                with self.assertRaisesRegex(
                    ValueError, "environment variable names must be strings"
                ):
                    build_safe_env({invalid_key: "value"})

    def test_raises_when_upstream_anchor_changes(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "safe stdio environment anchor"):
            module.patch_source(
                "_SAFE_ENV_KEYS = frozenset()\n" + module.OLD_ENV_UPDATE
            )

    def test_raises_when_explicit_env_anchor_changes(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "explicit stdio environment anchor"):
            module.patch_source(module.OLD_BLOCK + "\n\n    return env")


if __name__ == "__main__":
    unittest.main()
