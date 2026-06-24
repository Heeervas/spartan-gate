"""sitecustomize.py — Proxy interceptor for Hermes Agent.
Loaded automatically by Python when PYTHONPATH includes this directory.
Equivalent of proxy-bootstrap.js for the Node.js world.

Routing:
1. web_search (Brave API) → redirected to SearXNG (Brave-compatible response)
2. All HTTP traffic → audit logged to SPARTAN_PROXY_AUDIT_LOG
3. Direct SearXNG access detection → warning

Security: This file is COPY'd into the image (read-only layer).
The agent cannot modify it at runtime.
"""
import os
import sys
import datetime
import json
from urllib.parse import urlparse

SEARXNG_URL = os.environ.get('SEARXNG_URL', 'http://searxng:8080')
READER_URL = os.environ.get('READER_PROXY_URL', 'http://reader:3000')
PROXY_AUDIT_LOG = os.environ.get('SPARTAN_PROXY_AUDIT_LOG', '/opt/data/logs/proxy-audit.log').strip()
MAX_QUERY_LEN = 500  # Prevent data exfiltration via search queries
BRAVE_SEARCH_HOST = 'api.search.brave.com'

import re as _re
# Redact secrets from URLs before logging (bot tokens, API keys, bearer tokens)
_REDACT_PATTERNS = _re.compile(
    r'(/bot)[A-Za-z0-9:_-]{20,}(/)'       # Telegram bot tokens: /bot<token>/
    r'|([?&](?:key|token|apikey|api_key|secret|password|access_token)=)[^&\s]+'
    r'|(Bearer\s+)[^\s]+',                # Authorization headers
    _re.IGNORECASE
)
def _redact_url(url):
    """Replace secrets in URLs with [REDACTED] for safe logging."""
    def _sub(m):
        if m.group(1):  # Telegram /bot<token>/
            return f'{m.group(1)}[REDACTED]{m.group(2)}'
        if m.group(3):  # query param key=value
            return f'{m.group(3)}[REDACTED]'
        if m.group(4):  # Bearer token
            return f'{m.group(4)}[REDACTED]'
        return '[REDACTED]'
    return _REDACT_PATTERNS.sub(_sub, url)

def _audit_sink(line):
    if not PROXY_AUDIT_LOG or PROXY_AUDIT_LOG.lower() == 'stderr':
        print(line, file=sys.stderr)
        return
    try:
        log_dir = os.path.dirname(PROXY_AUDIT_LOG)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(PROXY_AUDIT_LOG, 'a', encoding='utf-8') as handle:
            print(line, file=handle)
    except Exception:
        print(line, file=sys.stderr)

def _audit_log(route, method, url, extra=''):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    safe_url = _redact_url(url[:200] + '…' if len(url) > 200 else url)
    _audit_sink(f'[proxy-audit] {ts} {route} {method} {safe_url} {extra}')

def _should_bypass_explicit_proxy(url):
    host = (urlparse(url).hostname or '').strip('[]').lower()
    raw = os.environ.get('NO_PROXY') or os.environ.get('no_proxy') or ''
    if not host or not raw:
        return False
    for entry in (part.strip().lower().lstrip('.') for part in raw.split(',') if part.strip()):
        if entry == '*' or host == entry or host.endswith(f'.{entry}'):
            return True
    return False

def _is_brave_search_url(url):
    return (urlparse(url).hostname or '').strip('[]').lower() == BRAVE_SEARCH_HOST

def _is_searxng_search_url(url):
    if not SEARXNG_URL:
        return False
    parsed = urlparse(url)
    searxng_host = (urlparse(SEARXNG_URL).hostname or '').strip('[]').lower()
    return (
        bool(searxng_host)
        and (parsed.hostname or '').strip('[]').lower() == searxng_host
        and parsed.path.startswith('/search')
    )

# --- Monkey-patch urllib.request.urlopen ---
try:
    import urllib.request
    _orig_urlopen = urllib.request.urlopen

    def _patched_urlopen(url, *args, **kwargs):
        url_str = url if isinstance(url, str) else getattr(url, 'full_url', str(url))
        if _is_brave_search_url(url_str):
            from urllib.parse import urlparse, parse_qs, urlencode
            parsed = urlparse(url_str)
            params = parse_qs(parsed.query)
            q = params.get('q', [''])[0][:MAX_QUERY_LEN]
            searxng_target = f'{SEARXNG_URL}/search?{urlencode({"q": q, "format": "json"})}'
            _audit_log('BRAVE→SEARXNG', 'GET', searxng_target, f'(original: {url_str[:100]})')
            return _orig_urlopen(searxng_target, *args, **kwargs)
        _audit_log('URLOPEN', 'GET', url_str)
        return _orig_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = _patched_urlopen
except Exception:
    pass

# --- Monkey-patch requests.Session.send ---
try:
    import requests as _req
    _orig_send = _req.Session.send

    # --- CDP URL rewriting helpers ---
    # browserless /json/version returns webSocketDebuggerUrl: ws://0.0.0.0:3000/
    # which is unreachable from other Docker containers.
    _CDP_BASE = os.environ.get('BROWSER_CDP_URL', '').strip()
    _CDP_LOCAL_HOSTS = {'0.0.0.0', 'localhost', '127.0.0.1', '::1'}
    _cdp_base_parsed = None
    if _CDP_BASE:
        from urllib.parse import urlparse as _urlparse_cdp
        _cdp_base_parsed = _urlparse_cdp(_CDP_BASE)

    def _rewrite_cdp_ws(ws_url):
        """Rewrite ws://0.0.0.0:… → ws://browserless:… using BROWSER_CDP_URL.
        Also copies the token query param from BROWSER_CDP_URL if not present."""
        if not ws_url or not _cdp_base_parsed:
            return ws_url
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
        parsed = urlparse(ws_url)
        if parsed.hostname not in _CDP_LOCAL_HOSTS:
            return ws_url
        netloc = f'{_cdp_base_parsed.hostname}:{_cdp_base_parsed.port}' if _cdp_base_parsed.port else _cdp_base_parsed.hostname
        # Merge token from BROWSER_CDP_URL into the rewritten URL
        base_params = parse_qs(_cdp_base_parsed.query)
        ws_params = parse_qs(parsed.query)
        if 'token' in base_params and 'token' not in ws_params:
            ws_params['token'] = base_params['token']
        new_query = urlencode({k: v[0] for k, v in ws_params.items()})
        rewritten = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        if rewritten != ws_url:
            _audit_log('CDP-REWRITE', 'WS', rewritten, f'(was: {ws_url})')
        return rewritten

    def _patched_send(self, request, **kwargs):
        url = request.url or ''
        if _is_brave_search_url(url):
            from urllib.parse import urlparse, parse_qs, urlencode
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            q = params.get('q', [''])[0][:MAX_QUERY_LEN]
            request.url = f'{SEARXNG_URL}/search?{urlencode({"q": q, "format": "json"})}'
            request.headers.pop('X-Subscription-Token', None)
            _audit_log('BRAVE→SEARXNG', request.method, request.url)
        elif _is_searxng_search_url(url):
            _audit_log('DIRECT-SEARXNG', request.method, url, '⚠️ direct access detected')
        else:
            _audit_log('SEND', request.method, url)

        response = _orig_send(self, request, **kwargs)

        # Rewrite webSocketDebuggerUrl in /json/version responses
        if _cdp_base_parsed and '/json/version' in url:
            try:
                data = response.json()
                ws = data.get('webSocketDebuggerUrl', '')
                rewritten = _rewrite_cdp_ws(ws)
                if rewritten != ws:
                    data['webSocketDebuggerUrl'] = rewritten
                    new_body = json.dumps(data).encode('utf-8')
                    response._content = new_body
                    response.headers['Content-Length'] = str(len(new_body))
            except Exception:
                pass  # Not JSON or parse error — pass through unchanged

        return response

    _req.Session.send = _patched_send
except ImportError:
    pass  # requests not available at import time — that's ok

# --- Monkey-patch httpx if available (Hermes may use it) ---
try:
    import httpx
    _orig_httpx_send = httpx.Client.send
    _DIRECT_HTTPX_CLIENT = None
    _DIRECT_HTTPX_ASYNC_CLIENT = None

    def _get_direct_httpx_client():
        global _DIRECT_HTTPX_CLIENT
        if _DIRECT_HTTPX_CLIENT is None:
            _DIRECT_HTTPX_CLIENT = httpx.Client(trust_env=False)
        return _DIRECT_HTTPX_CLIENT

    def _get_direct_httpx_async_client():
        global _DIRECT_HTTPX_ASYNC_CLIENT
        if _DIRECT_HTTPX_ASYNC_CLIENT is None:
            _DIRECT_HTTPX_ASYNC_CLIENT = httpx.AsyncClient(trust_env=False)
        return _DIRECT_HTTPX_ASYNC_CLIENT

    def _patched_httpx_send(self, request, **kwargs):
        url = str(request.url)
        if _is_brave_search_url(url):
            from urllib.parse import urlparse, parse_qs, urlencode
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            q = params.get('q', [''])[0][:MAX_QUERY_LEN]
            new_url = f'{SEARXNG_URL}/search?{urlencode({"q": q, "format": "json"})}'
            request = httpx.Request(request.method, new_url, headers={
                k: v for k, v in request.headers.items()
                if k.lower() != 'x-subscription-token'
            })
            _audit_log('BRAVE→SEARXNG', request.method, str(request.url))
        elif _should_bypass_explicit_proxy(url):
            _audit_log('HTTPX-DIRECT', request.method, url)
            return _orig_httpx_send(_get_direct_httpx_client(), request, **kwargs)
        else:
            _audit_log('HTTPX', request.method, url)
        return _orig_httpx_send(self, request, **kwargs)

    httpx.Client.send = _patched_httpx_send

    # Also patch AsyncClient for async HTTP calls
    _orig_httpx_async_send = httpx.AsyncClient.send

    async def _patched_httpx_async_send(self, request, **kwargs):
        url = str(request.url)
        if _is_brave_search_url(url):
            from urllib.parse import urlparse, parse_qs, urlencode
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            q = params.get('q', [''])[0][:MAX_QUERY_LEN]
            new_url = f'{SEARXNG_URL}/search?{urlencode({"q": q, "format": "json"})}'
            request = httpx.Request(request.method, new_url, headers={
                k: v for k, v in request.headers.items()
                if k.lower() != 'x-subscription-token'
            })
            _audit_log('BRAVE→SEARXNG', request.method, str(request.url))
        elif _should_bypass_explicit_proxy(url):
            _audit_log('HTTPX-ASYNC-DIRECT', request.method, url)
            return await _orig_httpx_async_send(_get_direct_httpx_async_client(), request, **kwargs)
        else:
            _audit_log('HTTPX-ASYNC', request.method, url)
        return await _orig_httpx_async_send(self, request, **kwargs)

    httpx.AsyncClient.send = _patched_httpx_async_send
except ImportError:
    pass
