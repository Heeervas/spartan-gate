import importlib.util
import io
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

    def test_default_base_remains_immutable_and_variant_is_optional(self):
        source = (APP / 'Dockerfile').read_text()
        self.assertIn('ARG HERMES_BASE_IMAGE=' + module.UPSTREAM_IMAGE, source)
        self.assertIn('FROM ${HERMES_BASE_IMAGE}', source)
        self.assertIn('patch_camofox_only_variant.py /opt/hermes', source)
        compose = (APP.parents[1] / 'infra/compose/compose.yml').read_text()
        self.assertNotIn('HERMES_CAMOFOX_ONLY', compose)


if __name__ == '__main__':
    unittest.main()
