import unittest
from unittest.mock import patch

from test_server import ReaderHarness, reader_server as reader, request


class Response:
    status = 404
    payload = b"remote private error body"
    def __init__(self, _socket):
        pass
    def begin(self):
        pass
    def getheader(self, key, default=None):
        return "text/plain" if key == "Content-Type" else default
    def read(self, _limit):
        return self.payload
    def close(self):
        pass


class Socket:
    def settimeout(self, _timeout):
        pass
    def sendall(self, _bytes):
        pass


class HTTPStatusTests(unittest.TestCase):
    def test_remote_errors_never_become_success_or_local_capacity(self):
        for status in (403, 404, 429, 500):
            with self.subTest(status=status), patch.object(Response, "status", status), patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "_open_checked_socket", return_value=Socket()), patch.object(reader.http.client, "HTTPResponse", Response):
                with ReaderHarness() as harness:
                    result, headers, body = request(harness.base_url + "/fetch?url=https://example.com/private-query")
                self.assertEqual(result, 502)
                self.assertEqual(headers["X-Spartan-Reader-Error"], "remote_http_error")
                self.assertEqual(headers["X-Spartan-Upstream-Status"], str(status))
                self.assertEqual(body, "remote_http_error\n")

    def test_download_overflow_rejects_instead_of_publishing_partial_source(self):
        with patch.object(Response, "status", 200), patch.object(Response, "payload", b"x" * (reader.MAX_RESPONSE_SIZE + 1)), patch.object(reader, "is_blocked", return_value=False), patch.object(reader, "_open_checked_socket", return_value=Socket()), patch.object(reader.http.client, "HTTPResponse", Response):
            with ReaderHarness() as harness:
                result, headers, body = request(harness.base_url + "/fetch?url=https://example.com")
        self.assertEqual(result, 422)
        self.assertEqual(headers["X-Spartan-Reader-Error"], "response_too_large")
        self.assertNotIn("BEGIN_UNTRUSTED", body)
