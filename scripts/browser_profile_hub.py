"""Bounded native MCPHub API operations for the browser-profile provisioner.

Operator-only, not an MCP tool. No retries, caller-header forwarding or direct
settings writes. One-time keys stay disabled until their secret is durably saved.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 4 * 1024 * 1024


class HubError(RuntimeError):
    """Safe category only; never include a native response or credential."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Hub:
    def __init__(self, base, token=None):
        url = urllib.parse.urlsplit(base)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or not url.port or url.path not in ('', '/') or url.username
                or url.password or url.query or url.fragment):
            raise HubError('invalid_admin_origin')
        self.base, self.token = base.rstrip('/'), token
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        if not path.startswith('/api/') or '..' in path or '?' in path:
            raise HubError('invalid_api_path')
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['x-auth-token'] = self.token
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            if len(data) > LIMIT:
                raise HubError('request_too_large')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.http.open(req, timeout=30) as response:
                raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise HubError('response_too_large')
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('success') is not True:
                raise HubError('api_refused')
            return result.get('data', result)
        except urllib.error.HTTPError as error:
            raise HubError('api_http_' + str(error.code)) from None
        except (OSError, ValueError, TimeoutError):
            raise HubError('api_outcome_unconfirmed') from None

    def login(self, password):
        result = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': password})
        if not result.get('user', {}).get('isAdmin') or not isinstance(result.get('token'), str):
            raise HubError('admin_login_refused')
        self.token = result['token']

    def wait_server(self, name, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.request('GET', '/api/servers')
            row = next((item for item in rows if item['name'] == name), {})
            if row.get('status') == 'connected':
                return row
            if row.get('status') in ('error', 'failed'):
                raise HubError('backend_startup_failed')
            time.sleep(0.2)
        raise HubError('backend_startup_timeout')

    def settings(self):
        return self.request('GET', '/api/settings')

    def keys(self):
        result = self.request('GET', '/api/auth/keys')
        if not isinstance(result, list):
            raise HubError('invalid_key_list')
        return result

    def prepare_key(self, name, group_id, saved, persist):
        """Resume an owned disabled key; never create twice after a lost reply."""
        rows = [row for row in self.keys() if row['name'] == name]
        if len(rows) > 1:
            raise HubError('ambiguous_key_name')
        expected = {'name': name, 'group': group_id}
        if saved and any(saved.get(k) != v for k, v in expected.items()):
            raise HubError('key_intent_conflict')
        if not rows:
            if saved:
                raise HubError('key_creation_unconfirmed')
            saved = {**expected, 'phase': 'creating'}
            persist(saved)
            created = self.request('POST', '/api/auth/keys', {'name': name, 'enabled': False,
                'kind': 'system', 'accessType': 'groups', 'allowedGroups': [group_id], 'allowedServers': []})
            return self._save_secret(created, saved, persist)
        row = rows[0]
        if not saved or (saved.get('id') and saved['id'] != row['id']):
            raise HubError('foreign_key_collision')
        self._check_scope(row, group_id)
        if saved.get('token'):
            return saved
        if row.get('enabled') is not False:
            raise HubError('cannot_rotate_published_key')
        saved = {**saved, 'id': row['id'], 'phase': 'rotating'}
        persist(saved)
        rotated = self.request('POST', '/api/auth/keys/' + row['id'] + '/reset', {})
        return self._save_secret(rotated, saved, persist)

    @staticmethod
    def _check_scope(row, group_id):
        if (row.get('kind') != 'system' or row.get('accessType') != 'groups'
                or row.get('allowedGroups') != [group_id] or row.get('allowedServers')):
            raise HubError('key_scope_drift')

    @staticmethod
    def _save_secret(row, saved, persist):
        Hub._check_scope(row, saved['group'])
        if (row.get('enabled') is not False or row.get('name') != saved['name']
                or not re.fullmatch(r'[a-f0-9-]{36}', row.get('id', ''))):
            raise HubError('unexpected_created_key_state')
        token = row.get('token')
        if not isinstance(token, str) or not re.fullmatch(r'mcphub_[a-f0-9]{64}', token):
            raise HubError('one_time_secret_missing')
        result = {**saved, 'id': row['id'], 'token': token, 'phase': 'stored'}
        persist(result)
        return result
