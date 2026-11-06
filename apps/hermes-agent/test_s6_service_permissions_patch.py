"""Exercise the pinned upstream's separated s6 trees and Gate numeric identity.

The old permission patch's root-scandir mutation/registration prohibition is
obsolete. These checks use the real upstream program, not that retired fixture.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

APP = Path(__file__).parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')


def patcher(name):
    spec = importlib.util.spec_from_file_location(name, APP / 'patches' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE for native s6 topology')
class NativeS6TopologyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(SOURCE)
        self.identity = patcher('patch_runtime_identity_drop')
        self.gateway = patcher('patch_gateway_service_cdp_env')

    def manager(self):
        source = (self.root / 'hermes_cli/service_manager.py').read_text()
        updated = self.gateway.patch_source(source)
        self.assertEqual(self.gateway.patch_source(updated), updated)
        module = types.ModuleType('native_s6_manager')
        exec(compile(updated, 'service_manager.py', 'exec'), module.__dict__)
        return module

    def test_native_registration_uses_only_unprivileged_dynamic_tree(self):
        module = self.manager()
        manager = module.S6ServiceManager()
        self.assertEqual(module.S6_STATIC_SCANDIR, Path('/run/service'))
        self.assertEqual(manager.scandir, Path('/run/hermes-service'))
        self.assertTrue(manager.supports_runtime_registration())
        self.assertEqual(manager._service_dir('work'), Path('/run/hermes-service/gateway-work'))
        with self.assertRaises(ValueError):
            manager._service_dir('../service')

    def test_root_control_commands_drop_to_numeric_owner(self):
        module = self.manager()
        with patch.dict(os.environ, {'SPARTAN_HERMES_RUN_UID': '1234', 'SPARTAN_HERMES_RUN_GID': '2345'}), \
                patch.object(module.os, 'geteuid', return_value=0), \
                patch.object(module, '_s6_run') as run:
            module.S6ServiceManager._s6('s6-svscanctl', '-a', '/run/hermes-service')
            self.assertEqual(run.call_args.args[:6], ('s6-applyuidgid', '-u', '1234', '-g', '2345', '/command/s6-svscanctl'))
            account = module._root_hermes_account()
            self.assertEqual((account.pw_uid, account.pw_gid), (1234, 2345))

    def test_new_scanner_and_reconciler_share_directory_and_numeric_owner(self):
        reconcile = self.identity.patch_reconcile_source(
            (self.root / 'docker/cont-init.d/02-reconcile-profiles').read_text())
        scanner = self.identity.patch_profile_scanner_source(
            (self.root / 'docker/s6-rc.d/profile-gateways/run').read_text())
        self.assertEqual(self.identity.patch_reconcile_source(reconcile), reconcile)
        self.assertEqual(self.identity.patch_profile_scanner_source(scanner), scanner)
        for source in (reconcile, scanner):
            subprocess.run(['sh', '-n'], input=source, text=True, check=True)
            self.assertNotIn('chown hermes:hermes /run/service', source)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$GATEWAY_SCANDIR"', reconcile)
        self.assertIn('s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid"', reconcile)
        self.assertIn('s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" s6-svscan "$scandir"', scanner)

    def test_reconciler_derives_owner_without_inheriting_stage2_shell_locals(self):
        source = self.identity.patch_reconcile_source(
            (self.root / 'docker/cont-init.d/02-reconcile-profiles').read_text())
        block = source[source.index('GATEWAY_SCANDIR='):source.index('# Skip the drop')]
        with tempfile.TemporaryDirectory() as raw:
            block = block.replace('/run/hermes-service', raw + '/gateways')
            script = 'set -eu\nchown() { printf "%s\\n" "$1"; }\n' + block
            environment = dict(os.environ, SPARTAN_HERMES_RUN_UID='12345', SPARTAN_HERMES_RUN_GID='12346')
            # Neither absent nor stale inherited stage2 state may own this directory.
            for inherited in (None, '0:0'):
                if inherited is None:
                    environment.pop('SPARTAN_HERMES_RUNTIME_OWNER', None)
                else:
                    environment['SPARTAN_HERMES_RUNTIME_OWNER'] = inherited
                result = subprocess.run(['sh'], input=script, env=environment,
                                        capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), '12345:12346')

    def test_registration_publishes_real_native_slot_in_disposable_tree(self):
        module = self.manager()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module, '_root_hermes_account', return_value=None), \
                patch.object(module.S6ServiceManager, '_render_finish_script', return_value='#!/bin/sh\nexit 0\n'), \
                patch.object(module.S6ServiceManager, '_s6', return_value=types.SimpleNamespace(returncode=0, stderr='')) as control:
            manager = module.S6ServiceManager(Path(temporary))
            manager.register_profile_gateway('work', start_now=False)
            slot = Path(temporary) / 'gateway-work'
            self.assertTrue((slot / 'down').is_file())
            run = (slot / 'run').read_text()
            subprocess.run(['sh', '-n'], input=run, text=True, check=True)
            self.assertIn('hermes -p work gateway run --replace', run)
            self.assertIn('SPARTAN_HERMES_RUN_UID', run)
            self.assertTrue(control.called)
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), ['gateway-work'])


if __name__ == '__main__':
    unittest.main()
