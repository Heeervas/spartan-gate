import importlib.util
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / 'patches' / 'patch_clawroute_named_provider.py'


def load_module():
    spec = importlib.util.spec_from_file_location('patch_clawroute_named_provider', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ClawrouteNamedProviderPatchTests(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('HERMES_HOME')
        self._old_openai_base_url = os.environ.get('OPENAI_BASE_URL')
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['HERMES_HOME'] = self.tmp.name
        os.environ.pop('OPENAI_BASE_URL', None)
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        if self._old_home is None:
            os.environ.pop('HERMES_HOME', None)
        else:
            os.environ['HERMES_HOME'] = self._old_home
        if self._old_openai_base_url is None:
            os.environ.pop('OPENAI_BASE_URL', None)
        else:
            os.environ['OPENAI_BASE_URL'] = self._old_openai_base_url

    def write_config(self, relative_path, text):
        path = self.home / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def route_config(self, default='clawroute/auto', provider='custom', base_url='${OPENAI_BASE_URL}'):
        return '\n'.join([
            'model:',
            f'  default: {default}',
            f'  provider: {provider}',
            f'  base_url: {base_url}',
            '  context_length: 256000',
            '  api_key_env: CUSTOM_1_API_KEY',
            'custom_providers:',
            '  - name: Spartan Route',
            '    base_url: ${OPENAI_BASE_URL}',
            '    key_env: CUSTOM_1_API_KEY',
            '    model: spartan-route/clawroute/auto',
            '',
        ])

    def test_migrates_legacy_clawroute_model_in_main_and_profiles(self):
        module = load_module()
        main = self.write_config('config.yaml', self.route_config())
        coach = self.write_config('profiles/coach/config.yaml', self.route_config())

        self.assertIn('Patched:', module.patch_file(main))
        self.assertIn('Patched:', module.patch_file(coach))

        for path in (main, coach):
            text = path.read_text(encoding='utf-8')
            self.assertIn('default: clawroute/auto', text)
            self.assertIn('provider: openai-api', text)
            self.assertIn('base_url: ${OPENAI_BASE_URL}', text)
            self.assertNotIn('provider: custom\n', text)
            self.assertNotIn('name: Spartan Route', text)
            self.assertNotIn('custom_providers:', text)

    def test_skips_unrelated_custom_provider_config(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'model:',
                '  default: local-model',
                '  provider: custom',
                '  base_url: http://llm.internal:8000/v1',
                '  api_key_env: OTHER_KEY',
                'custom_providers:',
                '  - name: custom-1',
                '    base_url: http://llm.internal:8000/v1',
                '    key_env: OTHER_KEY',
                '    model: local-model',
                '',
            ]),
        )

        self.assertIn('Already patched:', module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertIn('provider: custom', text)
        self.assertIn('name: custom-1', text)

    def test_patch_is_idempotent(self):
        module = load_module()
        path = self.write_config('config.yaml', self.route_config())

        self.assertIn('Patched:', module.patch_file(path))
        first = path.read_text(encoding='utf-8')
        self.assertIn('Already patched:', module.patch_file(path))
        self.assertEqual(first, path.read_text(encoding='utf-8'))

    def test_keeps_base_url_marker_for_already_openai_api_provider(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            self.route_config(default='clawroute/auto', provider='openai-api').replace(
                '\ncustom_providers:\n  - name: Spartan Route\n    base_url: ${OPENAI_BASE_URL}\n    key_env: CUSTOM_1_API_KEY\n    model: spartan-route/clawroute/auto\n',
                '\n',
            ),
        )

        self.assertIn('Already patched:', module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertIn('base_url: ${OPENAI_BASE_URL}', text)

    def test_replaces_materialized_route_base_url_with_marker(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            self.route_config(
                default='custom-1/clawroute/auto',
                provider='custom-1',
                base_url='http://clawroute:18790/v1',
            ),
        )

        self.assertIn('Patched:', module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertIn('default: clawroute/auto', text)
        self.assertIn('provider: openai-api', text)
        self.assertIn('base_url: ${OPENAI_BASE_URL}', text)
        self.assertNotIn('base_url: http://clawroute:18790/v1', text)

    def test_strips_route_prefix_from_direct_model_without_changing_model_family(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            self.route_config(
                default='spartan-route/google/gemini-3-flash-preview',
                provider='custom:spartan-route',
            ),
        )

        self.assertIn('Patched:', module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertIn('default: google/gemini-3-flash-preview', text)
        self.assertIn('provider: openai-api', text)

    def test_preserves_unrelated_custom_provider_block(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  api_key_env: CUSTOM_1_API_KEY',
                'custom_providers:',
                '  - name: Spartan Route',
                '    base_url: ${OPENAI_BASE_URL}',
                '    key_env: CUSTOM_1_API_KEY',
                '    model: clawroute/auto',
                '  - name: unrelated',
                '    base_url: http://llm.internal:8000/v1',
                '    key_env: OTHER_KEY',
                '    model: local-model',
                '',
            ]),
        )

        self.assertIn('Patched:', module.patch_file(path))
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('name: Spartan Route', text)
        self.assertIn('custom_providers:', text)
        self.assertIn('name: unrelated', text)
        self.assertIn('model: local-model', text)

    def test_main_applies_patch_to_all_targets(self):
        module = load_module()
        main = self.write_config('config.yaml', self.route_config())
        work = self.write_config('profiles/work/config.yaml', self.route_config())

        module.main()

        self.assertIn('provider: openai-api', main.read_text(encoding='utf-8'))
        self.assertIn('provider: openai-api', work.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
