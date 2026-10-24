"""Local Caddy authentication and bounded model discovery for Pi; never reads OAuth."""
import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlsplit

# Credential parser retained from the existing Pi Route discovery gateway.
# Usage actions reuse it directly; never execute or source credential files.


def configuration():
    override = os.environ.get('PI_SPARTAN_ROUTE_CONFIG')
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    path = Path(override) if override else data / 'pi-spartan-route/config.json'
    return json.loads(path.read_text())


def usage_request(config, action, document):
    """JSON-only usage protocol; authentication never crosses stdout."""
    url = urlsplit(config['baseUrl'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Gateway requires a credential-free HTTPS URL')
    paths = {'status': '/api/pi/status', 'register': '/api/pi/sessions',
             'refresh': '/api/pi/usage/refresh', 'binding': '/api/pi/reasoning-binding'}
    path = paths[action]
    method = 'GET' if action == 'status' else 'POST'
    if method == 'GET':
        from urllib.parse import urlencode
        path += '?' + urlencode({'sessionId': document['sessionId']})
    body = None if method == 'GET' else json.dumps(document).encode()
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=10)
    try:
        connection.request(method, path, body=body, headers={
            'Authorization': auth_header(config), 'Accept': 'application/json',
            'Content-Type': 'application/json', 'User-Agent': 'pi-spartan-route-usage/1',
        })
        response = connection.getresponse()
        return usage_response(response, action)
    finally:
        connection.close()


def usage_response(response, action):
    raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError('Usage response exceeds size limit')
    result = json.loads(raw)
    if action == 'binding' and response.status in (400, 409, 503):
        error = result.get('error') if isinstance(result, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if code in ('reasoning_binding_invalid', 'reasoning_binding_unavailable', 'reasoning_realm_mismatch'):
            return {'version': 1, 'error': {'code': code}}
    if response.status not in (200, 201, 202):
        raise ValueError('Usage endpoint unavailable')
    if not isinstance(result, dict) or result.get('version') != 1:
        raise ValueError('Unsupported usage protocol')
    return result


def credentials(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        key, sep, raw = line.partition('=')
        key = key.strip().removeprefix('export ')
        if not sep or key not in ('CADDY_AUTH_USER', 'CADDY_AUTH_PASSWORD'):
            continue
        raw = raw.strip()
        if raw[:1] in ('"', "'"):
            parts = shlex.split(raw, comments=True)
            if len(parts) != 1:
                raise ValueError('Invalid Caddy credential syntax')
            value = parts[0]
        else:
            value = re.split(r'\s+#', raw, maxsplit=1)[0].rstrip()
        if not value or any(c in value for c in '\r\n\0') or '${' in value or '$(' in value:
            raise ValueError('Caddy credential is absent, malformed or requires interpolation')
        result[key] = value
    if set(result) != {'CADDY_AUTH_USER', 'CADDY_AUTH_PASSWORD'} or ':' in result['CADDY_AUTH_USER']:
        raise ValueError('Caddy credentials are missing or invalid')
    return result['CADDY_AUTH_USER'], result['CADDY_AUTH_PASSWORD']


def auth_header(config):
    user, password = credentials(config['credentialsFile'])
    return 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()


def catalog(config):
    url = urlsplit(config['baseUrl'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Gateway requires a credential-free HTTPS URL')
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=10)
    try:
        connection.request('GET', url.path.rstrip('/') + '/models', headers={
            'Authorization': auth_header(config), 'Accept': 'application/json',
            'User-Agent': 'pi-spartan-route-discovery/1',
        })
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Gateway discovery returned HTTP ' + str(response.status))
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Gateway catalog exceeds size limit')
        document = json.loads(raw)
        if not isinstance(document, dict) or not isinstance(document.get('data'), list) or len(document['data']) > 1024:
            raise ValueError('Invalid gateway model catalog')
        return document
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('header', 'catalog', 'check', 'status', 'register', 'refresh', 'binding'))
    args = parser.parse_args()
    try:
        config = configuration()
        if args.action == 'header':
            print(auth_header(config))
        elif args.action == 'catalog':
            print(json.dumps(catalog(config)))
        elif args.action == 'check':
            print(json.dumps({'ok': True, 'models': len(catalog(config)['data']), 'credentials': 'existing Caddy credentials'}))
        else:
            raw = sys.stdin.buffer.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError('Usage request exceeds size limit')
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError('Usage request must be an object')
            print(json.dumps(usage_request(config, args.action, document)))
    except Exception:
        # Exceptions from file/HTTP parsers may contain private values. Never echo them.
        print('Pi Spartan Route: authentication or discovery unavailable; check local configuration.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
