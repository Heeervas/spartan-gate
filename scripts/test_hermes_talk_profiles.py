import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('talk_profiles', Path(__file__).with_name('hermes_talk_profiles.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TalkProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugin = self.root / 'plugins/hermes-talk'
        self.plugin.mkdir(parents=True)
        (self.plugin / 'plugin.yaml').write_text('name: hermes-talk\n')
        self.homes = [self.root, self.root / 'profiles/work']
        for home in self.homes:
            home.mkdir(exist_ok=True, parents=True)
            (home / 'config.yaml').write_text('plugins:\n  enabled: [existing]\nmodel: original\n')
            (home / 'config.yaml').chmod(0o600)

    def test_enable_idempotent_and_disable_preserves_other_plugins(self):
        self.assertEqual(module.prepare(self.root, self.plugin, True), 2)
        once = [(h / 'config.yaml').read_bytes() for h in self.homes]
        module.prepare(self.root, self.plugin, True)
        self.assertEqual(once, [(h / 'config.yaml').read_bytes() for h in self.homes])
        self.assertEqual((self.homes[1] / 'plugins/hermes-talk').resolve(), self.plugin)
        module.prepare(self.root, self.plugin, False)
        for home in self.homes:
            data = yaml.safe_load((home / 'config.yaml').read_text())
            self.assertEqual(data['plugins']['enabled'], ['existing'])
            self.assertEqual(data['model'], 'original')
            self.assertEqual((home / 'config.yaml').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.homes[1] / 'plugins/hermes-talk').is_symlink())
        self.assertTrue(self.plugin.is_dir())

    def test_all_preflight_before_first_write(self):
        before = (self.root / 'config.yaml').read_bytes()
        (self.homes[1] / 'config.yaml').write_text('plugins: wrong\n')
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)
        self.assertEqual((self.root / 'config.yaml').read_bytes(), before)

    def test_write_failure_rolls_back_prior_profiles(self):
        original = module.atomic_write
        count = 0
        def fail_once(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('synthetic disk failure')
            return original(*args)
        before = [(h / 'config.yaml').read_bytes() for h in self.homes]
        with patch.object(module, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                module.prepare(self.root, self.plugin, True)
        self.assertEqual(before, [(h / 'config.yaml').read_bytes() for h in self.homes])
        self.assertFalse((self.homes[1] / 'plugins').exists())

    def test_conflicting_install_fails_closed(self):
        path = self.homes[1] / 'plugins/hermes-talk'
        path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)

    def test_explicit_disable_fails_closed(self):
        (self.homes[1] / 'config.yaml').write_text('plugins:\n  disabled: [hermes-talk]\n')
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)

    def test_symlink_profile_fails_closed(self):
        (self.root / 'profiles/linked').symlink_to(self.homes[1])
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)

    def test_duplicate_policy_key_fails_closed(self):
        (self.homes[1] / 'config.yaml').write_text('plugins: {}\nplugins: {}\n')
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)

    def test_dangling_config_symlink_fails_closed(self):
        config = self.homes[1] / 'config.yaml'
        config.unlink()
        config.symlink_to(self.root / 'missing-config')
        with self.assertRaises(ValueError):
            module.prepare(self.root, self.plugin, True)

    def test_check_does_not_write(self):
        before = [(h / 'config.yaml').read_bytes() for h in self.homes]
        self.assertEqual(module.prepare(self.root, self.plugin, True, True), 2)
        self.assertEqual(before, [(h / 'config.yaml').read_bytes() for h in self.homes])
        self.assertFalse((self.homes[1] / 'plugins').exists())

    def test_missing_credential_blocks_before_profile_write(self):
        with patch.dict(os.environ, {'SPARTAN_HERMES_TALK_ENABLED': 'true'}, clear=True):
            with patch('sys.argv', ['helper', '--home', str(self.root)]):
                self.assertEqual(module.main(), 1)
        self.assertNotIn('hermes-talk', (self.root / 'config.yaml').read_text())


if __name__ == '__main__':
    unittest.main()
