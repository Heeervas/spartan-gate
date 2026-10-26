import asyncio
import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.http_parser import ZLibDecompressor
from aiohttp.test_utils import TestClient, TestServer

from server import create_app
from test_policy import TOKEN, client, write_policy


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "clients.json"
        write_policy(self.path)
        self.calls = []
        self.mode = "ok"
        self.release = asyncio.Event()
        self.arrived = asyncio.Queue()
        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", self.upstream)
        self.upstream_server = TestServer(upstream)
        await self.upstream_server.start_server()
        self.addAsyncCleanup(self.upstream_server.close)
        endpoint = str(self.upstream_server.make_url("/")).rstrip("/")
        self.app = create_app({"SEARCH_URL": endpoint, "READER_URL": endpoint,
                               "ALLOWED_HOSTS": "fixture", "POLICY_FILE": str(self.path)})
        self.http = TestClient(TestServer(self.app))
        await self.http.start_server()
        self.app["transport"].timeout = 0.15
        self.addAsyncCleanup(self.http.close)
        self.headers = {"Host": "fixture", "Authorization": "Bearer " + TOKEN}

    async def upstream(self, request):
        self.calls.append((request.path, list(request.query.items()), dict(request.headers)))
        if self.mode == "redirect":
            return web.Response(status=302, headers={"Location": "http://secret.invalid/?secret"})
        if self.mode == "malformed":
            return web.Response(text="private upstream exception")
        if self.mode == "large":
            return web.json_response({"payload": "x" * (128 * 1024 + 1)})
        if self.mode == "gzip":
            return web.Response(body=gzip.compress(b'{"results": []}'),
                                headers={"Content-Encoding": "gzip"})
        if self.mode in {"slow", "hold"}:
            await self.arrived.put(True)
            await self.release.wait()
        if request.path == "/fetch":
            text = "BEGIN_UNTRUSTED_WEB_CONTENT\nfixture\nEND_UNTRUSTED_WEB_CONTENT\n"
            if self.mode == "truncated":
                text = text[:-10]
            headers = {} if self.mode == "untrusted_header_missing" else {
                "X-Spartan-Content-Trust": "untrusted-web-content"}
            return web.Response(text=text, headers=headers)
        return web.json_response({"results": [{"title": "fixture"}]})

    async def get(self, path="/search?q=hello&format=json", *, headers=None):
        return await self.http.get(path, headers=self.headers if headers is None else headers)

    async def assert_error(self, response, status, code):
        self.assertEqual(response.status, status)
        self.assertEqual(await response.json(), {"error": {"code": code}})
        self.assertNotIn("Location", response.headers)

    async def test_search_query_duplicates_and_header_isolation(self):
        response = await self.get("/search?q=a%2Bb%26c&format=json&engines=a&engines=b&num=3",
                                  headers={**self.headers, "X-Backend-Key": "secret",
                                           "X-Spartan-Workflow": "secret"})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"results": [{"title": "fixture"}]})
        path, query, headers = self.calls[0]
        self.assertEqual(path, "/search")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertEqual(query, [("q", "a+b&c"), ("format", "json"),
                                 ("engines", "a"), ("engines", "b"), ("num", "3")])
        for key in ("Authorization", "Cookie", "X-Backend-Key", "X-Spartan-Workflow"):
            self.assertNotIn(key, headers)

    async def test_auth_capability_and_revocation_never_reach_upstream(self):
        await self.assert_error(await self.get(headers={"Host": "fixture"}), 401, "unauthorized")
        await self.assert_error(await self.get(headers={"Host": "fixture", "Authorization": "Bearer " + "b" * 64}),
                                401, "unauthorized")
        write_policy(self.path, [client(capabilities=["browser"])])
        await self.assert_error(await self.get(), 403, "forbidden")
        self.assertEqual(self.calls, [])
        write_policy(self.path)
        self.assertEqual((await self.get()).status, 200)
        self.calls.clear()
        write_policy(self.path, [])
        await self.assert_error(await self.get(), 401, "unauthorized")
        self.assertEqual(self.calls, [])

    async def test_ingress_and_unknown_fields_rejected_without_forwarding(self):
        for headers in ({**self.headers, "Origin": "null"}, {**self.headers, "Host": "evil"},
                        {**self.headers, "Cookie": "auth=secret"}):
            await self.assert_error(await self.get(headers=headers), 403, "forbidden")
        for path in ("/search?q=x&format=json&url=http://evil", "/search?q=x&format=html",
                     "/fetch?url=https://example.com&url=https://example.org",
                     "/fetch?url=http://127.0.0.1", "/fetch?url=http://[::1]",
                     "/fetch?url=http://127.1", "/fetch?url=http://2130706433",
                     "/fetch?url=https://user:secret@example.com"):
            await self.assert_error(await self.get(path), 400, "invalid_request")
        response = await self.http.request("GET", "/search?q=x&format=json",
                                           headers=self.headers, data=b"x" * 16385)
        await self.assert_error(response, 400, "invalid_request")
        self.assertEqual(self.calls, [])

    async def test_encoded_requests_rejected_before_parser_inflation(self):
        decompress = ZLibDecompressor.decompress_sync
        with patch.object(ZLibDecompressor, "decompress_sync", autospec=True,
                          side_effect=decompress) as decoder:
            for encoding, payload in (("gzip", gzip.compress(b"")),
                                      ("gzip", gzip.compress(b"x" * 65536)),
                                      ("identity", b"")):
                response = await self.http.get(
                    "/search?q=x&format=json",
                    headers={**self.headers, "Content-Encoding": encoding}, data=payload)
                await self.assert_error(response, 400, "invalid_request")
            decoder.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_redirect_malformed_encoded_overflow_and_deadline(self):
        for mode, status, code in (("redirect", 502, "upstream_error"),
                                   ("malformed", 502, "upstream_error"),
                                   ("large", 502, "upstream_error"),
                                   ("gzip", 502, "upstream_error"),
                                   ("slow", 504, "deadline")):
            self.mode = mode
            await self.assert_error(await self.get(), status, code)
        self.release.set()
        self.mode = "ok"
        self.assertEqual((await self.get()).status, 200)
        self.assertEqual(self.app["transport"].inflight, 0)
        self.assertEqual(len(self.calls), 6)  # no redirect/retry request

    async def test_reader_requires_complete_markers_and_trust_header(self):
        path = "/fetch?url=https://example.com/a%3Fq%3Dtest"
        response = await self.get(path)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Spartan-Content-Trust"], "untrusted-web-content")
        self.assertEqual(await response.text(), "BEGIN_UNTRUSTED_WEB_CONTENT\nfixture\nEND_UNTRUSTED_WEB_CONTENT\n")
        self.assertEqual(self.calls[-1][:2], ("/fetch", [("url", "https://example.com/a?q=test")]))
        for mode in ("truncated", "untrusted_header_missing"):
            self.mode = mode
            await self.assert_error(await self.get(path), 502, "upstream_error")

    async def test_per_principal_immediate_capacity_and_release(self):
        self.mode = "hold"
        self.app["transport"].timeout = 5
        tasks = [asyncio.create_task(self.get()) for _ in range(2)]
        try:
            await asyncio.wait_for(self.arrived.get(), 1)
            await asyncio.wait_for(self.arrived.get(), 1)
            await self.assert_error(await asyncio.wait_for(self.get(), 0.5), 429, "capacity")
            self.assertEqual(len(self.calls), 2)
        finally:
            self.release.set()
            responses = await asyncio.gather(*tasks)
        self.assertEqual([response.status for response in responses], [200, 200])
        self.assertEqual(self.app["transport"].inflight, 0)

    async def test_environment_proxy_is_not_used(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1",
                                       "http_proxy": "http://127.0.0.1:1",
                                       "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual((await self.get()).status, 200)
        self.assertEqual(len(self.calls), 1)

    async def test_global_capacity_is_immediate_across_principals(self):
        tokens = [character * 64 for character in "abcde"]
        write_policy(self.path, [client("fixture-" + str(index), token)
                                 for index, token in enumerate(tokens)])
        self.mode = "hold"
        self.app["transport"].timeout = 5
        tasks = [asyncio.create_task(self.get(headers={
            "Host": "fixture", "Authorization": "Bearer " + token}))
            for token in tokens[:4] for _ in range(2)]
        try:
            for _ in range(8):
                await asyncio.wait_for(self.arrived.get(), 1)
            response = await asyncio.wait_for(self.get(headers={
                "Host": "fixture", "Authorization": "Bearer " + tokens[4]}), 0.5)
            await self.assert_error(response, 429, "capacity")
            self.assertEqual(len(self.calls), 8)
        finally:
            self.release.set()
            responses = await asyncio.gather(*tasks)
        self.assertEqual([response.status for response in responses], [200] * 8)
        self.assertEqual(self.app["transport"].inflight, 0)


if __name__ == "__main__":
    unittest.main()
