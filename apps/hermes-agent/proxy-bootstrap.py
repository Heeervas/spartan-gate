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
PROXY_AUDIT_LOG = os.environ.get('SPARTAN_PROXY_AUDIT_LOG', 'stderr').strip()
MAX_QUERY_LEN = 500  # Prevent data exfiltration via search queries
BRAVE_SEARCH_HOST = 'api.search.brave.com'

def _redact_url(url):
    """Keep only the destination origin; paths and query keys can be secrets too."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme not in {'http', 'https', 'ws', 'wss'} or not host:
            return '[REDACTED]'
        host = f'[{host}]' if ':' in host else host
        port = f':{parsed.port}' if parsed.port is not None else ''
        origin = f'{parsed.scheme}://{host}{port}'
        return origin[:200] + '…' if len(origin) > 200 else origin
    except (TypeError, ValueError):
        return '[REDACTED]'

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

def _audit_log(route, method, url):
    # No free-text extras: original request URLs must never bypass minimization.
    event = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'route': route,
        'method': method,
        'url': _redact_url(url),
    }
    _audit_sink('[proxy-audit] ' + json.dumps(event, ensure_ascii=True))

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
            _audit_log('BRAVE→SEARXNG', 'GET', searxng_target)
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
            _audit_log('DIRECT-SEARXNG', request.method, url)
        else:
            _audit_log('SEND', request.method, url)

        return _orig_send(self, request, **kwargs)

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
