"""Synthetic Git/stdio contracts; native tests require explicit isolated program inputs."""
import contextlib
import io
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import codebase_memory_local as launcher


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.previous = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous)
        self.home = patch.dict(os.environ, HOME=str(self.base / 'home'))
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_subdirectory_and_worktree_resolve_to_full_roots(self):
        nested = self.repo / 'src'
        nested.mkdir()
        os.chdir(nested)
        self.assertEqual(launcher.repository_root(), self.repo)
        launcher.git(self.repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '--allow-empty', '-qm', 'fixture')
        worktree = self.base / 'worktree'
        launcher.git(self.repo, 'worktree', 'add', '--detach', str(worktree))
        os.chdir(worktree)
        self.assertEqual(launcher.repository_root(), worktree)

    def test_home_and_nonrepository_refuse(self):
        with patch.dict(os.environ, HOME=str(self.repo)):
            with self.assertRaisesRegex(ValueError, 'HOME'):
                launcher.repository_root()
        os.chdir(self.base)
        with self.assertRaises(subprocess.CalledProcessError):
            launcher.repository_root()

    def test_sensitive_untracked_exclusion_does_not_hide_tracked_files(self):
        secret = self.repo / '.env'
        secret.write_text('SYNTHETIC_ONLY=not-a-credential\n')
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root()
        (self.repo / '.gitignore').write_text('.env\nprivate/\n')
        self.assertEqual(launcher.repository_root(), self.repo)
        launcher.git(self.repo, 'add', '-f', '.env')
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root()
        secret.unlink()
        launcher.git(self.repo, 'rm', '--cached', '-f', '.env')
        private = self.repo / 'private/hidden.py'
        private.parent.mkdir()
        private.touch()
        self.assertEqual(launcher.repository_root(), self.repo)
        launcher.git(self.repo, 'add', '-f', 'private/hidden.py')
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root()

    def test_opaque_embedded_repository_and_submodule_refuse(self):
        nested = self.repo / 'nested'
        subprocess.run(['git', 'init', '-q', str(nested)], check=True)
        (nested / 'credentials').mkdir()
        (nested / 'credentials/hidden.py').write_text('SYNTHETIC_NESTED_PRIVATE = 1\n')
        with self.assertRaisesRegex(ValueError, 'Opaque'):
            launcher.repository_root()
        (self.repo / '.gitignore').write_text('nested/\n')
        self.assertEqual(launcher.repository_root(), self.repo)
        launcher.git(self.repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '--allow-empty', '-qm', 'fixture')
        commit = launcher.git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        launcher.git(self.repo, 'update-index', '--add', '--cacheinfo', f'160000,{commit},module')
        (self.repo / 'module').mkdir()
        with self.assertRaisesRegex(ValueError, 'Opaque'):
            launcher.repository_root()

    def test_local_git_info_exclusion_is_not_trusted(self):
        (self.repo / '.env').touch()
        (self.repo / '.git/info/exclude').write_text('.env\n')
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root()

    def test_exact_environment_templates_are_allowed_tracked_and_untracked(self):
        for name in ('.env.example', '.env.sample', '.env.template'):
            (self.repo / name).write_text('PUBLIC_PLACEHOLDER=value\n')
        self.assertEqual(launcher.repository_root(), self.repo)
        launcher.git(self.repo, 'add', '.')
        self.assertEqual(launcher.repository_root(), self.repo)
        for name in ('.env.local', '.env.production', '.env.example.backup', '.ENV.EXAMPLE'):
            with self.subTest(name=name):
                (self.repo / name).touch()
                with self.assertRaisesRegex(ValueError, 'Sensitive'):
                    launcher.repository_root()
                (self.repo / name).unlink()

    def test_sensitive_names_and_symlink_policy(self):
        for path in ('private/source.py', 'config/credentials.json', 'cert.pem', 'nested/.env.local'):
            with self.subTest(path=path):
                file = self.repo / path
                file.parent.mkdir(exist_ok=True, parents=True)
                file.touch()
                with self.assertRaisesRegex(ValueError, 'Sensitive'):
                    launcher.repository_root()
                file.unlink()
        (self.repo / '.cbmignore').symlink_to(self.base / 'policy')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            launcher.repository_root()

    def test_public_template_pin_is_exact_and_detects_changed_bytes(self):
        public = self.repo / 'private/public.py'
        public.parent.mkdir()
        content = b'PUBLIC_DOCUMENTATION_PLACEHOLDER = 1\n'
        public.write_bytes(content)
        pin = 'private/public.py=' + hashlib.sha256(content).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root()
        self.assertEqual(launcher.repository_root([pin]), self.repo)
        launcher.git(self.repo, 'add', '.')
        self.assertEqual(launcher.repository_root([pin]), self.repo)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            launcher.repository_root(['private/public.py=' + '0' * 64])
        public.write_bytes(content + b'CHANGED_PUBLIC_CONTENT = 2\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            launcher.run('/must-not-execute', self.base, [pin])
        self.assertFalse(list(self.base.glob('session-*')))

    def test_same_global_pins_allow_absence_but_not_unknown_private_bytes(self):
        public = self.repo / 'private/public.py'
        public.parent.mkdir()
        content = b'PUBLIC_ONLY = 1\n'
        public.write_bytes(content)
        global_pins = ['private/public.py=' + hashlib.sha256(content).hexdigest()]
        self.assertEqual(launcher.repository_root(global_pins), self.repo)
        other = self.base / 'other'
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        os.chdir(other)
        self.assertEqual(launcher.repository_root(global_pins), other)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            launcher.repository_root(['absent.py=malformed'])
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            launcher.repository_root(global_pins + global_pins)
        (other / 'private').mkdir()
        copy = other / 'private/public.py'
        copy.write_bytes(b'UNAPPROVED_PRIVATE_BYTES = 2\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            launcher.repository_root(global_pins)
        copy.write_bytes(content)
        self.assertEqual(launcher.repository_root(global_pins), other)
        (other / 'private/unapproved.py').touch()
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root(global_pins)
        os.chdir(self.repo)
        public.write_bytes(b'CHANGED = 2\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            launcher.run('/must-not-execute', self.base, global_pins)
        self.assertFalse(list(self.base.glob('session-*')))

    def test_public_template_pin_refuses_invalid_paths_and_unpinned_private(self):
        public = self.repo / 'private/public.py'
        public.parent.mkdir()
        public.touch()
        digest = hashlib.sha256(b'').hexdigest()
        for name in ('/private/public.py', '../public.py', 'private/../public.py',
                     'private/*.py', 'private/', './private/public.py', '.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                launcher.repository_root([name + '=' + digest])
        with self.assertRaises(ValueError):
            launcher.repository_root(['private/public.py=not-a-sha'])
        pin = 'private/public.py=' + digest
        with self.assertRaises(ValueError):
            launcher.repository_root([pin, pin])
        (self.repo / 'private/unapproved.py').touch()
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            launcher.repository_root([pin])
        self.assertEqual(launcher.repository_root([pin, 'private/unapproved.py=' + digest]), self.repo)

    def test_public_template_pin_refuses_symlink_without_reading_target(self):
        private = self.repo / 'private'
        private.mkdir()
        target = self.base / 'public.py'
        target.touch()
        (private / 'link.py').symlink_to(target)
        with patch.object(Path, 'open', side_effect=AssertionError('must not read target')):
            with self.assertRaisesRegex(ValueError, 'symlink'):
                launcher.repository_root(['private/link.py=' + hashlib.sha256(b'').hexdigest()])

    def test_state_refuses_symlinks_and_writable_ancestry(self):
        state = self.base / 'state'
        state.mkdir(mode=0o700)
        link = self.base / 'link'
        link.symlink_to(state, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'trusted'):
            launcher.trusted_state(link, self.repo)
        state.chmod(0o770)
        with self.assertRaisesRegex(ValueError, 'trusted'):
            launcher.trusted_state(state, self.repo)
        with patch('scripts.codebase_memory_local.trusted_state', return_value=self.base):
            with self.assertRaisesRegex(ValueError, 'approved'):
                launcher.run(__file__, self.base)
        self.assertFalse(list(self.base.glob('session-*')))

    def test_failure_diagnostics_do_not_emit_native_payload(self):
        error = subprocess.CalledProcessError(1, 'native', output=b'SYNTHETIC_PRIVATE_SOURCE')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['launcher', '--binary', '/program', '--state-parent', '/state']), \
                patch.object(launcher, 'run', side_effect=error), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(launcher.main(), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertNotIn('SYNTHETIC_PRIVATE_SOURCE', stderr.getvalue())
        self.assertIn('refused startup', stderr.getvalue())


@unittest.skipUnless(os.environ.get('CBM_TEST_BINARY') and os.environ.get('CBM_TEST_SCRATCH'),
                     'Native proof requires explicit network/PID-isolated binary and scratch')
class NativeTests(unittest.TestCase):
    def setUp(self):
        # Retain native storage until the enclosing PID namespace is gone. No
        # TemporaryDirectory cleanup races a daemon still using its database.
        self.base = Path(tempfile.mkdtemp(dir=os.environ['CBM_TEST_SCRATCH']))
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / '.gitignore').write_text('.env\nprivate/\n')
        (self.repo / '.env').write_text('NATIVE_HIDDEN_ENV=synthetic\n')
        (self.repo / 'private').mkdir()
        (self.repo / 'private/hidden.py').write_text('NATIVE_HIDDEN_PRIVATE = 1\n')
        (self.repo / 'public.py').write_text('def public_marker():\n    return "NATIVE_PUBLIC_MARKER"\n')
        (self.repo / 'scripts').mkdir()
        (self.repo / 'scripts/ops.py').write_text('def operator_marker():\n    return "NATIVE_OPERATOR_MARKER"\n')
        self.assertEqual(launcher.trusted_state(self.state, self.repo), self.state)
        with self.assertRaisesRegex(ValueError, 'outside'):
            launcher.trusted_state(self.repo, self.repo)
        self.binary = os.environ['CBM_TEST_BINARY']
        self.env = dict(os.environ, HOME=str(self.base / 'home'))
        Path(self.env['HOME']).mkdir()
        self.buffer = b''
        self.sequence = 0

    def call(self, method, params):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}
        self.proc.stdin.write((json.dumps(request) + '\n').encode())
        self.proc.stdin.flush()
        deadline = time.monotonic() + 40
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if b'\n' not in self.buffer:
                    self.assertTrue(selector.select(max(0, deadline - time.monotonic())), method)
                    chunk = os.read(self.proc.stdout.fileno(), 65536)
                    self.assertTrue(chunk, 'unexpected MCP EOF')
                    self.buffer += chunk
                while b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    message = json.loads(line)  # Any launcher stdout contaminates this contract.
                    if message.get('id') == self.sequence:
                        return message
        self.fail('MCP deadline exceeded')

    def tool(self, name, **arguments):
        return self.call('tools/call', {'name': name, 'arguments': arguments})['result']

    def test_actual_index_search_snippet_and_normal_eof(self):
        public = self.repo / 'private/public_template.py'
        content = b'def approved_template():\n    return "NATIVE_APPROVED_PUBLIC_TEMPLATE"\n'
        public.write_bytes(content)
        launcher.git(self.repo, 'add', '-f', 'private/public_template.py')
        command = [sys.executable, str(Path(launcher.__file__).resolve()),
                   '--binary', self.binary, '--state-parent', str(self.state),
                   '--public-template', 'private/public_template.py=' + hashlib.sha256(content).hexdigest()]
        with (self.base / 'stderr').open('wb') as stderr:
            self.proc = subprocess.Popen(command, cwd=self.repo, env=self.env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
            try:
                self.assertIn('result', self.call('initialize', {
                    'protocolVersion': '2024-11-05', 'capabilities': {},
                    'clientInfo': {'name': 'synthetic-native-test', 'version': '1'}}))
                self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                self.proc.stdin.flush()
                projects = self.tool('list_projects', include_details=True)
                self.assertIn('current_repository', json.dumps(projects))
                search = self.tool('search_code', project='current_repository',
                                   pattern='NATIVE_', mode='full', format='json')
                text = json.dumps(search)
                self.assertIn('NATIVE_PUBLIC_MARKER', text)
                self.assertIn('NATIVE_OPERATOR_MARKER', text)
                self.assertNotIn('NATIVE_HIDDEN', text)
                self.assertIn('NATIVE_APPROVED_PUBLIC_TEMPLATE', text)
                template = self.tool('get_code_snippet', project='current_repository',
                                     qualified_name='approved_template', source_mode='full', format='json')
                self.assertIn('NATIVE_APPROVED_PUBLIC_TEMPLATE', json.dumps(template))
                hidden = self.tool('get_code_snippet', project='current_repository',
                                   qualified_name='NATIVE_HIDDEN_PRIVATE', format='json')
                self.assertTrue(hidden['isError'])
                snippet = self.tool('get_code_snippet', project='current_repository',
                                    qualified_name='public_marker', source_mode='full', format='json')
                self.assertIn('NATIVE_PUBLIC_MARKER', json.dumps(snippet))
                denied = self.tool('index_repository', repo_path=str(self.repo / 'private'))
                self.assertTrue(denied['isError'])
                self.assertIn('not available in the analysis', json.dumps(denied))
                missing = self.tool('search_code', project='other_repository', pattern='NATIVE_')
                self.assertTrue(missing['isError'])
                print('NATIVE_RECEIPTS=' + json.dumps({'projects': projects, 'search': search,
                      'snippet': snippet, 'approved_public_template': template, 'hidden_snippet_denial': hidden,
                      'runtime_index_denial': denied, 'other_project_denial': missing}))
            finally:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
                self.proc.stdout.close()
        self.assertEqual(self.proc.returncode, 0)
        sessions = list(self.state.iterdir())
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].stat().st_mode & 0o777, 0o700)
        self.assertTrue((sessions[0] / '_config.db').is_file())
        # Retention is intentional, not a claim that the native daemon was reaped.
        print('NATIVE_EOF_OK; retained uniquely owned cache:', sessions[0])

    def test_actual_other_root_indexes_with_same_global_pin_arguments(self):
        other = self.base / 'other'
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        (other / 'second.py').write_text('def second_marker():\n    return "NATIVE_SECOND_ROOT"\n')
        content = b'PUBLIC_TEMPLATE = "NATIVE_APPROVED_GLOBAL_TEMPLATE"\n'
        (self.repo / 'private/public_template.py').write_bytes(content)
        launcher.git(self.repo, 'add', '-f', 'private/public_template.py')
        command = [sys.executable, str(Path(launcher.__file__).resolve()),
                   '--binary', self.binary, '--state-parent', str(self.state),
                   '--public-template', 'private/public_template.py=' + hashlib.sha256(content).hexdigest()]
        for selected, marker in ((self.repo, 'NATIVE_PUBLIC_MARKER'), (other, 'NATIVE_SECOND_ROOT')):
            self.buffer = b''
            with (self.base / (selected.name + '.stderr')).open('wb') as stderr:
                self.proc = subprocess.Popen(command, cwd=selected, env=self.env,
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
                try:
                    self.assertIn('result', self.call('initialize', {
                        'protocolVersion': '2024-11-05', 'capabilities': {},
                        'clientInfo': {'name': 'global-args-test', 'version': '1'}}))
                    self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                    self.proc.stdin.flush()
                    search = self.tool('search_code', project='current_repository',
                                       pattern='NATIVE_', mode='full', format='json')
                    self.assertIn(marker, json.dumps(search))
                    self.assertNotIn('NATIVE_HIDDEN', json.dumps(search))
                    if selected == other:
                        self.assertNotIn('NATIVE_APPROVED_GLOBAL_TEMPLATE', json.dumps(search))
                    else:
                        self.assertIn('NATIVE_APPROVED_GLOBAL_TEMPLATE', json.dumps(search))
                    print('GLOBAL_ARGS_ROOT_SEARCH=' + json.dumps(search))
                finally:
                    self.proc.stdin.close()
                    self.proc.wait(timeout=10)
                    self.proc.stdout.close()
            self.assertEqual(self.proc.returncode, 0)
        self.assertEqual(len(list(self.state.iterdir())), 2)
        (other / 'private').mkdir()
        (other / 'private/credentials.json').write_text('SYNTHETIC_PRIVATE_ONLY\n')
        refused = subprocess.run(command, cwd=other, env=self.env, input=b'',
                                 capture_output=True, timeout=40)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(refused.stdout, b'')
        self.assertEqual(len(list(self.state.iterdir())), 2)
        print('SECOND_ROOT_PRIVATE_REFUSED_WITH_SAME_GLOBAL_ARGS')

    def test_actual_launcher_refuses_opaque_nested_repo_before_indexing(self):
        nested = self.repo / 'nested'
        subprocess.run(['git', 'init', '-q', str(nested)], check=True)
        (nested / 'credentials').mkdir()
        (nested / 'credentials/hidden.py').write_text('NATIVE_HIDDEN_NESTED_CREDENTIAL = 1\n')
        result = subprocess.run([sys.executable, str(Path(launcher.__file__).resolve()),
                                 '--binary', self.binary, '--state-parent', str(self.state)],
                                cwd=self.repo, env=self.env, input=b'', capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'refused startup', result.stderr)
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertNotIn(b'NATIVE_HIDDEN_NESTED_CREDENTIAL', result.stderr)
        print('OPAQUE_NATIVE_STARTUP_REFUSED_BEFORE_CACHE_OR_INDEX')

    def test_actual_native_index_failure_is_not_success(self):
        env = dict(self.env, CBM_ALLOWED_ROOT=str(self.base / 'elsewhere'),
                   CBM_CACHE_DIR=str(self.state), CBM_RUNTIME_DIR=str(self.base / 'runtime'),
                   CBM_LOG_LEVEL='error', CBM_MEM_BUDGET_MB='256', CBM_WORKERS='1')
        (self.base / 'runtime').mkdir(mode=0o700)
        (self.base / 'elsewhere').mkdir()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)) as failure:
            launcher.native_setup(Path(self.binary), self.repo, env, self.state)
        print('NATIVE_INDEX_REFUSED=' + type(failure.exception).__name__)
        self.assertTrue(self.state.exists())


if __name__ == '__main__':
    unittest.main()
