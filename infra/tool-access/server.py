"""Managed tools REST boundary. Start with python server.py; no access logs."""

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

from aiohttp import web
from yarl import URL

from policy import AccessError, Policy, authenticate, strict_json
from transport import Transport

REQUEST_LIMIT = 16 * 1024
SEARCH_LIMIT = 128 * 1024
READER_LIMIT = 512 * 1024
SEARCH_FIELDS = {"q", "format", "num", "language", "time_range", "categories",
                 "engines", "safesearch", "pageno"}


def error_response(error):
    return web.json_response({"error": {"code": error.code}}, status=error.status)


async def bounded_body(request):
    body = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        if len(body) + len(chunk) > REQUEST_LIMIT:
            raise AccessError("invalid_request")
        body.extend(chunk)
    # Cache bounded bytes for browser request.json()/read().
    request._read_bytes = bytes(body)


def check_ingress(request):
    if "Origin" in request.headers or "Cookie" in request.headers:
        raise AccessError("forbidden")
    hosts = request.headers.getall("Host", [])
    if len(hosts) != 1 or hosts[0] not in request.app["allowed_hosts"]:
        raise AccessError("forbidden")
    if not request.raw_path.startswith("/") or request.raw_path.startswith("//"):
        raise AccessError("invalid_request")
    if ("Content-Encoding" in request.headers
            or (request.content_length is not None and request.content_length > REQUEST_LIMIT)):
        raise AccessError("invalid_request")


@web.middleware
async def boundary(request, handler):
    try:
        check_ingress(request)
        async with asyncio.timeout(30):
            await bounded_body(request)
            return await handler(request)
    except AccessError as error:
        return error_response(error)
    except TimeoutError:
        code = ("remote_outcome_unknown" if request.get("browser_mutation_started")
                else "deadline")
        return error_response(AccessError(code))
    except web.HTTPException as error:
        code = "not_found" if error.status == 404 else "invalid_request"
        return error_response(AccessError(code))
    except (ValueError, UnicodeError, RecursionError):
        return error_response(AccessError("invalid_request"))
    except Exception:
        # No traceback/URL/body/credential details in routine server logs/errors.
        return error_response(AccessError("unavailable"))


def search_params(request):
    pairs = list(request.query.items())
    if request._read_bytes or any(key not in SEARCH_FIELDS for key, _ in pairs):
        raise AccessError("invalid_request")
    if not request.query.getall("q", []) or not all(request.query.getall("q")):
        raise AccessError("invalid_request")
    if request.query.getall("format", []) != ["json"]:
        raise AccessError("invalid_request")
    return pairs


def json_upstream(response):
    if response.status != 200:
        raise AccessError("upstream_error")
    try:
        data = strict_json(response.body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid search JSON")
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise AccessError("upstream_error") from None


async def search(request):
    principal = authenticate(request, "search")
    params = search_params(request)
    async with request.app["transport"].admit(principal):
        result = await request.app["transport"].request(
            "search", "GET", "/search", limit=SEARCH_LIMIT, params=params)
        json_upstream(result)
        return web.Response(body=result.body, content_type="application/json")


def url_host(value):
    if not value or len(value) > 8192 or any(ord(char) <= 32 for char in value):
        raise ValueError("invalid URL")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or "\\" in value or parsed.port == 0):
        raise ValueError("invalid URL")
    return URL(value).host


def literal_address(host):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        try:
            # inet_aton parses legacy short/octal/hex IPv4 without any DNS lookup.
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None


def public_url(value):
    try:
        host = url_host(value)
        address = literal_address(host)
        if address is not None:
            if not address.is_global or address.is_multicast:
                raise ValueError("nonpublic literal")
        elif host.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
            raise ValueError("nonpublic host")
        # Reader remains authoritative for DNS, redirects and connection checks.
        return value
    except (ValueError, TypeError, UnicodeError):
        raise AccessError("invalid_request") from None


def reader_text(response):
    if response.status != 200:
        raise AccessError("upstream_error")
    headers = {key.lower(): value for key, value in response.headers.items()}
    if headers.get("x-spartan-content-trust") != "untrusted-web-content":
        raise AccessError("upstream_error")
    try:
        text = response.body.decode("utf-8")
    except UnicodeError:
        raise AccessError("upstream_error") from None
    if not text.startswith("BEGIN_UNTRUSTED_WEB_CONTENT\n") or not text.endswith(
        "\nEND_UNTRUSTED_WEB_CONTENT\n"
    ):
        raise AccessError("upstream_error")
    return text


async def fetch(request):
    principal = authenticate(request, "reader")
    if request._read_bytes or list(request.query.keys()) != ["url"]:
        raise AccessError("invalid_request")
    url = public_url(request.query["url"])
    async with request.app["transport"].admit(principal):
        result = await request.app["transport"].request(
            "reader", "GET", "/fetch", limit=READER_LIMIT, params=[("url", url)])
        return web.Response(text=reader_text(result), content_type="text/plain",
                            headers={"X-Spartan-Content-Trust": "untrusted-web-content"})


def create_app(settings=None, *, policy_path=None, install_browser=None):
    settings = dict(os.environ if settings is None else settings)
    allowed = settings.get("ALLOWED_HOSTS", "").split(",")
    if not allowed or any(not host.strip() for host in allowed):
        raise ValueError("ALLOWED_HOSTS required")
    origins = {"search": settings["SEARCH_URL"], "reader": settings["READER_URL"]}
    if settings.get("CAMOFOX_URL"):
        origins["browser"] = settings["CAMOFOX_URL"]
    # aiohttp parser errors can otherwise log raw credential-bearing request lines.
    protocol_logger = logging.Logger("tool-access.protocol")
    protocol_logger.disabled = True
    app = web.Application(middlewares=[boundary], client_max_size=REQUEST_LIMIT,
                          handler_args={"logger": protocol_logger,
                                        "auto_decompress": False,
                                        "max_line_size": REQUEST_LIMIT,
                                        "max_field_size": REQUEST_LIMIT})
    app["policy"] = Policy(policy_path or settings["POLICY_FILE"])
    app["transport"] = Transport(origins)
    app["allowed_hosts"] = frozenset(host.strip() for host in allowed)
    app["settings"] = settings

    async def lifecycle(application):
        await application["transport"].start()
        try:
            yield
        finally:
            await application["transport"].close()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/search", search, allow_head=False)
    app.router.add_get("/fetch", fetch, allow_head=False)
    if install_browser is not None:
        install_browser(app)
    return app


if __name__ == "__main__":
    from browser import register_routes

    web.run_app(create_app(install_browser=register_routes),
                host="0.0.0.0", port=9081, access_log=None,
                handler_cancellation=True)
