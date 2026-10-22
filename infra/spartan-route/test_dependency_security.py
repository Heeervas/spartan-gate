"""Behavioral guards for security fixes in Route's HTTP dependency."""

import asyncio
import unittest
from http.cookies import SimpleCookie

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestServer
from yarl import URL


class InitialRequestTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_and_partial_initial_headers_are_closed(self):
        # GHSA-5wr8-gcqf-gqf3: keepalive must also bound a connection
        # before the first complete request, not just between requests.
        app = web.Application()
        server = TestServer(app)
        await server.start_server(keepalive_timeout=0.05)
        self.addAsyncCleanup(server.close)
        async with server:
            for initial_bytes in (b'', b'GET / HTTP/1.1\r\nHost:'):
                with self.subTest(initial_bytes=initial_bytes):
                    reader, writer = await asyncio.open_connection(
                        server.host, server.port,
                    )
                    try:
                        writer.write(initial_bytes)
                        await writer.drain()
                        self.assertEqual(
                            await asyncio.wait_for(reader.read(1), timeout=2), b'',
                        )
                    finally:
                        writer.close()
                        await writer.wait_closed()


class SharedCookieSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_secure_shared_cookie_is_not_sent_over_plaintext(self):
        # GHSA-494j-54xm-rwp3: cookies without an origin must still honor
        # Secure, including after an HTTPS request has populated the cache.
        jar = CookieJar()
        cookies = SimpleCookie()
        cookies.load('session=secret; Secure')
        jar.update_cookies(cookies)
        jar.update_cookies({'preference': 'light'})

        for scheme in ('http', 'https', 'http', 'wss', 'ws'):
            with self.subTest(scheme=scheme):
                filtered = jar.filter_cookies(URL(f'{scheme}://example.com/'))
                self.assertEqual(filtered['preference'].value, 'light')
                if scheme in ('https', 'wss'):
                    self.assertEqual(filtered['session'].value, 'secret')
                else:
                    self.assertNotIn('session', filtered)
