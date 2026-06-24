import asyncio
import contextlib
import io
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('proxy-bootstrap.py')


def load_module():
    spec = importlib.util.spec_from_file_location('proxy_bootstrap', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
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
            self.assertIn('SEND GET https://example.test/?token=[REDACTED]', audit_text)
            self.assertNotIn('secret', audit_text)

    def test_audit_log_can_be_sent_to_stderr(self):
        os.environ['SPARTAN_PROXY_AUDIT_LOG'] = 'stderr'
        module = load_module()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            module._audit_log('SEND', 'GET', 'https://example.test/')

        self.assertIn('[proxy-audit]', stderr.getvalue())
        self.assertIn('SEND GET https://example.test/', stderr.getvalue())

    def test_audit_log_falls_back_to_stderr_when_file_cannot_be_opened(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ['SPARTAN_PROXY_AUDIT_LOG'] = temp_dir
            module = load_module()

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                module._audit_log('SEND', 'GET', 'https://example.test/?token=secret')

            stderr_text = stderr.getvalue()
            self.assertIn('[proxy-audit]', stderr_text)
            self.assertIn('SEND GET https://example.test/?token=[REDACTED]', stderr_text)
            self.assertNotIn('secret', stderr_text)


if __name__ == '__main__':
    unittest.main()
