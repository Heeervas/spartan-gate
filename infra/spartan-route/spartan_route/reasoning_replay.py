"""Opaque Codex replay receipts; legacy chains retain their cooperative contract.

Possession of a receipt is a replay capability, not a client identity. The root
secret is shared with legacy Hermes: integrity assumes uncompromised holders.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
from typing import Any

REALM = "codex_subscription"
REALMS = {REALM, "openai_api"}
FIELDS = ("spartan_chain_id", "spartan_expected_realm", "spartan_binding_mac")
PREFIX = "sgr:"
MAX_RECEIPT_BYTES = 64 * 1024 * 1024  # Existing HTTP request-body ceiling.
HEX = re.compile(r"[0-9a-f]{64}")
CURRENT: ContextVar[ReplayPolicy | None] = ContextVar("reasoning_replay", default=None)


class ReplayError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self), "type": "invalid_request_error"}}


def secret() -> bytes:
    value = os.environ.get("SPARTAN_REASONING_BINDING_SECRET", "").strip().lower()
    if not HEX.fullmatch(value):
        raise ReplayError("reasoning_binding_unavailable", "Reasoning signing is unavailable", 503)
    return bytes.fromhex(value)


def legacy_binding(store: Any, body: dict[str, Any], realm: str) -> bool:
    values = [body.get(field) for field in FIELDS]
    if all(value is None for value in values):
        return False
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ReplayError("reasoning_binding_invalid", "Incomplete reasoning binding")
    chain, expected, supplied = (value.strip().lower() for value in values)
    key = secret()
    if not (HEX.fullmatch(chain) and HEX.fullmatch(supplied) and expected in REALMS):
        raise ReplayError("reasoning_binding_invalid", "Invalid reasoning binding")
    calculated = hmac.new(key, f"{chain}\0{expected}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied):
        raise ReplayError("reasoning_binding_invalid", "Invalid reasoning binding")
    if expected != realm:
        raise ReplayError("reasoning_realm_mismatch", "Start a new session or branch for a different realm", 409)
    try:
        bound = store.bind_reasoning_chain(chain, realm)
    except Exception as exc:
        raise ReplayError("reasoning_binding_unavailable", "Reasoning binding store is unavailable", 503) from exc
    if not bound:
        raise ReplayError("reasoning_realm_mismatch", "Start a new session or branch for a different realm", 409)
    return True


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if len(value) > MAX_RECEIPT_BYTES or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid encoding")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise ValueError("Noncanonical encoding")
    return decoded


@dataclass(frozen=True)
class ReplayPolicy:
    key: bytes | None
    standard: bool
    scope: str | None = None

    def account_scope(self, auth: Any) -> str:
        identity = ("account", auth.account_key) if auth.account_key else ("credential", auth.access_token)
        return hmac.new(self.key, b"scope\0" + _json(identity), hashlib.sha256).hexdigest()

    def receipt(self, ciphertext: str, scope: str) -> str:
        if not isinstance(ciphertext, str) or ciphertext.startswith(PREFIX):
            raise ReplayError("reasoning_replay_invalid", "Invalid upstream opaque reasoning", 502)
        payload = _encode(_json([1, REALM, scope, ciphertext]))
        mac = hmac.new(self.key, b"receipt\0" + payload.encode("ascii"), hashlib.sha256).hexdigest()
        result = f"{PREFIX}1.{payload}.{mac}"
        if len(result) > MAX_RECEIPT_BYTES:
            raise ReplayError("reasoning_replay_too_large", "Opaque replay exceeds the request-body ceiling", 502)
        return result

    def unwrap(self, value: str, realm: str) -> tuple[str, str]:
        try:
            version, payload, mac = value.split(".")
            if version != PREFIX + "1" or not HEX.fullmatch(mac):
                raise ValueError("Invalid receipt")
            raw = _decode(payload)
            expected = hmac.new(self.key, b"receipt\0" + payload.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, mac):
                raise ValueError("Invalid MAC")
            data = json.loads(raw)
            _validate_payload(data, raw)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise ReplayError("reasoning_replay_invalid", "Invalid opaque replay receipt") from exc
        if data[1] != realm:
            raise ReplayError("reasoning_realm_mismatch", "Opaque reasoning belongs to a different realm", 409)
        return data[3], data[2]

    def output(self, payload: dict[str, Any], scope: str) -> dict[str, Any]:
        if not self.standard:
            return payload
        result = copy.deepcopy(payload)
        target = result.get("response", result)
        items = [result.get("item"), *target.get("output", [])]
        for item in items:
            if isinstance(item, dict) and item.get("type") == "reasoning" and item.get("encrypted_content"):
                item["encrypted_content"] = self.receipt(item["encrypted_content"], scope)
        return result


def _validate_payload(data: Any, raw: bytes) -> None:
    if not isinstance(data, list) or len(data) != 4 or _json(data) != raw:
        raise ValueError("Invalid payload")
    if type(data[0]) is not int or data[0] != 1 or data[1] != REALM:
        raise ValueError("Invalid version or realm")
    if not isinstance(data[2], str) or not HEX.fullmatch(data[2]):
        raise ValueError("Invalid scope")
    if not isinstance(data[3], str) or not data[3] or data[3].startswith(PREFIX):
        raise ValueError("Invalid ciphertext")


def policy(body: dict[str, Any], realm: str) -> ReplayPolicy:
    standard = realm == REALM and all(body.get(field) is None for field in FIELDS)
    items = body.get("input") if isinstance(body.get("input"), list) else []
    opaque = [i.get("encrypted_content") for i in items if isinstance(i, dict) and i.get("type") == "reasoning"]
    needs_key = standard or any(isinstance(value, str) and value.startswith(PREFIX) for value in opaque)
    key = hmac.new(secret(), b"spartan-route/replay/v1", hashlib.sha256).digest() if needs_key else None
    result = ReplayPolicy(key, standard)
    scopes = set()
    for value in filter(None, opaque):
        if isinstance(value, str) and value.startswith(PREFIX):
            _, scope = result.unwrap(value, realm)
            scopes.add(scope)
        elif standard:
            raise ReplayError("reasoning_binding_required", "Raw Codex reasoning requires a legacy binding; replay this endpoint's opaque output")
    if len(scopes) > 1:
        raise ReplayError("reasoning_replay_scope_mismatch", "Opaque history mixes upstream accounts", 409)
    return ReplayPolicy(key, standard, next(iter(scopes), None))


@contextmanager
def replay_request(body: dict[str, Any], realm: str):
    selected = policy(body, realm)
    prepared = copy.deepcopy(body)
    if isinstance(prepared.get("input"), list):
        for item in prepared["input"]:
            if isinstance(item, dict):
                value = item.get("encrypted_content")
                if item.get("type") == "reasoning" and isinstance(value, str) and value.startswith(PREFIX):
                    item["encrypted_content"] = selected.unwrap(value, realm)[0]
    token = CURRENT.set(selected)
    try:
        yield prepared
    finally:
        CURRENT.reset(token)


def allows_auth(auth: Any) -> bool:
    selected = CURRENT.get()
    return selected is None or selected.scope is None or selected.account_scope(auth) == selected.scope


def unavailable_error(error: dict[str, Any]) -> dict[str, Any]:
    selected = CURRENT.get()
    if selected is None or selected.scope is None:
        return error
    return {**error, "code": "reasoning_replay_account_unavailable",
            "message": "No eligible issuing-account slot for opaque replay; retry later or start with visible history."}


def output_transform(auth: Any):
    selected = CURRENT.get()
    if selected is None or not selected.standard:
        return lambda payload: payload
    scope = selected.account_scope(auth)
    return lambda payload: selected.output(payload, scope)
