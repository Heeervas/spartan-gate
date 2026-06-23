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
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['HERMES_HOME'] = self.tmp.name
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        if self._old_home is None:
            os.environ.pop('HERMES_HOME', None)
        else:
            os.environ['HERMES_HOME'] = self._old_home

    def write_config(self, relative_path, text):
        path = self.home / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_migrates_legacy_clawroute_model_in_main_and_profiles(self):
        module = load_module()
        main = self.write_config(
            'config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  context_length: 256000',
                '  api_key_env: CUSTOM_1_API_KEY',
                '',
            ]),
        )
        coach = self.write_config(
            'profiles/coach/config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  context_length: 256000',
                '  api_key_env: CUSTOM_1_API_KEY',
                '',
            ]),
        )

        self.assertIn('Patched:', module.patch_file(main))
        self.assertIn('Patched:', module.patch_file(coach))

        for path in (main, coach):
            text = path.read_text(encoding='utf-8')
            self.assertIn('default: custom-1/clawroute/auto', text)
            self.assertIn('provider: custom-1', text)
            self.assertNotIn('default: clawroute/auto', text)
            self.assertNotIn('provider: custom\n', text)

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
                '',
            ]),
        )

        self.assertIn('Skip:', module.patch_file(path))
        self.assertIn('provider: custom', path.read_text(encoding='utf-8'))

    def test_patch_is_idempotent(self):
        module = load_module()
        path = self.write_config(
            'config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  context_length: 256000',
                '  api_key_env: CUSTOM_1_API_KEY',
                '',
            ]),
        )

        self.assertIn('Patched:', module.patch_file(path))
        first = path.read_text(encoding='utf-8')
        self.assertIn('Already patched:', module.patch_file(path))
        self.assertEqual(first, path.read_text(encoding='utf-8'))

    def test_main_applies_patch_to_all_targets(self):
        module = load_module()
        main = self.write_config(
            'config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  context_length: 256000',
                '  api_key_env: CUSTOM_1_API_KEY',
                '',
            ]),
        )
        work = self.write_config(
            'profiles/work/config.yaml',
            '\n'.join([
                'model:',
                '  default: clawroute/auto',
                '  provider: custom',
                '  base_url: ${OPENAI_BASE_URL}',
                '  context_length: 256000',
                '  api_key_env: CUSTOM_1_API_KEY',
                '',
            ]),
        )

        module.main()

        self.assertIn('provider: custom-1', main.read_text(encoding='utf-8'))
        self.assertIn('provider: custom-1', work.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()