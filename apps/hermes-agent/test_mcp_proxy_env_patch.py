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
        patched = module.patch_source(module.OLD_BLOCK)

        self.assertIn(module.MARKER, patched)
        for token in module.PROXY_TOKENS:
            self.assertIn(token, patched)
        self.assertEqual(module.patch_source(patched), patched)

    def test_raises_when_upstream_anchor_changes(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "safe stdio environment anchor"):
            module.patch_source("_SAFE_ENV_KEYS = frozenset()")


if __name__ == "__main__":
    unittest.main()
