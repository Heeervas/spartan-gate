import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('dependency_recovery', Path(__file__).with_name('hermes_dependency_recovery.py'))
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


class DependencyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'image'
        (self.root / '.venv').mkdir(parents=True)
        self.path = Path(self.tmp.name) / 'facts.json'
        self.record = {'schema': 1, 'packages': {'venv': {'stamp': 'original-stamp', 'extras': [], 'environment': '/synthetic/selected', 'resolved_lock': '/synthetic/lock'}, 'other': {'version': 'keep'}}}
        self.path.write_text(json.dumps(self.record))
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.backup = Path(self.tmp.name) / 'backup'
        self.backup.mkdir(mode=0o700)
        self.locked = False
        self.calls = []

    def native_modules(self, change_during_lock=False):
        test = self

        @contextlib.contextmanager
        def runtime_lock(root, timeout=None):
            test.locked = True
            if change_during_lock:
                test.path.write_text('concurrent-edit')
            try:
                yield
            finally:
                test.locked = False

        class Facts:
            def __init__(self, path, strict=False):
                test.assertTrue(strict)
                test.assertTrue(test.locked)
            def record_state(self, name, stamp, extras):
                test.assertTrue(test.locked)
                test.assertEqual((test.backup / 'facts.original.json').read_text(), json.dumps(test.record))
                test.calls.append((name, stamp, extras))
                doc = json.loads(test.path.read_text())
                doc['packages'][name] = {'stamp': stamp, 'extras': extras}
                test.path.write_text(json.dumps(doc))

        modules = {}
        for name, attrs in {
            'pm.environments': {'committed_venv': Mock(return_value=Path('/synthetic/selected') if 'environment' in self.record['packages']['venv'] else None), 'runtime_facts_path': lambda root: self.path},
            'pm.features': {'installed_extras': Mock(return_value=list(RECOVERY.REQUIRED))},
            'pm.lock': {'Facts': Facts}, 'pm.paths': {'repo_root': lambda: self.root},
            'hermes_cli.runtime_state': {'runtime_lock': runtime_lock},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            modules[name] = module
        return patch.dict(sys.modules, modules)

    def test_dry_run_never_changes_selection_or_creates_backup(self):
        with self.native_modules(), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base'):
            self.assertEqual(RECOVERY.recover(self.root, self.sha), len(RECOVERY.REQUIRED))
        self.assertEqual(json.loads(self.path.read_text()), self.record)
        self.assertEqual(list(self.backup.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_apply_uses_native_fallback_preserves_stamp_and_other_packages(self):
        with self.native_modules(), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base'):
            RECOVERY.recover(self.root, self.sha, self.backup)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc['packages']['other'], self.record['packages']['other'])
        self.assertEqual(doc['packages']['venv'], {'stamp': 'original-stamp', 'extras': sorted(RECOVERY.REQUIRED)})
        self.assertEqual((self.backup / 'facts.original.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.calls), 1)

    def test_native_fallback_without_generation_pointers_is_recoverable(self):
        for key in ('environment', 'resolved_lock'):
            self.record['packages']['venv'].pop(key)
        self.path.write_text(json.dumps(self.record))
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.native_modules(), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base'):
            self.assertEqual(RECOVERY.recover(self.root, self.sha), len(RECOVERY.REQUIRED))
            self.assertEqual(self.calls, [])
            self.assertEqual(json.loads(self.path.read_text()), self.record)
            RECOVERY.recover(self.root, self.sha, self.backup)
        self.assertEqual(self.calls, [('venv', 'original-stamp', sorted(RECOVERY.REQUIRED))])
        self.assertEqual(json.loads(self.path.read_text())['packages']['other'], self.record['packages']['other'])

    def test_malformed_optional_generation_pointers_are_refused(self):
        for key in ('environment', 'resolved_lock'):
            for value in (None, '', False, [], {}):
                with self.subTest(key=key, value=value):
                    changed = json.loads(json.dumps(self.record))
                    changed['packages']['venv'][key] = value
                    with self.assertRaises(ValueError):
                        RECOVERY.validate_selection(json.dumps(changed).encode(), list(RECOVERY.REQUIRED))

    def test_concurrent_selection_change_is_not_overwritten(self):
        with self.native_modules(True), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base'):
            with self.assertRaisesRegex(ValueError, 'changed during preflight'):
                RECOVERY.recover(self.root, self.sha, self.backup)
        self.assertEqual(self.path.read_text(), 'concurrent-edit')
        self.assertEqual(list(self.backup.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_base_probe_failure_does_not_write(self):
        with self.native_modules(), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base', side_effect=ValueError('ABI mismatch')):
            with self.assertRaises(ValueError):
                RECOVERY.recover(self.root, self.sha, self.backup)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_wrong_hash_or_explicit_feature_selection_is_refused(self):
        with self.native_modules(), patch.object(sys, 'prefix', str(self.root / '.venv')), patch.object(RECOVERY, 'probe_base'):
            with self.assertRaisesRegex(ValueError, 'changed since diagnosis'):
                RECOVERY.recover(self.root, 'wrong-hash', self.backup)
        changed = json.loads(json.dumps(self.record))
        changed['packages']['venv']['extras'] = ['web']
        with self.assertRaises(ValueError):
            RECOVERY.validate_selection(json.dumps(changed).encode(), list(RECOVERY.REQUIRED))
        for inventory in [[], ['web'], None, list(RECOVERY.REQUIRED) + [None]]:
            with self.assertRaises(ValueError):
                RECOVERY.validate_selection(json.dumps(self.record).encode(), inventory)


if __name__ == '__main__':
    unittest.main()
