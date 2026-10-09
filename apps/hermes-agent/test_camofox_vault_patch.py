"""Focused native-source regressions for the Camofox vault boundary."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


transport = load('spartan_camofox_vault', HERE / 'spartan_camofox_vault.py')
patcher = load('vault_patcher', HERE / 'patches/patch_camofox_vault.py')


def functions(source, names, namespace):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), '<transformed-native-vault>', 'exec'), namespace)
    return namespace


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.session = {'user_id': 'profile', 'session_key': 'durable-task', 'tab_id': 'tab1'}
        self.camofox = types.ModuleType('tools.browser_camofox')
        self.camofox.is_camofox_mode = lambda: True
        self.camofox._require_tab = Mock(return_value=(self.session, None))
        self.camofox._sessions = types.SimpleNamespace(get=lambda tid: self.session)
        self.camofox._auth_headers = lambda: {'Authorization': 'Bearer auth'}
        self.camofox.get_camofox_url = lambda: 'http://camofox:9377'
        self.modules = patch.dict(sys.modules, {'tools.browser_camofox': self.camofox})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_pins_once_across_nested_save_fill_and_uses_private_endpoint(self):
        calls = []
        def request(method, path, body=None):
            calls.append((method, path, body))
            return {'vaultEvaluateVersion': 1} if method == 'GET' else {'ok': True, 'result': {'filled': 1}}
        @transport.page_operation
        def fill(handle, task_id=None):
            return json.dumps(transport.evaluate(task_id, 'native_secret_expression', secret=True))
        @transport.page_operation
        def save(label='', task_id=None):
            return fill('handle', task_id=task_id)
        with patch.object(transport, '_request', side_effect=request):
            self.assertTrue(json.loads(save(task_id='durable-task'))['success'])
        self.camofox._require_tab.assert_called_once_with('durable-task', 'fill vault fields')
        self.assertEqual(calls[1][1], '/tabs/tab1/spartan/vault-evaluate')
        self.assertEqual(calls[1][2]['sessionKey'], 'durable-task')

    def test_capability_and_reserved_group_deny_before_native_operation(self):
        native = Mock(return_value='{}')
        operation = transport.page_operation(native)
        with patch.object(transport, '_request', return_value={'vaultEvaluateVersion': 0}):
            self.assertEqual(json.loads(operation(task_id='t'))['error_type'], 'vault_unavailable')
        native.assert_not_called()
        self.session['session_key'] = 'spartan-operator-login'
        with patch.object(transport, '_request') as request:
            self.assertEqual(json.loads(operation(task_id='t'))['error_type'], 'vault_scope_denied')
        request.assert_not_called()

    def test_private_page_guard_denies_before_capability_or_prompt(self):
        self.camofox._require_tab.return_value = (self.session, '{"success":false,"error":"private page"}')
        native = Mock()
        with patch.object(transport, '_request') as request:
            result = json.loads(transport.page_operation(native)(task_id='t'))
        self.assertEqual(result['error_type'], 'vault_session_required')
        native.assert_not_called()
        request.assert_not_called()

    def test_prompt_identity_change_denies_write(self):
        @transport.page_operation
        def operation(handle='', task_id=None):
            self.session['tab_id'] = 'other-tab'  # a prompt callback changed browser focus
            return json.dumps(transport.evaluate(task_id, 'secret', secret=True))
        with patch.object(transport, '_request', return_value={'vaultEvaluateVersion': 1}) as request:
            self.assertEqual(json.loads(operation(task_id='t'))['error_type'], 'vault_session_changed')
        self.assertEqual(request.call_count, 1)

    def test_stale_tab_requires_navigation_and_fixed_failure(self):
        @transport.page_operation
        def operation(handle='', task_id=None):
            return json.dumps(transport.evaluate(task_id, 'secret', secret=True))
        with patch.object(transport, '_request', side_effect=[{'vaultEvaluateVersion': 1}, transport.VaultTransportError('vault_tab_stale')]):
            result = json.loads(operation(task_id='t'))
        self.assertEqual(result, transport.failure('vault_tab_stale'))
        self.assertIsNone(self.session['tab_id'])
        self.assertFalse(self.session['adopt_existing_tab'])

    def test_indeterminate_secret_timeout_retires_local_tab_before_retry(self):
        @transport.page_operation
        def operation(handle='', task_id=None):
            return json.dumps(transport.evaluate(task_id, 'secret', secret=True))
        with patch.object(transport, '_request', side_effect=[{'vaultEvaluateVersion': 1}, transport.VaultTransportError('vault_transport_failed')]):
            result = json.loads(operation(task_id='t'))
        self.assertEqual(result, transport.failure('vault_transport_failed'))
        self.assertIsNone(self.session['tab_id'])
        self.assertFalse(self.session['adopt_existing_tab'])
        with patch.object(transport, '_request') as request:
            self.assertEqual(json.loads(operation(task_id='t'))['error_type'], 'vault_session_required')
        request.assert_not_called()

    def test_direct_bounded_transport_never_reflects_http_body(self):
        for status in (302, 400, 401, 403, 404, 409, 410, 413, 500, 503):
            with self.subTest(status=status):
                response = Mock(status_code=status)
                response.iter_content.return_value = [b'{"error":"secret raw response"}']
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=None)
                client = Mock()
                client.request.return_value = response
                client.__enter__ = Mock(return_value=client)
                client.__exit__ = Mock(return_value=None)
                with patch.object(transport.requests, 'Session', return_value=client):
                    with self.assertRaises(transport.VaultTransportError) as raised:
                        transport._request('POST', '/protected', {'expression': 'secret'})
                self.assertNotIn('secret', str(raised.exception))
                self.assertFalse(client.trust_env)
                self.assertFalse(client.request.call_args.kwargs['allow_redirects'])
                if status in (404, 410):
                    response.iter_content.assert_not_called()
                else:
                    response.iter_content.assert_called_once_with(8192)


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE to clean or installed Hermes source')
class NativeSourceTests(unittest.TestCase):
    def setUp(self):
        TransportTests.setUp(self)
        root = Path(SOURCE)
        self.vault = (root / 'tools/browser_vault_tool.py').read_text()
        self.browser = (root / 'tools/browser_camofox.py').read_text()
        self.patched = patcher.patch_vault(self.vault)
        self.ns = {'_spartan_vault': transport, 'json': json, 'secrets': types.SimpleNamespace(token_hex=lambda n: 'nonce')}

    def test_transform_is_idempotent_and_handlers_reuse_navigation_session(self):
        self.assertEqual(patcher.patch_vault(self.patched), self.patched)
        browser = patcher.patch_camofox(self.browser)
        self.assertEqual(patcher.patch_camofox(browser), browser)
        callback = Mock(return_value='ok')
        self.ns.update({'_fenced_page_op': lambda tid, fn: fn(), 'browser_vault_fill': callback})
        functions(self.patched, ['_handle_vault_fill'], self.ns)
        self.ns['_handle_vault_fill']({'handle': 'h'}, session_id='durable-task', task_id='transient-task')
        callback.assert_called_once_with(handle='h', task_id='durable-task')

    def test_native_evaluators_never_reach_supervisor_or_cli_in_camofox(self):
        functions(self.patched, ['_eval_js', '_eval_js_secret', '_fenced_page_op'], self.ns)
        with patch.object(transport, 'evaluate', return_value={'success': True}) as evaluate:
            self.ns['_eval_js']('t', 'origin')
            self.ns['_eval_js_secret']('t', 'secret')
        self.assertEqual(evaluate.call_args_list[0].args, ('t', 'origin'))
        self.assertTrue(evaluate.call_args_list[1].kwargs['secret'])
        self.assertEqual(self.ns['_fenced_page_op']('t', lambda: 'native'), 'native')

    def test_native_otp_origin_deny_precedes_resolution(self):
        backend = Mock()
        backend.get_meta.return_value = types.SimpleNamespace(kind='login', allowed_origins=['https://saved.example'], origin='https://saved.example')
        mods = {
            'agent.redact': types.SimpleNamespace(register_vault_redaction_value=Mock()),
            'agent.vault_backends': types.SimpleNamespace(backend_for_handle=lambda h: backend),
            'agent.vault_backends.unlock': types.SimpleNamespace(can_prompt_here=lambda: True, get_code_prompt_callback=Mock()),
            'agent.vault_login_classifier': types.SimpleNamespace(LoginControl=types.SimpleNamespace(from_dict=lambda d: d), build_fill_js=Mock(), build_inspection_js=lambda n: 'inspection', build_otp_fills=Mock(), classify_otp_controls=lambda c: c),
        }
        self.ns.update({'_focus_bound_origin': lambda *a: None, '_current_page_origin': lambda t: 'https://other.example', '_eval_js': lambda *a: {'success': True, 'result': [{}]}, '_parse_json_result': lambda r: r})
        functions(self.patched, ['browser_vault_enter_code'], self.ns)
        with patch.dict(sys.modules, mods), patch.object(transport, '_request', return_value={'vaultEvaluateVersion': 1}):
            result = json.loads(self.ns['browser_vault_enter_code']('h', task_id='t'))
        self.assertEqual(result['error_type'], 'origin_mismatch')
        backend.resolve_otp.assert_not_called()

    def test_native_login_payment_address_keep_backends_and_fill_script(self):
        class UnlockRequired(Exception):
            pass
        for kind in ('login', 'payment', 'address'):
            with self.subTest(kind=kind):
                backend = Mock(name='backend')
                backend.name = 'native'
                backend.needs_unlock = False
                backend.get_meta.return_value = types.SimpleNamespace(kind=kind, origin='https://saved.example', allowed_origins=['https://saved.example'], label='saved', has_otp=False)
                backend.resolve_password.return_value = 'SECRET'
                backend.resolve_secret.return_value = {'card_number': 'SECRET'}
                fill = [{'token': 'field', 'value': 'SECRET'}]
                build = Mock(return_value='native-build-fill-js')
                redactions = Mock()
                classifier = types.SimpleNamespace(ClassifiedLoginControl=object, LoginControl=types.SimpleNamespace(from_dict=lambda d: d), build_fill_js=build, build_inspection_js=lambda n: 'native-inspect', classify_checkout_control=lambda c: c, classify_login_control=lambda c: c, select_checkout_fills=lambda *a: fill, select_password_fill=lambda *a: fill)
                mods = {'agent.redact': types.SimpleNamespace(register_vault_redaction_value=redactions), 'agent.vault_login_classifier': classifier, 'agent.vault_backends': types.SimpleNamespace(UnlockRequired=UnlockRequired, backend_for_handle=lambda h: backend), 'agent.vault_store': types.SimpleNamespace(ADDRESS_FIELDS=[], PAYMENT_FIELDS=[], scrub_secret_from_text=lambda s, secret: s)}
                self.ns.update({'_focus_bound_origin': lambda *a: 'https://saved.example', '_current_page_origin': lambda t: 'https://saved.example', '_eval_js': lambda *a: {'success': True, 'result': [{'token': 'field'}]}, '_parse_json_result': lambda r: r, '_confirm_payment_fill': Mock(return_value=True), '_eval_js_secret': lambda t, expression: transport.evaluate(t, expression, secret=True)})
                functions(self.patched, ['browser_vault_fill'], self.ns)
                with patch.dict(sys.modules, mods), patch.object(transport, '_request', side_effect=[{'vaultEvaluateVersion': 1}, {'ok': True, 'result': {'filled': 1}}]) as request:
                    result = json.loads(self.ns['browser_vault_fill']('h', task_id='t'))
                self.assertTrue(result['success'])
                self.assertEqual(request.call_args.args[2]['expression'], 'native-build-fill-js')
                build.assert_called_once_with(fill, expected_origin='https://saved.example', nonce='nonce')
                if kind == 'login':
                    backend.resolve_password.assert_called_once_with('h')
                else:
                    backend.resolve_secret.assert_called_once_with('h')
                if kind == 'payment':
                    self.ns['_confirm_payment_fill'].assert_called_once()
                if kind != 'address':
                    redactions.assert_called_with('SECRET')

    def test_short_snapshot_uses_native_vault_redactor_before_truncation(self):
        source = patcher.patch_camofox(self.browser)
        ns = {'_snapshot_data': lambda s: {'snapshot': 'password: SECRET', 'refsCount': 1}}
        modules = {'tools.browser_tool_snapshot': types.SimpleNamespace(_redact_browser_output=lambda d: {**d, 'snapshot': d['snapshot'].replace('SECRET', '[REDACTED]')}, _truncate_snapshot=lambda s, **kw: s), 'tools.browser_tool': types.SimpleNamespace(get_browser_snapshot_threshold=lambda: 1000)}
        functions(source, ['_fetch_snapshot'], ns)
        with patch.dict(sys.modules, modules):
            snapshot, _ = ns['_fetch_snapshot'](self.session)
        self.assertNotIn('SECRET', snapshot)


if __name__ == '__main__':
    unittest.main()
