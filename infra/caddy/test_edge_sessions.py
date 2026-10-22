import importlib.util
import http.client
import threading
import socket
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('edge_sessions', Path(__file__).with_name('edge_sessions.py'))
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


class SessionTests(unittest.TestCase):
    def test_query_routes_preserve_service_auth_and_exact_endpoint_matching(self):
        server = edge.Server(('127.0.0.1', 0), {'camofox': 'a' * 64, 'apps': 'b' * 64}, 'c' * 64)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def request(path, headers):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.putrequest('GET', path)
                for name, value in headers:
                    connection.putheader(name, value)
                connection.endheaders()
                response = connection.getresponse()
                response.read()
                return response.status, response.headers
            finally:
                connection.close()

        service = [('X-Edge-Service-Key', 'c' * 64)]
        query = '?evidence=horizontal&next=%2Fverify%2Fcamofox&x=1&x=2'
        try:
            for audience in ('apps', 'camofox'):
                with self.subTest(audience=audience):
                    status, headers = request('/mint/' + audience + query, service)
                    self.assertEqual(status, 200)
                    cookie = headers['X-Edge-Cookie'].split(';')[0]
                    status, headers = request('/verify/' + audience + query, service + [('Cookie', cookie)])
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['X-Edge-Valid'], 'yes')
                    key = server.keys[audience]
                    for token in (edge.mint(key, audience, time.time() - 3600), 'invalid'):
                        status, headers = request('/verify/' + audience + query,
                                                  service + [('Cookie', edge.COOKIE_NAMES[audience] + '=' + token)])
                        self.assertEqual(status, 200)
                        self.assertEqual(headers['X-Edge-Valid'], 'no')
                    other = 'camofox' if audience == 'apps' else 'apps'
                    status, headers = request('/verify/' + other + query, service + [('Cookie', cookie)])
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['X-Edge-Valid'], 'no')
            for operation in ('mint', 'verify'):
                for credentials in ([], [('X-Edge-Service-Key', 'wrong')], service * 2):
                    with self.subTest(operation=operation, credentials=len(credentials)):
                        status, headers = request('/' + operation + '/apps' + query, credentials)
                        self.assertEqual(status, 403)
                        self.assertNotIn('X-Edge-Cookie', headers)
                        self.assertNotIn('X-Edge-Valid', headers)
            for path in ('/verify/unknown', '/unknown/apps', '/verify/apps/extra',
                         '/verify/apps/', '/verify/apps%3Fignored', '/verify%2Fapps',
                         '/verify/../apps', '/verify//apps'):
                with self.subTest(path=path):
                    status, headers = request(path + query, service)
                    self.assertEqual(status, 404)
                    self.assertNotIn('X-Edge-Cookie', headers)
                    self.assertNotIn('X-Edge-Valid', headers)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_expiry_signature_audience_rotation_and_future_fail_closed(self):
        token = edge.mint('a' * 64, 'camofox', 10000)
        self.assertTrue(edge.valid(token, 'a' * 64, 'camofox', 13599))
        for key, audience, now, value in (
            ('a' * 64, 'camofox', 13600, token),
            ('a' * 64, 'camofox', 9999, token),
            ('b' * 64, 'camofox', 10001, token),
            ('a' * 64, 'apps', 10001, token),
            ('a' * 64, 'camofox', 10001, token[:-1] + 'z'),
            ('a' * 64, 'camofox', 10001, 'a' * 64),
            ('a' * 64, 'camofox', 10001, 'v1.camofox.9999999999999999999999.' + 'a' * 64),
        ):
            self.assertFalse(edge.valid(value, key, audience, now))

    def test_duplicate_and_malformed_cookie_are_denied(self):
        cookie = 'spartan_edge_apps=' + edge.mint('a' * 64, 'apps', 10000)
        self.assertTrue(edge.authorized_cookie(cookie + '; app=1', 'a' * 64, 'apps', 10001))
        for raw in ('', cookie + '; ' + cookie, 'x' * 8193, 'spartan_edge_apps="broken'):
            self.assertFalse(edge.authorized_cookie(raw, 'a' * 64, 'apps', 10001))

    def test_slow_headers_are_closed_by_absolute_deadline(self):
        server = edge.Server(('127.0.0.1', 0), {'camofox': 'a' * 64, 'apps': 'b' * 64}, 'c' * 64)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with socket.create_connection(('127.0.0.1', server.server_port), timeout=2) as client:
                client.sendall(b'GET /verify/apps HTTP/1.1\r\nX-Slow: ')
                start = time.monotonic()
                closed = False
                while time.monotonic() - start < 7:
                    try:
                        client.sendall(b'x')
                        client.settimeout(.2)
                        if client.recv(1) == b'':
                            closed = True
                            break
                    except (BrokenPipeError, ConnectionResetError):
                        closed = True
                        break
                    except TimeoutError:
                        pass
                self.assertTrue(closed, 'trickled headers escaped lifecycle deadline')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_requires_service_auth_and_verifies_minted_session(self):
        server = edge.Server(('127.0.0.1', 0), {'camofox': 'a' * 64, 'apps': 'b' * 64}, 'c' * 64)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + '/mint/camofox')
            self.assertEqual(error.exception.code, 403)
            headers = {'X-Edge-Service-Key': 'c' * 64, 'X-Edge-Secure': 'yes'}
            with patch.object(edge.time, 'time', return_value=10000):
                with urllib.request.urlopen(urllib.request.Request(base + '/mint/camofox', headers=headers)) as response:
                    cookie = response.headers['X-Edge-Cookie']
                    self.assertIn('; Secure', cookie)
            headers['Cookie'] = cookie.split(';')[0]
            for now, expected in ((10001, 'yes'), (13600, 'no')):
                with patch.object(edge.time, 'time', return_value=now):
                    with urllib.request.urlopen(urllib.request.Request(base + '/verify/camofox', headers=headers)) as response:
                        self.assertEqual(response.headers['X-Edge-Valid'], expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
