import copy
from pathlib import Path
import stat
import tempfile
import unittest

import yaml

from scripts import prepare_hermes_memory as memory


class MemoryPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        (self.home / 'profiles').mkdir()
        self.compose = self.base / 'compose.yml'
        self.compose.write_text('# Compose comment\nservices:\n  hermes:\n    image: fixture\n    mem_limit: 8g # limit comment\n    memswap_limit: 10g\n  other: {mem_limit: 9g}\n')
        self.backup = self.base / 'backup'
        self.backup.mkdir(mode=0o700)
        self.config = self.home / 'config.yaml'
        self.config.write_text('# profile comment\nlsp:\n  idle_timeout: 900 # timeout comment\n  servers:\n    python: {command: pyright, version: 1.1.414}\nother: keep\n')
        self.config.chmod(0o640)

    def test_twelve_profiles_exact_delta_comments_metadata_and_idempotence(self):
        for index in range(12):
            profile = self.home / 'profiles' / f'{index:02}'
            profile.mkdir()
            # Exercise absent, empty flow, and existing block mappings.
            (profile / 'config.yaml').write_text(['other: keep\n', 'lsp: {}\nother: keep\n', self.config.read_text()][index % 3])
        changes = memory.plan(self.home, self.compose)
        self.assertEqual(len(changes), 14)
        for change in changes:
            before = yaml.safe_load(change.original)
            expected = copy.deepcopy(before)
            if change.path == self.compose:
                expected['services']['hermes'].update(mem_limit='4g', memswap_limit='6g')
            else:
                expected.setdefault('lsp', {})['idle_timeout'] = 300
            self.assertEqual(yaml.safe_load(change.replacement), expected)
        metadata = self.config.stat()
        memory.apply(changes, self.backup, home=self.home)
        self.assertEqual(memory.plan(self.home, self.compose), [])
        self.assertIn('# profile comment', self.config.read_text())
        self.assertIn('# timeout comment', self.config.read_text())
        self.assertIn('# limit comment', self.compose.read_text())
        after = self.config.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (metadata.st_uid, metadata.st_gid, 0o640))
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in self.backup.iterdir()))

    def test_late_malformed_and_duplicate_configs_leave_everything_unchanged(self):
        profile = self.home / 'profiles' / 'last'
        profile.mkdir()
        path = profile / 'config.yaml'
        for invalid in ['lsp: [\n', 'lsp: {}\nlsp: {}\n', 'lsp: null\n']:
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                before = {p: p.read_bytes() for p in [self.config, path, self.compose]}
                with self.assertRaises((ValueError, yaml.YAMLError)):
                    memory.plan(self.home, self.compose)
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                self.assertEqual(list(self.backup.iterdir()), [])

    def test_flow_missing_limits_and_no_final_newline(self):
        self.config.write_text('other: keep')
        self.compose.write_text('services: {hermes: {image: fixture}} # retained\n')
        memory.apply(memory.plan(self.home, self.compose), self.backup, home=self.home)
        self.assertEqual(yaml.safe_load(self.config.read_text()), {'other': 'keep', 'lsp': {'idle_timeout': 300}})
        self.assertIn('# retained', self.compose.read_text())
        self.assertEqual(memory.plan(self.home, self.compose), [])

    def test_missing_block_values_preserve_next_section_and_comments(self):
        self.config.write_text('lsp:\n  enabled: true\n# next section\nother: keep\n')
        self.compose.write_text('services:\n  hermes:\n    image: fixture\n  other: {image: keep}\nnetworks: {}\n')
        changes = memory.plan(self.home, self.compose)
        self.assertIn('# next section', changes[0].replacement.decode())
        memory.apply(changes, self.backup, home=self.home)
        self.assertEqual(memory.plan(self.home, self.compose), [])

    def test_unsafe_paths_shapes_and_aliases_are_refused(self):
        original = self.compose.read_bytes()
        for invalid in ['services: []\n', 'services: {hermes: []}\n', 'services: {hermes: {mem_limit: []}}\n',
                        'services: {hermes: {}}\nservices: {}\n',
                        'services: {hermes: &shared {image: fixture}, other: *shared}\n']:
            with self.subTest(invalid=invalid):
                self.compose.write_text(invalid)
                with self.assertRaises(ValueError):
                    memory.plan(self.home, self.compose)
        self.compose.write_bytes(original)
        self.config.rename(self.home / 'real.yaml')
        self.config.symlink_to(self.home / 'real.yaml')
        with self.assertRaises(ValueError):
            memory.plan(self.home, self.compose)
        self.config.unlink()
        (self.home / 'real.yaml').rename(self.config)
        alias = self.base / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            memory.plan(alias, self.compose)


if __name__ == '__main__':
    unittest.main()
