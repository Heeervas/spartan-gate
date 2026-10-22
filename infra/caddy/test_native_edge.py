"""Native loopback Caddy/HTTP contract; CADDY_BIN must be provisioned locally."""
import base64
import importlib.util
import http.client
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location('sessions', Path(__file__).with_name('edge_sessions.py'))
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            self.send_response(101)
            self.send_header('Connection', 'Upgrade')
            self.send_header('Upgrade', 'websocket')
            self.end_headers()
            self.wfile.write(b'fixture-ws')
            self.wfile.flush()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(dict(self.headers)).encode())


class NativeEdgeTests(unittest.TestCase):
    def test_basic_bootstrap_expiry_spoofing_and_upstream_cleanup(self):
        binary = os.environ.get('CADDY_BIN')
        if not binary:
            self.fail('CADDY_BIN is required for native security acceptance')
        verifier = edge.Server(('127.0.0.1', 0), {'camofox': 'a' * 64, 'apps': 'b' * 64}, 'c' * 64)
        backend = HTTPServer(('127.0.0.1', 0), Backend)
        threads = [threading.Thread(target=server.serve_forever) for server in (verifier, backend)]
        for thread in threads:
            thread.start()
        process = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                listen = port()
                password_hash = subprocess.check_output([binary, 'hash-password', '--plaintext', 'fixture', '--algorithm', 'bcrypt'], text=True).strip()
                source = Path(__file__).with_name('Caddyfile').read_text()
                source = source[source.index(':26080 {'):source.index('import /etc/caddy/local.d')]
                source = source.replace(':26080 {', f'http://127.0.0.1:{listen} {{')
                source = source.replace('edge-sessions:9080', f'127.0.0.1:{verifier.server_port}')
                source = source.replace('camofox:6080', f'127.0.0.1:{backend.server_port}')
                source = source.replace('{$CADDY_SESSION_SERVICE_KEY}', 'c' * 64)
                source = source.replace('{$CADDY_AUTH_HASH}', password_hash)
                config = root / 'Caddyfile'
                config.write_text('{\n admin off\n auto_https off\n}\n' + source)
                log = (root / 'log').open('w+')
                process = subprocess.Popen([binary, 'run', '--config', str(config), '--adapter', 'caddyfile'], stdout=log, stderr=log)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(('127.0.0.1', listen), .1):
                            break
                    except OSError:
                        time.sleep(.05)
                base = f'http://127.0.0.1:{listen}'

                def request(headers):
                    connection = http.client.HTTPConnection('127.0.0.1', listen, timeout=3)
                    try:
                        connection.request('GET', '/', headers=headers)
                        response = connection.getresponse()
                        return response.status, response.headers, response.read()
                    finally:
                        connection.close()

                # Legacy secret-as-cookie and spoofed verifier headers must not authorize.
                status, _, _ = request({'Cookie': 'spartan_edge_camofox=' + 'a' * 64})
                self.assertEqual(status, 401)
                status, _, _ = request({'X-Edge-Valid': 'yes', 'X-Edge-Cookie': 'forged'})
                self.assertEqual(status, 401)
                auth = 'Basic ' + base64.b64encode(b'admin:fixture').decode()
                status, headers, body = request({'Authorization': auth})
                self.assertEqual(status, 200)
                cookies = headers.get_all('Set-Cookie')
                cookie = next(value.split(';')[0] for value in cookies if value.startswith('spartan_edge_camofox='))
                self.assertNotIn('a' * 64, cookie)
                status, _, body = request({'Cookie': cookie + '; app=preserved', 'X-Edge-Service-Key': 'spoof'})
                self.assertEqual(status, 200)
                forwarded = json.loads(body)
                self.assertEqual(forwarded.get('Cookie'), 'app=preserved')
                for name in ('Authorization', 'X-Edge-Valid', 'X-Edge-Service-Key', 'X-Edge-Cookie'):
                    self.assertNotIn(name, forwarded)
                status, _, body = request({'Cookie': cookie, 'Connection': 'Upgrade', 'Upgrade': 'websocket'})
                self.assertEqual(status, 101)
                expired = 'spartan_edge_camofox=' + edge.mint('a' * 64, 'camofox', time.time() - 3600)
                status, headers, _ = request({'Cookie': expired, 'Connection': 'Upgrade', 'Upgrade': 'websocket'})
                self.assertEqual(status, 401)
                self.assertNotIn('WWW-Authenticate', headers)
                status, _, _ = request({'Cookie': expired})
                self.assertEqual(status, 401)
                verifier.shutdown()
                verifier.server_close()
                status, _, _ = request({'Cookie': cookie})
                self.assertEqual(status, 502)
        finally:
            if process:
                process.terminate()
                process.wait(timeout=5)
                log.close()
            for server in (verifier, backend):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()
