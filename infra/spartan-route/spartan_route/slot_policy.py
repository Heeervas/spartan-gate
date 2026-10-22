"""Committed durable slot policy; hot-path reads never acquire the telemetry lock.

The Route process is the sole writer. External edits require restart; failed writes
retain the last committed snapshot. Invalid startup policy refuses startup.
"""
from __future__ import annotations

from typing import Any
from functools import wraps
from aiohttp import web


class SlotPolicyUnavailable(RuntimeError):
    pass


def decode_override(row: dict[str, Any]) -> dict[str, Any]:
    return {'slotIndex': int(row['slot_index']), 'routingEnabled': bool(row['routing_enabled']),
            'accountKey': row.get('account_key'), 'slotPathHash': row.get('slot_path_hash'),
            'authFingerprintHash': row.get('auth_fingerprint_hash'), 'updatedAt': row.get('updated_at')}


def load_policy(connection: Any, decode: Any) -> dict[int, dict[str, Any]]:
    rows = connection.execute('SELECT * FROM codex_slot_overrides ORDER BY slot_index').fetchall()
    if any(type(row['routing_enabled']) is not int or row['routing_enabled'] not in (0, 1) for row in rows):
        raise SlotPolicyUnavailable('invalid_slot_policy')
    return {int(row['slot_index']): decode(dict(row)) for row in rows}


def require_policy(handler: Any) -> Any:
    @wraps(handler)
    async def guarded(request: Any) -> Any:
        try:
            return await handler(request)
        except SlotPolicyUnavailable:
            return web.json_response({'error': {'code': 'slot_policy_unavailable',
                'type': 'server_error', 'message': 'Durable slot policy unavailable; no upstream dispatch permitted'}}, status=503)
    return guarded


def policy_snapshot(store: Any) -> dict[int, dict[str, Any]]:
    try:
        return {index: dict(row) for index, row in store._slot_policy.items()}
    except (AttributeError, TypeError):
        raise SlotPolicyUnavailable('slot_policy_unavailable') from None
