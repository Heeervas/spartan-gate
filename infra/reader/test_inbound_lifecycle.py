"""Real loopback regressions for bounded incoming Reader connections."""
import importlib.util
from pathlib import Path
import socket
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("reader_lifecycle", Path(__file__).with_name("server.py"))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class InboundLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.closed = False
        self.timeout_patch = patch.object(reader, "REQUEST_TIMEOUT", 1)
        self.timeout_patch.start()
        self.addCleanup(self.timeout_patch.stop)
        self.server = reader.BoundedHTTPServer(
            ("127.0.0.1", 0), reader.ReaderHandler, max_workers=1, max_pending=1,
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        for client in self.clients:
            client.close()
        if self.closed:
            return
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def connect(self):
        client = socket.create_connection(self.server.server_address, timeout=1)
        self.clients.append(client)
        return client

    def assert_health_recovers(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with socket.create_connection(self.server.server_address, timeout=1) as client:
                client.sendall(b"GET /health HTTP/1.0\r\n\r\n")
                client.settimeout(1.5)
                try:
                    response = client.recv(4096)
                except (TimeoutError, ConnectionResetError):
                    response = b""
                if b"200 OK" in response:
                    return
            time.sleep(0.02)
        self.fail("Reader worker did not recover while incomplete clients remained open")

    def test_incomplete_request_and_queued_connection_expire(self):
        active = self.connect()
        active.sendall(b"GET /health HTTP/1.1\r\nHost: fixture\r\n")
        queued = self.connect()
        queued.sendall(b"GET /health HTTP/1.1\r\n")
        self.assert_health_recovers()

    def test_expired_incomplete_fetch_does_not_dispatch(self):
        dispatched = threading.Event()
        client = self.connect()
        with patch.object(reader, "is_blocked", lambda _url: False), patch.object(
            reader, "fetch_url", lambda _url: (dispatched.set() or "unexpected", "text/plain"),
        ):
            client.sendall(b"GET /fetch?url=https%3A%2F%2Fpublic.example HTTP/1.1\r\nHost: fixture\r\n")
            self.assert_health_recovers()
            self.assertFalse(dispatched.is_set(), "EOF after expiry must not authorize a partial request")

    def test_header_drips_do_not_extend_absolute_deadline(self):
        client = self.connect()
        client.sendall(b"GET /health HTTP/1.1\r\nX-Slow: ")
        stopped = threading.Event()

        def drip():
            while not stopped.wait(0.1):
                try:
                    client.sendall(b"x")
                except OSError:
                    return

        sender = threading.Thread(target=drip)
        sender.start()
        try:
            self.assert_health_recovers()
        finally:
            stopped.set()
            sender.join(timeout=1)

    def test_slow_response_consumer_releases_worker(self):
        client = self.connect()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        # Force backpressure deterministically without changing response semantics.
        with patch.object(reader.ReaderHandler, "do_GET", lambda handler: handler._respond(200, "x" * 8_000_000)):
            client.sendall(b"GET /health HTTP/1.0\r\n\r\n")
            time.sleep(1.3)
        self.assert_health_recovers()

    def test_sigterm_exits_with_incomplete_clients(self):
        # Use an independent process to exercise the real main/signal lifecycle.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).with_name("server.py"))],
            env={**os.environ, "READER_PORT": str(port), "READER_TIMEOUT": "30",
                 "READER_WORKERS": "1", "READER_MAX_PENDING": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        clients = []
        try:
            deadline = time.monotonic() + 3
            while True:
                try:
                    clients.append(socket.create_connection(("127.0.0.1", port), timeout=0.2))
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("Reader subprocess did not become ready")
                    time.sleep(0.02)
            clients.append(socket.create_connection(("127.0.0.1", port), timeout=1))
            for client in clients:
                client.sendall(b"GET /health HTTP/1.1\r\n")
            time.sleep(0.1)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            for client in clients:
                client.close()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_server_close_interrupts_active_and_pending_header_reads(self):
        for _ in range(2):
            self.connect().sendall(b"GET /health HTTP/1.1\r\n")
        time.sleep(0.1)
        self.server.shutdown()
        self.thread.join(timeout=2)
        closer = threading.Thread(target=self.server.server_close, daemon=True)
        before = time.monotonic()
        closer.start()
        closer.join(timeout=0.5)
        closed_promptly = not closer.is_alive()
        # Ensure a defective implementation cannot strand non-daemon pool workers.
        if not closed_promptly:
            for connection in self.clients:
                connection.close()
            closer.join(timeout=2)
        self.closed = not closer.is_alive()
        self.assertTrue(closed_promptly, "server_close waited for incomplete headers")
        self.assertLess(time.monotonic() - before, 0.8)
        self.assertTrue(all(not worker.is_alive() for worker in self.server._workers))


if __name__ == "__main__":
    unittest.main()
