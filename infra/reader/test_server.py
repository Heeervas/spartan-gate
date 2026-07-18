import importlib.util
import io
import pathlib
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from contextlib import redirect_stderr


MODULE_PATH = pathlib.Path(__file__).with_name("server.py")


def load_module():
    spec = importlib.util.spec_from_file_location("reader_server", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


reader_server = load_module()


class ReaderHarness:
    def __enter__(self):
        self.server = HTTPServer(("127.0.0.1", 0), reader_server.ReaderHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def request(path: str, method: str = "GET"):
    req = urllib.request.Request(path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()


class ReaderServerTests(unittest.TestCase):
    def setUp(self):
        self.old_fetch_url = reader_server.fetch_url
        self.old_is_blocked = reader_server.is_blocked
        self.old_getaddrinfo = reader_server.socket.getaddrinfo
        self.old_open_checked_socket = reader_server._open_checked_socket
        self.old_http_response = reader_server.http.client.HTTPResponse

    def tearDown(self):
        reader_server.fetch_url = self.old_fetch_url
        reader_server.is_blocked = self.old_is_blocked
        reader_server.socket.getaddrinfo = self.old_getaddrinfo
        reader_server._open_checked_socket = self.old_open_checked_socket
        reader_server.http.client.HTTPResponse = self.old_http_response

    def test_fetch_wraps_output_as_untrusted_content(self):
        reader_server.is_blocked = lambda _url: False
        reader_server.fetch_url = lambda _url: (
            "<html><head><title>x</title></head><body><h1>Visible</h1><script>ignore</script></body></html>",
            "text/html",
        )

        target = urllib.parse.quote("https://public.example/page", safe="")
        with ReaderHarness() as harness:
            status, headers, body = request(f"{harness.base_url}/fetch?url={target}")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Spartan-Content-Trust"), "untrusted-web-content")
        self.assertTrue(body.startswith("BEGIN_UNTRUSTED_WEB_CONTENT\n"))
        self.assertTrue(body.endswith("\nEND_UNTRUSTED_WEB_CONTENT\n"))
        self.assertIn("Visible", body)
        self.assertNotIn("ignore", body)

    def test_blocked_destinations(self):
        blocked = [
            "file:///etc/passwd",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://clawroute:18790/health",
            "http://100.64.0.1/private",
            "http://metadata.google.internal/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertTrue(reader_server.is_blocked(url))

    def test_representative_non_global_address_classes_are_blocked(self):
        blocked_ips = [
            "100.64.0.0",          # first shared carrier-grade NAT address
            "100.127.255.255",     # last shared carrier-grade NAT address
            "0.0.0.0",             # unspecified
            "192.0.2.1",          # documentation range
            "198.18.0.1",         # benchmark range
            "169.254.1.1",        # IPv4 link-local
            "224.0.0.1",          # IPv4 multicast
            "fc00::1",            # IPv6 unique-local
            "ff02::1",            # IPv6 multicast
            "fe80::1",            # IPv6 link-local
            "2001:db8::1",        # IPv6 documentation range
        ]

        for value in blocked_ips:
            with self.subTest(value=value):
                self.assertTrue(reader_server._is_blocked_ip(value))

    def test_globally_routable_addresses_remain_allowed(self):
        for value in ("93.184.216.34", "100.128.0.1", "2606:4700:4700::1111"):
            with self.subTest(value=value):
                self.assertFalse(reader_server._is_blocked_ip(value))

    def test_arbitrary_unlisted_public_domain_remains_allowed(self):
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]

        self.assertFalse(reader_server.is_blocked("https://arbitrary-unlisted.example/page"))

    def test_mixed_global_and_non_global_dns_answers_fail_closed(self):
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 80)),
        ]

        self.assertTrue(reader_server.is_blocked("http://mixed.example/page"))

    def test_dns_resolution_failure_is_blocked(self):
        def raise_gaierror(*_args, **_kwargs):
            raise reader_server.socket.gaierror("not found")

        reader_server.socket.getaddrinfo = raise_gaierror
        self.assertTrue(reader_server.is_blocked("http://missing.example/page"))

    def test_blocked_redirect_raises_explicit_exception(self):
        class RedirectResponse:
            status = 302

            def __init__(self, _sock):
                pass

            def begin(self):
                pass

            def getheader(self, name, default=None):
                if name == "Location":
                    return "http://100.64.0.1/private"
                return default

        class FakeSocket:
            def sendall(self, _data):
                pass

            def close(self):
                pass

        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        ]
        reader_server._open_checked_socket = lambda *_args, **_kwargs: FakeSocket()
        reader_server.http.client.HTTPResponse = RedirectResponse

        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://public.example/page")

    def test_fetch_blocks_private_resolution_used_for_actual_connection(self):
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]

        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://public.example/page")

    def test_actual_connection_rejects_cgnat_after_preflight(self):
        reader_server.is_blocked = lambda _url: False
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 80)),
        ]

        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://public.example/page")

    def test_fetch_validates_url_even_when_called_directly(self):
        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://127.0.0.1:3000/private")

    def test_audit_log_redacts_query_string(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            reader_server.audit_log("FETCH", "https://public.example/path?token=secret&email=a@example.com")

        line = stderr.getvalue()
        self.assertIn("https://public.example/path?[REDACTED_QUERY]", line)
        self.assertNotIn("token=secret", line)
        self.assertNotIn("a@example.com", line)

    def test_non_get_methods_are_rejected(self):
        with ReaderHarness() as harness:
            status, _headers, body = request(f"{harness.base_url}/fetch?url=https%3A%2F%2Fexample.com", "POST")

        self.assertEqual(status, 405)
        self.assertIn("Only GET", body)


if __name__ == "__main__":
    unittest.main()
