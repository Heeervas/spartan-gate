"""
Reader - secure GET-only web page reader for Spartan Gate.

Fetches web pages and returns text content only.
Only GET requests allowed – POST, PUT, DELETE are rejected (405).

Security:
- GET-only: all other HTTP methods return 405
- SSRF protection: blocks private/internal IPs (RFC 1918, link-local, loopback)
  before fetch and again for the address used by each connection
- Response size cap: 2 MB download, 100k chars output
- Audit logging: every request logged with timestamp and query-redacted URL
"""

import os
import ipaddress
import re
import sys
import signal
import socket
import ssl
import http.client
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from html.parser import HTMLParser
from datetime import datetime, timezone

MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2 MB max download
MAX_TEXT_LENGTH = 100_000              # 100k chars max response
LISTEN_PORT = int(os.environ.get("READER_PORT", "3000"))
REQUEST_TIMEOUT = int(os.environ.get("READER_TIMEOUT", "15"))
MAX_REDIRECTS = 5
UNTRUSTED_BEGIN = "BEGIN_UNTRUSTED_WEB_CONTENT"
UNTRUSTED_END = "END_UNTRUSTED_WEB_CONTENT"
UNTRUSTED_HEADER = "untrusted-web-content"
_ORIGINAL_CREATE_CONNECTION = socket.create_connection

# Internal Docker service names (prevent SSRF to internal services)
BLOCKED_HOSTNAMES = {
    "hermes", "clawroute", "searxng", "browserless",
    "reader", "proxy", "caddy", "ollama", "dns",
}

BLOCKED_HOSTNAME_SUFFIXES = (
    ".internal",
    ".local",
)


class BlockedDestination(Exception):
    """Raised when a requested or redirected URL targets a blocked destination."""


# ── Audit logger ──
def redact_url_for_log(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    query = "[REDACTED_QUERY]" if parsed.query else ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def audit_log(action: str, url: str = "", detail: str = ""):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = redact_url_for_log(url)
    trunc_url = url[:200] + "…" if len(url) > 200 else url
    parts = [f"[reader-audit] {ts} {action}"]
    if trunc_url:
        parts.append(trunc_url)
    if detail:
        parts.append(f"({detail})")
    sys.stderr.write(" ".join(parts) + "\n")
    sys.stderr.flush()


class TextExtractor(HTMLParser):
    """Extracts readable text from HTML, ignoring scripts/styles."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "svg", "head"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "tr", "blockquote", "article", "section"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self):
        text = " ".join(self._text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()


def _is_blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_blocked(url: str) -> bool:
    """Check if URL points to internal/blocked networks."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return True

    if (
        hostname in BLOCKED_HOSTNAMES
        or hostname == "localhost"
        or hostname == "metadata.google.internal"
        or any(hostname.endswith(suffix) for suffix in BLOCKED_HOSTNAME_SUFFIXES)
    ):
        return True

    if _is_blocked_ip(hostname):
        return True

    # Resolve every address and fail closed for any private/internal result.
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 443)
    except socket.gaierror:
        return True

    for info in infos:
        if _is_blocked_ip(info[4][0]):
            return True

    return False


def create_checked_connection(address, timeout=None, source_address=None):
    host, port = address[:2]
    try:
        ipaddress.ip_address(host)
        candidates = [(host, port)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise OSError(f"DNS resolution failed for {host}") from exc
        candidates = [(info[4][0], info[4][1]) for info in infos]

    if not candidates:
        raise OSError(f"DNS resolution returned no addresses for {host}")

    for candidate_host, _candidate_port in candidates:
        if _is_blocked_ip(candidate_host):
            raise BlockedDestination("Resolved address blocked: internal/private network")

    last_error = None
    for candidate in candidates:
        try:
            return _ORIGINAL_CREATE_CONNECTION(candidate, timeout=timeout, source_address=source_address)
        except OSError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise OSError(f"Could not connect to {host}")


def mark_untrusted(text: str) -> str:
    """Wrap fetched page text so downstream agents keep it separated as data."""
    return f"{UNTRUSTED_BEGIN}\n{text}\n{UNTRUSTED_END}\n"


def _request_path(parsed: urllib.parse.ParseResult) -> str:
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _open_checked_socket(hostname: str, port: int, use_tls: bool):
    sock = create_checked_connection((hostname, port), timeout=REQUEST_TIMEOUT)
    if not use_tls:
        return sock
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context.wrap_socket(sock, server_hostname=hostname)


def fetch_url(url: str, redirects: int = 0) -> tuple[str, str]:
    """Fetch URL content. Returns (content, content_type)."""
    if redirects > MAX_REDIRECTS:
        raise BlockedDestination("Redirect blocked: too many redirects")

    if is_blocked(url):
        raise BlockedDestination("Destination blocked: internal/private network")

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        raise BlockedDestination("Destination blocked: missing hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"
    host_header = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    request = (
        f"GET {_request_path(parsed)} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "User-Agent: Mozilla/5.0 (compatible; SpartanGate-Reader/1.0)\r\n"
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8\r\n"
        "Accept-Language: en,de;q=0.5\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii", errors="ignore")

    sock = _open_checked_socket(hostname, port, use_tls)
    try:
        sock.sendall(request)
        resp = http.client.HTTPResponse(sock)
        resp.begin()
        if resp.status in {301, 302, 303, 307, 308}:
            location = resp.getheader("Location")
            if not location:
                raise BlockedDestination("Redirect blocked: missing Location header")
            newurl = urllib.parse.urljoin(url, location)
            if is_blocked(newurl):
                audit_log("BLOCKED-REDIRECT", newurl)
                raise BlockedDestination("Redirect blocked: internal/private network")
            return fetch_url(newurl, redirects + 1)

        content_type = resp.getheader("Content-Type", "text/html")
        data = resp.read(MAX_RESPONSE_SIZE)
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        return data.decode(charset, errors="replace"), content_type
    finally:
        sock.close()


def extract_text(html_content: str) -> str:
    """Extract readable text from HTML."""
    parser = TextExtractor()
    parser.feed(html_content)
    text = parser.get_text()
    return text[:MAX_TEXT_LENGTH]


class ReaderHandler(BaseHTTPRequestHandler):
    """HTTP Handler – GET only."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/health":
            self._respond(200, "ok")
            return

        if parsed.path == "/fetch":
            params = urllib.parse.parse_qs(parsed.query)
            url = params.get("url", [None])[0]

            if not url:
                self._respond(400, "Missing 'url' parameter")
                return

            if not url.startswith(("http://", "https://")):
                self._respond(400, "URL must start with http:// or https://")
                return

            if is_blocked(url):
                audit_log("BLOCKED-PRIVATE", url)
                self._respond(403, "URL blocked: internal/private network")
                return

            audit_log("FETCH", url)

            try:
                content, content_type = fetch_url(url)

                if "html" in content_type.lower():
                    text = extract_text(content)
                else:
                    text = content[:MAX_TEXT_LENGTH]

                self._respond(
                    200,
                    mark_untrusted(text),
                    {"X-Spartan-Content-Trust": UNTRUSTED_HEADER},
                )

            except BlockedDestination as e:
                self._respond(403, str(e))
            except urllib.error.HTTPError as e:
                self._respond(502, f"Remote server returned {e.code}: {e.reason}")
            except urllib.error.URLError as e:
                self._respond(502, f"Could not reach URL: {e.reason}")
            except TimeoutError:
                self._respond(504, "Request timed out")
            except Exception as e:
                self._respond(500, f"Error: {str(e)}")
            return

        self._respond(404, "Not found. Use /fetch?url=... or /health")

    def do_POST(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def do_PUT(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def do_DELETE(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def do_PATCH(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def do_HEAD(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def do_OPTIONS(self):
        self._respond(405, "Method not allowed. Only GET requests are permitted.")

    def _respond(self, code: int, body: str, headers: dict[str, str] | None = None):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        sys.stderr.write(f"[reader-proxy] {args[0]} {args[1]} {args[2]}\n")


def main():
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), ReaderHandler)
    print(f"[reader-proxy] Listening on 0.0.0.0:{LISTEN_PORT} (GET-only)",
          flush=True)

    def shutdown(sig, frame):
        print("[reader-proxy] Shutting down...", flush=True)
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.serve_forever()


if __name__ == "__main__":
    main()
