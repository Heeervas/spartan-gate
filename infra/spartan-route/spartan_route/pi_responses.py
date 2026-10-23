"""Native Pi protocol capabilities and domain-separated reasoning bindings."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from typing import Any

from aiohttp import web

from .config import AUTO_ROUTING_TIERS
from .pi_usage_store import session_uuid

REALMS = {"codex": "codex_subscription", "openai": "openai_api"}
HEX64 = re.compile(r"[0-9a-f]{64}")
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def model_capabilities(entry: Any) -> dict[str, Any]:
    realm = REALMS.get(entry.provider) if entry.tool_capable else None
    if realm is None:
        return {"api_modes": ["chat_completions"]}
    return {"api_modes": ["chat_completions", "responses"], "reasoning_realm": realm}


def supported_efforts(model_ids: list[str], catalog: Any) -> list[str]:
    if catalog is None:
        return []
    return [effort for effort in EFFORTS if all(catalog.supports_effort(model, effort) for model in model_ids)]


def model_payload(entry: Any, catalog: Any = None, base_model: str | None = None) -> dict[str, Any]:
    payload = {
        "id": entry.id, "object": "model", "created": 1_700_000_000,
        "owned_by": entry.provider, "max_context": entry.max_context,
        "context_length": entry.max_context, "max_model_len": entry.max_context,
        "tool_capable": entry.tool_capable, "multimodal": entry.multimodal,
        **model_capabilities(entry),
    }
    if entry.provider == "codex":
        payload["reasoning_efforts"] = supported_efforts([base_model or entry.id], catalog)
    return payload


def auto_entries(state: Any) -> list[Any]:
    """Resolve the same active policy choices used by ordinary auto routing."""
    config = state.config
    choices = {config.baseline_model}
    if config.enabled and not config.dry_run:
        overrides = state.routing_policy.tier_models if state.routing_policy else {}
        for tier in AUTO_ROUTING_TIERS:
            choices.add(overrides.get(tier) or config.tier_models.get(tier, {}).get("primary") or config.baseline_model)
    return [config.models.get(model) for model in choices]


def auto_capabilities(state: Any) -> dict[str, Any]:
    entries = auto_entries(state)
    if any(entry is None or not entry.enabled or not entry.tool_capable for entry in entries):
        return {"api_modes": ["chat_completions"]}
    realms = {REALMS.get(entry.provider) for entry in entries}
    if len(realms) != 1 or None in realms:
        return {"api_modes": ["chat_completions"]}
    realm = realms.pop()
    effort_metadata = ({"reasoning_efforts": supported_efforts([entry.id for entry in entries], state.codex_catalog)}
                       if realm == "codex_subscription" else {})
    return {"api_modes": ["chat_completions", "responses"], "reasoning_realm": realm,
            **effort_metadata,
            "max_context": min(entry.max_context for entry in entries),
            "context_length": min(entry.max_context for entry in entries),
            "max_model_len": min(entry.max_context for entry in entries)}


def request_binding(body: Any) -> dict[str, str]:
    if not isinstance(body, dict) or set(body) != {"scope", "sessionId", "realm"}:
        raise ValueError("Invalid binding request")
    scope, realm = body["scope"], body["realm"]
    if not isinstance(scope, str) or not HEX64.fullmatch(scope) or realm not in REALMS.values():
        raise ValueError("Invalid binding scope or realm")
    session = session_uuid(body["sessionId"])
    secret_hex = os.environ.get("SPARTAN_REASONING_BINDING_SECRET", "").strip().lower()
    if not HEX64.fullmatch(secret_hex):
        raise RuntimeError("Binding unavailable")
    secret = bytes.fromhex(secret_hex)
    chain = hmac.new(secret, f"pi\0{scope}\0{session}".encode(), hashlib.sha256).hexdigest()
    mac = hmac.new(secret, f"{chain}\0{realm}".encode(), hashlib.sha256).hexdigest()
    return {"spartan_chain_id": chain, "spartan_expected_realm": realm, "spartan_binding_mac": mac}


def register_routes(app: web.Application, get_state: Any) -> None:
    async def issue(request: web.Request) -> web.Response:
        try:
            binding = request_binding(await request.json())
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": {"code": "reasoning_binding_invalid"}}, status=400)
        except RuntimeError:
            return web.json_response({"error": {"code": "reasoning_binding_unavailable"}}, status=503)
        try:
            bound = await asyncio.to_thread(get_state(request).telemetry.bind_reasoning_chain,
                                            binding["spartan_chain_id"], binding["spartan_expected_realm"])
        except Exception:  # noqa: BLE001 - no storage details cross the API
            return web.json_response({"error": {"code": "reasoning_binding_unavailable"}}, status=503)
        if not bound:
            return web.json_response({"error": {"code": "reasoning_realm_mismatch"}}, status=409)
        return web.json_response({"version": 1, "binding": binding})

    app.router.add_post("/api/pi/reasoning-binding", issue)
