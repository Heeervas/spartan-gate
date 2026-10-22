import asyncio
import contextlib
import io
import json
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

import urllib.request


MODULE_PATH = pathlib.Path(__file__).with_name('proxy-bootstrap.py')


def load_module():
    spec = importlib.util.spec_from_file_location('proxy_bootstrap', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Restore process-wide hooks after every fixture import.
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(urllib.request, 'urlopen', urllib.request.urlopen))
        try:
            import requests
        except ImportError:
            pass
        else:
            stack.enter_context(mock.patch.object(requests.Session, 'send', requests.Session.send))
        import httpx
        stack.enter_context(mock.patch.object(httpx.Client, 'send', httpx.Client.send))
        stack.enter_context(mock.patch.object(httpx.AsyncClient, 'send', httpx.AsyncClient.send))
        spec.loader.exec_module(module)
    return module


def build_fake_httpx():
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            return None

        def send(self, request, **kwargs):
            return request

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def aclose(self):
            return None

        async def send(self, request, **kwargs):
            return request

    return types.SimpleNamespace(Client=FakeClient, AsyncClient=FakeAsyncClient)


class ProxyBootstrapTests(unittest.TestCase):
    def setUp(self):
        self._old_no_proxy = os.environ.get('NO_PROXY')
        self._old_no_proxy_lower = os.environ.get('no_proxy')
        self._old_proxy_audit_log = os.environ.get('SPARTAN_PROXY_AUDIT_LOG')
        self._old_httpx = sys.modules.get('httpx')
        sys.modules['httpx'] = build_fake_httpx()
        self.module = load_module()

    def tearDown(self):
        direct_client = getattr(self.module, '_DIRECT_HTTPX_CLIENT', None)
        if direct_client is not None:
            direct_client.close()
        async_client = getattr(self.module, '_DIRECT_HTTPX_ASYNC_CLIENT', None)
        if async_client is not None:
            asyncio.run(async_client.aclose())
        if self._old_no_proxy is None:
            os.environ.pop('NO_PROXY', None)
        else:
            os.environ['NO_PROXY'] = self._old_no_proxy
        if self._old_no_proxy_lower is None:
            os.environ.pop('no_proxy', None)
        else:
            os.environ['no_proxy'] = self._old_no_proxy_lower
        if self._old_proxy_audit_log is None:
            os.environ.pop('SPARTAN_PROXY_AUDIT_LOG', None)
        else:
            os.environ['SPARTAN_PROXY_AUDIT_LOG'] = self._old_proxy_audit_log
        if self._old_httpx is None:
            sys.modules.pop('httpx', None)
        else:
            sys.modules['httpx'] = self._old_httpx

    def test_bypasses_explicit_proxy_for_internal_host(self):
        os.environ['NO_PROXY'] = 'localhost,clawroute,searxng'
        self.assertTrue(
            self.module._should_bypass_explicit_proxy(
                'http://clawroute:18790/v1/chat/completions'
            )
        )

    def test_keeps_proxy_for_external_host(self):
        os.environ['NO_PROXY'] = 'localhost,clawroute,searxng'
        self.assertFalse(
            self.module._should_bypass_explicit_proxy(
                'https://api.openai.com/v1/chat/completions'
            )
        )

    def test_brave_search_detection_requires_exact_hostname(self):
        self.assertTrue(self.module._is_brave_search_url('https://api.search.brave.com/res/v1/web/search?q=test'))
        self.assertFalse(self.module._is_brave_search_url('https://api.search.brave.com.attacker.test/search?q=test'))
        self.assertFalse(self.module._is_brave_search_url('https://attacker.test/?next=api.search.brave.com'))

    def test_searxng_detection_requires_exact_configured_hostname(self):
        self.module.SEARXNG_URL = 'http://searxng:8080'
        self.assertTrue(self.module._is_searxng_search_url('http://searxng:8080/search?q=test'))
        self.assertFalse(self.module._is_searxng_search_url('http://evil-searxng.test/search?q=test'))
        self.assertFalse(self.module._is_searxng_search_url('http://attacker.test/path?next=searxng/search'))

    def test_supports_suffix_entries(self):
        os.environ['NO_PROXY'] = '.internal.example'
        self.assertTrue(
            self.module._should_bypass_explicit_proxy(
                'http://clawroute.internal.example/v1/chat/completions'
            )
        )

    def test_reuses_direct_sync_client(self):
        self.assertIs(
            self.module._get_direct_httpx_client(),
            self.module._get_direct_httpx_client(),
        )

    def test_reuses_direct_async_client(self):
        self.assertIs(
            self.module._get_direct_httpx_async_client(),
            self.module._get_direct_httpx_async_client(),
        )

    def test_audit_log_writes_to_configured_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = pathlib.Path(temp_dir).joinpath('proxy-audit.log')
            os.environ['SPARTAN_PROXY_AUDIT_LOG'] = str(audit_path)
            module = load_module()

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                module._audit_log('SEND', 'GET', 'https://example.test/?token=secret')

            self.assertEqual('', stderr.getvalue())
            audit_text = audit_path.read_text(encoding='utf-8')
            self.assertIn('[proxy-audit]', audit_text)
            self.assertIn('https://example.test', audit_text)
            self.assertNotIn('token=', audit_text)
            self.assertNotIn('secret', audit_text)

    def test_audit_log_defaults_to_stderr(self):
        os.environ.pop('SPARTAN_PROXY_AUDIT_LOG', None)
        module = load_module()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            module._audit_log('SEND', 'GET', 'https://example.test/')

        self.assertEqual('stderr', module.PROXY_AUDIT_LOG)
        self.assertIn('[proxy-audit]', stderr.getvalue())
        self.assertEqual('https://example.test', json.loads(stderr.getvalue().split(' ', 1)[1])['url'])

    def test_audit_log_can_be_sent_to_stderr(self):
        os.environ['SPARTAN_PROXY_AUDIT_LOG'] = 'stderr'
        module = load_module()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            module._audit_log('SEND', 'GET', 'https://example.test/')

        self.assertIn('[proxy-audit]', stderr.getvalue())
        self.assertEqual('https://example.test', json.loads(stderr.getvalue().split(' ', 1)[1])['url'])

    def test_audit_log_falls_back_to_stderr_when_file_cannot_be_opened(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ['SPARTAN_PROXY_AUDIT_LOG'] = temp_dir
            module = load_module()

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                module._audit_log('SEND', 'GET', 'https://example.test/?token=secret')

            stderr_text = stderr.getvalue()
            self.assertIn('[proxy-audit]', stderr_text)
            self.assertIn('https://example.test', stderr_text)
            self.assertNotIn('token=', stderr_text)
            self.assertNotIn('secret', stderr_text)

    def test_original_search_url_canary_never_reaches_audit(self):
        original = 'https://api.search.brave.com/search?q=ORIGINAL_QUERY_CANARY&token=ORIGINAL_TOKEN_CANARY'
        captured = []
        self.module._orig_urlopen = lambda url, *args, **kwargs: captured.append(url)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.module._patched_urlopen(original)
        self.assertEqual(1, len(captured))
        self.assertIn('q=ORIGINAL_QUERY_CANARY', captured[0])
        self.assertNotIn('CANARY', stderr.getvalue())

    def test_audit_minimizes_every_url_before_truncation(self):
        urls = [
            'https://USER_CANARY:PASSWORD_CANARY@example.test/path?unknown=QUERY_CANARY#FRAGMENT_CANARY',
            'https://example.test/bot' + 'BOT_CANARY_' * 40 + '/sendMessage',
            'https://example.test/' + 'x' * 190 + '?token=TRUNCATION_CANARY',
            'https://example.test/%74oken/PATH_CANARY?%61pi_key=ENCODED_CANARY',
            'https://[broken/INVALID_CANARY',
            'not-a-url/MALFORMED_CANARY',
        ]
        for url in urls:
            with self.subTest(url=url):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.module._audit_log('SEND', 'GET', url)
                self.assertNotIn('CANARY', stderr.getvalue())
                self.assertEqual(1, len(stderr.getvalue().splitlines()))

    def test_search_redirects_preserve_transport_and_remove_credentials(self):
        original = 'https://api.search.brave.com/search?q=SEARCH_CANARY&token=TOKEN_CANARY'
        fake_httpx = sys.modules['httpx']
        fake_httpx.Request = lambda method, url, headers: types.SimpleNamespace(method=method, url=url, headers=headers)
        calls = []

        def send(client, request, **kwargs):
            calls.append((client, request, kwargs))
            return request

        async def async_send(client, request, **kwargs):
            return send(client, request, **kwargs)

        self.module._orig_send = send
        self.module._orig_httpx_send = send
        self.module._orig_httpx_async_send = async_send
        client = object()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            for transport in ('requests', 'httpx', 'async-httpx'):
                request = types.SimpleNamespace(url=original, method='GET', headers={
                    'X-Subscription-Token': 'HEADER_CANARY', 'Accept': 'application/json'})
                if transport == 'requests':
                    result = self.module._patched_send(client, request, proxies={'https': 'http://proxy:8888'})
                elif transport == 'httpx':
                    result = self.module._patched_httpx_send(client, request, follow_redirects=False)
                else:
                    result = asyncio.run(self.module._patched_httpx_async_send(client, request, follow_redirects=False))
                self.assertIn('/search?q=SEARCH_CANARY&format=json', result.url)
                self.assertNotIn('X-Subscription-Token', result.headers)
                self.assertEqual('application/json', result.headers['Accept'])
        self.assertNotIn('CANARY', stderr.getvalue())
        self.assertTrue(all(call[0] is client for call in calls))
        self.assertEqual({'proxies': {'https': 'http://proxy:8888'}}, calls[0][2])
        self.assertEqual({'follow_redirects': False}, calls[1][2])
        self.assertEqual({'follow_redirects': False}, calls[2][2])

    def test_requests_response_is_not_rewritten(self):
        # Retired CDP rewriting must not alter arbitrary JSON responses.
        response = types.SimpleNamespace(_content=b'{"webSocketDebuggerUrl":"ws://127.0.0.1:3000/"}',
                                         headers={'Content-Length': '51'})
        self.module._orig_send = lambda *args, **kwargs: response
        request = types.SimpleNamespace(url='http://service:3000/json/version', method='GET')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIs(response, self.module._patched_send(object(), request))
        self.assertEqual(b'{"webSocketDebuggerUrl":"ws://127.0.0.1:3000/"}', response._content)
        self.assertEqual({'Content-Length': '51'}, response.headers)

    def test_import_restores_global_client_hooks(self):
        original = urllib.request.urlopen
        load_module()
        load_module()
        self.assertIs(original, urllib.request.urlopen)


if __name__ == '__main__':
    unittest.main()
