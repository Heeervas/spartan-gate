"""Gateway compatibility and usage security contracts; no real provider calls."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parent / 'extensions/spartan-route-usage/gateway.py'
spec = importlib.util.spec_from_file_location('gateway', SOURCE)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def test_credentials_parse_without_shell_or_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials'
            path.write_text("export CADDY_AUTH_USER='example'\nCADDY_AUTH_PASSWORD=literal-password # comment\n")
            self.assertEqual(gateway.credentials(path), ('example', 'literal-password'))
            for value in ('"$(command)"', '"${SECRET}"', '"broken', '"two" "values"', ''):
                path.write_text('CADDY_AUTH_USER=example\nCADDY_AUTH_PASSWORD=' + value + '\n')
                with self.assertRaises(ValueError):
                    gateway.credentials(path)

    def test_usage_http_is_same_https_origin_bounded_and_never_follows_redirects(self):
        class Response:
            status = 200
            def read(self, limit):
                self.limit = limit
                return b'{"version":1,"session":null}'
        class Connection:
            def __init__(self, host, port, timeout):
                self.host, self.port, self.timeout = host, port, timeout
                self.response = Response()
                self.closed = False
            def request(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs
            def getresponse(self):
                return self.response
            def close(self):
                self.closed = True
        connection = Connection('example.invalid', 443, 10)
        config = {'baseUrl': 'https://example.invalid/v1'}
        with patch.object(gateway.http.client, 'HTTPSConnection', return_value=connection) as factory, patch.object(gateway, 'auth_header', return_value='Basic synthetic'):
            self.assertEqual(gateway.usage_request(config, 'status', {'sessionId': 'abc&other=bad'})['version'], 1)
            factory.assert_called_once_with('example.invalid', 443, timeout=10)
            self.assertEqual(connection.args[:2], ('GET', '/api/pi/status?sessionId=abc%26other%3Dbad'))
            self.assertEqual(connection.response.limit, 256 * 1024 + 1)
            self.assertTrue(connection.closed)
            document = {'scope': 'a' * 64, 'sessionId': 'synthetic', 'realm': 'codex_subscription'}
            gateway.usage_request(config, 'binding', document)
            self.assertEqual(connection.args[:2], ('POST', '/api/pi/reasoning-binding'))
            self.assertEqual(json.loads(connection.kwargs['body']), document)
            self.assertEqual(connection.kwargs['headers']['Authorization'], 'Basic synthetic')
            connection.response.status = 302
            with self.assertRaises(ValueError):
                gateway.usage_request(config, 'register', {'sessionId': 'abc'})
            connection.response.status = 200
            connection.response.read = lambda limit: b'x' * limit
            with self.assertRaises(ValueError):
                gateway.usage_request(config, 'refresh', {'sessionId': 'abc'})
        for base in ('http://example.invalid', 'https://user:secret@example.invalid', 'https://example.invalid/?secret=x', 'https://example.invalid/#secret'):
            with self.assertRaises(ValueError):
                gateway.usage_request({'baseUrl': base}, 'status', {'sessionId': 'abc'})

    def test_json_usage_cli_failure_never_prints_credentials_or_parser_exception(self):
        private = 'synthetic-sensitive-value'
        stream = io.TextIOWrapper(io.BytesIO(b'{"sessionId":"synthetic"}'))
        with patch.object(gateway, 'configuration', return_value={}), patch.object(gateway, 'usage_request', side_effect=ValueError(private)), patch('sys.argv', ['gateway.py', 'status']), patch('sys.stdin', stream), patch('sys.stdout', new_callable=io.StringIO) as stdout, patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(gateway.main(), 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertNotIn(private, stderr.getvalue())
            self.assertIn('unavailable', stderr.getvalue())

    def test_binding_errors_expose_only_allowlisted_codes_not_private_body(self):
        class Response:
            status = 409
            def read(self, limit):
                return self.raw[:limit]
        response = Response()
        for code in ('reasoning_binding_invalid', 'reasoning_binding_unavailable', 'reasoning_realm_mismatch'):
            response.raw = json.dumps({'error': {'code': code, 'message': 'synthetic-private-detail'}}).encode()
            self.assertEqual(gateway.usage_response(response, 'binding'), {'version': 1, 'error': {'code': code}})
            with self.assertRaises(ValueError):
                gateway.usage_response(response, 'register')
        response.raw = b'{"error":{"code":"synthetic-private-detail"}}'
        with self.assertRaises(ValueError):
            gateway.usage_response(response, 'binding')

    def test_configuration_uses_existing_shared_path_or_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / 'pi-spartan-route/config.json'
            shared.parent.mkdir()
            shared.write_text(json.dumps({'baseUrl': 'https://example.invalid/v1'}))
            with patch.dict(gateway.os.environ, {'XDG_DATA_HOME': directory}, clear=True):
                self.assertEqual(gateway.configuration()['baseUrl'], 'https://example.invalid/v1')
            with patch.dict(gateway.os.environ, {'PI_SPARTAN_ROUTE_CONFIG': str(shared)}, clear=True):
                self.assertEqual(gateway.configuration()['baseUrl'], 'https://example.invalid/v1')


if __name__ == '__main__':
    unittest.main()
