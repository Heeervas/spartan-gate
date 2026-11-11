"""Run the unmodified entrypoint with isolated synthetic /state and vendor exec."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('bwrap') and shutil.which('node'), 'requires bubblewrap and node')
class MCPHubEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mcphub-entrypoint-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.settings = self.state / 'mcp_settings.json'
        self.password = self.state / 'bootstrap-password'
        self.password.write_text('synthetic-bootstrap-only\n')
        self.vendor = self.root / 'vendor.sh'
        self.vendor.write_text('#!/bin/sh\nprintf "VENDOR_STARTED\\n"\nenv\n')
        self.vendor.chmod(0o700)

    def run_entrypoint(self):
        command = ['bwrap', '--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent']
        for path in ('/usr', '/bin', '/lib', '/lib64'):
            if Path(path).exists():
                command += ['--ro-bind', path, path]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                    '--bind', str(self.state), '/state', '--tmpfs', '/usr/local/bin',
                    '--ro-bind', str(self.vendor), '/usr/local/bin/entrypoint.sh',
                    '--ro-bind', str(ROOT / 'infra/mcphub/entrypoint.sh'), '/gate-entrypoint.sh',
                    '--setenv', 'HOME', '/state/home', '/bin/sh', '/gate-entrypoint.sh']
        return subprocess.run(command, env={'PATH': '/usr/bin:/bin',
                                           'ADMIN_PASSWORD': 'synthetic-inherited',
                                           'KEEP_ME': 'synthetic-preserved'},
                              capture_output=True, text=True, timeout=15, check=False)

    def put_users(self, users):
        self.settings.write_text(json.dumps({'users': users, 'mcpServers': {}, 'keep': [1, 2]}, indent=2) + '\n')

    def assert_started(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('VENDOR_STARTED\n', result.stdout)
        self.assertIn('KEEP_ME=synthetic-preserved\n', result.stdout)

    def test_provisioned_admin_clears_inherited_password_and_preserves_accounts(self):
        self.put_users([{'username': 'existing', 'password': 'synthetic-hash', 'isAdmin': True}])
        before = self.settings.read_bytes()
        for password in ('present', 'missing', 'empty'):
            with self.subTest(password=password):
                if password == 'missing':
                    self.password.unlink()
                elif password == 'empty':
                    self.password.touch()
                result = self.run_entrypoint()
                self.assert_started(result)
                self.assertNotIn('ADMIN_PASSWORD=', result.stdout)
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertEqual(result.stderr, '')

    def test_bootstrap_then_restart_drops_password(self):
        self.put_users([])
        before = self.settings.read_bytes()
        result = self.run_entrypoint()
        self.assert_started(result)
        self.assertIn('ADMIN_PASSWORD=synthetic-bootstrap-only\n', result.stdout)
        self.assertIn('must be restarted before any backend is granted', result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertEqual(self.settings.read_bytes(), before)
        # Simulate the vendor persisting its first admin, not an entrypoint write.
        self.put_users([{'username': 'admin', 'password': 'synthetic-hash', 'isAdmin': True}])
        restarted = self.run_entrypoint()
        self.assert_started(restarted)
        self.assertNotIn('ADMIN_PASSWORD=', restarted.stdout)

    def test_invalid_or_unreadable_settings_never_executes_vendor(self):
        for raw in ('{', '[]', '{}', '{"users":null}', '{"users":{}}',
                    '{"users":[{"username":"member","isAdmin":false}]}',
                    '{"users":[null]}'):
            with self.subTest(raw=raw):
                self.settings.write_text(raw)
                result = self.run_entrypoint()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('VENDOR_STARTED', result.stdout)
                self.assertEqual(self.settings.read_text(), raw)
        self.settings.unlink()
        self.settings.mkdir()  # A directory cannot be read as settings JSON.
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('VENDOR_STARTED', result.stdout)

    def test_bootstrap_requires_nonempty_password_and_settings(self):
        self.put_users([])
        for mode in ('missing', 'empty', 'newlines'):
            with self.subTest(mode=mode):
                if mode == 'missing':
                    self.password.unlink()
                else:
                    self.password.write_text('' if mode == 'empty' else '\n\n')
                result = self.run_entrypoint()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('VENDOR_STARTED', result.stdout)
        self.settings.unlink()
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('VENDOR_STARTED', result.stdout)


if __name__ == '__main__':
    unittest.main()
