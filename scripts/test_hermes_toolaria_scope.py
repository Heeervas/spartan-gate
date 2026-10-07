import ast
from contextvars import ContextVar
import importlib.util
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('plugin_repairs', BASE / 'hermes_plugin_scope_repairs.py')
REPAIRS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIRS)
SOURCE = (BASE / 'fixtures/toolaria_scope.py').read_text()
PASSREF = (BASE / 'fixtures/toolaria_passref_scope.py').read_text()


class ToolariaScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = ContextVar('toolaria_test_home', default=self.root / 'neutral')
        constants = ModuleType('hermes_constants')
        constants.get_hermes_home = self.home.get
        labels = ModuleType('labels')
        labels._parse_tool_label_map = lambda value: None
        entities = ModuleType('entities')
        entities.parse_entity_registry = lambda value: None
        self.registry = SimpleNamespace(mark_ambient=Mock())
        module = ModuleType('tools.registry')
        module.registry = self.registry
        self.patcher = patch.dict(sys.modules, {'hermes_constants': constants, 'labels': labels, 'entities': entities, 'tools.registry': module})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def namespace(self):
        namespace = {'Path': Path, 'os': os, '__package__': '', 'logger': logging.getLogger('toolaria-test'),
                     '_PHASE1_DEFAULTS': {}, '_PHASE2_DEFAULTS': {}, '_UNCONDITIONAL_EXCLUDES': frozenset(),
                     '_RESCUER_TOOLSET': 'rescuer', '_load_defaults': lambda: {'store_path': '~/.hermes/toolaria'},
                     '_BLOB_ID_RE': re.compile(r'^[a-f0-9]{12}$'), '_is_rescuable': lambda tool: True,
                     '_make_passref_mw': lambda *args: None, '_on_start': Mock(), '_on_end': Mock(),
                     '_status_cmd': Mock(), '_rotate_key_cmd': Mock()}
        exec(REPAIRS.patch_toolaria(SOURCE), namespace)
        return namespace

    def test_same_session_id_in_two_profiles_uses_distinct_stores(self):
        class Store:
            def __init__(self, config):
                self.path = Path(config['store_path'])
                self.path.mkdir()
                (self.path / 'synthetic-payload').write_text(self.path.parent.name)
            def lazy_sweep(self):
                pass
            def backfill_labels(self):
                pass
            def fetch(self, bid, mode, **kwargs):
                return (self.path / 'synthetic-payload').read_text() if kwargs['session_id'] == 'same-sid' else 'denied'

        namespaces = []
        for owner in ['alpha', 'beta']:
            home = self.root / owner
            home.mkdir()
            (home / 'config.yaml').write_text('toolaria:\n  store_path: ~/.hermes/toolaria\n')
            token = self.home.set(home)
            try:
                namespace = self.namespace()
                namespace['BlobStore'] = Store
                ctx = SimpleNamespace(get_config=lambda key, default: default, register_hook=Mock(), register_tool=Mock(), register_command=Mock())
                namespace['register'](ctx)
                namespaces.append(namespace)
            finally:
                self.home.reset(token)
        for owner, namespace in zip(['alpha', 'beta'], namespaces):
            self.assertEqual(namespace['_fetch']({'id': 'abcdef123456'}, session_id='same-sid'), owner)
            self.assertEqual(namespace['_fetch']({'id': 'abcdef123456'}, session_id='other-sid'), 'denied')
        self.assertEqual(self.home.get(), self.root / 'neutral')

    def test_fetch_denies_absent_identity_and_model_audit_without_store_access(self):
        namespace = self.namespace()
        store = Mock()
        namespace['_store'] = store
        for sid in [None, '', '   ', 42]:
            self.assertIn('owning session', namespace['_fetch']({'id': 'abcdef123456'}, session_id=sid))
        self.assertIn('operator command', namespace['_fetch']({'id': 'abcdef123456', 'mode': 'audit'}, session_id='valid-sid'))
        store.fetch.assert_not_called()
        store._audit_summary.assert_not_called()

    def test_store_path_cannot_escape_or_borrow_process_profile(self):
        namespace = self.namespace()
        home = self.root / 'owner'
        home.mkdir()
        token = self.home.set(home)
        try:
            with patch.dict(os.environ, {'HERMES_HOME': '/launch-profile'}):
                self.assertEqual(namespace['_merge_cfg']({})['store_path'], str(home / 'toolaria'))
            for path in [str(self.root / 'other'), '../other', '~/.hermes/../../other']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    namespace['_merge_cfg']({'store_path': path})
            ctx = SimpleNamespace(get_config=lambda key, default: 'local-store' if key == 'store_path' else default)
            self.assertEqual(namespace['_safe_cfg'](ctx)['store_path'], 'local-store')
        finally:
            self.home.reset(token)

    def test_existing_authority_import_failure_never_selects_process_home(self):
        namespace = self.namespace()
        with patch.dict(sys.modules, {'hermes_constants': ModuleType('hermes_constants')}), patch.dict(os.environ, {'HERMES_HOME': '/launch-profile'}):
            with self.assertRaises(ImportError):
                namespace['_profile_home']()

    def test_unavailable_ambient_retrieval_never_stores_or_truncates_result(self):
        namespace = self.namespace()
        namespace['_store'] = Mock()
        namespace['_rescue'] = Mock()
        namespace['_cfg'] = {'max_result_chars': 1}
        for mark in [None, 1, Mock(side_effect=RuntimeError('synthetic failure'))]:
            self.registry.mark_ambient = mark
            namespace['_retrieval_available'] = namespace['_mark_rescuer_ambient']()
            self.assertFalse(namespace['_retrieval_available'])
            self.assertIsNone(namespace['_on_transform']('web_search', 'original result', session_id='sid'))
        namespace['_rescue'].assert_not_called()
        namespace['_store'].put.assert_not_called()

    def test_reload_clears_previous_retrieval_success(self):
        namespace = self.namespace()
        namespace['_safe_cfg'] = lambda ctx: {}
        namespace['_merge_cfg'] = lambda cfg: {}
        namespace['BlobStore'] = lambda cfg: Mock()
        ctx = SimpleNamespace(register_hook=Mock(), register_tool=Mock(), register_command=Mock())
        namespace['register'](ctx)
        self.assertTrue(namespace['_retrieval_available'])
        self.registry.mark_ambient = None
        namespace['register'](ctx)
        self.assertFalse(namespace['_retrieval_available'])
        self.assertIsNone(namespace['_on_transform']('web_search', 'original result', session_id='sid'))

    def test_unidentified_pass_reference_never_reads_store(self):
        namespace = {'re': re, 'TOKEN_RE': re.compile(r'tla:[a-f0-9]{12}(?:@\d+)?')}
        source = REPAIRS.patch_toolaria_passref(PASSREF)
        exec(source, namespace)
        store = Mock()
        for sid in ['', None, '  ', 42]:
            result, denied = namespace['_expand_string']('payload tla:abcdef123456', store, {}, {}, session_id=sid)
            self.assertTrue(denied)
            self.assertIn('owning session required', result)
        self.assertEqual(store.mock_calls, [])

    def test_patches_are_idempotent_and_reject_drift(self):
        for patcher, source in [(REPAIRS.patch_toolaria, SOURCE), (REPAIRS.patch_toolaria_passref, PASSREF)]:
            repaired = patcher(source)
            self.assertEqual(patcher(repaired), repaired)
            with self.assertRaises(ValueError):
                patcher('upstream drift')


if __name__ == '__main__':
    unittest.main()
