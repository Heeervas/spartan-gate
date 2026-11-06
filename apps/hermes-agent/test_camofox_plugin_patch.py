"""Catalog-pinned official-plugin regressions; network effects are mocked, not code."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

HERE = Path(__file__).parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')
PLUGIN = os.environ.get('HERMES_CAMOFOX_PLUGIN_SOURCE')
spec = importlib.util.spec_from_file_location('official_camofox_patch', HERE / 'patches/patch_camofox_plugin.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def functions(source, names, namespace):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError('missing native function')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<official-camofox>', 'exec'), namespace)
    return namespace


@unittest.skipUnless(SOURCE and PLUGIN, 'explicit pinned Hermes and official Camofox source required')
class OfficialCamofoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(Path(PLUGIN), self.root / 'plugins/camofox')
        for name in ('tools/browser_vault_tool.py', 'hermes_cli/left_core_migration.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(SOURCE) / name, path)
        patcher.patch_tree(self.root)
        self.tools = (self.root / 'plugins/camofox/camofox_tools.py').read_text()
        self.client_source = (self.root / 'plugins/camofox/camofox_client.py').read_text()
        self.session = {'user_id': 'profile', 'session_key': 'durable', 'tab_id': 'tab',
                        'managed': True, 'adopt_existing_tab': True}
        self.client = SimpleNamespace(_sessions_lock=threading.Lock(),
            _sessions={('profile-home', 'durable'): self.session},
            _session_key=lambda task: ('profile-home', task),
            get_session=Mock(return_value=self.session), resync_tab=Mock(),
            post=Mock(), get=Mock(), delete=Mock(), ensure_tab=Mock(),
            tab_path=lambda session, suffix: f"/tabs/{session['tab_id']}/{suffix}",
            NO_SESSION_ERROR='Call browser_navigate first.')
        self.ns = {'client': self.client, 'requests': requests, 'logger': Mock(),
                   '_dumps': json.dumps, '_fail': lambda message: json.dumps({'success': False, 'error': message}),
                   '_TAB_GONE_ERROR': 'Call browser_navigate first.'}
        functions(self.tools, {'_tab_gone', '_run_on_tab', '_kw_task',
                              'browser_viewport', 'handle_viewport'}, self.ns)

    def test_complete_transform_is_idempotent_preserves_modes_and_preflights_all_files(self):
        snapshot = lambda: {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
                            for p in self.root.rglob('*') if p.is_file()}
        before = snapshot()
        patcher.patch_tree(self.root)
        self.assertEqual(before, snapshot())
        path = self.root / 'hermes_cli/left_core_migration.py'
        path.write_text('def unsupported(): pass\n')
        before = snapshot()
        with self.assertRaises((ValueError, StopIteration)):
            patcher.patch_tree(self.root)
        self.assertEqual(before, snapshot())

    def test_durable_registry_scope_and_native_cleanup_precedence(self):
        self.assertEqual(self.ns['_kw_task']({'session_id': 'durable', 'task_id': 'turn'}), 'durable')
        self.assertEqual(self.ns['_kw_task']({'task_id': 'turn'}), 'turn')
        entry = (self.root / 'plugins/camofox/__init__.py').read_text()
        nested = next(n for n in ast.walk(ast.parse(entry)) if isinstance(n, ast.FunctionDef) and n.name == '_on_session_end')
        ns = {'t': SimpleNamespace(headed_mode=lambda: False, cleanup_task=Mock())}
        exec(compile(ast.Module(body=[nested], type_ignores=[]), '<official-cleanup>', 'exec'), ns)
        ns['_on_session_end'](task_id='turn', session_id='durable')
        ns['t'].cleanup_task.assert_called_once_with('durable')
        self.assertIn('(t.SPARTAN_VIEWPORT_SCHEMA, t.handle_viewport', entry)

    def test_actual_native_manifest_gate_honors_disable_and_only_camofox_gets_baked_presence(self):
        from hermes_cli.plugins_manifest import parse_manifest_file
        from hermes_cli.plugins_discovery import gate_manifest
        directory = self.root / 'plugins/camofox'
        manifest = parse_manifest_file(directory / 'plugin.yaml', directory, 'bundled', '')
        self.assertIsNotNone(manifest)
        self.assertEqual(gate_manifest(manifest, set(), set()).action, 'load_now')
        self.assertEqual(gate_manifest(manifest, {'camofox'}, set()).action, 'placeholder')
        self.assertEqual(gate_manifest(manifest, {'camofox'}, {'camofox'}).action, 'placeholder')
        source = (self.root / 'hermes_cli/left_core_migration.py').read_text()
        ns = {'Path': Path, '__file__': str(self.root / 'hermes_cli/left_core_migration.py')}
        functions(source, {'plugin_present'}, ns)
        self.assertTrue(ns['plugin_present']('camofox', self.root / 'synthetic-home'))
        self.assertFalse(ns['plugin_present']('other', self.root / 'synthetic-home'))
        original = (Path(SOURCE) / 'hermes_cli/left_core_migration.py').read_text()
        bodies = lambda text: {n.name: ast.dump(n) for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(bodies(original)['_record_migration'], bodies(source)['_record_migration'])

    def test_native_adoption_never_crosses_durable_session_key(self):
        ns = {'get_camofox_url': lambda: 'http://browser', 'user_params': lambda s: {},
              'get': self.client.get, 'logger': Mock()}
        functions(self.client_source, {'_adopt_existing_tab'}, ns)
        self.session['tab_id'] = None
        self.client.get.return_value = {'tabs': [{'tabId': 'operator', 'listItemId': 'other-session'}]}
        ns['_adopt_existing_tab'](self.session)
        self.assertIsNone(self.session['tab_id'])
        self.client.get.return_value['tabs'].append({'tabId': 'own', 'listItemId': 'durable'})
        ns['_adopt_existing_tab'](self.session)
        self.assertEqual(self.session['tab_id'], 'own')

    def test_stale_read_and_write_never_replay_or_adopt_but_navigation_can_recover(self):
        for status in (404, 410):
            for read_only in (False, True):
                with self.subTest(status=status, read_only=read_only):
                    self.session.update(tab_id='tab', adopt_existing_tab=True)
                    exc = requests.HTTPError(response=SimpleNamespace(status_code=status))
                    action = Mock(side_effect=[exc, '{"success":true}'])
                    result = json.loads(self.ns['_run_on_tab'](self.session, action, read_only=read_only))
                    self.assertTrue(result['requires_navigation'])
                    self.assertIsNone(self.session['tab_id'])
                    self.assertFalse(self.session['adopt_existing_tab'])
                    action.assert_called_once_with(self.session)
                    self.client.resync_tab.assert_not_called()
            functions(self.tools, {'_navigate_tab'}, self.ns)
            self.session['tab_id'] = 'stale'
            self.client.post.side_effect = requests.HTTPError(response=SimpleNamespace(status_code=status))
            self.client.ensure_tab.return_value = {'tab_id': 'new'}
            session, result = self.ns['_navigate_tab']('durable', 'https://example.test')
            self.assertEqual(session['tab_id'], 'new')
            self.assertTrue(result['ok'])
            self.client.ensure_tab.assert_called_with('durable', 'https://example.test')

    def test_managed_close_only_deletes_its_tab(self):
        ns = {'drop_session': lambda task: self.session, 'delete': self.client.delete,
              'user_params': lambda session: {'userId': session['user_id']}}
        functions(self.client_source, {'close_session'}, ns)
        self.assertIsNone(ns['close_session']('durable'))
        self.client.delete.assert_called_once_with('/tabs/tab', {'userId': 'profile'})
        self.client.delete.reset_mock()
        self.session['tab_id'] = None
        ns['close_session']('durable')
        self.client.delete.assert_not_called()

    def test_viewport_uses_existing_durable_tab_and_checks_effective_dimensions(self):
        self.client.post.return_value = {'ok': True, 'effective': {'innerWidth': 430, 'innerHeight': 900}}
        result = json.loads(self.ns['handle_viewport']({'width': 430, 'height': 900},
                                                     session_id='durable', task_id='turn'))
        self.assertTrue(result['success'])
        self.client.post.assert_called_once_with('/tabs/tab/spartan/viewport', {
            'userId': 'profile', 'width': 430, 'height': 900, 'relaunch': False})
        self.client.get_session.assert_not_called()
        self.client.post.return_value['effective']['innerWidth'] = 800
        self.assertFalse(json.loads(self.ns['browser_viewport'](430, 900, 'durable'))['success'])

    def test_viewport_rejects_invalid_missing_stale_and_untrusted_transport_failures(self):
        for width, height in ((True, 900), (99, 900), (430, 4097), ('430', 900)):
            self.assertFalse(json.loads(self.ns['browser_viewport'](width, height, 'durable'))['success'])
        self.assertFalse(json.loads(self.ns['browser_viewport'](430, 900, 'unknown'))['success'])
        self.client.post.assert_not_called()
        self.client.post.side_effect = requests.HTTPError(response=SimpleNamespace(status_code=410))
        self.assertTrue(json.loads(self.ns['browser_viewport'](430, 900, 'durable'))['requires_navigation'])
        self.client.post.assert_called_once()
        self.session['tab_id'] = 'new'
        self.client.post.side_effect = RuntimeError('Bearer PRIVATE response body')
        result = self.ns['browser_viewport'](430, 900, 'durable')
        self.assertNotIn('PRIVATE', result)
        self.client.resync_tab.assert_not_called()

    def test_native_vault_origin_otp_redaction_and_page_api_bodies_are_not_reimplemented(self):
        original = (Path(SOURCE) / 'tools/browser_vault_tool.py').read_text()
        current = (self.root / 'tools/browser_vault_tool.py').read_text()
        bodies = lambda text: {n.name: ast.dump(ast.Module(body=n.body, type_ignores=[]))
                              for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
        for name in ('_page_eval', '_eval_js', '_eval_js_secret', '_bound_page_origin',
                     'browser_vault_fill', 'browser_vault_enter_code', '_fill_bound_page'):
            self.assertEqual(bodies(original)[name], bodies(current)[name], name)
        self.assertIn('kwargs.get("session_id") or kwargs.get("task_id")', current)
        self.assertEqual(current.count('@_spartan_vault.page_operation'), 3)


if __name__ == '__main__':
    unittest.main()
