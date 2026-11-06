"""Fail-closed policy guards on the current native browser entry points."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')


def load_patcher():
    spec = importlib.util.spec_from_file_location('policy_patcher', HERE / 'patches/patch_camofox_managed_policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(source, name, namespace):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise AssertionError(name)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<installed-browser-function>', 'exec'), namespace)
    return namespace[name]


def fixture(filename, name):
    signature = 'section' if name == 'read_selection' else ''
    source = f'def {name}({signature}):\n    """Upstream selector."""\n    return "upstream"\n'
    if filename == 'browser_use_cli.py':
        source += 'def browser_exec(code):\n    return "upstream"\n'
    return source


class PolicyPatchTransformTests(unittest.TestCase):
    def test_all_transformations_compile_and_are_idempotent(self):
        module = load_patcher()
        for filename, (name, _) in module.TARGETS.items():
            with self.subTest(filename=filename):
                result = module.patch_source(filename, fixture(filename, name))
                compile(result, filename, 'exec')
                self.assertEqual(module.patch_source(filename, result), result)

    def test_incompatible_marker_is_rejected(self):
        module = load_patcher()
        source = module.patch_source('browser_session_cdp.py', fixture('', 'get_cdp_override_raw'))
        with self.assertRaises(ValueError):
            module.patch_source('browser_session_cdp.py', source.replace('return ""', 'return "ws://unsafe"'))

    def test_late_preflight_failure_does_not_partially_patch_other_files(self):
        module = load_patcher()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'tools').mkdir()
            before = {}
            for filename, (name, _) in module.TARGETS.items():
                source = fixture(filename, name)
                if filename == 'browser_session_cloud.py':
                    source = 'def incompatible():\n    return None\n'
                before[filename] = source
                (root / 'tools' / filename).write_text(source)
            with self.assertRaises(ValueError):
                module.patch_tree(root)
            self.assertEqual(before, {name: (root / 'tools' / name).read_text() for name in before})

    def test_patch_tree_preserves_file_modes(self):
        module = load_patcher()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'tools').mkdir()
            for filename, (name, _) in module.TARGETS.items():
                path = root / 'tools' / filename
                path.write_text(fixture(filename, name))
                path.chmod(0o640)
            module.patch_tree(root)
            module.patch_tree(root)
            self.assertTrue(all((root / 'tools' / name).stat().st_mode & 0o777 == 0o640 for name in module.TARGETS))


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE for installed-source regression')
class InstalledPolicyTests(unittest.TestCase):
    def setUp(self):
        self.helper = types.ModuleType('spartan_browser_policy')
        self.helper.camofox_policy_enabled = lambda: True
        self.modules = patch.dict(sys.modules, {'spartan_browser_policy': self.helper})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def source(self, filename):
        source = (Path(SOURCE) / 'tools' / filename).read_text()
        return source if os.environ.get('SPARTAN_TEST_UNPATCHED') == '1' else load_patcher().patch_source(filename, source)

    def test_browser_harness_unavailable_and_direct_execution_denied_before_side_effects(self):
        source = self.source('browser_use_cli.py')
        self.assertFalse(function(source, 'browser_exec_available', {})())
        namespace = {'_DEFAULT_TIMEOUT_S': 30}
        execute = function(source, 'browser_exec', namespace)
        self.assertFalse(json.loads(execute('print("unsafe")'))['success'])

    def test_cdp_override_is_disabled_before_env_or_profile_reads(self):
        fn = function(self.source('browser_session_cdp.py'), 'get_cdp_override_raw', {})
        with patch.dict(os.environ, {'BROWSER_CDP_URL': 'ws://stale'}):
            self.assertEqual(fn(), '')

    def test_browser_selection_wins_but_other_modalities_are_unchanged(self):
        ns = {'_raw_section': lambda section: {'cloud_provider': 'browser-use', 'provider': 'vendor'},
              'is_truthy_value': bool, 'NOUS_MANAGED_PROVIDER': 'nous',
              '_SELECTION_NAME_KEYS': {'browser': ('cloud_provider',)}, '_DEFAULT_NAME_KEYS': ('provider',)}
        fn = function(self.source('tool_backend_helpers.py'), 'read_selection', ns)
        self.assertEqual(fn('browser'), 'camofox')
        self.assertEqual(fn('image'), 'vendor')

    def test_cloud_resolver_does_not_instantiate_or_autodetect_provider(self):
        fn = function(self.source('browser_session_cloud.py'), 'get_cloud_provider', {})
        self.assertIsNone(fn())

    def test_invalid_policy_propagates_before_browser_fallback(self):
        def invalid():
            raise RuntimeError('invalid administrator browser policy')
        self.helper.camofox_policy_enabled = invalid
        fn = function(self.source('browser_use_cli.py'), 'browser_exec_available', {})
        with self.assertRaisesRegex(RuntimeError, 'administrator browser policy'):
            fn()


if __name__ == '__main__':
    unittest.main()
