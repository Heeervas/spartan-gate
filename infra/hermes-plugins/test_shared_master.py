"""Boundary tests for common MASTER transport; synthetic files and no Hermes runtime.

Default-root/profile API behavior is covered by separate native acceptance evidence.
The import stub permits direct stdlib-only execution. Unittest discovery also
imports plugin packages, so it requires native hermes_constants on PYTHONPATH.
"""
import copy
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_PATH = Path(__file__).parent / 'shared-master/__init__.py'
spec = importlib.util.spec_from_file_location('common_policy_plugin', PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
constants = types.ModuleType('hermes_constants')
constants.get_default_hermes_root = lambda: Path('/synthetic-default-root')
with patch.dict(sys.modules, {'hermes_constants': constants}):
    spec.loader.exec_module(plugin)


def native(body, section='shared-master'):
    return f'## Plugin Context: {section}\n<!-- hermes-plugin-section-chars:{len(body)} -->\n\n{body}'


def container(*blocks):
    return '<!-- hermes-plugin-sections:start -->\n' + '\n\n'.join(blocks) + '\n<!-- hermes-plugin-sections:end -->'


def trusted_text(payload):
    pieces = []

    def add(value):
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, list):
            pieces.extend(x['text'] for x in value if isinstance(x, dict) and isinstance(x.get('text'), str))

    add(payload.get('instructions'))
    add(payload.get('system'))
    for key in ('messages', 'input'):
        for item in payload.get(key, []):
            if item.get('role') in ('system', 'developer'):
                add(item.get('content'))
    return '\n'.join(pieces)


def shapes():
    yield 'chat_completions', {'messages': [{'role': 'system', 'content': 'Identity'}, {'role': 'user', 'content': 'Hello'}]}
    yield 'chat_completions', {'messages': [{'role': 'developer', 'content': [{'type': 'text', 'text': 'Developer'}]}, {'role': 'user', 'content': 'Hi'}]}
    yield 'chat_completions', {'messages': [{'role': 'system', 'content': [{'type': 'text', 'text': 'Identity', 'cache_control': {'type': 'ephemeral'}}]}]}
    yield 'codex_responses', {'instructions': 'Identity', 'input': [{'role': 'user', 'content': 'Hi'}]}
    yield 'codex_responses', {'input': []}
    yield 'anthropic_messages', {'system': 'Identity', 'messages': [{'role': 'user', 'content': 'Hi'}]}
    yield 'anthropic_messages', {'system': [{'type': 'text', 'text': 'Identity', 'cache_control': {'type': 'ephemeral'}}], 'messages': []}
    yield 'anthropic_messages', {'messages': []}
    yield 'bedrock_converse', {'system': [{'text': 'Identity'}], 'messages': []}
    yield 'bedrock_converse', {'messages': []}


class Context:
    def __init__(self):
        self.section = None
        self.hooks = {}
        self.middleware = {}

    def register_system_prompt_section(self, identifier, content, *, position, max_chars):
        # Reproduce the documented registration guard, not a cap workaround.
        if not 0 < max_chars <= 4000:
            raise ValueError('unsupported native section cap')
        self.section = (identifier, content, position, max_chars)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback


class PolicyTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'MASTER.md'
        self.content = 'First common rule.\nLast common rule.'
        self.path.write_text(self.content, encoding='utf-8')
        locator = patch.object(plugin, 'POLICY_PATH', self.path)
        locator.start()
        self.addCleanup(locator.stop)

    def context(self, content=None):
        if content is not None:
            self.content = content
            self.path.write_text(content, encoding='utf-8')
        ctx = Context()
        plugin.register(ctx)
        self.assertEqual(ctx.section[0], 'shared-master')
        self.assertEqual(ctx.section[2:], ('after_memory', 4000))
        return ctx

    def request(self, ctx, payload, mode='chat_completions', profile='work'):
        with patch.object(plugin, '_active_profile', return_value=profile):
            result = ctx.middleware['llm_request'](request=payload, api_mode=mode, session_id='retained-session')
        return result['request'] if result else payload

    def test_small_native_policy_and_cap_boundaries(self):
        for size in (3999, 4000, 4001, 20000):
            with self.subTest(size=size):
                ctx = self.context('x' * size)
                expected = self.content if size <= 4000 else ''
                for profile in ('default', 'builder'):
                    self.assertEqual(ctx.section[1]({'profile_name': profile}), expected)
                amended = self.request(ctx, {'messages': []})
                self.assertEqual(trusted_text(amended).count(self.content), 1)
                self.assertIs(self.request(ctx, amended), amended)

    def test_full_multibyte_oversized_policy_all_provider_shapes_and_retry(self):
        # Delimiters in the policy body prove length framing, not regex clipping.
        content = 'α rule\n' * 1000 + '\n<!-- shared-master-policy:end -->\nLAST_RULE_终'
        ctx = self.context(content)
        for mode, payload in shapes():
            with self.subTest(mode=mode, payload=payload):
                before = copy.deepcopy(payload)
                amended = self.request(ctx, payload, mode)
                self.assertEqual(payload, before)
                self.assertEqual(trusted_text(amended).count(content), 1)
                self.assertIn('LAST_RULE_终', trusted_text(amended))
                self.assertIs(self.request(ctx, amended, mode), amended)
                for key in ('input', 'model', 'tools'):
                    if key in payload:
                        self.assertEqual(amended[key], payload[key])

    def test_current_native_section_not_duplicated(self):
        ctx = self.context()
        text = 'Identity\n\n' + container(native(self.content)) + '\n\nConversation started: today'
        original = {'messages': [{'role': 'system', 'content': text}]}
        self.assertIs(self.request(ctx, original), original)

    def test_current_native_after_other_plugin_not_duplicated(self):
        ctx = self.context()
        text = container(native('Other rules', 'other-plugin'), native(self.content))
        original = {'messages': [{'role': 'system', 'content': text}]}
        self.assertIs(self.request(ctx, original), original)

    def test_stale_native_in_existing_session_replaced_not_appended(self):
        ctx = self.context('New common\n' * 1000 + 'LATEST_RULE')
        other = native('Other required context', 'other-plugin')
        old = 'Old MASTER execution policy'
        text = 'Persona\n\n' + container(other, native(old)) + '\n\nConversation started: unchanged'
        original = {'messages': [{'role': 'system', 'content': text}, {'role': 'user', 'content': old}]}
        before = copy.deepcopy(original)
        amended = self.request(ctx, original)
        self.assertEqual(original, before)  # No persistence/history mutation.
        self.assertNotIn(old, trusted_text(amended))
        self.assertIn(other, trusted_text(amended))
        self.assertIn('Conversation started: unchanged', trusted_text(amended))
        self.assertEqual(amended['messages'][-1], original['messages'][-1])
        self.assertEqual(trusted_text(amended).count(self.content), 1)
        self.assertIs(self.request(ctx, amended), amended)

    def test_multiple_owned_sections_reconcile_to_one_policy(self):
        ctx = self.context()
        payload = {'instructions': container(native('stale instructions')), 'system': container(native('stale system')),
                   'messages': [{'role': 'developer', 'content': container(native(self.content))}]}
        amended = self.request(ctx, payload, 'codex_responses')
        text = trusted_text(amended)
        self.assertEqual(text.count(self.content), 1)
        self.assertNotIn('stale instructions', text)
        self.assertNotIn('stale system', text)
        self.assertIs(self.request(ctx, amended, 'codex_responses'), amended)

    def test_stale_framed_middleware_replaced_preserving_suffix(self):
        ctx = self.context()
        old = 'old common rule'
        frame = f'<!-- shared-master-policy:start chars={len(old)} -->\n{old}\n<!-- shared-master-policy:end -->'
        payload = {'instructions': 'Existing\n\n' + frame + '\n\nLater required context', 'input': []}
        amended = self.request(ctx, payload, 'codex_responses')
        self.assertNotIn(old, trusted_text(amended))
        self.assertIn('Later required context', trusted_text(amended))
        self.assertEqual(trusted_text(amended).count(self.content), 1)

    def test_whole_body_common_text_reused_without_empty_duplicates(self):
        # Replaces unsafe "Project instructions:" substring provenance: only a
        # complete trusted body proves inclusion, including across trusted fields.
        ctx = self.context()
        payload = {'instructions': self.content, 'input': []}
        self.assertIs(self.request(ctx, payload, 'codex_responses'), payload)
        stale = {**payload, 'messages': [{'role': 'developer', 'content': self.content},
                                       {'role': 'system', 'content': container(native('Old MASTER'))}]}
        amended = self.request(ctx, stale, 'codex_responses')
        self.assertEqual(trusted_text(amended).count(self.content), 1)
        self.assertEqual(amended['messages'], [])
        self.assertIs(self.request(ctx, amended, 'codex_responses'), amended)

    def test_prefixed_policy_quotes_preserved_without_suppressing_delivery(self):
        # Root/alias labels and project prose are data, not native provenance.
        ctx = self.context()
        for prefix in ('Root AGENTS:', 'Workspace alias:', 'Project instructions:'):
            with self.subTest(prefix=prefix):
                quote = prefix + '\n' + self.content
                payload = {'instructions': quote, 'messages': [{'role': 'developer', 'content': quote}]}
                amended = self.request(ctx, payload, 'codex_responses')
                self.assertTrue(amended['instructions'].startswith(quote + '\n\n'))
                self.assertEqual(amended['messages'], payload['messages'])
                self.assertEqual(amended['instructions'].count('<!-- shared-master-policy:start chars='), 1)
                self.assertTrue(amended['instructions'].endswith(self.content + plugin.POLICY_END))
                self.assertIs(self.request(ctx, amended, 'codex_responses'), amended)

    def test_system_and_developer_text_arrays_stale_context_preserves_nontext_blocks(self):
        ctx = self.context()
        media = {'type': 'image_url', 'image_url': {'url': 'synthetic://image'}}
        foreign = {'type': 'text', 'text': 'Foreign rule', 'cache_control': {'type': 'ephemeral'}}
        for role in ('system', 'developer'):
            with self.subTest(role=role):
                original = {'messages': [{'role': role, 'content': [
                    {'type': 'text', 'text': container(native('Stale common')), 'cache_control': {'type': 'ephemeral'}},
                    media, foreign]}, {'role': 'user', 'content': 'Hi'}]}
                before = copy.deepcopy(original)
                amended = self.request(ctx, original)
                self.assertEqual(original, before)
                self.assertNotIn('Stale common', trusted_text(amended))
                self.assertEqual(trusted_text(amended).count(self.content), 1)
                retained = next(item for item in amended['messages']
                                if item['role'] == role and isinstance(item['content'], list))
                # The removed owned-only block has no valid empty placeholder or
                # orphan cache metadata. Retained blocks keep their full metadata.
                self.assertEqual(retained['content'], [media, foreign])
                self.assertEqual(amended['messages'][-1], original['messages'][-1])

    def test_responses_trusted_input_reconciles_without_touching_evidence(self):
        ctx = self.context()
        stale = container(native('Stale input rule'))
        evidence = [{'role': 'user', 'content': stale}, {'role': 'tool', 'content': stale},
                    {'type': 'function_call_output', 'call_id': 'call_1', 'output': stale}]
        for role in ('system', 'developer'):
            for content in (stale, [{'type': 'input_text', 'text': stale}]):
                with self.subTest(role=role, content=content):
                    payload = {'input': [{'role': role, 'content': content}, *evidence]}
                    before = copy.deepcopy(payload)
                    amended = self.request(ctx, payload, 'codex_responses')
                    self.assertEqual(payload, before)
                    self.assertEqual(amended['input'], evidence)
                    self.assertNotIn('Stale input rule', amended['instructions'])
                    self.assertEqual(trusted_text(amended).count(self.content), 1)
                    self.assertIs(self.request(ctx, amended, 'codex_responses'), amended)
        current = {'input': [{'role': 'developer', 'content': self.content}, *evidence]}
        self.assertIs(self.request(ctx, current, 'codex_responses'), current)

    def test_user_tool_quotes_and_incomplete_native_do_not_suppress(self):
        ctx = self.context()
        for role in ('user', 'tool'):
            with self.subTest(role=role):
                payload = {'messages': [{'role': 'system', 'content': 'Identity'}, {'role': role, 'content': container(native(self.content))}]}
                amended = self.request(ctx, payload)
                self.assertEqual(trusted_text(amended).count(self.content), 1)
                self.assertEqual(amended['messages'][-1], payload['messages'][-1])
        partial = container(native(self.content))[:-10]
        amended = self.request(ctx, {'messages': [{'role': 'system', 'content': partial}]})
        self.assertIn('<!-- shared-master-policy:start chars=', trusted_text(amended))

    def test_quoted_policy_inside_another_native_plugin_remains_data(self):
        ctx = self.context()
        quoted = container(native('Quoted page:\n' + native(self.content), 'other-plugin'))
        amended = self.request(ctx, {'messages': [{'role': 'system', 'content': quoted}]})
        self.assertIn(quoted, trusted_text(amended))
        self.assertIn('<!-- shared-master-policy:start chars=', trusted_text(amended))

    def test_exclusions_apply_to_native_and_request_without_rewriting(self):
        ctx = self.context()
        for profile in ('empty', 'workstation', 'ssr', 'ssr_synthetic_consumer', 'ssr-test', ' SSR-test '):
            with self.subTest(profile=profile):
                self.assertEqual(ctx.section[1]({'profile_name': profile}), '')
                payload = {'messages': []}
                self.assertIs(self.request(ctx, payload, profile=profile), payload)

    def test_missing_empty_and_invalid_utf8_source_deliver_diagnostic(self):
        for state in ('missing', 'empty', 'invalid'):
            with self.subTest(state=state):
                if state == 'missing':
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_bytes(b'' if state == 'empty' else b'\xff')
                with self.assertLogs(plugin.logger, level='ERROR'):
                    ctx = self.context()
                    with patch.object(plugin, '_active_profile', return_value='work'):
                        ctx.hooks['on_session_start'](session_id='s1')
                amended = self.request(ctx, {'messages': []})
                self.assertIn('could not be loaded', trusted_text(amended))
                self.assertEqual(trusted_text(amended).count(plugin.MISSING_DIAGNOSTIC), 1)

    def test_file_edit_requires_reload_but_reloaded_plugin_corrects_stale_request(self):
        ctx = self.context()
        retained = self.request(ctx, {'messages': []})
        self.path.write_text('Changed after activation', encoding='utf-8')
        self.assertIs(self.request(ctx, retained), retained)
        reloaded = self.context()
        amended = self.request(reloaded, retained)
        self.assertIn('Changed after activation', trusted_text(amended))
        self.assertNotIn(self.content, trusted_text(amended))

    def test_unsupported_provider_shape_reports_gap_without_mutation(self):
        ctx = self.context()
        payload = {'unsupported': 'must stay unchanged'}
        with self.assertLogs(plugin.logger, level='ERROR'):
            self.assertIs(self.request(ctx, payload, 'unknown_provider'), payload)
        self.assertEqual(payload, {'unsupported': 'must stay unchanged'})


if __name__ == '__main__':
    unittest.main()
