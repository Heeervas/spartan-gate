"""Dedicated native tool-edge transport; authentication belongs to tool-access."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer


class Backend(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        Backend.calls.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"path": self.path, "headers": dict(self.headers)}).encode())


def vacant_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ToolEdgeTests(unittest.TestCase):
    def setUp(self):
        binary = os.environ.get("CADDY_BIN")
        self.assertTrue(binary, "CADDY_BIN is required for native tool-edge acceptance")
        Backend.calls = []
        backend = HTTPServer(("127.0.0.1", 0), Backend)
        self.backend = backend
        thread = threading.Thread(target=backend.serve_forever)
        thread.start()
        self.addCleanup(backend.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(backend.shutdown)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.listen = vacant_port()
        source = Path(__file__).with_name("Caddyfile.tools").read_text()
        source = source.replace(":18792 {", f"http://:{self.listen} {{")
        source = source.replace("tool-access:9081", f"127.0.0.1:{backend.server_port}")
        config = Path(scratch.name) / "Caddyfile"
        config.write_text(source)
        log = (Path(scratch.name) / "log").open("w+")
        self.addCleanup(log.close)
        self.log = log
        env = {**os.environ, "HOME": scratch.name,
               "XDG_CONFIG_HOME": str(Path(scratch.name) / "config"),
               "XDG_DATA_HOME": str(Path(scratch.name) / "data"),
               "XDG_CACHE_HOME": str(Path(scratch.name) / "cache")}
        process = subprocess.Popen([binary, "run", "--config", str(config), "--adapter", "caddyfile"],
                                   stdout=log, stderr=log, env=env)
        self.addCleanup(process.wait, timeout=5)
        self.addCleanup(process.terminate)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.listen), .1):
                    return
            except OSError:
                time.sleep(.05)
        self.fail("native tool-edge failed to start")

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.listen, timeout=3)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def test_query_bearer_and_host_origin_boundary(self):
        path = "/search?q=one%20two&q=three&format=json"
        status, headers, body = self.request(path, {
            "Authorization": "Bearer synthetic-token", "Cookie": "untrusted=secret",
            "Forwarded": "host=forged", "X-Forwarded-Host": "forged",
        })
        self.assertEqual(status, 200, body.decode(errors="replace"))
        forwarded = json.loads(body)
        self.assertEqual(forwarded["path"], path)
        self.assertEqual(forwarded["headers"].get("Authorization"), "Bearer synthetic-token")
        self.assertNotIn("Cookie", forwarded["headers"])
        self.assertNotIn("Forwarded", forwarded["headers"])
        self.assertNotIn("forged", json.dumps(forwarded))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request(path, {"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(self.request(path, {"Host": "untrusted.example"})[0], 403)
        self.assertEqual(len(Backend.calls), 1)
        self.backend.shutdown()
        self.backend.server_close()
        sentinel = "private-query-sentinel"
        self.assertEqual(self.request("/search?q=" + sentinel + "&format=json")[0], 502)
        self.log.seek(0)
        self.assertNotIn(sentinel, self.log.read())


if __name__ == "__main__":
    unittest.main()
