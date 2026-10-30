"""Small discriminating extraction corpus and actual converter lifecycle checks."""

import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import extraction
from test_server import ReaderHarness, reader_server as reader, request


class MarkdownTests(unittest.TestCase):
    def convert(self, html, url="https://docs.example/guide/start"):
        with reader._FetchDeadline() as deadline:
            return extraction.extract_markdown(html, url, deadline)

    def test_article_structure_links_table_and_exact_code(self):
        html = '''<html><body><nav>unrelated navigation</nav><article>
        <h1>Protocol guide</h1><p>A useful guide with <a href="../reference?q=x">reference material</a><a href="http://[bad">invalid link</a>
        and enough context to explain this supported protocol to readers.</p>
        <pre><code>def answer():\n\treturn 42  \n\n</code></pre>
        <table><tr><th>Key</th><th>Value</th></tr><tr><td>Mode</td><td>safe</td></tr></table>
        <p>Final guidance explains how to preserve source data and test the protocol carefully.</p>
        </article><footer>unrelated footer</footer></body></html>'''
        text, method, truncated = self.convert(html)
        self.assertEqual(method, "trafilatura-markdown")
        self.assertFalse(truncated)
        self.assertIn("# Protocol guide", text)
        self.assertIn("https://docs.example/reference?q=x", text)
        self.assertIn("def answer():\n\treturn 42  \n\n", text)
        self.assertIn("| Key | Value |", text)
        self.assertIn("| Mode | safe |", text)
        self.assertNotIn("unrelated navigation", text)
        self.assertNotIn("unrelated footer", text)

    def test_github_file_and_opaque_slash_ref_listing(self):
        raw = "function value() {\n\treturn 1;  \n}\n"
        payload = {"payload": {"codeViewBlob": {"rawLines": raw.split("\n")}}}
        html = '<html><body><script type="application/json">' + json.dumps(payload) + '</script></body></html>'
        text, method, _ = self.convert(html, "https://github.com/org/repo/blob/feature/x/src/code.js")
        self.assertEqual((text, method), (raw, "github-source"))
        listing = '<html><body><a href="/org/repo/blob/feature/x/src/code.js">code.js</a><a href="/org/repo/tree/feature/x/src">src</a></body></html>'
        text, method, _ = self.convert(listing, "https://github.com/org/repo/tree/feature/x")
        self.assertEqual(method, "github-listing")
        self.assertIn("https://github.com/org/repo/blob/feature/x/src/code.js", text)
        self.assertIn("https://github.com/org/repo/tree/feature/x/src", text)
        self.assertNotIn("raw.githubusercontent", text)

    def test_github_styled_blob_raw_lines_preserve_exact_source_not_page_numbers(self):
        # Exact advertised key observed by the owning Reader's checked fetch.
        raw = 'class LocalAdb {\n\tString label = "café 東京";  \n\tif (a < b && ready) {\n        return "déjà vu";\n\t}\n}\n'
        payload = {"payload": {"codeViewBlobLayoutRoute.StyledBlob": {"rawLines": raw.split("\n")}}}
        html = '<html><body><h1>LocalAdb.java</h1><p>751 lines (691 loc)</p><pre>123 | escaped page source</pre><script type="application/json">' + json.dumps(payload) + '</script></body></html>'
        text, method, truncated = self.convert(html, "https://github.com/techgenius1/tvOS-Launcher/blob/main/src/com/techgenius/tvoslauncher/LocalAdb.java")
        self.assertEqual(method, "github-source")
        self.assertEqual(text.encode("utf-8"), raw.encode("utf-8"))
        self.assertFalse(truncated)
        self.assertNotIn("751 lines", text)
        self.assertNotIn("123 |", text)

    def test_native_representations_preserve_whitespace_and_report_truncation(self):
        for media in ("text/markdown", "text/plain", "application/xml", "text/x-python", "application/json"):
            with self.subTest(media=media), reader._FetchDeadline() as deadline:
                raw = '\tindent  \n\n<node> exact </node>\n'
                self.assertEqual(reader.representation(raw, media, "https://example.com/source", deadline),
                                 (raw, "native-source", False))
                bounded = reader.representation(" " * (reader.MAX_TEXT_LENGTH + 1), media, "https://example.com", deadline)
                self.assertEqual(bounded, (" " * reader.MAX_TEXT_LENGTH, "native-source", True))

    def test_redirect_destination_owns_relative_link_resolution(self):
        html = '<html><body><article><h1>Redirected guide</h1><p>Read the <a href="next">next reference</a> for a detailed explanation of this static protocol and its supported behavior.</p></article></body></html>'
        hops = [("https://docs.example/new/location/", None), (None, (html, "text/html"))]
        with patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "_fetch_hop", side_effect=hops):
            with ReaderHarness() as harness:
                status, _, body = request(harness.base_url + "/fetch?url=https://docs.example/old")
        self.assertEqual(status, 200)
        self.assertIn("https://docs.example/new/location/next", body)
        self.assertNotIn("https://docs.example/next", body)

    def test_native_markdown_http_preserves_bytes_and_declares_truncation(self):
        raw = "# Source\n\tindent  \n\n"
        with patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "fetch_url", return_value=(raw, "text/markdown")):
            with ReaderHarness() as harness:
                status, headers, body = request(harness.base_url + "/fetch?url=https://example.com/doc.md")
        self.assertEqual((status, body), (200, reader.mark_untrusted(raw)))
        self.assertEqual(headers["X-Spartan-Extraction"], "native-source")
        self.assertEqual(headers["X-Spartan-Truncated"], "false")

    def test_xml_declared_html_preserves_decoded_unicode_and_raw_xml_bypass(self):
        for encoding in ("UTF-8", "ISO-8859-1"):
            document = f'''<?xml version="1.0" encoding="{encoding}"?>
            <!DOCTYPE html SYSTEM "http://127.0.0.1:1/external.dtd">
            <html xmlns="http://www.w3.org/1999/xhtml"><body><article>
            <h1>Guía café 東京</h1><p>Useful public documentation preserves déjà vu and 日本語
            after transport decoding, regardless of the original declared encoding.</p>
            <img src="http://127.0.0.1:1/image"/></article></body></html>'''
            # Fetch already decoded the original bytes. In particular, the
            # ISO declaration must not cause UTF-8 re-encoding/re-decoding.
            for media in ("application/xhtml+xml", "text/html"):
                with self.subTest(encoding=encoding, media=media), patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "fetch_url", return_value=(document, media)):
                    with ReaderHarness() as harness:
                        status, headers, body = request(harness.base_url + "/fetch?url=https://example.com/guide")
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["X-Spartan-Content-Trust"], "untrusted-web-content")
                    self.assertIn("# Guía café 東京", body)
                    self.assertIn("déjà vu and 日本語", body)
                    self.assertTrue(body.endswith("\nEND_UNTRUSTED_WEB_CONTENT\n"))
            with reader._FetchDeadline() as deadline:
                self.assertEqual(reader.representation(document, "application/xml", "https://example.com/source.xml", deadline),
                                 (document, "native-source", False))

    def test_script_only_shell_has_explicit_limitation(self):
        for shell in ('<div id="root"></div>', '<div id="root"><h1>Loading app</h1></div><noscript>Enable JavaScript</noscript>'):
            with self.subTest(shell=shell), self.assertRaises(extraction.ExtractionError) as raised:
                self.convert('<html><head><title>App</title></head><body>' + shell + '<script>loadApp()</script></body></html>')
            self.assertEqual(raised.exception.code, "needs_render")

    def test_hostile_dtd_and_resources_are_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "entity"
            secret.write_text("LOCAL_ENTITY_MUST_NOT_APPEAR")
            html = f'''<!DOCTYPE html SYSTEM "http://127.0.0.1:1/external.dtd" [<!ENTITY secret SYSTEM "{secret.as_uri()}">]>
            <html><body><article><h1>Offline facts</h1><p>&secret; Public content is sufficient to
            establish this extraction without loading external resources or executing scripts.</p>
            <img src="http://127.0.0.1:1/image"><iframe src="http://127.0.0.1:1/frame"></iframe>
            <script src="http://127.0.0.1:1/code"></script></article></body></html>'''
            text, _, _ = self.convert(html)
            self.assertIn("Offline facts", text)
            self.assertNotIn("LOCAL_ENTITY_MUST_NOT_APPEAR", text)

    def test_actual_child_timeout_reaps_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / "slow.py"
            worker.write_text("import time\ntime.sleep(5)\nprint('late success')\n")
            started = time.monotonic()
            with patch.object(extraction, "WORKER", worker), patch.object(extraction, "EXTRACTION_SECONDS", 0.05):
                for _ in range(3):
                    with self.assertRaises(TimeoutError):
                        self.convert("<h1>late</h1>")
            self.assertLess(time.monotonic() - started, 1)
            self.assertIn("Recovery", self.convert("<html><body><h1>Recovery</h1></body></html>")[0])

    def test_expired_conversion_cannot_publish_http_success(self):
        def late(*_args):
            time.sleep(0.04)
            return "late success", "injected", False
        with patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "fetch_url", return_value=("html", "text/html")), patch.object(reader, "extract_markdown", side_effect=late), patch.object(reader, "REQUEST_TIMEOUT", 0.02):
            with ReaderHarness() as harness:
                status, headers, body = request(harness.base_url + "/fetch?url=https://example.com")
        self.assertEqual(status, 504)
        self.assertEqual(headers["X-Spartan-Reader-Error"], "deadline")
        self.assertNotIn("late success", body)


if __name__ == "__main__":
    unittest.main()
