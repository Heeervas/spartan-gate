"""
Reader - secure GET-only web page reader for Spartan Gate.

Fetches web pages and returns text content only.
Only GET requests allowed – POST, PUT, DELETE are rejected (405).

Security:
- GET-only: all other HTTP methods return 405
- SSRF protection: permits only globally routable addresses, blocking every
  special-use range before fetch and again for the address used by each
  connection
- Response size cap: 2 MB download, 100k chars output
- Fixed concurrency: bounded worker and pending-request budgets shed overload
- URL privacy: userinfo rejected; paths, queries, and access targets redacted
- Runtime: intended for fixed unprivileged UID, read-only root, no capabilities,
  no-new-privileges, and a conservative PID ceiling
"""

import os
import ipaddress
import re
import sys
import signal
import socket
import ssl
import http.client
import queue
import threading
import time
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from html.parser import HTMLParser
from datetime import datetime, timezone


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer from {minimum} to {maximum}") from exc
    if value < minimum or value > maximum:
        raise SystemExit(f"{name} must be an integer from {minimum} to {maximum}")
    return value


MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2 MB max download
MAX_TEXT_LENGTH = 100_000              # 100k chars max response
LISTEN_PORT = _bounded_env_int("READER_PORT", 3000, 1, 65_535)
REQUEST_TIMEOUT = _bounded_env_int("READER_TIMEOUT", 15, 1, 120)
WORKER_COUNT = _bounded_env_int("READER_WORKERS", 4, 1, 32)
MAX_PENDING_REQUESTS = _bounded_env_int("READER_MAX_PENDING", 8, 0, 128)
MAX_REDIRECTS = 5
UNTRUSTED_BEGIN = "BEGIN_UNTRUSTED_WEB_CONTENT"
UNTRUSTED_END = "END_UNTRUSTED_WEB_CONTENT"
UNTRUSTED_HEADER = "untrusted-web-content"
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
OVERLOAD_BODY = b"Reader overloaded; retry later\n"

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
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "[INVALID_URL]"

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        return "[INVALID_URL]"
    if re.search(r"[\r\n\s]", hostname):
        return "[INVALID_URL]"

    hostname = hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    origin = f"{hostname}:{port}" if port is not None else hostname
    path = "/" if parsed.path in {"", "/"} else "/[REDACTED_PATH]"
    query = "[REDACTED_QUERY]" if parsed.query else ""
    return urllib.parse.urlunparse((scheme, origin, path, "", query, ""))


def audit_log(action: str, url: str = "", detail: str = ""):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if url:
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

    # A public page reader must deny every special-use range, including shared
    # carrier-grade NAT space such as 100.64.0.0/10. Positive global
    # classification is safer than trying to maintain a deny-list of address
    # attributes whose definitions differ across IP versions and Python
    # releases.
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_blocked(url: str) -> bool:
    """Check whether a URL violates the public global-address boundary."""
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        return True
    if parsed.scheme not in {"http", "https"}:
        return True
    if parsed.username is not None or parsed.password is not None:
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

    # Resolve every address and fail closed for any non-global result.
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        infos = socket.getaddrinfo(hostname, port or default_port)
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
            raise BlockedDestination("Resolved address blocked: non-global network")

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
        raise BlockedDestination("Destination blocked: non-global or internal destination")

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
                raise BlockedDestination("Redirect blocked: non-global or internal destination")
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


class BoundedHTTPServer(HTTPServer):
    """HTTP server backed by a fixed worker pool and bounded admission."""

    def __init__(
        self,
        server_address,
        handler_class,
        *,
        max_workers: int = WORKER_COUNT,
        max_pending: int = MAX_PENDING_REQUESTS,
    ):
        super().__init__(server_address, handler_class)
        self._closing = threading.Event()
        self._connections_lock = threading.Lock()
        self._connections = {}
        self._capacity = threading.BoundedSemaphore(max_workers + max_pending)
        self._requests = queue.Queue(maxsize=max_workers + max_pending)
        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                name=f"reader-worker-{index + 1}",
                daemon=False,
            )
            for index in range(max_workers)
        ]
        for worker in self._workers:
            worker.start()
        self._watchdog = threading.Thread(
            target=self._expire_connections, name="reader-deadlines", daemon=False,
        )
        self._watchdog.start()

    def process_request(self, request, client_address):
        # Admission and close share a lock: no socket can arrive behind drain.
        with self._connections_lock:
            if not self._closing.is_set() and self._capacity.acquire(blocking=False):
                request.settimeout(REQUEST_TIMEOUT)
                self._connections[request] = time.monotonic() + REQUEST_TIMEOUT
                self._requests.put_nowait((request, client_address))
                return
        self._reject_overload(request)

    @staticmethod
    def _interrupt_connection(request):
        # shutdown, not close: the worker still owns the descriptor and files.
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _expire_connections(self):
        # One watchdog for the bounded pool, not one timer/thread per client.
        # A socket inactivity timeout alone lets a header drip live forever.
        while not self._closing.wait(0.05):
            with self._connections_lock:
                now = time.monotonic()
                for request, deadline in self._connections.items():
                    if deadline <= now:
                        self._interrupt_connection(request)


    def connection_expired(self, request):
        with self._connections_lock:
            deadline = self._connections.get(request, 0)
            return self._closing.is_set() or deadline <= time.monotonic()

    def _release_connection(self, request):
        with self._connections_lock:
            self._connections.pop(request, None)
        self.shutdown_request(request)
        self._capacity.release()

    def _worker_loop(self):
        while True:
            item = self._requests.get()
            try:
                if item is None:
                    return
                request, client_address = item
                try:
                    self.finish_request(request, client_address)
                except (TimeoutError, ConnectionError):
                    pass  # Expected for expired or disconnected clients.
                except Exception:
                    self.handle_error(request, client_address)
                finally:
                    self._release_connection(request)
            finally:
                self._requests.task_done()

    def _reject_overload(self, request):
        audit_log("OVERLOAD")
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"X-Content-Type-Options: nosniff\r\n"
            b"Retry-After: 1\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(OVERLOAD_BODY)}\r\n\r\n".encode("ascii")
            + OVERLOAD_BODY
        )
        try:
            request.settimeout(1)
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def server_close(self):
        with self._connections_lock:
            if self._closing.is_set():
                return
            self._closing.set()
            for request in self._connections:
                self._interrupt_connection(request)
        self._watchdog.join()
        super().server_close()

        while True:
            try:
                request, _client_address = self._requests.get_nowait()
            except queue.Empty:
                break
            self._release_connection(request)
            self._requests.task_done()

        for _worker in self._workers:
            self._requests.put(None)
        for worker in self._workers:
            worker.join()


class ReaderHandler(BaseHTTPRequestHandler):
    """HTTP Handler – GET only."""

    def parse_request(self):
        parsed = super().parse_request()
        # http.client accepts EOF as the end of headers. A watchdog-induced
        # EOF must not dispatch an incomplete /fetch after its budget expires.
        if isinstance(self.server, BoundedHTTPServer):
            return parsed and not self.server.connection_expired(self.connection)
        return parsed

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
                audit_log("BLOCKED-DESTINATION", url)
                self._respond(403, "URL blocked: non-global or internal destination")
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
        route = urllib.parse.urlparse(self.path).path
        if route not in {"/fetch", "/health"}:
            route = "[other]"
        method = self.command if self.command in {
            "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"
        } else "[other]"
        status = str(args[1]) if len(args) > 1 and str(args[1]).isdigit() else "-"
        sys.stderr.write(f"[reader-proxy] {method} {route} {status}\n")


def main():
    server = BoundedHTTPServer(("0.0.0.0", LISTEN_PORT), ReaderHandler)
    shutdown_requested = threading.Event()
    serving_stopped = threading.Event()

    def serve():
        try:
            server.serve_forever()
        finally:
            serving_stopped.set()

    serving_thread = threading.Thread(
        target=serve,
        name="reader-accept",
        daemon=False,
    )

    def request_shutdown(_sig, _frame):
        print("[reader-proxy] Shutting down...", flush=True)
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    print(
        f"[reader-proxy] Listening on 0.0.0.0:{server.server_port} "
        f"(GET-only, workers={WORKER_COUNT}, pending={MAX_PENDING_REQUESTS})",
        flush=True,
    )
    serving_thread.start()
    try:
        while not shutdown_requested.wait(timeout=0.5):
            if serving_stopped.is_set():
                break
    finally:
        if serving_thread.is_alive():
            server.shutdown()
        serving_thread.join()
        server.server_close()


if __name__ == "__main__":
    main()
