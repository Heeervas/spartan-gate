"""Fixed-origin outbound I/O and immediate bounded admission; no caller headers."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

from policy import AccessError


def origin(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and parsed.path in {"", "/"} and not parsed.query
                 and not parsed.fragment and parsed.port != 0)
        if not valid or any(char.isspace() for char in value) or "\\" in value:
            raise ValueError("invalid origin")
        return str(URL(value).origin())
    except (ValueError, TypeError):
        raise ValueError("invalid upstream origin") from None


@dataclass(frozen=True)
class Upstream:
    status: int
    headers: dict
    body: bytes


class Transport:
    def __init__(self, origins, *, timeout=30):
        self.origins = {key: origin(value) for key, value in origins.items()}
        if not 0 < timeout <= 30:
            raise ValueError("invalid timeout")
        self.timeout = timeout
        self.session = None
        self.inflight = 0
        self.clients = {}

    async def start(self):
        self.session = aiohttp.ClientSession(
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(limit=8),
            skip_auto_headers={"User-Agent"})
        # aiohttp otherwise retries a disconnected persistent GET once.
        self.session._retry_connection = False

    async def close(self):
        if self.session is not None:
            await self.session.close()

    @asynccontextmanager
    async def admit(self, principal):
        key = principal.principal
        if self.inflight >= 8 or self.clients.get(key, 0) >= 2:
            raise AccessError("capacity")
        # No await between checking and reserving; no semaphore waiters.
        self.inflight += 1
        self.clients[key] = self.clients.get(key, 0) + 1
        try:
            yield
        finally:
            self.inflight -= 1
            self.clients[key] -= 1
            if not self.clients[key]:
                del self.clients[key]

    async def request(self, backend, method, path, *, limit, params=None,
                      json_body=None, headers=None):
        """Internal-only paths/headers; callers never supply URL or auth here.

        Browser registration must authenticate, then hold admit(principal) across
        the complete operation (including capability probes and mutation).
        """
        if backend not in self.origins or not path.startswith("/"):
            raise ValueError("invalid fixed upstream")
        if path.startswith("//") or any(char in path for char in "?#\\"):
            raise ValueError("invalid upstream path")
        outgoing = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if headers:
            outgoing.update(headers)  # Only server-owned browser master header.
        try:
            async with asyncio.timeout(self.timeout):
                async with self.session.request(
                    method, self.origins[backend] + path, params=params,
                    json=json_body, headers=outgoing, allow_redirects=False,
                ) as response:
                    if (300 <= response.status < 400 or
                            response.headers.get("Content-Encoding", "identity").lower() != "identity"):
                        # Never allocate an unbounded decoded chunk before byte admission.
                        raise AccessError("upstream_error")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        if len(body) + len(chunk) > limit:
                            raise AccessError("upstream_error")
                        body.extend(chunk)
                    return Upstream(response.status, dict(response.headers), bytes(body))
        except TimeoutError:
            raise AccessError("deadline") from None
        except (aiohttp.ClientError, OSError):
            raise AccessError("upstream_error") from None
