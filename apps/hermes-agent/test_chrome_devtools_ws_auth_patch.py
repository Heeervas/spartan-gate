import importlib.util
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / 'patches' / 'patch_chrome_devtools_ws_auth.py'


def load_module():
    spec = importlib.util.spec_from_file_location('patch_chrome_devtools_ws_auth', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ChromeDevtoolsWsAuthPatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self._old_home = os.environ.get('HERMES_HOME')
        self._old_main_url = os.environ.get('BROWSER_CDP_MAIN_URL')
        self._old_broker_enabled = os.environ.get('BROWSERLESS_CDP_BROKER_ENABLED')
        self._old_camofox_url = os.environ.get('CAMOFOX_URL')
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['HERMES_HOME'] = self.tmp.name
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        if self._old_home is None:
            os.environ.pop('HERMES_HOME', None)
        else:
            os.environ['HERMES_HOME'] = self._old_home
        if self._old_main_url is None:
            os.environ.pop('BROWSER_CDP_MAIN_URL', None)
        else:
            os.environ['BROWSER_CDP_MAIN_URL'] = self._old_main_url
        if self._old_broker_enabled is None:
            os.environ.pop('BROWSERLESS_CDP_BROKER_ENABLED', None)
        else:
            os.environ['BROWSERLESS_CDP_BROKER_ENABLED'] = self._old_broker_enabled
        if self._old_camofox_url is None:
            os.environ.pop('CAMOFOX_URL', None)
        else:
            os.environ['CAMOFOX_URL'] = self._old_camofox_url

    def write_config(self, relative_path, text):
        path = self.home / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_migrates_legacy_browser_cdp_urls_in_main_and_profiles(self):
        legacy = 'ws://browserless:3000/chromium?token=${BROWSERLESS_TOKEN}&stealth=true&launch=%7B%22headless%22%3Afalse%7D'
        main = self.write_config('config.yaml', f'browser:\n  cdp_url: {legacy}\n')
        coach = self.write_config(
            'profiles/coach/config.yaml',
            f'browser:\n  profiles:\n    stealth:\n      cdp_url: {legacy}\n',
        )

        self.assertIn('Patched:', self.module.patch_file(main))
        self.assertIn('Patched:', self.module.patch_file(coach))

        main_text = main.read_text(encoding='utf-8')
        coach_text = coach.read_text(encoding='utf-8')
        self.assertIn('cdp_url: ${BROWSER_CDP_URL}', main_text)
        self.assertIn('cdp_url: ${BROWSER_CDP_URL}', coach_text)
        self.assertIn('main:', main_text)
        self.assertIn('cdp_url: ${BROWSER_CDP_MAIN_URL}', main_text)
        self.assertIn('cdp_url: ${BROWSER_CDP_MAIN_URL}', coach_text)

    def test_leaves_custom_cdp_urls_untouched(self):
        path = self.write_config('config.yaml', 'browser:\n  cdp_url: ws://remote.example/chromium\n')

        self.assertIn('No-op:', self.module.patch_file(path))
        self.assertIn('ws://remote.example/chromium', path.read_text(encoding='utf-8'))

    def test_adds_main_browser_profile_to_already_migrated_browser_config(self):
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'browser:',
                '  default_profile: stealth',
                '  cdp_url: ${BROWSER_CDP_URL}',
                '  profiles:',
                '    stealth:',
                '      cdp_url: ${BROWSER_CDP_URL}',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        first = path.read_text(encoding='utf-8')
        self.assertIn('cdp_url: ${BROWSER_CDP_MAIN_URL}', first)
        self.assertIn('Already patched:', self.module.patch_file(path))
        self.assertEqual(first, path.read_text(encoding='utf-8'))

    def test_preserves_existing_custom_main_browser_profile(self):
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'browser:',
                '  cdp_url: ${BROWSER_CDP_URL}',
                '  profiles:',
                '    stealth:',
                '      cdp_url: ${BROWSER_CDP_URL}',
                '    main:',
                '      cdp_url: ws://custom.example/chromium',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertEqual(text.count('    main:'), 1)
        self.assertIn('cdp_url: ws://custom.example/chromium', text)
        self.assertNotIn('cdp_url: ${BROWSER_CDP_MAIN_URL}', text)

    def test_does_not_add_main_profile_when_broker_is_disabled_without_main_url(self):
        os.environ['BROWSERLESS_CDP_BROKER_ENABLED'] = 'false'
        os.environ.pop('BROWSER_CDP_MAIN_URL', None)
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'browser:',
                '  default_profile: stealth',
                '  cdp_url: ${BROWSER_CDP_URL}',
                '  profiles:',
                '    stealth:',
                '      cdp_url: ${BROWSER_CDP_URL}',
                '',
            ]),
        )

        self.assertIn('Already patched:', self.module.patch_file(path))
        self.assertNotIn('BROWSER_CDP_MAIN_URL', path.read_text(encoding='utf-8'))

    def test_adds_main_profile_when_broker_disabled_but_main_url_is_explicit(self):
        os.environ['BROWSERLESS_CDP_BROKER_ENABLED'] = 'false'
        os.environ['BROWSER_CDP_MAIN_URL'] = 'ws://127.0.0.1:9229'
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'browser:',
                '  default_profile: stealth',
                '  cdp_url: ${BROWSER_CDP_URL}',
                '  profiles:',
                '    stealth:',
                '      cdp_url: ${BROWSER_CDP_URL}',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        self.assertIn('cdp_url: ${BROWSER_CDP_MAIN_URL}', path.read_text(encoding='utf-8'))

    def test_repairs_default_and_profiles_that_all_point_to_main_broker(self):
        for relative_path in ['config.yaml', 'profiles/coach/config.yaml', 'profiles/work/config.yaml']:
            path = self.write_config(
                relative_path,
                '\n'.join([
                    'browser:',
                    '  cdp_url: ${BROWSER_CDP_MAIN_URL}',
                    '  default_profile: main',
                    '  profiles:',
                    '    main:',
                    "      color: '#AA6600'",
                    '      cdp_url: ${BROWSER_CDP_MAIN_URL}',
                    '      headless: false',
                    '    stealth:',
                    "      color: '#00AA00'",
                    '      cdp_url: ${BROWSER_CDP_MAIN_URL}',
                    '      headless: false',
                    '',
                ]),
            )

            self.assertIn('Patched:', self.module.patch_file(path))
            text = path.read_text(encoding='utf-8')
            self.assertIn('  cdp_url: ${BROWSER_CDP_URL}', text)
            self.assertIn('  default_profile: stealth', text)
            self.assertIn("    stealth:\n      color: '#00AA00'\n      cdp_url: ${BROWSER_CDP_URL}", text)
            self.assertIn("    main:\n      color: '#AA6600'\n      cdp_url: ${BROWSER_CDP_MAIN_URL}", text)

    def test_normalizes_empty_default_profile_browser_config(self):
        path = self.write_config(
            'profiles/default/config.yaml',
            '\n'.join([
                'browser:',
                '  inactivity_timeout: 120',
                "  cdp_url: ''",
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertIn('  cdp_url: ${BROWSER_CDP_URL}', text)
        self.assertIn('  default_profile: stealth', text)
        self.assertIn('    stealth:', text)
        self.assertIn('    main:', text)

    def test_removes_stale_cdp_launch_env_override_in_home_and_profiles(self):
        root_env = self.write_config(
            '.env',
            '\n'.join([
                'BROWSER_CDP_URL=${BROWSER_CDP_URL}',
                'BROWSER_CDP_LAUNCH_URL=${BROWSER_CDP_URL}',
                'OPENAI_BASE_URL=http://clawroute:18790/v1',
                '',
            ]),
        )
        profile_env = self.write_config(
            'profiles/work/.env',
            '\n'.join([
                'API_SERVER_PORT=8644',
                'BROWSER_CDP_LAUNCH_URL=${BROWSER_CDP_URL}',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_env_file(root_env))
        self.assertIn('Patched:', self.module.patch_env_file(profile_env))
        self.assertNotIn('BROWSER_CDP_LAUNCH_URL', root_env.read_text(encoding='utf-8'))
        self.assertNotIn('BROWSER_CDP_LAUNCH_URL', profile_env.read_text(encoding='utf-8'))
        self.assertIn('BROWSER_CDP_URL=${BROWSER_CDP_URL}', root_env.read_text(encoding='utf-8'))

    def test_chrome_devtools_migration_remains_idempotent(self):
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'mcp_servers:',
                '  chrome_devtools:',
                '    args:',
                '    - --browserUrl=http://browserless:3000?token=${BROWSERLESS_TOKEN}',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        first = path.read_text(encoding='utf-8')
        self.assertIn('- --wsEndpoint=${BROWSER_CDP_URL}', first)
        self.assertIn('Bearer ${BROWSERLESS_TOKEN}', first)

        self.assertIn('Already patched:', self.module.patch_file(path))
        self.assertEqual(first, path.read_text(encoding='utf-8'))

    def test_camofox_mode_removes_managed_cdp_placeholders(self):
        os.environ['CAMOFOX_URL'] = 'http://camofox:9377'
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'browser:',
                '  default_profile: stealth',
                '  cdp_url: ${BROWSER_CDP_URL}',
                '  profiles:',
                '    stealth:',
                '      cdp_url: ${BROWSER_CDP_URL}',
                'mcp_servers:',
                '  chrome_devtools:',
                '    args:',
                '    - --wsEndpoint=${BROWSER_CDP_URL}',
                '    - \'--wsHeaders={"Authorization":"Bearer ${BROWSERLESS_TOKEN}"}\'',
                '  keep_me:',
                '    command: true',
                '',
            ]),
        )

        self.assertIn('Patched:', self.module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('${BROWSER_CDP_URL}', text)
        self.assertNotIn('chrome_devtools:', text)
        self.assertIn('keep_me:', text)


if __name__ == '__main__':
    unittest.main()
