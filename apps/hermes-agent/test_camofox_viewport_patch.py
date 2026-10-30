"""Selected native viewport contracts; no browser/profile access in unit tests."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).parent


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        'viewport_patcher', HERE / 'patches/patch_camofox_viewport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ViewportTests(unittest.TestCase):
    def setUp(self):
        self.patcher = load_patcher()
        self.camofox = types.ModuleType('tools.browser_camofox')
        self.camofox._require_tab = Mock(return_value=(
            {'user_id': 'profile-a', 'tab_id': 'tab-a'}, None))
        self.camofox._post = Mock(return_value={
            'ok': True, 'requested': {'width': 390, 'height': 844},
            'effective': {'innerWidth': 390, 'innerHeight': 844,
                          'href': 'https://private.invalid/?secret=hidden'},
            'strategy': 'pageViewport'})
        self.camofox._spartan_stale_tab = Mock(return_value=None)
        self.modules = patch.dict(sys.modules, {'tools.browser_camofox': self.camofox})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.registry = Mock()
        self.ns = {'json': json, 'registry': self.registry,
                   '_is_camofox_mode': lambda: True}
        exec(self.patcher.ADDITION, self.ns)
        self.handler = self.registry.register.call_args.kwargs['handler']

    def test_registered_tool_uses_durable_session_and_live_only_route(self):
        registration = self.registry.register.call_args.kwargs
        self.assertEqual((registration['name'], registration['toolset']),
                         ('browser_viewport', 'browser'))
        for width, height in ((1440, 900), (390, 844)):
            with self.subTest(size=(width, height)):
                self.camofox._post.return_value['requested'] = {'width': width, 'height': height}
                self.camofox._post.return_value['effective'].update(
                    innerWidth=width, innerHeight=height)
                result = json.loads(self.handler({'width': width, 'height': height},
                                                session_id='durable', task_id='transient'))
                self.assertTrue(result['success'])
                self.assertEqual(result['effective']['innerWidth'], width)
                self.assertNotIn('private.invalid', json.dumps(result))
                self.camofox._require_tab.assert_called_with('durable', 'resize the viewport')
                self.camofox._post.assert_called_with('/tabs/tab-a/spartan/viewport',
                    {'userId': 'profile-a', 'width': width, 'height': height, 'relaunch': False})

    def test_invalid_size_or_missing_tab_does_not_issue_resize(self):
        result = json.loads(self.handler({'width': 99, 'height': 844}, session_id='s'))
        self.assertFalse(result['success'])
        self.camofox._require_tab.assert_not_called()
        blocked = json.dumps({'success': False, 'error': 'Call browser_navigate first.'})
        self.camofox._require_tab.return_value = ({'tab_id': None}, blocked)
        self.assertEqual(self.handler({'width': 390, 'height': 844}, session_id='s'), blocked)
        self.camofox._post.assert_not_called()

    def test_stale_generation_is_not_replayed_and_raw_errors_are_hidden(self):
        self.camofox._post.side_effect = RuntimeError('Bearer secret / private endpoint')
        self.camofox._spartan_stale_tab.return_value = json.dumps(
            {'success': False, 'code': 'stale_browser_generation', 'requires_navigation': True})
        result = json.loads(self.handler({'width': 390, 'height': 844}, session_id='s'))
        self.assertEqual(result['code'], 'stale_browser_generation')
        self.camofox._post.assert_called_once()
        self.camofox._spartan_stale_tab.return_value = None
        result = self.handler({'width': 390, 'height': 844}, session_id='s')
        self.assertFalse(json.loads(result)['success'])
        self.assertNotIn('secret', result)

    def test_patch_is_idempotent_and_refuses_unexpected_input(self):
        source = '# SPARTAN_CAMOFOX_REGISTRY_SCOPE_V1\n_BROWSER_TOOL_TABLE = ()\n'
        result = self.patcher.patch_source(source)
        compile(result, 'patched-browser-tool', 'exec')
        self.assertEqual(self.patcher.patch_source(result), result)
        with self.assertRaises(ValueError):
            self.patcher.patch_source('unrelated_source = True\n')

    @unittest.skipUnless(os.environ.get('HERMES_UPSTREAM_SOURCE'), 'requires pinned Hermes source')
    def test_pinned_native_source_accepts_patch_after_tab_isolation(self):
        root = Path(os.environ['HERMES_UPSTREAM_SOURCE'])
        spec = importlib.util.spec_from_file_location('tab_isolation', HERE / 'patches/patch_camofox_tab_isolation.py')
        isolation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolation)
        source = isolation.patch_browser_tool((root / 'tools/browser_tool.py').read_text())
        result = self.patcher.patch_source(source)
        self.assertEqual(self.patcher.patch_source(result), result)
        tree = ast.parse(result)
        self.assertEqual(sum(isinstance(n, ast.FunctionDef) and n.name == 'browser_viewport'
                             for n in tree.body), 1)


if __name__ == '__main__':
    unittest.main()
