"""Pinned Camofox transport for native Hermes vault operations.

Only native build_fill_js expressions travel over the protected secret endpoint.
Transport failures never expose request bodies, response text or exception strings.
"""
from contextvars import ContextVar
from functools import wraps
import json
from urllib.parse import quote

import requests

_PIN = ContextVar('spartan_camofox_vault_pin', default=None)
_MAX_BODY = 100 * 1024
_MAX_RESULT = 256 * 1024


class VaultTransportError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def active():
    from tools.browser_camofox import is_camofox_mode
    return is_camofox_mode()


def failure(code):
    return {'success': False, 'error_type': code, 'error': code}


def _request(method, path, body=None):
    from tools.browser_camofox import _auth_headers, get_camofox_url
    encoded = None if body is None else json.dumps(body).encode('utf-8')
    if encoded is not None and len(encoded) > _MAX_BODY:
        raise VaultTransportError('vault_request_too_large')
    try:
        with requests.Session() as client:
            client.trust_env = False
            headers = _auth_headers()
            if encoded is not None:
                headers = {**headers, 'Content-Type': 'application/json'}
            with client.request(method, get_camofox_url() + path, headers=headers,
                                data=encoded, timeout=(5, 145), allow_redirects=False,
                                stream=True) as response:
                if response.status_code in (404, 410):
                    raise VaultTransportError('vault_tab_stale' if response.status_code == 404 else 'vault_evaluate_timeout')
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > _MAX_RESULT:
                        raise VaultTransportError('vault_invalid_response')
                    chunks.append(chunk)
                result = json.loads(b''.join(chunks))
                if not isinstance(result, dict):
                    raise VaultTransportError('vault_invalid_response')
                if response.status_code != 200:
                    codes = {400: 'invalid_request', 413: 'invalid_request',
                             401: 'unauthorized', 403: 'vault_scope_denied',
                             409: 'vault_trace_denied', 410: 'vault_evaluate_timeout',
                             500: 'vault_evaluate_failed', 503: 'vault_unavailable'}
                    code = codes.get(response.status_code)
                    if code and result == {'ok': False, 'error': code, 'error_type': code}:
                        raise VaultTransportError(code)
                    raise VaultTransportError('vault_transport_failed')
                return result
    except VaultTransportError:
        raise
    except Exception:
        raise VaultTransportError('vault_transport_failed') from None


def _identity(session):
    return tuple(session.get(k) for k in ('user_id', 'session_key', 'tab_id'))


def _check_pin(task_id):
    pin = _PIN.get()
    if pin is None or pin['task_id'] != (task_id or 'default'):
        raise VaultTransportError('vault_session_required')
    if pin.get('failure'):
        raise VaultTransportError(pin['failure'])
    from tools.browser_camofox import _sessions
    if _sessions.get(pin['task_id']) is not pin['source']:
        raise VaultTransportError('vault_session_changed')
    if _identity(pin['source']) != pin['identity']:
        raise VaultTransportError('vault_session_changed')
    return pin


def _invalidate(pin):
    # Never adopt/recreate during this operation. Explicit navigation is required.
    if _identity(pin['source']) == pin['identity']:
        pin['source']['tab_id'] = None
        pin['source']['adopt_existing_tab'] = False


def page_operation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not active():
            return fn(*args, **kwargs)
        # All native page functions receive task_id as their second parameter.
        task_id = kwargs.get('task_id', args[1] if len(args) > 1 else None) or 'default'
        if _PIN.get() is not None:
            try:
                _check_pin(task_id)
                return fn(*args, **kwargs)
            except VaultTransportError as exc:
                return json.dumps(failure(exc.code))
        token = None
        try:
            from tools.browser_camofox import _require_tab
            session, blocked = _require_tab(task_id, 'fill vault fields')
            if blocked:
                return json.dumps(failure('vault_session_required'))
            identity = _identity(session)
            if not all(isinstance(v, str) and v for v in identity):
                raise VaultTransportError('vault_session_required')
            if identity[1] == 'spartan-operator-login':
                raise VaultTransportError('vault_scope_denied')
            pin = {'task_id': task_id, 'source': session, 'identity': identity}
            token = _PIN.set(pin)
            capabilities = _request('GET', '/spartan/capabilities')
            if type(capabilities.get('vaultEvaluateVersion')) is not int or capabilities['vaultEvaluateVersion'] != 1:
                raise VaultTransportError('vault_unavailable')
            _check_pin(task_id)
            result = fn(*args, **kwargs)
            if pin.get('failure'):
                return json.dumps(failure(pin['failure']))
            _check_pin(task_id)
            return result
        except VaultTransportError as exc:
            if exc.code in ('vault_tab_stale', 'vault_evaluate_timeout') and token is not None:
                _invalidate(_PIN.get())
            return json.dumps(failure(exc.code))
        except Exception:
            return json.dumps(failure('vault_operation_failed'))
        finally:
            if token is not None:
                _PIN.reset(token)
    return wrapped


def evaluate(task_id, expression, *, secret=False):
    try:
        pin = _check_pin(task_id)
        user_id, session_key, tab_id = pin['identity']
        suffix = 'spartan/vault-evaluate' if secret else 'evaluate'
        result = _request('POST', '/tabs/' + quote(tab_id, safe='') + '/' + suffix,
                          {'userId': user_id, 'sessionKey': session_key, 'expression': expression})
        _check_pin(task_id)
        if result.get('ok') is not True or 'result' not in result:
            raise VaultTransportError('vault_invalid_response')
        value = result['result']
        if secret:
            if not isinstance(value, dict):
                raise VaultTransportError('vault_invalid_response')
            if value == {'refused': 'origin_changed'}:
                pass
            elif set(value) != {'filled'} or type(value['filled']) is not int or value['filled'] < 0:
                raise VaultTransportError('vault_invalid_response')
        return {'success': True, 'result': value}
    except VaultTransportError as exc:
        if _PIN.get() is not None:
            _PIN.get()['failure'] = exc.code
        if _PIN.get() is not None and (exc.code in ('vault_tab_stale', 'vault_evaluate_timeout') or
                (secret and exc.code in ('vault_transport_failed', 'vault_invalid_response', 'vault_evaluate_failed'))):
            _invalidate(_PIN.get())
        return failure(exc.code)


def validate_pin(task_id):
    if active():
        _check_pin(task_id)


def focus_current(task_id, origin):
    from tools.browser_vault_tool import _current_page_origin
    current = _current_page_origin(task_id)
    return current if current and (not origin or origin == current) else None


def otp_origin_allowed(backend, handle, origin):
    # Never resolve a manager's OTP before validating its exact saved origins.
    try:
        meta = backend.get_meta(handle)
        allowed = list(meta.allowed_origins) or ([str(meta.origin)] if meta.origin else [])
        return meta.kind == 'login' and origin in allowed
    except Exception:
        return False
