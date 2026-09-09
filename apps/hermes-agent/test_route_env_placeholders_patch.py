import importlib.util
import pathlib
import sys
import tempfile
import unittest
from typing import Optional


MODULE_PATH = pathlib.Path(__file__).parent / "patches" / "patch_route_env_placeholders.py"


def load_module():
    spec = importlib.util.spec_from_file_location("patch_route_env_placeholders", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RouteEnvPlaceholdersPatchTests(unittest.TestCase):
    @staticmethod
    def credential_pool_source():
        return """
def get_env_prefer_dotenv(key: str) -> str:
    env_file = load_env()
    raw = env_file.get(key, "").strip()
    scoped_value = (_get_secret(key, "") or "").strip()
    # If .env contains an unresolved op:// reference, prefer the
    # already-resolved value supplied by the active secret scope.
    if raw.startswith("op://") and scoped_value:
        return scoped_value
    return raw or scoped_value
"""

    def assert_reference_contract(self, patched, argument_name):
        namespace = {"Optional": Optional}
        exec(patched, namespace)
        get_reference = namespace["_spartan_route_env_reference"]

        self.assertEqual(get_reference("${OPENAI_API_KEY}", argument_name), "OPENAI_API_KEY")
        self.assertEqual(get_reference("  ${CUSTOM_1_API_KEY}  ", argument_name), "CUSTOM_1_API_KEY")
        self.assertIsNone(get_reference("${ANTHROPIC_API_KEY}", argument_name))
        self.assertIsNone(get_reference("actual-token", argument_name))
        self.assertIsNone(get_reference(None, argument_name))

    def test_config_patch_ignores_literal_openai_key_before_secret_fallback(self):
        module = load_module()
        source = """
def get_env_value_prefer_dotenv(key: str) -> Optional[str]:
    env_vars = load_env()
    val = env_vars.get(key)
    if val:
        return val
    try:
        from agent.secret_scope import get_secret as _get_secret

        return _get_secret(key)
    except Exception:
        return os.environ.get(key)
"""
        patched = module.patch_config_source(source)

        self.assertIn(module.CONFIG_MARKER, patched)
        self.assertIn("reference = _spartan_route_env_reference(val, key)", patched)
        self.assertIn("val = os.environ.get(reference)", patched)
        self.assert_reference_contract(patched, "OPENAI_API_KEY")
        self.assertEqual(module.patch_config_source(patched), patched)

    def test_secret_scope_patch_delegates_explicit_route_key_references(self):
        module = load_module()
        source = """
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    scope = _SECRET_SCOPE.get()
    if scope is not None:
        val = scope.get(name)
        if val is not None:
            return val
        if _MULTIPLEX_ACTIVE:
            return default

    val = os.environ.get(name)
    return val if val is not None else default
"""
        patched = module.patch_secret_scope_source(source)

        self.assertIn(module.SECRET_MARKER, patched)
        self.assertIn("reference = _spartan_route_env_reference(val, name)", patched)
        self.assertIn("val = os.environ.get(reference)", patched)
        self.assert_reference_contract(patched, "OPENAI_API_KEY")
        self.assertEqual(module.patch_secret_scope_source(patched), patched)

    def test_helper_allows_route_aliases_but_not_unrelated_placeholders(self):
        module = load_module()
        source = """
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    scope = _SECRET_SCOPE.get()
    if scope is not None:
        val = scope.get(name)
        if val is not None:
            return val
        if _MULTIPLEX_ACTIVE:
            return default
"""
        patched = module.patch_secret_scope_source(source)
        namespace = {"Optional": Optional}
        exec(patched, namespace)
        get_reference = namespace["_spartan_route_env_reference"]

        self.assertEqual(get_reference("${CUSTOM_1_API_KEY}", "OPENAI_API_KEY"), "CUSTOM_1_API_KEY")
        self.assertEqual(get_reference("${OPENAI_API_KEY}", "CUSTOM_1_API_KEY"), "OPENAI_API_KEY")
        self.assertIsNone(get_reference("${ANTHROPIC_API_KEY}", "ANTHROPIC_API_KEY"))

    def execute_credential_pool(self, dotenv, secrets, process_env=None):
        module = load_module()
        patched = module.patch_credential_pool_source(self.credential_pool_source())
        namespace = {
            "Optional": Optional,
            "List": list,
            "Tuple": tuple,
            "Set": set,
            "PooledCredential": object,
            "os": type("FakeOs", (), {"environ": process_env or {}}),
            "load_env": lambda: dotenv,
            "_get_secret": lambda key, default="": secrets.get(key, default),
        }
        exec(patched, namespace)
        return module, patched, namespace["get_env_prefer_dotenv"]("OPENAI_API_KEY")

    def test_credential_pool_resolves_route_alias_before_seeding(self):
        module, patched, result = self.execute_credential_pool(
            {"OPENAI_API_KEY": "${CUSTOM_1_API_KEY}"},
            {"CUSTOM_1_API_KEY": "route-token"},
            {"CUSTOM_1_API_KEY": "other-profile-token"},
        )

        self.assertEqual(result, "route-token")
        self.assertIn(module.CREDENTIAL_POOL_MARKER, patched)
        self.assertEqual(module.patch_credential_pool_source(patched), patched)

    def test_credential_pool_preserves_direct_dotenv_precedence(self):
        _module, _patched, result = self.execute_credential_pool(
            {"OPENAI_API_KEY": "direct-token"},
            {"OPENAI_API_KEY": "scoped-token"},
        )

        self.assertEqual(result, "direct-token")

    def test_credential_pool_missing_route_alias_fails_closed(self):
        _module, _patched, result = self.execute_credential_pool(
            {"OPENAI_API_KEY": "${CUSTOM_1_API_KEY}"},
            {},
            {"CUSTOM_1_API_KEY": "unscoped-token"},
        )

        self.assertEqual(result, "")

    def test_credential_pool_does_not_expand_unrelated_reference(self):
        _module, _patched, result = self.execute_credential_pool(
            {"OPENAI_API_KEY": "${ANTHROPIC_API_KEY}"},
            {},
        )

        self.assertEqual(result, "${ANTHROPIC_API_KEY}")

    def test_credential_pool_preserves_op_reference_precedence(self):
        _module, _patched, result = self.execute_credential_pool(
            {"OPENAI_API_KEY": "op://Private/OpenAI/key"},
            {"OPENAI_API_KEY": "resolved-op-token"},
        )

        self.assertEqual(result, "resolved-op-token")

    def test_preflights_all_targets_before_any_write(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            third = root / "third.py"
            for path in (first, second, third):
                path.write_text("original", encoding="utf-8")

            def fail(_source):
                raise ValueError("third target incompatible")

            with self.assertRaisesRegex(ValueError, "third target incompatible"):
                module.patch_files_atomically((
                    (first, lambda source: source + "-patched"),
                    (second, lambda source: source + "-patched"),
                    (third, fail),
                ))

            for path in (first, second, third):
                self.assertEqual(path.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
