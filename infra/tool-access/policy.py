"""Fail-closed, uncached client policy for managed tool admission."""

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass

HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
POLICY_LIMIT = 64 * 1024


class AccessError(Exception):
    """Only fixed public error codes may cross the HTTP boundary."""

    STATUS = {"unauthorized": 401, "forbidden": 403, "invalid_request": 400,
              "capacity": 429, "upstream_error": 502, "unavailable": 503,
              "deadline": 504, "not_found": 404, "stale_workflow": 409,
              "stale_tab": 409, "remote_outcome_unknown": 502,
              "workflow_busy": 429, "unsupported_backend": 503,
              "egress_denied": 403, "reader_overloaded": 503,
              "remote_http_error": 502, "response_too_large": 422,
              "extraction_failed": 422, "needs_render": 422}

    def __init__(self, code, *, upstream_status=None):
        self.code = code if code in self.STATUS else "unavailable"
        self.status = self.STATUS[self.code]
        self.upstream_status = (upstream_status if self.code == "remote_http_error"
                                and type(upstream_status) is int and 300 <= upstream_status <= 599 else None)
        super().__init__(self.code)


@dataclass(frozen=True)
class Principal:
    principal: str
    browser_user: str
    fingerprint: str


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def strict_json(data):
    def invalid_constant(_value):
        raise ValueError("invalid constant")
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def validate_client(client):
    if not isinstance(client, dict) or set(client) != {
        "principal", "tokenSha256", "capabilities", "browserUser"
    }:
        raise ValueError("invalid client")
    for field in ("principal", "browserUser"):
        if not isinstance(client[field], str) or not IDENTITY.fullmatch(client[field]):
            raise ValueError("invalid identity")
    digest = client["tokenSha256"]
    if not isinstance(digest, str) or not HEX.fullmatch(digest):
        raise ValueError("invalid digest")
    caps = client["capabilities"]
    if not isinstance(caps, list) or not all(isinstance(cap, str) for cap in caps):
        raise ValueError("invalid capabilities")
    if len(caps) != len(set(caps)) or not set(caps) <= {"search", "reader", "browser"}:
        raise ValueError("invalid capabilities")


def validate_policy(data):
    if not isinstance(data, dict) or set(data) != {"version", "clients"}:
        raise ValueError("invalid policy")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("invalid version")
    clients = data["clients"]
    if not isinstance(clients, list) or len(clients) > 128:
        raise ValueError("invalid clients")
    seen = {field: set() for field in ("principal", "tokenSha256", "browserUser")}
    for client in clients:
        validate_client(client)
        for field, values in seen.items():
            if client[field] in values:
                raise ValueError("duplicate client binding")
            values.add(client[field])
    return clients


class Policy:
    def __init__(self, path):
        self.path = os.fspath(path)

    def load(self):
        # O_NONBLOCK avoids hanging on a FIFO before fstat rejects it.
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("invalid policy file")
            if info.st_size > POLICY_LIMIT:
                raise ValueError("policy too large")
            data = source.read(POLICY_LIMIT + 1)
        if len(data) > POLICY_LIMIT:
            raise ValueError("policy too large")
        return validate_policy(strict_json(data))

    def authenticate(self, authorization, capability):
        try:
            clients = self.load()
        except (OSError, ValueError, TypeError, RecursionError):
            raise AccessError("unauthorized") from None
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise AccessError("unauthorized")
        token = authorization[7:]
        if not HEX.fullmatch(token):
            raise AccessError("unauthorized")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        for client in clients:
            if not hmac.compare_digest(client["tokenSha256"], digest):
                continue
            if capability not in client["capabilities"]:
                raise AccessError("forbidden")
            # Includes all bindings so policy changes invalidate browser authority.
            binding = json.dumps(client, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(binding.encode()).hexdigest()
            return Principal(client["principal"], client["browserUser"], fingerprint)
        raise AccessError("unauthorized")


def authenticate(request, capability):
    if len(request.headers.getall("Authorization", [])) != 1:
        raise AccessError("unauthorized")
    return request.app["policy"].authenticate(request.headers["Authorization"], capability)
