from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import threading
import types
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch


PLUGIN_DIR = pathlib.Path(__file__).parent / "web" / "spartan-reader"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class FakeWebSearchProvider:
    pass


def install_fake_hermes_modules() -> None:
    agent = types.ModuleType("agent")
    provider = types.ModuleType("agent.web_search_provider")
    provider.WebSearchProvider = FakeWebSearchProvider
    sys.modules["agent"] = agent
    sys.modules["agent.web_search_provider"] = provider


def remove_fake_hermes_modules() -> None:
    sys.modules.pop("agent.web_search_provider", None)
    sys.modules.pop("agent", None)


def load_plugin():
    install_fake_hermes_modules()
    package_parent = types.ModuleType("hermes_plugins")
    package_parent.__path__ = []
    sys.modules["hermes_plugins"] = package_parent
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.web__spartan_reader",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self) -> None:
        self.providers: list[object] = []

    def register_web_search_provider(self, provider: object) -> None:
        self.providers.append(provider)


class ReaderHarness:
    def __init__(self, status: int = 200, body: str | None = None, trust_header: bool = True):
        self.status = status
        self.body = body
        self.trust_header = trust_header
        self.paths: list[str] = []

    def __enter__(self):
        harness = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                harness.paths.append(self.path)
                payload = (
                    harness.body
                    if harness.body is not None
                    else "BEGIN_UNTRUSTED_WEB_CONTENT\nVisible text\nEND_UNTRUSTED_WEB_CONTENT\n"
                )
                self.send_response(harness.status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                if harness.trust_header:
                    self.send_header("X-Spartan-Content-Trust", "untrusted-web-content")
                self.end_headers()
                self.wfile.write(payload.encode("utf-8"))

            def log_message(self, _format, *_args):
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class SpartanReaderPluginTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name in list(sys.modules):
            if name.startswith("hermes_plugins.web__spartan_reader"):
                sys.modules.pop(name, None)
        sys.modules.pop("hermes_plugins", None)
        remove_fake_hermes_modules()

    def test_registers_extract_only_web_provider(self) -> None:
        plugin = load_plugin()
        ctx = FakeContext()

        plugin.register(ctx)

        self.assertEqual(len(ctx.providers), 1)
        provider = ctx.providers[0]
        self.assertEqual(provider.name, "spartan-reader")
        self.assertEqual(provider.display_name, "Spartan Reader")
        self.assertFalse(provider.supports_search())
        self.assertTrue(provider.supports_extract())

    def test_runtime_availability_requires_internal_reader_url(self) -> None:
        plugin = load_plugin()
        provider = plugin.SpartanReaderWebExtractProvider()

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(provider.is_available())
        with patch.dict(os.environ, {"READER_PROXY_URL": "https://example.com"}, clear=True):
            self.assertFalse(provider.is_available())
        with patch.dict(os.environ, {"READER_PROXY_URL": "http://reader:3000"}, clear=True):
            self.assertTrue(provider.is_available())
        with patch.dict(os.environ, {"READER_PROXY_URL": "http://reader:3000/"}, clear=True):
            self.assertTrue(provider.is_available())

    def test_extract_calls_reader_fetch_and_returns_bare_result_list(self) -> None:
        plugin = load_plugin()

        with ReaderHarness() as reader:
            provider = plugin.SpartanReaderWebExtractProvider(
                reader_url=reader.url,
                allow_test_reader_url=True,
            )
            results = provider.extract(["https://public.example/path?q=1"], format="markdown")

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://public.example/path?q=1")
        self.assertEqual(results[0]["title"], "")
        self.assertIn("BEGIN_UNTRUSTED_WEB_CONTENT", results[0]["content"])
        self.assertIn("END_UNTRUSTED_WEB_CONTENT", results[0]["raw_content"])
        self.assertEqual(results[0]["metadata"]["source"], "spartan-reader")
        self.assertEqual(results[0]["metadata"]["content_trust"], "untrusted-web-content")
        self.assertEqual(urllib.parse.urlparse(reader.paths[0]).path, "/fetch")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(reader.paths[0]).query)
        self.assertEqual(query["url"], ["https://public.example/path?q=1"])

    def test_external_reader_url_fails_closed_without_network_call(self) -> None:
        plugin = load_plugin()
        provider = plugin.SpartanReaderWebExtractProvider(reader_url="https://example.com")

        with patch.object(urllib.request, "urlopen") as urlopen:
            results = provider.extract(["https://public.example/"])

        urlopen.assert_not_called()
        self.assertEqual(results[0]["url"], "https://public.example/")
        self.assertIn("Spartan Reader is not available", results[0]["error"])

    def test_reader_http_errors_are_generic(self) -> None:
        plugin = load_plugin()

        with ReaderHarness(status=403, body="internal detail: 127.0.0.1") as reader:
            provider = plugin.SpartanReaderWebExtractProvider(
                reader_url=reader.url,
                allow_test_reader_url=True,
            )
            results = provider.extract(["https://public.example/private"])

        self.assertIn("Reader blocked the URL", results[0]["error"])
        self.assertNotIn("127.0.0.1", results[0]["error"])
        self.assertNotIn("internal detail", results[0]["error"])

    def test_reader_response_must_include_untrusted_contract(self) -> None:
        plugin = load_plugin()

        with ReaderHarness(body="plain text without markers", trust_header=False) as reader:
            provider = plugin.SpartanReaderWebExtractProvider(
                reader_url=reader.url,
                allow_test_reader_url=True,
            )
            results = provider.extract(["https://public.example/page"])

        self.assertIn("invalid Reader trust contract", results[0]["error"])

    def test_reader_timeout_is_generic(self) -> None:
        plugin = load_plugin()
        provider = plugin.SpartanReaderWebExtractProvider(
            reader_url="http://127.0.0.1:1",
            allow_test_reader_url=True,
        )

        with patch.object(urllib.request, "urlopen", side_effect=TimeoutError()):
            results = provider.extract(["https://public.example/slow"])

        self.assertEqual(results[0]["error"], "Reader request timed out")

    def test_manifest_uses_nested_backend_key_shape(self) -> None:
        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")

        self.assertIn("name: web-spartan-reader", manifest)
        self.assertIn("kind: backend", manifest)
        self.assertIn("spartan-reader", manifest)

    def test_full_compose_does_not_mount_spartan_reader_by_default(self) -> None:
        compose = (REPO_ROOT / "infra" / "compose" / "compose.yml").read_text(encoding="utf-8")

        self.assertNotIn(
            "../hermes-plugins/web/spartan-reader:/opt/data/plugins/web/spartan-reader:ro",
            compose,
        )
        self.assertNotIn(":/opt/data/plugins:ro", compose)


if __name__ == "__main__":
    unittest.main()
