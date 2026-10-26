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
        self.wfile.write(json.dumps({'path': self.path, 'headers': dict(self.headers)}).encode())


class NativeEdgeTests(unittest.TestCase):
    def setUp(self):
        binary = os.environ.get('CADDY_BIN')
        if not binary:
            self.fail('CADDY_BIN is required for native security acceptance')
        self.verifier = edge.Server(('127.0.0.1', 0), {'camofox': 'a' * 64, 'apps': 'b' * 64}, 'c' * 64)
        self.addCleanup(self.verifier.server_close)
        backend = HTTPServer(('127.0.0.1', 0), Backend)
        self.addCleanup(backend.server_close)
        for server in (self.verifier, backend):
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(server.shutdown)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.listen = port()
        password_hash = subprocess.check_output([binary, 'hash-password', '--plaintext', 'fixture', '--algorithm', 'bcrypt'], text=True).strip()
        source = Path(__file__).with_name('Caddyfile').read_text()
        source = source[source.index(':26080 {'):source.index('import /etc/caddy/local.d')]
        source = source.replace(':26080 {', f'http://127.0.0.1:{self.listen} {{')
        source = source.replace('edge-sessions:9080', f'127.0.0.1:{self.verifier.server_port}')
        source = source.replace('camofox:6080', f'127.0.0.1:{backend.server_port}')
        source = source.replace('{$CADDY_SESSION_SERVICE_KEY}', 'c' * 64)
        source = source.replace('{$CADDY_AUTH_HASH}', password_hash)
        config = root / 'Caddyfile'
        config.write_text('{\n admin off\n auto_https off\n}\n' + source)
        log = (root / 'log').open('w+')
        self.addCleanup(log.close)
        env = {**os.environ, 'HOME': str(root),
               'XDG_CONFIG_HOME': str(root / 'config'),
               'XDG_DATA_HOME': str(root / 'data'),
               'XDG_CACHE_HOME': str(root / 'cache')}
        process = subprocess.Popen([binary, 'run', '--config', str(config), '--adapter', 'caddyfile'],
                                   stdout=log, stderr=log, env=env)
        self.addCleanup(process.wait, timeout=5)
        self.addCleanup(process.terminate)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', self.listen), .1):
                    return
            except OSError:
                time.sleep(.05)
        self.fail('native Caddy did not start')

    def request(self, headers, path='/'):
        connection = http.client.HTTPConnection('127.0.0.1', self.listen, timeout=3)
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def test_query_bootstrap_and_session_preserve_exact_backend_target(self):
        query_path = '/es/?evidence=horizontal&x=1&x=2&next=%2Fes%2F%3Fa%3D1'
        status, _, _ = self.request({}, query_path)
        self.assertEqual(status, 401)
        auth = 'Basic ' + base64.b64encode(b'admin:fixture').decode()
        status, headers, body = self.request({'Authorization': auth}, query_path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['path'], query_path)
        cookie = next(value.split(';')[0] for value in headers.get_all('Set-Cookie')
                      if value.startswith('spartan_edge_camofox='))
        status, _, body = self.request({'Cookie': cookie}, query_path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['path'], query_path)

    def test_basic_bootstrap_expiry_spoofing_and_upstream_cleanup(self):
        # Legacy secret-as-cookie and spoofed verifier headers must not authorize.
        status, _, _ = self.request({'Cookie': 'spartan_edge_camofox=' + 'a' * 64})
        self.assertEqual(status, 401)
        status, _, _ = self.request({'X-Edge-Valid': 'yes', 'X-Edge-Cookie': 'forged'})
        self.assertEqual(status, 401)
        auth = 'Basic ' + base64.b64encode(b'admin:fixture').decode()
        status, headers, body = self.request({'Authorization': auth})
        self.assertEqual(status, 200)
        cookies = headers.get_all('Set-Cookie')
        cookie = next(value.split(';')[0] for value in cookies if value.startswith('spartan_edge_camofox='))
        self.assertNotIn('a' * 64, cookie)
        status, _, body = self.request({'Cookie': cookie + '; app=preserved', 'X-Edge-Service-Key': 'spoof'})
        self.assertEqual(status, 200)
        forwarded = json.loads(body)['headers']
        self.assertEqual(forwarded.get('Cookie'), 'app=preserved')
        for name in ('Authorization', 'X-Edge-Valid', 'X-Edge-Service-Key', 'X-Edge-Cookie'):
            self.assertNotIn(name, forwarded)
        status, _, body = self.request({'Cookie': cookie, 'Connection': 'Upgrade', 'Upgrade': 'websocket'})
        self.assertEqual(status, 101)
        expired = 'spartan_edge_camofox=' + edge.mint('a' * 64, 'camofox', time.time() - 3600)
        status, headers, _ = self.request({'Cookie': expired, 'Connection': 'Upgrade', 'Upgrade': 'websocket'})
        self.assertEqual(status, 401)
        self.assertNotIn('WWW-Authenticate', headers)
        status, _, _ = self.request({'Cookie': expired})
        self.assertEqual(status, 401)
        self.verifier.shutdown()
        self.verifier.server_close()
        status, _, _ = self.request({'Cookie': cookie})
        self.assertEqual(status, 502)
