"""Run with HERMES_UPSTREAM_SOURCE pointing at the pinned upstream source tree.

These checks exercise real upstream code rather than only historical patch fixtures.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')
HERE = Path(__file__).parent


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError(f'{path}: missing functions {names - {n.name for n in nodes}}')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace


def image_patch_names():
    names = re.findall(r'(?:^RUN|&&) python3 /opt/hermes/patches/(patch_\S+)', (HERE / 'Dockerfile').read_text(), re.M)
    # This command lives inside the baked-marker conditional. The default image
    # does not execute it; every unconditional generic patch must still run.
    return [name for name in names if name != 'patch_camofox_only_variant.py']


def apply_patch_chain(root, names):
    def redirect(p):
        for old, target in (('/opt/hermes', root), ('/etc/cont-init.d', root / 'docker/cont-init.d'), ('/etc/s6-overlay/s6-rc.d', root / 'docker/s6-rc.d')):
            if str(p) == old or str(p).startswith(old + '/'):
                return Path(str(target) + str(p)[len(old):])
        return p
    for name in names:
        p = HERE / 'patches' / name
        ns = {'__name__': 'patch_test', '__file__': str(p)}
        exec(compile(p.read_text(), str(p), 'exec'), ns)
        for key, value in list(ns.items()):
            if isinstance(value, Path): ns[key] = redirect(value)
            elif isinstance(value, tuple) and all(isinstance(v, Path) for v in value): ns[key] = tuple(redirect(v) for v in value)
        ns['Path'] = lambda *a, **kw: redirect(Path(*a, **kw))
        with contextlib.redirect_stdout(io.StringIO()):
            with patch('sys.argv', [str(p)]):
                (ns.get('main') or ns['patch'])()



def source_hashes(root):
    return {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE for pinned-source integration')
class UpstreamRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / 'source'
        shutil.copytree(SOURCE, cls.root)
        cls.names = image_patch_names()
        cls.apply_chain()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def apply_chain(cls):
        apply_patch_chain(cls.root, cls.names)

    def test_complete_chain_is_idempotent_and_compiles(self):
        def hashes():
            return {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        before = hashes()
        self.apply_chain()
        self.assertEqual(before, hashes())
        for name in before:
            original = Path(SOURCE) / name
            if name.suffix == '.py' and (not original.exists() or original.read_bytes() != (self.root / name).read_bytes()):
                compile((self.root / name).read_text(), str(name), 'exec')

    def test_default_image_keeps_variant_unselected(self):
        self.assertNotIn('patch_camofox_only_variant.py', self.names)
        self.assertFalse((self.root / 'pm/spartan_browser_variant.py').exists())
        self.assertFalse((self.root / 'pm/spartan-browser-variant.json').exists())

    def test_numeric_identity_covers_new_boot_paths(self):
        source = (self.root / 'docker/stage2-hook.sh').read_text()
        self.assertNotIn('chown hermes:hermes "$XDG_RUNTIME_DIR"', source)
        self.assertNotIn('s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python"', source)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$XDG_RUNTIME_DIR"', source)

    def test_cache_key_bound_and_single_wire_field(self):
        ns = definitions(self.root / 'agent/transports/chat_completions.py', {'_spartan_add_clawroute_prompt_cache_key'}, {})
        fn = ns['_spartan_add_clawroute_prompt_cache_key']
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'http://route/v1'}):
            args = {'base_url': 'http://route/v1', 'session_id': 'x' * 100, 'reasoning_config': {'effort': 'ultra'}}
            result = fn({}, args)
            self.assertEqual(len(result['extra_body']['prompt_cache_key']), 64)
            self.assertEqual(result['reasoning_effort'], 'ultra')
            result = fn({'prompt_cache_key': 'explicit'}, args)
            self.assertNotIn('prompt_cache_key', result)
            self.assertEqual(result['extra_body']['prompt_cache_key'], 'explicit')
            direct = {'base_url': 'https://api.openai.com/v1', 'session_id': 'x'}
            self.assertEqual(fn({}, direct), {})

    def test_stale_browser_action_cannot_reopen_or_adopt(self):
        ns = definitions(self.root / 'tools/browser_camofox.py', {'_spartan_stale_tab'}, {'json': json, '_sessions_lock': threading.Lock()})
        session = {'tab_id': 'old', 'adopt_existing_tab': True}
        error = SimpleNamespace(response=SimpleNamespace(status_code=410))
        result = json.loads(ns['_spartan_stale_tab'](error, session, 'click'))
        self.assertTrue(result['requires_navigation'])
        self.assertIsNone(session['tab_id'])
        self.assertFalse(session['adopt_existing_tab'])

    def test_managed_browser_close_preserves_profile(self):
        calls = []
        ns = definitions(self.root / 'tools/browser_camofox.py', {'camofox_close'}, {
            'json': json, '_drop_session': lambda task: {'managed': True, 'tab_id': 'tab', 'user_id': 'profile'},
            '_delete': lambda *a: calls.append(a)})
        self.assertTrue(json.loads(ns['camofox_close']('session'))['success'])
        self.assertEqual(calls, [('/tabs/tab', {'userId': 'profile'})])

    def test_meet_transcription_does_not_unmute(self):
        source = (self.root / 'plugins/google_meet/meet_bot.py').read_text()
        self.assertIn('mic_state=_ensure_mic_on(page) if rt["enabled"] else "muted"', source)
        ns = definitions(self.root / 'plugins/google_meet/meet_bot.py', {'_resolve_meet_cdp', '_meet_camofox_mode_enabled'}, {'os': os})
        with patch.dict(os.environ, {'CAMOFOX_URL': 'http://camofox', 'BROWSER_CDP_URL': 'ws://stale'}):
            self.assertEqual(ns['_resolve_meet_cdp'](), ('', 'camofox'))


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE for pinned-source integration')
class CamofoxOnlyRefreshChainTests(unittest.TestCase):
    def test_complete_slim_chain_final_manifest_is_current_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            shutil.copytree(SOURCE, root)
            (root / 'bootstrap').mkdir(exist_ok=True)
            for name in ('spartan_browser_policy.py', 'spartan_browser_variant.py'):
                shutil.copyfile(HERE / name, root / name)
                shutil.copyfile(HERE / name, root / 'bootstrap' / name)
            command = [sys.executable, '-B', str(HERE / 'patches/patch_camofox_only_variant.py'),
                       str(root), '--helper', str(root / 'spartan_browser_variant.py')]
            # The upstream optional base guards PM before the downstream generic
            # patches, including any runtime-recovery or SDK repair adaptations.
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            names = image_patch_names()
            self.assertTrue(names)
            apply_patch_chain(root, names)
            subprocess.run([*command, '--meet-only'], check=True, capture_output=True, text=True, timeout=30)
            manifest = json.loads((root / 'pm/spartan-browser-variant.json').read_text())
            self.assertEqual(manifest['schema'], 1)
            required = {'pm/install.py', 'pm/client.py', 'pm/build_operations.py',
                        'pm/spartan_browser_variant.py', 'tools/browser_tool.py',
                        'tools/browser_tool_install.py', 'hermes_cli/browser_runtime.py',
                        'plugins/google_meet/meet_bot.py', 'plugins/google_meet/cli.py',
                        'spartan_browser_policy.py', 'bootstrap/spartan_browser_policy.py',
                        'spartan_browser_variant.py', 'bootstrap/spartan_browser_variant.py'}
            self.assertEqual(set(manifest['files']), required)
            for name, expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected, name)
            before = source_hashes(root)
            apply_patch_chain(root, names)
            subprocess.run([*command, '--meet-only'], check=True, capture_output=True, text=True, timeout=30)
            self.assertEqual(before, source_hashes(root))
            for name in before:
                original = Path(SOURCE) / name
                if name.suffix == '.py' and (not original.exists() or original.read_bytes() != (root / name).read_bytes()):
                    compile((root / name).read_text(), str(name), 'exec')

if __name__ == '__main__': unittest.main()
