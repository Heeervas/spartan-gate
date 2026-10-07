import importlib.util
import logging
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from typing import Optional


BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('lsp_sdk_patch', BASE / 'patches/patch_lsp_sdk_repair.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
SOURCE = (BASE / 'fixtures/lsp_sdk_install.py').read_text()


class LspSdkRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'alpha'
        self.install_calls = []
        servers = ModuleType('agent.lsp.servers')
        servers._node_modules_trees = lambda binary, root: [str(self.home / 'lsp/node_modules')]
        servers._typescript_sdk_dir = lambda trees: next((str(Path(tree) / 'typescript/lib') for tree in trees if (Path(tree) / 'typescript/lib/typescript.js').is_file()), None)
        self.patcher = patch.dict(sys.modules, {'agent.lsp.servers': servers})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.recipe = {'strategy': 'npm', 'bin': 'typescript-language-server', 'pkg': 'typescript-language-server', 'extra_pkgs': ['typescript@6']}
        self.namespace = {'Path': Path, 'Optional': Optional, 'shutil': shutil, 'threading': threading,
                          'logger': logging.getLogger('lsp-test'), 'INSTALL_RECIPES': {'typescript-language-server': self.recipe},
                          '_install_results': {}, '_install_locks': {}, '_install_lock_meta': threading.Lock(),
                          '_existing_binary': lambda name: str(self.home / 'lsp/bin' / name),
                          'hermes_lsp_bin_dir': lambda: self.home / 'lsp/bin', 'TYPESCRIPT_SDK_PKG': 'typescript@6',
                          '_INSTALLERS': {'npm': Mock(side_effect=AssertionError('must preserve installed server'))}}
        exec(PATCH.patch_text(SOURCE), self.namespace)
        self.namespace['_install_npm'] = self.install_sdk

    def install_sdk(self, pkg, binary):
        self.install_calls.append((pkg, binary, self.home))
        sdk = self.home / 'lsp/node_modules/typescript/lib'
        sdk.mkdir(parents=True)
        for name in ['typescript.js', 'tsserver.js']:
            (sdk / name).write_text('synthetic inert SDK marker')
        return str(self.home / 'lsp/bin' / binary)

    def test_native_existing_binary_shortcut_reproduces_false_installed(self):
        namespace = dict(self.namespace)
        exec(SOURCE, namespace)
        self.assertEqual(namespace['detect_status']('typescript-language-server'), 'installed')
        self.assertEqual(self.namespace['detect_status']('typescript-language-server'), 'missing')

    def test_repairs_only_companion_sdk_and_reports_ready_after_validation(self):
        binary = self.namespace['try_install']('typescript-language-server')
        self.assertEqual(binary, str(self.home / 'lsp/bin/typescript-language-server'))
        self.assertEqual(self.install_calls, [('typescript@6', 'typescript-language-server', self.home)])
        self.assertEqual(self.namespace['detect_status']('typescript-language-server'), 'installed')
        self.namespace['try_install']('typescript-language-server')
        self.assertEqual(len(self.install_calls), 1)

    def test_manual_and_off_never_install_broken_sdk(self):
        for strategy in ['manual', 'off']:
            self.assertIsNone(self.namespace['try_install']('typescript-language-server', strategy))
        self.assertEqual(self.install_calls, [])

    def test_install_cache_and_locks_do_not_cross_profiles(self):
        first = self.namespace['try_install']('typescript-language-server')
        self.home = Path(self.tmp.name) / 'beta'
        second = self.namespace['try_install']('typescript-language-server')
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.install_calls), 2)

    def test_failed_repair_never_claims_server_is_ready(self):
        self.namespace['_install_npm'] = Mock(return_value=str(self.home / 'lsp/bin/typescript-language-server'))
        self.assertIsNone(self.namespace['try_install']('typescript-language-server'))
        self.assertEqual(self.namespace['detect_status']('typescript-language-server'), 'missing')

    def test_idempotence_and_anchor_drift(self):
        result = PATCH.patch_text(SOURCE)
        self.assertEqual(PATCH.patch_text(result), result)
        with self.assertRaises(ValueError):
            PATCH.patch_text('unexpected upstream source')


if __name__ == '__main__':
    unittest.main()
