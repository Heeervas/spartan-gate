"""Outbound deadline regressions using synthetic DNS and real loopback sockets."""
import importlib.util
from pathlib import Path
import socket
import socketserver
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("reader_deadline", Path(__file__).with_name("server.py"))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
PUBLIC_IP = "93.184.216.34"


class Upstream(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, mode):
        self.mode = mode
        self.started = threading.Event()
        self.disconnected = threading.Event()
        self.paths = []
        super().__init__(("127.0.0.1", 0), UpstreamHandler)
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class UpstreamHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        data = b""
        while b"\r\n\r\n" not in data:
            part = self.request.recv(4096)
            if not part:
                return
            data += part
        path = data.split(b" ")[1].decode()
        self.server.paths.append(path)
        self.server.started.set()
        try:
            if path == "/ok":
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nok")
            elif self.server.mode == "redirect":
                time.sleep(0.3)
                location = {"/a": "/b", "/b": "/c"}.get(path, "/ok")
                self.request.sendall(f"HTTP/1.1 302 Found\r\nLocation: {location}\r\nContent-Length: 0\r\n\r\n".encode())
            else:
                prefix = (b"HTTP/1.1 200 OK\r\nX-Drip: " if self.server.mode == "header"
                          else b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                self.request.sendall(prefix)
                # Stop only on peer disconnect, not because the test releases us.
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    self.request.sendall(b"x")
                    time.sleep(0.04)
        except OSError:
            self.server.disconnected.set()


class FetchDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.timeout = patch.object(reader, "REQUEST_TIMEOUT", 0.5)
        self.timeout.start()
        self.addCleanup(self.timeout.stop)
        original_resolve = socket.getaddrinfo
        original_socket = socket.socket

        def resolve(host, port, *args, **kwargs):
            if host == "fixture.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
            return original_resolve(host, port, *args, **kwargs)

        class FixtureSocket(original_socket):
            def connect(self, address):
                if address[0] == PUBLIC_IP:
                    address = ("127.0.0.1", address[1])
                return super().connect(address)

        for patcher in (patch.object(socket, "getaddrinfo", resolve), patch.object(socket, "socket", FixtureSocket)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def upstream(self, mode):
        server = Upstream(mode)
        self.addCleanup(server.close)
        return server, f"http://fixture.example:{server.server_address[1]}"

    def health_during_dns_saturation(self, url):
        server = reader.BoundedHTTPServer(("127.0.0.1", 0), reader.ReaderHandler, max_workers=1, max_pending=0)
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        serving.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(base + "/fetch?url=" + urllib.parse.quote(url, safe=""), timeout=1)
            with raised.exception as response:
                self.assertEqual(response.code, 504)
                self.assertEqual(response.read(), b"Request timed out")
            with urllib.request.urlopen(base + "/health", timeout=1) as response:
                self.assertEqual(response.read(), b"ok")
        finally:
            server.shutdown()
            serving.join(timeout=1)
            server.server_close()

    def test_header_and_body_drips_expire_and_release_upstream(self):
        for mode in ("header", "body"):
            with self.subTest(mode=mode):
                upstream, url = self.upstream(mode)
                before = time.monotonic()
                with self.assertRaises(TimeoutError):
                    reader.fetch_url(url + "/drip")
                self.assertLess(time.monotonic() - before, 1.2)
                self.assertTrue(upstream.disconnected.wait(0.5), "outbound socket remained live")

    def test_redirects_share_one_budget_and_do_not_start_late_hop(self):
        upstream, url = self.upstream("redirect")
        before = time.monotonic()
        with self.assertRaises(TimeoutError):
            reader.fetch_url(url + "/a")
        self.assertLess(time.monotonic() - before, 0.9)
        time.sleep(0.2)  # Late response must not dispatch /c.
        self.assertEqual(upstream.paths, ["/a", "/b"])

    def test_dns_slots_outlive_waiters_and_recover_only_after_dns_finishes(self):
        upstream, url = self.upstream("body")
        resolver = reader._get_fetch_runtime().resolver
        original = socket.getaddrinfo
        releases = [threading.Event() for _ in range(4)]
        calls = []
        outcomes = []
        all_started = threading.Event()

        def blocked_dns(host, port, *args, **kwargs):
            if host.startswith("slot"):
                index = int(host[4])
                calls.append(index)
                if len(calls) == 4:
                    all_started.set()
                releases[index].wait(3)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
            return original(host, port, *args, **kwargs)

        def fetch(index):
            try:
                reader.fetch_url(url.replace("fixture", f"slot{index}") + "/ok")
                outcomes.append("unexpected success")
            except TimeoutError:
                outcomes.append("timeout")

        with patch.object(socket, "getaddrinfo", blocked_dns):
            clients = [threading.Thread(target=fetch, args=(index,)) for index in range(4)]
            try:
                for client in clients:
                    client.start()
                self.assertTrue(all_started.wait(1))
                for client in clients:
                    client.join(timeout=1)
                self.assertEqual(outcomes, ["timeout"] * 4)
                before = time.monotonic()
                with self.assertRaises(TimeoutError):
                    reader.fetch_url(url + "/ok")
                self.assertLess(time.monotonic() - before, 0.2)
                self.assertEqual(len(calls), 4)
                self.assertEqual(upstream.paths, [], "late DNS result connected")
                self.assertEqual(resolver._jobs.unfinished_tasks, 4)
                self.health_during_dns_saturation(url + "/ok")
                releases[0].set()
                until = time.monotonic() + 1
                while resolver._jobs.unfinished_tasks == 4 and time.monotonic() < until:
                    time.sleep(0.01)
                self.assertEqual(reader.fetch_url(url + "/ok"), ("ok", "text/plain"))
                self.assertEqual(upstream.paths, ["/ok"], "expired DNS waiter started a late connection")
            finally:
                for release in releases:
                    release.set()
                for client in clients:
                    client.join(timeout=1)
                resolver._jobs.join()

    def test_expired_queued_dns_is_discarded_and_reservation_released_once(self):
        resolver = reader._BoundedResolver()
        gate = threading.Event()
        original_run = resolver._run
        resolver._run = lambda: (gate.wait(2), original_run())
        errors = []

        def resolve():
            try:
                with reader._FetchDeadline(time.monotonic() + 0.1) as deadline:
                    resolver.resolve("fixture.example", 80, deadline)
            except TimeoutError:
                errors.append("timeout")

        with patch.object(socket, "getaddrinfo") as dns:
            waiter = threading.Thread(target=resolve)
            waiter.start()
            try:
                waiter.join(timeout=0.5)
                self.assertFalse(waiter.is_alive())
                self.assertEqual(errors, ["timeout"])
                gate.set()
                resolver._jobs.join()
                dns.assert_not_called()
                for _ in range(4):
                    self.assertTrue(resolver._slots.acquire(blocking=False))
                self.assertFalse(resolver._slots.acquire(blocking=False))
                for _ in range(4):
                    resolver._slots.release()
            finally:
                gate.set()
                waiter.join(timeout=1)

    def test_real_drip_releases_only_worker_without_fixture_release(self):
        upstream, url = self.upstream("body")
        server = reader.BoundedHTTPServer(("127.0.0.1", 0), reader.ReaderHandler, max_workers=1, max_pending=0)
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        serving.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=2) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read()
            except (OSError, urllib.error.URLError):
                return 0, b""

        client = threading.Thread(target=get, args=("/fetch?url=" + urllib.parse.quote(url + "/drip", safe=""),))
        try:
            client.start()
            self.assertTrue(upstream.started.wait(1))
            self.assertEqual(get("/health")[0], 503)
            self.assertTrue(upstream.disconnected.wait(1.2))
            client.join(timeout=1)
            self.assertFalse(client.is_alive())
            self.assertEqual(get("/health"), (200, b"ok"))
            status, body = get("/fetch?url=" + urllib.parse.quote(url + "/ok", safe=""))
            self.assertEqual(status, 200)
            self.assertEqual(body, b"BEGIN_UNTRUSTED_WEB_CONTENT\nok\nEND_UNTRUSTED_WEB_CONTENT\n")
        finally:
            server.shutdown()
            serving.join(timeout=2)
            server.server_close()
            client.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
