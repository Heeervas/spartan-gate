#!/usr/bin/env python3
"""Internal, bounded HMAC session verifier for the Caddy Basic-auth edge.

Only Caddy holds the service authentication secret. Mint is called exclusively
inside its successful Basic-auth route. No browser-supplied identity is trusted.
"""
import hashlib
import hmac
import http.cookies
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

TTL = 3600
COOKIE_NAMES = {'camofox': 'spartan_edge_camofox', 'apps': 'spartan_edge_apps'}


def mint(key, audience, now):
    payload = f'v1.{audience}.{int(now)}'
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}.{signature}'


def valid(token, key, audience, now):
    if not isinstance(token, str) or len(token) > 128:
        return False
    match = re.fullmatch(r'v1\.(camofox|apps)\.([0-9]{1,12})\.([0-9a-f]{64})', token)
    if not match or match[1] != audience:
        return False
    issued = int(match[2])
    if not 0 <= now - issued < TTL:
        return False
    return hmac.compare_digest(token, mint(key, audience, issued))


def authorized_cookie(raw, key, audience, now):
    if len(raw) > 8192:
        return False
    name = COOKIE_NAMES[audience]
    # Reject ambiguity rather than let differing cookie parsers choose a value.
    if sum(part.strip().partition('=')[0] == name for part in raw.split(';')) != 1:
        return False
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(raw)
    except http.cookies.CookieError:
        return False
    return name in cookies and valid(cookies[name].value, key, audience, now)


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, keys, service_key):
        self.keys = keys
        self.service_key = service_key
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        # An absolute lifecycle deadline also defeats slow trickled headers.
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            request.close()
        deadline = threading.Timer(5, expire)
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, address)
        finally:
            deadline.cancel()
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    # One bounded request per connection, no bodies or forwarded URL parsing.
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.close_connection = True
        credentials = self.headers.get_all('X-Edge-Service-Key', [])
        if len(credentials) != 1 or not hmac.compare_digest(credentials[0], self.server.service_key):
            self.send_error(403)
            return
        # Caddy's auth subrequest retains the original application's query.
        parts = self.path.partition('?')[0].split('/')
        if len(parts) != 3 or parts[1] not in {'verify', 'mint'} or parts[2] not in COOKIE_NAMES:
            self.send_error(404)
            return
        operation, audience = parts[1:]
        key = self.server.keys[audience]
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        if operation == 'verify':
            cookies = self.headers.get_all('Cookie', [])
            ok = len(cookies) == 1 and authorized_cookie(cookies[0], key, audience, time.time())
            self.send_header('X-Edge-Valid', 'yes' if ok else 'no')
        else:
            secure = '; Secure' if self.headers.get('X-Edge-Secure') == 'yes' else ''
            cookie = f'{COOKIE_NAMES[audience]}={mint(key, audience, time.time())}; Path=/; Max-Age={TTL}; HttpOnly; SameSite=Strict{secure}'
            self.send_header('X-Edge-Cookie', cookie)
        self.send_header('Content-Length', '0')
        self.end_headers()


def main():
    keys = {audience: os.environ[name] for audience, name in (
        ('camofox', 'CADDY_CAMOFOX_EDGE_TOKEN'), ('apps', 'CADDY_WS_EDGE_TOKEN'))}
    service_key = os.environ['CADDY_SESSION_SERVICE_KEY']
    if any(not re.fullmatch('[0-9a-f]{64}', key) for key in [*keys.values(), service_key]):
        raise SystemExit('edge session keys must be 64 lowercase hex')
    if len(set([*keys.values(), service_key])) != 3:
        raise SystemExit('edge session keys must differ')
    Server(('0.0.0.0', 9080), keys, service_key).serve_forever()


if __name__ == '__main__':
    main()
