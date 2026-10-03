import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location('browser_overlay_test', ROOT / 'scripts/hermes-browser-policy-overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserOverlayTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.patcher = self.module.patcher()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'private').mkdir()
        app = self.root / 'apps/hermes-agent'
        app.mkdir(parents=True)
        for name in ['spartan_browser_policy.py', 'spartan-browser-policy-init.sh']:
            shutil.copy2(ROOT / 'apps/hermes-agent' / name, app / name)
        (app / 'patches').mkdir()
        shutil.copy2(ROOT / 'apps/hermes-agent/patches/patch_camofox_managed_policy.py',
                     app / 'patches/patch_camofox_managed_policy.py')
        self.module.ROOT = self.root
        self.module.OVERLAY = self.root / 'private/compose.hermes-browser-policy.yml'
        self.image = 'sha256:' + 'a' * 64
        self.commands = []

    def fake_run(self, args):
        self.commands.append(args)
        if args[1] == 'inspect' or args[1:3] == ['image', 'inspect']:
            return (self.image + '\n').encode()
        if args[1] == 'exec':
            filename = Path(args[-1]).name
            if filename in self.patcher.TARGETS:
                name = self.patcher.TARGETS[filename][0]
                signature = 'section' if name == 'read_selection' else ''
                return f'def {name}({signature}):\n    """Selector."""\n    return None\n'.encode()
            return b'compatibility-anchor\n'
        if args[1] == 'compose':
            return b''
        self.fail(args)

    def prepare(self):
        with patch.object(self.module, 'patcher', return_value=self.patcher), patch.object(self.module, 'run', side_effect=self.fake_run):
            self.module.prepare('test-hermes')

    def test_bundle_is_pinned_read_only_and_idempotent(self):
        self.prepare()
        first = self.module.OVERLAY.read_bytes()
        self.prepare()
        self.assertEqual(first, self.module.OVERLAY.read_bytes())
        config = yaml.safe_load(first)
        service = config['services']['hermes']
        self.assertEqual(service['image'], self.image)
        mounts = {mount['target']: mount for mount in service['volumes']}
        self.assertEqual(mounts['/opt/hermes/spartan_browser_policy.py']['source'],
                         mounts['/opt/hermes/bootstrap/spartan_browser_policy.py']['source'])
        self.assertTrue(all(m['read_only'] and not m['bind']['create_host_path'] for m in service['volumes']))
        with patch.object(self.module, 'run', side_effect=self.fake_run):
            self.module.check('test-image')

    def test_compose_preflight_failure_publishes_nothing(self):
        def failing(args):
            if args[1] == 'compose':
                raise RuntimeError('invalid compose')
            return self.fake_run(args)
        with patch.object(self.module, 'patcher', return_value=self.patcher), patch.object(self.module, 'run', side_effect=failing):
            with self.assertRaises(RuntimeError):
                self.module.prepare('test-hermes')
        self.assertFalse(self.module.OVERLAY.exists())
        self.assertFalse((self.root / 'private/runtime').exists())

    def test_image_drift_and_code_tampering_are_rejected(self):
        self.prepare()
        with patch.object(self.module, 'run', return_value=('sha256:' + 'b' * 64).encode()):
            with self.assertRaisesRegex(ValueError, 'image changed'):
                self.module.check('changed-image')
        config = yaml.safe_load(self.module.OVERLAY.read_text())
        source = Path(config['services']['hermes']['volumes'][0]['source'])
        source.chmod(0o644)
        source.write_text('tampered')
        with patch.object(self.module, 'run', side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                self.module.check('test-image')

    def test_manifest_cannot_omit_required_code_or_support(self):
        for field in ('files', 'support', 'anchors'):
            with self.subTest(field=field):
                self.prepare()
                config = yaml.safe_load(self.module.OVERLAY.read_text())
                _, path = self.module.expected_manifest(config)
                manifest = json.loads(path.read_text())
                manifest[field].pop(next(iter(manifest[field])))
                path.chmod(0o644)
                path.write_text(json.dumps(manifest))
                with patch.object(self.module, 'run', side_effect=self.fake_run):
                    with self.assertRaisesRegex(ValueError, 'manifest'):
                        self.module.check('test-image')
                path.unlink()
                shutil.rmtree(path.parent)

    def test_duplicate_mount_and_symlink_manifest_are_rejected(self):
        self.prepare()
        config = yaml.safe_load(self.module.OVERLAY.read_text())
        config['services']['hermes']['volumes'].append(config['services']['hermes']['volumes'][0].copy())
        self.module.OVERLAY.write_text(yaml.safe_dump(config))
        with patch.object(self.module, 'run', side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, 'mount'):
                self.module.check('test-image')
        config['services']['hermes']['volumes'].pop()
        self.module.OVERLAY.write_text(yaml.safe_dump(config))
        _, path = self.module.expected_manifest(config)
        renamed = path.with_name('other-manifest.json')
        path.rename(renamed)
        path.symlink_to(renamed)
        with patch.object(self.module, 'run', side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, 'manifest'):
                self.module.check('test-image')

    def test_unrelated_overlay_is_never_overwritten(self):
        self.module.OVERLAY.write_text('services:\n  hermes:\n    image: unrelated\n')
        before = self.module.OVERLAY.read_bytes()
        with patch.object(self.module, 'patcher', return_value=self.patcher), patch.object(self.module, 'run', side_effect=self.fake_run):
            with self.assertRaises((ValueError, KeyError)):
                self.module.prepare('test-hermes')
        self.assertEqual(self.module.OVERLAY.read_bytes(), before)


class BrowserOverlayAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/aliases.sh', self.root / 'scripts/aliases.sh')
        (self.root / 'private').mkdir()
        (self.root / 'private/compose.hermes-browser-policy.yml').write_text('services: {}\n')

    def shell(self, command):
        return subprocess.run(['bash', '-c', 'source "$1/scripts/aliases.sh"\n' + command, 'test', str(self.root)], capture_output=True, text=True)

    def test_each_tier_includes_overlay_without_changing_tier_normalization(self):
        for tier in ['L0', 'L1', 'L2', 'L3', 'L4']:
            result = self.shell(f'sg_normalize_tier {tier}\nsg_tier_compose_args {tier} ""')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[0], tier)
            self.assertEqual(result.stdout.splitlines()[-1], str(self.root / 'private/compose.hermes-browser-policy.yml'))

    def test_shell_image_override_is_checked(self):
        result = self.shell('python3() { printf "%s\\n" "$@"; }\nHERMES_IMAGE=custom-hermes:latest sg_hermes_browser_policy_preflight up hermes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], 'custom-hermes:latest')

    def test_building_hermes_is_blocked_but_other_services_are_unaffected(self):
        result = self.shell('sg_hermes_browser_policy_preflight build hermes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('before rebuilding Hermes', result.stderr)
        result = self.shell('sg_hermes_browser_policy_preflight build spartan-route')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
