import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_prompt_load_callback.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_prompt_load_callback',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class PromptLoadCallbackPatchTests(unittest.TestCase):
    def test_injects_prompt_load_callback_before_update_prompt(self):
        module = load_module()
        source = '''async def _handle_callback_query(self, update, context):
    query = update.callback_query
    data = query.data
    if not data.startswith("update_prompt:"):
        return
    answer = data.split(":", 1)[1]
'''

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn('if data.startswith("pl:"):', patched)
        self.assertIn('synth_text = f"/prompt_load_select {skill_name}{session_context}"', patched)
        self.assertLess(patched.index(module.MARKER), patched.index(module.ANCHOR))

    def test_idempotent_when_marker_present(self):
        module = load_module()
        source = f'before\n{module.MARKER}\nafter\n'

        self.assertEqual(module.patch_source(source), source)

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
