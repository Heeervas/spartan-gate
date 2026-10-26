import importlib.util
import io
import os
import pathlib
import signal
import socket
import subprocess
import sys
import threading
import time
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


class BoundedReaderHarness:
    def __init__(self, *, max_workers, max_pending):
        self.max_workers = max_workers
        self.max_pending = max_pending

    def __enter__(self):
        self.server = reader_server.BoundedHTTPServer(
            ("127.0.0.1", 0),
            reader_server.ReaderHandler,
            max_workers=self.max_workers,
            max_pending=self.max_pending,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


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
        reader_server.is_blocked = lambda _url, **_kwargs: False
        reader_server.fetch_url = lambda _url, **_kwargs: (
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

            def close(self):
                pass

            def getheader(self, name, default=None):
                if name == "Location":
                    return "http://100.64.0.1/private"
                return default

        class FakeSocket:
            def settimeout(self, _timeout):
                pass

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
        reader_server.is_blocked = lambda _url, **_kwargs: False
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 80)),
        ]

        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://public.example/page")

    def test_fetch_validates_url_even_when_called_directly(self):
        with self.assertRaises(reader_server.BlockedDestination):
            reader_server.fetch_url("http://127.0.0.1:3000/private")

    def test_audit_log_keeps_only_sanitized_origin_and_redacted_target(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            reader_server.audit_log(
                "FETCH",
                "https://alice:password@public.example/private/token?token=secret&email=a@example.com",
            )

        line = stderr.getvalue()
        self.assertIn("https://public.example/[REDACTED_PATH]?[REDACTED_QUERY]", line)
        self.assertNotIn("alice", line)
        self.assertNotIn("password", line)
        self.assertNotIn("private/token", line)
        self.assertNotIn("token=secret", line)
        self.assertNotIn("a@example.com", line)

    def test_url_userinfo_is_rejected_without_logging_credentials(self):
        reader_server.socket.getaddrinfo = lambda *_args, **_kwargs: [
            (reader_server.socket.AF_INET, reader_server.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        target = "https://alice:password@public.example/private"
        self.assertTrue(reader_server.is_blocked(target))
        reader_server.socket.getaddrinfo = self.old_getaddrinfo

        stderr = io.StringIO()
        with redirect_stderr(stderr), ReaderHarness() as harness:
            status, _headers, body = request(
                f"{harness.base_url}/fetch?url={urllib.parse.quote(target, safe='')}"
            )

        self.assertEqual(status, 403)
        self.assertIn("blocked", body.lower())
        self.assertNotIn("alice", stderr.getvalue())
        self.assertNotIn("password", stderr.getvalue())

    def test_access_log_does_not_reemit_fetched_path_or_query(self):
        reader_server.is_blocked = lambda _url, **_kwargs: False
        reader_server.fetch_url = lambda _url, **_kwargs: ("public text", "text/plain")
        target = "https://public.example/private/token?credential=secret"

        stderr = io.StringIO()
        with redirect_stderr(stderr), ReaderHarness() as harness:
            status, _headers, _body = request(
                f"{harness.base_url}/fetch?url={urllib.parse.quote(target, safe='')}"
            )

        line = stderr.getvalue()
        self.assertEqual(status, 200)
        self.assertIn("[reader-proxy] GET /fetch 200", line)
        self.assertNotIn("private/token", line)
        self.assertNotIn("credential", line)
        self.assertNotIn("secret", line)

    def test_slow_fetch_does_not_block_health(self):
        started = threading.Event()
        release = threading.Event()

        def slow_fetch(_url, **_kwargs):
            started.set()
            release.wait(timeout=5)
            return "done", "text/plain"

        reader_server.is_blocked = lambda _url, **_kwargs: False
        reader_server.fetch_url = slow_fetch
        fetch_result = []

        with BoundedReaderHarness(max_workers=2, max_pending=0) as harness:
            target = urllib.parse.quote("https://public.example/slow", safe="")
            fetch_thread = threading.Thread(
                target=lambda: fetch_result.append(request(f"{harness.base_url}/fetch?url={target}"))
            )
            fetch_thread.start()
            self.assertTrue(started.wait(timeout=2))

            before = time.monotonic()
            status, _headers, body = request(f"{harness.base_url}/health")
            elapsed = time.monotonic() - before

            release.set()
            fetch_thread.join(timeout=2)

        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")
        self.assertLess(elapsed, 1)
        self.assertEqual(fetch_result[0][0], 200)

    def test_overload_is_shed_immediately_with_503(self):
        started = threading.Event()
        release = threading.Event()

        def slow_fetch(_url, **_kwargs):
            started.set()
            release.wait(timeout=5)
            return "done", "text/plain"

        reader_server.is_blocked = lambda _url, **_kwargs: False
        reader_server.fetch_url = slow_fetch

        with BoundedReaderHarness(max_workers=1, max_pending=0) as harness:
            target = urllib.parse.quote("https://public.example/slow", safe="")
            fetch_thread = threading.Thread(
                target=lambda: request(f"{harness.base_url}/fetch?url={target}")
            )
            fetch_thread.start()
            self.assertTrue(started.wait(timeout=2))

            before = time.monotonic()
            status, headers, body = request(f"{harness.base_url}/health")
            elapsed = time.monotonic() - before

            release.set()
            fetch_thread.join(timeout=2)

        self.assertEqual(status, 503)
        self.assertEqual(headers.get("Retry-After"), "1")
        self.assertIn("overloaded", body.lower())
        self.assertLess(elapsed, 1)

    def test_signal_shutdown_does_not_deadlock_serving_thread(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        env = os.environ.copy()
        env.update(
            {
                "READER_PORT": str(port),
                "READER_WORKERS": "2",
                "READER_MAX_PENDING": "1",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-u", str(MODULE_PATH)],
            cwd=MODULE_PATH.parent,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            else:
                self.fail("Reader did not become ready")

            process.send_signal(signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("Reader deadlocked while handling SIGTERM")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

        self.assertEqual(process.returncode, 0, f"{stdout}\n{stderr}")

    def test_runtime_integer_configuration_fails_closed(self):
        cases = [
            ("READER_PORT", "70000"),
            ("READER_TIMEOUT", "0"),
            ("READER_WORKERS", "0"),
            ("READER_WORKERS", "many"),
            ("READER_MAX_PENDING", "-1"),
        ]
        loader = f"import runpy; runpy.run_path({str(MODULE_PATH)!r}, run_name='reader_config_test')"
        for name, value in cases:
            with self.subTest(name=name, value=value):
                env = os.environ.copy()
                env[name] = value
                result = subprocess.run(
                    [sys.executable, "-c", loader],
                    cwd=MODULE_PATH.parent,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=3,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_reader_image_and_compose_drop_runtime_privilege(self):
        dockerfile = MODULE_PATH.with_name("Dockerfile").read_text()
        compose = (MODULE_PATH.parents[1] / "compose" / "compose.yml").read_text()
        reader_block = compose.split("\n  reader:\n", 1)[1].split("\n  dns:\n", 1)[0]

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn('user: "10001:10001"', reader_block)
        self.assertIn("cap_drop:", reader_block)
        self.assertIn("- ALL", reader_block)
        self.assertIn("no-new-privileges:true", reader_block)
        self.assertIn("pids_limit:", reader_block)

    def test_non_get_methods_are_rejected(self):
        with ReaderHarness() as harness:
            status, _headers, body = request(f"{harness.base_url}/fetch?url=https%3A%2F%2Fexample.com", "POST")

        self.assertEqual(status, 405)
        self.assertIn("Only GET", body)


if __name__ == "__main__":
    unittest.main()
