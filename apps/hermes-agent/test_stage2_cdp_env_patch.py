import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_stage2_cdp_env.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location('patch_stage2_cdp_env', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Stage2GatewayStatePatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.source = f'#!/bin/sh\n# before\n{self.module.ANCHOR}\n# after\n'

    def test_inserts_once_before_supported_discovery_and_is_idempotent(self):
        for anchor in (self.module.ANCHOR, self.module.ALTERNATE_ANCHOR):
            with self.subTest(anchor=anchor):
                source = self.source.replace(self.module.ANCHOR, anchor)
                patched = self.module.patch_source(source)
                self.assertEqual(patched, self.module.patch_source(patched))
                self.assertEqual(source, patched.replace(self.module.NEW_BLOCK, ''))
                self.assertLess(patched.index(self.module.MARKER), patched.index(anchor))
                subprocess.run(['sh', '-n'], input=patched, text=True, check=True)
                for retired in ('BROWSERLESS', 'BROWSER_CDP', 'browserless-cdp', '/run/service/'):
                    self.assertNotIn(retired, patched)

    def test_preserves_other_stage2_patch_owners(self):
        source = self.source.replace('# before', '# Spartan Gate patch: runtime Hermes identity')
        patched = self.module.patch_source(source)
        self.assertEqual(patched, self.module.patch_source(patched))
        self.assertIn('# Spartan Gate patch: runtime Hermes identity', patched)

    def test_marker_only_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.module.patch_source(self.source.replace('# before', self.module.MARKER))

    def test_missing_or_inverted_migration_guard_is_rejected(self):
        valid = self.module.patch_source(self.source)
        guard = '[ ! -f "$state_file" ] || return 0'
        for replacement in (':', '[ -f "$state_file" ] || return 0'):
            with self.subTest(replacement=replacement):
                broken = valid.replace(guard, replacement)
                self.assertNotEqual(valid, broken)
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    self.module.patch_source(broken)

    def test_incompatible_or_ambiguous_source_is_rejected(self):
        candidates = [
            '# no anchor',
            self.source + self.module.ANCHOR,
            self.source + self.module.ALTERNATE_ANCHOR,
            self.source.replace('# before', 'seed_legacy_autostart_profile_state() { :; }'),
            self.source.replace('# before', '# Spartan Gate patch: browser runtime for supervised services'),
            self.module.NEW_BLOCK + '# gap\n' + self.source,
            self.module.NEW_BLOCK + self.module.NEW_BLOCK + self.source,
        ]
        for source in candidates:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    self.module.patch_source(source)

    def test_main_leaves_invalid_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory, 'stage2-hook.sh')
            invalid = self.source.replace('# before', self.module.MARKER)
            target.write_text(invalid)
            with mock.patch.object(self.module, 'TARGET', target):
                with self.assertRaises(SystemExit) as raised:
                    self.module.main()
            self.assertEqual(1, raised.exception.code)
            self.assertEqual(invalid, target.read_text())

    def run_migrations(self, home, *, no_supervise='', command='/opt/hermes/entrypoint-wrapper.sh gateway run'):
        # Only process-command detection and ownership are simulated; execute the
        # actual generated shell against real disposable profile/state files.
        prelude = '''set -eu
chown() { :; }
tr() { printf '%s' "$TEST_COMMAND"; }
'''
        env = dict(os.environ, HERMES_HOME=str(home), SPARTAN_HERMES_RUNTIME_OWNER='0:0',
                   HERMES_AUTOSTART_PROFILES=' work ,existing,missing,default,../escape,BAD',
                   HERMES_GATEWAY_NO_SUPERVISE=no_supervise, TEST_COMMAND=command)
        return subprocess.run(['sh'], input=prelude + self.module.patch_source(self.source),
                              text=True, env=env, capture_output=True, check=True)

    def test_legacy_profiles_seed_only_configured_names_and_preserve_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            for name in ('work', 'existing', 'default'):
                profile = home / 'profiles' / name
                profile.mkdir(parents=True)
                (profile / 'SOUL.md').write_text('soul')
                (profile / 'config.yaml').write_text('model: test')
            existing = home / 'profiles/existing/gateway_state.json'
            existing.write_text('{"gateway_state":"stopped","other":"preserve"}')
            expected = existing.read_bytes()
            self.run_migrations(home)
            state = json.loads((home / 'profiles/work/gateway_state.json').read_text())
            self.assertEqual({'gateway_state': 'running', 'migrated_from': 'HERMES_AUTOSTART_PROFILES'}, state)
            self.assertEqual(expected, existing.read_bytes())
            self.assertFalse((home / 'profiles/default/gateway_state.json').exists())
            self.assertFalse((home / 'profiles/missing').exists())
            self.assertFalse((home / 'escape').exists())
            seeded = (home / 'profiles/work/gateway_state.json').read_bytes()
            self.run_migrations(home)
            self.assertEqual(seeded, (home / 'profiles/work/gateway_state.json').read_bytes())

    def test_default_state_respects_command_opt_out_and_existing_state(self):
        cases = [('', '/opt/hermes/entrypoint-wrapper.sh gateway run', True),
                 ('', '/opt/hermes/docker/main-wrapper.sh gateway run', True),
                 ('', 'hermes chat', False), ('1', '/opt/hermes/entrypoint-wrapper.sh gateway run', False),
                 ('true', '/opt/hermes/entrypoint-wrapper.sh gateway run', False),
                 ('yes', '/opt/hermes/entrypoint-wrapper.sh gateway run', False)]
        for opt_out, command, expected in cases:
            with self.subTest(opt_out=opt_out, command=command), tempfile.TemporaryDirectory() as directory:
                home = pathlib.Path(directory)
                self.run_migrations(home, no_supervise=opt_out, command=command)
                target = home / 'gateway_state.json'
                self.assertEqual(expected, target.exists())
                if expected:
                    self.assertEqual('spartan-container-cmd', json.loads(target.read_text())['migrated_from'])
                target.write_text('{"gateway_state":"stopped"}')
                self.run_migrations(home, no_supervise=opt_out, command=command)
                self.assertEqual('{"gateway_state":"stopped"}', target.read_text())


if __name__ == '__main__':
    unittest.main()
