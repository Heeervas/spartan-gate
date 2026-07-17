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

    def test_unauthorized_callback_does_not_generate_synthetic_event(self):
        import asyncio
        from types import SimpleNamespace

        module = load_module()
        source = """class TelegramAdapter:
    async def _handle_callback_query(self, update, context):
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data
        query_message = getattr(query, "message", None)
        query_chat_id = getattr(query_message, "chat_id", None)
        query_chat = getattr(query_message, "chat", None)
        query_chat_type = getattr(query_chat, "type", None)
        query_thread_id = getattr(query_message, "message_thread_id", None)
        query_user_name = getattr(query.from_user, "first_name", None)
        if not data.startswith("update_prompt:"):
            return
"""
        patched = module.patch_source(source)
        namespace = {}
        exec(patched, namespace)
        adapter = namespace["TelegramAdapter"]()
        adapter._is_callback_user_authorized = lambda *_args, **_kwargs: False
        adapter.handled = []

        async def handle_message(event):
            adapter.handled.append(event)

        adapter.handle_message = handle_message
        answers = []

        async def answer(**kwargs):
            answers.append(kwargs)

        user = SimpleNamespace(id=7, first_name="intruder", full_name="Intruder")
        chat = SimpleNamespace(id=42, type="group", title="test", full_name="test")
        message = SimpleNamespace(chat_id=42, chat=chat, message_thread_id=None)
        query = SimpleNamespace(data="pl:private-skill", from_user=user, message=message, answer=answer)
        update = SimpleNamespace(callback_query=query)

        asyncio.run(adapter._handle_callback_query(update, None))

        self.assertEqual(adapter.handled, [])
        self.assertEqual(answers, [{"text": "⛔ You are not authorized to load prompts."}])

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
