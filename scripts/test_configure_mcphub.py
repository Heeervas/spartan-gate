"""Synthetic native-config boundaries; never discovers a live agent home."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest

from ruamel.yaml import YAML
from scripts import configure_mcphub as setup


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        (self.root / 'hermes').mkdir(mode=0o700)
        self.hermes = self.root / 'hermes/config.yaml'
        self.hermes.write_text('# keep comment\nmodel: existing\nmcp_servers:\n  foreign: {url: "https://example.invalid/mcp"}\n')
        self.hermes.chmod(0o600)
        self.targets = {'hermes': {'config': str(self.hermes), 'url': 'http://caddy:18793/mcp/agents'}, 'pi': []}
        for client in ('host-pi', 'container-pi'):
            directory = self.root / client
            directory.mkdir(mode=0o700)
            settings = directory / 'settings.json'
            settings.write_text(json.dumps({'theme': 'preserve', 'extensions': [
                '-builtin:mcp', '-builtin:codemode', '-builtin:tool-search', '-foreign']}))
            settings.chmod(0o600)
            self.targets['pi'].append({'client': client, 'settings': str(settings),
                                       'mcp': str(directory / 'mcp.json'),
                                       'url': 'http://127.0.0.1:18793/mcp/agents' if client == 'host-pi'
                                       else 'http://caddy:18793/mcp/agents'})
        self.manifest = self.root / 'targets.json'
        self.manifest.write_text(json.dumps(self.targets))
        self.manifest.chmod(0o600)
        self.args = argparse.Namespace(state_dir=str(self.state), targets=str(self.manifest),
                                       backup_dir=str(self.root / 'backup'), state_owner=None, apply=True)

    def test_merge_native_preservation_backup_and_idempotence(self):
        before = self.hermes.read_bytes()
        setup.run(self.args)
        value = YAML().load(self.hermes)
        self.assertEqual(value['model'], 'existing')
        self.assertEqual(value['mcp_servers']['foreign']['url'], 'https://example.invalid/mcp')
        self.assertIn('# keep comment', self.hermes.read_text())
        backup = json.loads((self.root / 'backup/manifest.json').read_text())
        self.assertEqual((self.root / 'backup' / backup[str(self.hermes)]).read_bytes(), before)
        for target in self.targets['pi']:
            settings = json.loads(Path(target['settings']).read_text())
            self.assertEqual(settings['theme'], 'preserve')
            self.assertEqual(settings['extensions'], ['-builtin:codemode', '-builtin:tool-search', '-foreign', 'builtin:mcp'])
            mcp = json.loads(Path(target['mcp']).read_text())['mcpServers'][setup.NAME]
            self.assertEqual(mcp['exposure'], 'direct')
            self.assertEqual(len(mcp['headers']['Authorization'].removeprefix('Bearer ')), 64)
        snapshot = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.args.backup_dir = str(self.root / 'second-backup')
        setup.run(self.args)
        self.assertFalse((self.root / 'second-backup').exists())
        self.assertTrue(all(p.read_bytes() == data for p, data in snapshot.items()))
        settings = json.loads((self.state / 'mcp_settings.json').read_text())
        self.assertEqual(settings['mcpServers'], {})
        self.assertTrue(all(k['accessType'] == 'groups' and k['allowedGroups'] == [setup.GROUP_ID]
                            for k in settings['bearerKeys']))
        self.assertEqual(settings['systemConfig']['toolPolicies'][0]['serverId'], setup.CLOSED_SERVER_ID)
        self.assertFalse(settings['systemConfig']['activityLog']['storeToolPayload'])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.state.iterdir()))

    def test_late_occupied_or_malformed_target_refuses_before_any_writes(self):
        target = Path(self.targets['pi'][-1]['mcp'])
        for content in ('{bad-json', json.dumps({'mcpServers': {setup.NAME: {'url': 'https://foreign.invalid/mcp'}}})):
            with self.subTest(content=content):
                target.write_text(content)
                target.chmod(0o600)
                before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                with self.assertRaises(ValueError):
                    setup.run(self.args)
                self.assertFalse(self.state.exists())
                self.assertFalse((self.root / 'backup').exists())
                self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))

    def test_preview_and_symlink_refusal(self):
        self.args.apply = False
        setup.run(self.args)
        self.assertFalse(self.state.exists())
        target = Path(self.targets['pi'][-1]['mcp'])
        target.symlink_to(self.hermes)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            setup.run(self.args)
        self.assertFalse(self.state.exists())

    def test_occupied_state_is_not_reinitialized(self):
        self.state.mkdir(mode=0o700)
        (self.state / 'foreign').write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            setup.run(self.args)
        self.assertEqual((self.state / 'foreign').read_text(), 'preserve')
        self.assertNotIn(setup.NAME, self.hermes.read_text())


if __name__ == '__main__':
    unittest.main()
