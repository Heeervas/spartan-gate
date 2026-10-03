import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sys

MODULE_PATH = pathlib.Path(__file__).with_name('patches') / 'patch_checkpoint_rollback_scan.py'

def load_module():
    spec = importlib.util.spec_from_file_location('patch_checkpoint', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class CheckpointRollbackScanPatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_preserves_native_restore_and_is_idempotent(self):
        for gateway in (True, False):
            declaration = 'async def' if gateway else 'def'
            cwd = 'self._terminal_cwd()' if gateway else 'os.getenv("TERMINAL_CWD", os.getcwd())'
            source = f'class Commands:\n    {declaration} _handle_rollback_command(self, event):\n        cwd = {cwd}\n        return mgr.restore(cwd, "hash", safe=True)\n'
            changed = self.module.patch_handler(source, gateway=gateway)
            self.assertIn('mgr.restore(cwd, "hash", safe=True)', changed)
            self.assertEqual(changed, self.module.patch_handler(changed, gateway=gateway))
            compile(changed, '<test>', 'exec')

    def test_preflights_all_targets_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [pathlib.Path(tmp) / name for name in ('cm.py', 'gw.py', 'cli.py')]
            for p in paths: p.write_text('class Empty: pass\n')
            self.module.CM_TARGET, self.module.GW_TARGET, self.module.CLI_TARGET = paths
            with self.assertRaises(ValueError): self.module.main()
            for p in paths: self.assertEqual(p.read_text(), 'class Empty: pass\n')

    def scope(self, directories, cwd='/tmp', blocked=None):
        ns = {'Path': pathlib.Path, 'format_checkpoint_list': lambda entries, directory: directory}
        exec(self.module.CM_PATCH, ns)
        mgr = SimpleNamespace(list_checkpoints=lambda p: [], unsupported_backend_reason=lambda: blocked,
                              list_all_checkpoints=lambda: [{'workdir': p} for p in directories])
        mocks = {'hermes_constants': SimpleNamespace(get_hermes_home=lambda: '/unused'),
                 'tools.terminal_scope': SimpleNamespace(terminal_env=lambda key, default: default),
                 'hermes_cli.config': SimpleNamespace(read_raw_config=lambda: {})}
        with patch.dict(sys.modules, mocks): return ns['spartan_checkpoint_scope'](mgr, cwd)

    def test_selects_single_project(self):
        self.assertEqual(self.scope(['/projects/one']), ('/projects/one', None))

    def test_ambiguous_projects_cannot_restore(self):
        cwd, error = self.scope(['/projects/one', '/projects/two'])
        self.assertEqual(cwd, '/tmp')
        self.assertIn('Ambiguous rollback scope', error)

    def test_nested_project_is_unambiguous(self):
        self.assertEqual(self.scope(['/projects/one', '/other'], '/projects'), ('/projects/one', None))

    def test_unsupported_backend_keeps_native_denial(self):
        self.assertEqual(self.scope(['/projects/one'], blocked='remote backend'), ('/tmp', None))

if __name__ == '__main__': unittest.main()
