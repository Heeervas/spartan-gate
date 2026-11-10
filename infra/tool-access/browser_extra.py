"""Strict contracts for additional managed-browser operations, not a proxy API."""
import math
import re
from urllib.parse import urlsplit

from policy import AccessError

FIELDS = {
    'viewport': {'tabId', 'width', 'height'},
    'back': {'tabId'}, 'forward': {'tabId'}, 'refresh': {'tabId'},
    'press': {'tabId', 'key'}, 'select': {'tabId', 'ref', 'selector', 'option'},
    'links': {'tabId', 'offset', 'limit'}, 'images': {'tabId', 'limit'},
    'extract': {'tabId', 'fields'},
    'file_stage': {'path'}, 'file_remove': {'file_id'},
    'upload': {'tabId', 'file_ids', 'ref', 'selector'},
}
READ_ACTIONS = {'tabs', 'snapshot', 'screenshot', 'links', 'images', 'extract'}
KEYS = {'Enter', 'Tab', 'Escape', 'Backspace', 'Delete', 'Space', 'ArrowUp',
        'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp',
        'PageDown', 'Control+a', 'Meta+a', 'Shift+Tab'}
TYPES = {'string', 'number', 'integer', 'boolean'}


def integer(data, key, default, minimum, maximum):
    value = data.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise AccessError('invalid_request')
    return value


def bounded_text(data, key, limit, *, empty=False):
    value = data.get(key)
    if not isinstance(value, str) or len(value) > limit or (not empty and not value):
        raise AccessError('invalid_request')
    return value


def target(data):
    keys = [key for key in ('ref', 'selector') if key in data]
    if len(keys) != 1:
        raise AccessError('invalid_request')
    return {keys[0]: bounded_text(data, keys[0], 2048)}


def extraction_fields(data):
    fields = data.get('fields')
    if not isinstance(fields, list) or not 1 <= len(fields) <= 32:
        raise AccessError('invalid_request')
    properties = {}
    for field in fields:
        if not isinstance(field, dict) or set(field) != {'name', 'ref', 'type'}:
            raise AccessError('invalid_request')
        name, ref, kind = field['name'], field['ref'], field['type']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name)
                or name in properties or not isinstance(ref, str)
                or not re.fullmatch(r'e[0-9]{1,8}', ref) or not isinstance(kind, str) or kind not in TYPES):
            raise AccessError('invalid_request')
        properties[name] = {'type': kind, 'x-ref': ref}
    return {'type': 'object', 'properties': properties}


def arguments(data, action):
    if action == 'viewport':
        return {'width': integer(data, 'width', None, 100, 4096),
                'height': integer(data, 'height', None, 100, 4096),
                'relaunch': False, 'reload': False, 'timeoutMs': 10000}
    if action == 'press':
        if not isinstance(data.get('key'), str) or data['key'] not in KEYS:
            raise AccessError('invalid_request')
        return {'key': data['key']}
    if action == 'select':
        return {**target(data), 'option': bounded_text(data, 'option', 2048)}
    if action == 'links':
        return {'offset': integer(data, 'offset', 0, 0, 1000000),
                'limit': integer(data, 'limit', 20, 1, 50)}
    if action == 'images':
        return {'limit': integer(data, 'limit', 8, 1, 20), 'includeData': 'false'}
    if action == 'extract':
        return {'schema': extraction_fields(data)}
    if action == 'upload':
        return {**target(data), 'file_ids': data.get('file_ids')}
    if action == 'file_stage':
        return {'path': bounded_text(data, 'path', 1024)}
    if action == 'file_remove':
        return {'file_id': bounded_text(data, 'file_id', 32)}
    return {}


def safe_url(value):
    if not isinstance(value, str) or len(value.encode()) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def metadata_result(data, action):
    rows = data.get(action)
    if not isinstance(rows, list) or len(rows) > (50 if action == 'links' else 20):
        raise AccessError('upstream_error')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise AccessError('upstream_error')
        url = safe_url(row.get('url', row.get('src')))
        if url:
            label = row.get('text' if action == 'links' else 'alt', '')
            result.append({'url': url, 'text': label[:300] if isinstance(label, str) else ''})
    output = {action: result}
    if action == 'links':
        pagination = data.get('pagination', {})
        if type(pagination.get('hasMore')) is not bool:
            raise AccessError('upstream_error')
        output['hasMore'] = pagination['hasMore']
    return output


def viewport_result(data, outgoing):
    dimensions = data.get('effective', {})
    effective = {}
    for key, expected in (('innerWidth', outgoing['width']), ('innerHeight', outgoing['height'])):
        value = dimensions.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise AccessError('upstream_error')
        if abs(value - expected) > 2:
            raise AccessError('viewport_mismatch')
        effective[key] = value
    if data.get('ok') is not True or data.get('strategy') != 'pageViewport':
        raise AccessError('upstream_error')
    return {'ok': True, 'requested': {key: outgoing[key] for key in ('width', 'height')},
            'effective': effective, 'strategy': 'pageViewport'}


def primitive(value, kind):
    if value is None:
        return True
    if kind == 'string':
        return isinstance(value, str) and len(value) <= 4096
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'integer':
        return type(value) is int
    return type(value) in (float, int) and abs(value) < 1e308 and math.isfinite(value)


def extract_result(data, outgoing):
    values = data.get('data')
    properties = outgoing['schema']['properties']
    if data.get('ok') is not True or not isinstance(values, dict) or set(values) != set(properties):
        raise AccessError('upstream_error')
    if any(not primitive(value, properties[name]['type']) for name, value in values.items()):
        raise AccessError('upstream_error')
    return {'data': values}


def result(data, action, outgoing):
    if action in ('links', 'images'):
        return metadata_result(data, action)
    if action == 'viewport':
        return viewport_result(data, outgoing)
    if action == 'extract':
        return extract_result(data, outgoing)
    if data.get('ok') is not True:
        raise AccessError('upstream_error')
    return {'ok': True}
