import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml

MODULE_PATH = Path(__file__).parent / 'patches/patch_chrome_devtools_ws_auth.py'
spec = importlib.util.spec_from_file_location('retired_cdp', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RetiredCdpMigrationTests(unittest.TestCase):
    def test_removes_managed_settings_preserves_custom_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            path.write_text('''browser:
  cdp_url: ${BROWSER_CDP_URL}
  profiles:
    custom:
      cdp_url: ws://custom.example/browser
    main:
      cdp_url: ws://browserless:3000/chromium?token=fixture
mcp_servers:
  chrome_devtools:
    args: ['--wsEndpoint=${BROWSER_CDP_URL}']
  keep:
    command: custom
''')
            module.patch_file(path)
            config = yaml.safe_load(path.read_text())
            self.assertNotIn('cdp_url', config['browser'])
            self.assertNotIn('cdp_url', config['browser']['profiles']['main'])
            self.assertEqual(config['browser']['profiles']['custom']['cdp_url'], 'ws://custom.example/browser')
            self.assertNotIn('chrome_devtools', config['mcp_servers'])
            self.assertEqual(config['mcp_servers']['keep']['command'], 'custom')
            first = path.read_bytes()
            module.patch_file(path)
            self.assertEqual(path.read_bytes(), first)

    def test_custom_tooling_is_byte_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            original = '# keep formatting\nbrowser:\n  cdp_url: ws://custom.example/browser\n'
            path.write_text(original)
            module.patch_file(path)
            self.assertEqual(path.read_text(), original)

    def test_malformed_config_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            original = 'browser: [invalid'
            path.write_text(original)
            with self.assertRaises(yaml.YAMLError):
                module.patch_file(path)
            self.assertEqual(path.read_text(), original)

    def test_env_removes_only_managed_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('BROWSER_CDP_URL=${BROWSER_CDP_URL}\nBROWSER_CDP_MAIN_URL=ws://custom.example/browser\nAPI_SERVER_KEY=fixture\n')
            module.patch_env_file(path)
            self.assertEqual(path.read_text(), 'BROWSER_CDP_MAIN_URL=ws://custom.example/browser\nAPI_SERVER_KEY=fixture\n')
