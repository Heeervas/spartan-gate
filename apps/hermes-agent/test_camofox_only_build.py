import importlib.util
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).parent
spec = importlib.util.spec_from_file_location('prepare_variant', APP / 'build/prepare_camofox_only.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CamofoxOnlyBuildTests(unittest.TestCase):
    def original(self):
        return (APP / 'build/tests/upstream.Dockerfile').read_text()

    def test_only_browser_install_and_probe_are_removed(self):
        original = self.original()
        result = module.transformed(original)
        self.assertNotIn(module.ENSURE, result)
        self.assertNotIn('browser_bin=', result)
        self.assertNotIn('> /etc/hermes/agent-browser-executable-path', result)
        self.assertIn('("uv", "npm", "ffmpeg", "ripgrep")', result)
        self.assertIn('RUN test "$HERMES_BOT_DESKTOP" = "0"', result)
        for preserved in ('libatk-bridge2.0-0t64', 'gcc g++ make cmake', 'FROM runtime_base AS python_deps', '--extra all --extra messaging', 'ffmpeg -version', 'node --version; npm --version'):
            self.assertIn(preserved, result)
        self.assertEqual(result.count('python3 /opt/hermes/patch_camofox_only_variant.py /opt/hermes'), 2)
        self.assertLess(result.index(module.EARLY), result.index('from pm import ensure'))
        self.assertLess(result.index(module.EARLY), result.index('from pm import stage_manager_runtime'))
        self.assertIn(module.COPY_SOURCE + module.LATE, result)

    def test_drift_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'source drift'):
            module.transformed(self.original().replace('"chromium", ', ''))
        result = module.transformed(self.original())
        self.assertEqual(module.transformed(result), result)

    def test_wrong_revision_leaves_output_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'context'
            with mock.patch.object(module, 'git', return_value=b'wrong-revision\n'):
                with self.assertRaisesRegex(ValueError, 'pinned upstream revision'):
                    module.prepare(Path(temp) / 'upstream', output)
            self.assertFalse(output.exists())

    def test_dirty_source_leaves_output_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'context'
            with mock.patch.object(module, 'git', side_effect=[module.REVISION.encode(), b' M Dockerfile']):
                with self.assertRaisesRegex(ValueError, 'must be clean'):
                    module.prepare(Path(temp) / 'upstream', output)
            self.assertFalse(output.exists())

    def test_context_preparation_is_idempotent_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / 'app'
            (app / 'patches').mkdir(parents=True)
            (app / 'spartan_browser_variant.py').write_text('helper')
            (app / 'patches/patch_camofox_only_variant.py').write_text('patcher')
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tar:
                info = tarfile.TarInfo('pm/example.py')
                info.size = 3
                tar.addfile(info, io.BytesIO(b'old'))
            def fake_git(source, *args):
                return {
                    'rev-parse': module.REVISION.encode(), 'status': b'',
                    'show': self.original().encode(), 'archive': archive.getvalue(),
                }[args[0]]
            output = root / 'context'
            with mock.patch.object(module, 'APP', app), mock.patch.object(module, 'git', side_effect=fake_git):
                first = module.prepare(root / 'upstream', output)
                self.assertEqual(module.prepare(root / 'upstream', output), first)
                self.assertEqual((output / 'pm/example.py').read_bytes(), b'old')
                self.assertIn(module.REVISION, (output / 'install-stamp.json').read_text())
                (output / 'pm/example.py').write_text('tampered')
                with self.assertRaisesRegex(ValueError, 'context differs'):
                    module.prepare(root / 'upstream', output)
                self.assertEqual((output / 'pm/example.py').read_text(), 'tampered')

    def test_relocated_patcher_cli_uses_explicit_helper_for_early_and_late_layers(self):
        patcher = Path(os.environ.get('HERMES_VARIANT_PATCHER_TEST_PATH',
                                     APP / 'patches/patch_camofox_only_variant.py'))
        if not patcher.exists():
            self.skipTest('runtime variant patch has not been integrated')
        runtime_spec = importlib.util.spec_from_file_location('runtime_patcher', patcher)
        runtime = importlib.util.module_from_spec(runtime_spec)
        runtime_spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'hermes'
            root.mkdir()
            relocated = root / 'patch_camofox_only_variant.py'
            relocated.write_bytes(patcher.read_bytes())
            helper = root / 'spartan_browser_variant.py'
            helper.write_text('ERROR = "unsupported"\n')
            for name, guards in runtime.GUARDS.items():
                source = ''.join(f'def {function}(*args, **kwargs):\n    pass\n\n' for function in guards)
                if name == 'tools/browser_tool.py':
                    source += '''f"a missing Chromium install ('agent-browser install'), "\n'''
                if name == 'pm/client.py':
                    source += 'def sync_tools():\n    for name in tool_roots(Lockfile(paths.lockfile_path()).names()):\n        ensure(name, explicit=True)\n'
                if name == 'pm/install.py':
                    source += 'def drift():\n    for name in lockfile.names():\n        pass\ndef activate():\n    for name in lockfile.names():\n        pass\n'
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            # Reproduce the root-level script placement used by upstream COPY,
            # without placing a helper in its implicit parents[1] directory.
            for command in (module.EARLY.splitlines()[-1], module.LATE.strip()[4:]):
                self.assertIn('--helper /opt/hermes/spartan_browser_variant.py', command)
                args = command.strip().split()[1:]
                args = [arg.replace('/opt/hermes', str(root)) for arg in args]
                result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'pm/spartan_browser_variant.py').read_bytes(), helper.read_bytes())
            self.assertIn('tools/browser_tool_install.py', (root / 'pm/spartan-browser-variant.json').read_text())

    def test_default_base_remains_immutable_and_variant_is_optional(self):
        source = (APP / 'Dockerfile').read_text()
        self.assertIn('ARG HERMES_BASE_IMAGE=' + module.UPSTREAM_IMAGE, source)
        self.assertIn('FROM ${HERMES_BASE_IMAGE}', source)
        self.assertIn('--helper /opt/hermes/spartan_browser_variant.py --meet-only', source)
        self.assertLess(source.index('patch_state_fts_empty_migration.py'), source.index('--meet-only'))
        self.assertIn('if [ -f /etc/spartan/hermes-browser-mode ]; then', source)
        self.assertNotIn('COPY --chown=0:0 spartan_browser_variant.py /opt/hermes/spartan_browser_variant.py', source)
        self.assertIn('install -m 0755 /opt/hermes/patches/spartan-browser-variant-init.sh /etc/cont-init.d/0196-spartan-browser-variant', source)
        self.assertLess(source.index('0196-spartan-browser-variant'), source.index('--meet-only'))
        self.assertLess(source.index('if [ -f /etc/spartan/hermes-browser-mode ]; then'), source.index('install -m 0644 /opt/hermes/patches/spartan_browser_variant.py'))
        compose = (APP.parents[1] / 'infra/compose/compose.yml').read_text()
        self.assertNotIn('HERMES_CAMOFOX_ONLY', compose)


if __name__ == '__main__':
    unittest.main()
