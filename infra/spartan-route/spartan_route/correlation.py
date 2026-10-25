"""Bounded, client-asserted telemetry labels; never execution authority."""
from __future__ import annotations

import hashlib
import json
import re
from functools import wraps
from typing import Any

from aiohttp import web

HEADER = "X-Spartan-Correlation"
REQUEST_KEY = web.RequestKey("client_correlation", dict)
TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
REQUIRED = frozenset(("version", "client", "conversation", "purpose"))
IDS = frozenset(("conversation", "parent", "root", "operation"))
ALLOWED = REQUIRED | IDS


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def validate_document(document: Any) -> None:
    if not isinstance(document, dict) or not REQUIRED <= document.keys() <= ALLOWED:
        raise ValueError("invalid fields")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("invalid version")
    if document["client"] not in ("pi", "ultimate"):
        raise ValueError("invalid client")
    if document["purpose"] not in ("agent", "summary", "compaction", "other"):
        raise ValueError("invalid purpose")
    for key in IDS & document.keys():
        value = document[key]
        if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
            raise ValueError("invalid token")


def parse_correlation(headers: Any) -> dict[str, Any] | None:
    values = headers.getall(HEADER, [])
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("duplicate header")
    raw = values[0]
    if not raw.isascii() or len(raw) > 1024:
        raise ValueError("invalid envelope")
    document = json.loads(raw, object_pairs_hook=unique_object)
    validate_document(document)
    result = {"version": 1, "source": "client_asserted", "client": document["client"], "purpose": document["purpose"]}
    for key in IDS & document.keys():
        result[key] = hashlib.sha256(f"{document['client']}\0{document[key]}".encode("ascii")).hexdigest()[:16]
    return result


def require_correlation(handler: Any) -> Any:
    @wraps(handler)
    async def guarded(request: web.Request) -> web.StreamResponse:
        try:
            request[REQUEST_KEY] = parse_correlation(request.headers)
        except (ValueError, RecursionError):
            return web.json_response({"error": {"message": "Invalid correlation header", "code": "invalid_correlation",
                                                "type": "invalid_request_error"}}, status=400)
        return await handler(request)
    return guarded


def context_fields(correlation: dict[str, Any] | None, session_identity_source: str) -> dict[str, Any]:
    context = {"session_identity": {"version": 1, "source": session_identity_source}}
    if correlation is not None:
        context["correlation"] = correlation
    return context


def grouping_session(correlation: dict[str, Any] | None, fallback: str | None) -> str | None:
    return correlation["conversation"] if correlation is not None else fallback


def parse_session_filter(query: Any) -> str | None:
    values = query.getall("sessionId", [])
    if not values:
        return None
    if len(values) != 1 or TOKEN.fullmatch(values[0]) is None:
        raise ValueError("invalid session filter")
    return values[0]


def add_group_correlation(group: dict[str, Any], correlation: Any) -> None:
    # Selected rows arrive newest-first. Do not infer ancestry from cache keys,
    # signed replay chains or historical rows without this explicit envelope.
    group.setdefault("correlation", None)
    group.setdefault("purposes", [])
    if not isinstance(correlation, dict):
        return
    if group["correlation"] is None:
        group["correlation"] = correlation
    purpose = correlation.get("purpose")
    if purpose in ("agent", "summary", "compaction", "other"):
        group["purposes"] = sorted(set(group["purposes"]) | {purpose})
