"""Provider opt-out settings, SQLite document ownership, and resolved admission."""
from __future__ import annotations

import hashlib
import json
import re
from functools import wraps
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from .util import json_error, stable_json

CHAT = "/v1/chat/completions"
RESPONSES = "/v1/responses"
GENERATIONS = "/v1/images/generations"
EDITS = "/v1/images/edits"
DECISIONS = "/v1/decisions"
REALTIME = "/v1/realtime"
MINT = "/v1/realtime/client_secrets"
SUPPORTED = {
    "openai": (CHAT, RESPONSES, GENERATIONS, EDITS, DECISIONS),
    "codex": (CHAT, RESPONSES, GENERATIONS, EDITS, REALTIME, MINT),
    "openrouter": (CHAT,),
    "ollama": (CHAT,),
}
MODEL_ID = re.compile(r"[A-Za-z0-9._:/-]{1,272}", re.ASCII)
EDITABLE = {"enabled", "rejectEndpoints", "rejectModels"}


def canonical_model(provider: str, model: str) -> str:
    """The caller resolves aliases first. Prefixes are identities, not patterns."""
    for prefix in ("custom-1/", "spartan-route/"):
        model = model.removeprefix(prefix)
    return model if model.startswith(provider + "/") else provider + "/" + model


def parse_rule(provider: str, value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != EDITABLE or type(value["enabled"]) is not bool:
        raise ValueError("Each provider requires enabled, rejectEndpoints and rejectModels.")
    endpoints, models = value["rejectEndpoints"], value["rejectModels"]
    if not isinstance(endpoints, list) or any(not isinstance(e, str) or e not in SUPPORTED[provider] for e in endpoints):
        raise ValueError("Reject endpoints must be supported by this provider.")
    return {"enabled": value["enabled"], "rejectEndpoints": sorted(set(endpoints)), "rejectModels": parse_models(provider, models)}


def parse_models(provider: str, models: Any) -> list[str]:
    if not isinstance(models, list):
        raise ValueError("rejectModels must be a list of exact model IDs.")
    canonical = []
    for model in models:
        if (not isinstance(model, str) or not MODEL_ID.fullmatch(model)
                or any(p in {"", ".", ".."} for p in model.split("/"))):
            raise ValueError("Reject models must be exact model IDs, without patterns.")
        prefix = model.split("/", 1)[0]
        if prefix in SUPPORTED and prefix != provider:
            raise ValueError("Reject model belongs to another provider.")
        model = canonical_model(provider, model)
        if model.split("/", 1)[0] != provider:
            raise ValueError("Reject model belongs to another provider.")
        canonical.append(model)
    return sorted(set(canonical))


def parse_providers(value: Any) -> dict[str, dict]:
    if not isinstance(value, dict) or not set(value) <= set(SUPPORTED):
        raise ValueError("providers must contain only implemented providers.")
    return {provider: parse_rule(provider, rule) for provider, rule in value.items()}


def defaults() -> dict[str, dict]:
    return {p: {"enabled": True, "rejectEndpoints": [], "rejectModels": []} for p in SUPPORTED}


class ProviderPolicyStore:
    """Reuse TelemetryStore's connection, lock, and commit-before-publication pattern."""
    def __init__(self, connection, lock):
        self.connection, self.lock = connection, lock
        with lock, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS provider_config (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), document TEXT NOT NULL)")

    def read(self) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT document FROM provider_config WHERE singleton = 1").fetchone()
        if row is None:
            return None
        document = json.loads(row["document"])
        if not isinstance(document, dict):
            raise ValueError("Invalid stored provider document")
        return document

    def replace(self, providers: dict[str, dict]) -> str:
        providers = parse_providers(providers)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        document = stable_json({"version": 1, "providers": providers, "updatedAt": updated_at})
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("INSERT OR REPLACE INTO provider_config VALUES (1, ?)", (document,))
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return updated_at


def initialize_store(telemetry) -> ProviderPolicyStore:
    """Attach provider storage when the application enables Providers, not telemetry.

    Telemetry retains connection lifetime ownership. The provider module owns
    schema initialization and the attached store used by its configuration API.
    """
    telemetry.provider_settings = ProviderPolicyStore(telemetry._conn, telemetry._lock)
    return telemetry.provider_settings


@dataclass(frozen=True)
class ProviderPolicy:
    providers: dict[str, dict] | None = None
    updated_at: str | None = None
    unavailable: bool = False

    @classmethod
    def load(cls, store: ProviderPolicyStore) -> ProviderPolicy:
        try:
            document = store.read()
            if document is None:
                return cls()
            if (not isinstance(document, dict) or set(document) != {"version", "providers", "updatedAt"}
                    or type(document["version"]) is not int or document["version"] != 1
                    or not isinstance(document["updatedAt"], str) or not document["updatedAt"]):
                raise ValueError("Invalid stored provider document")
            return cls(parse_providers(document["providers"]), document["updatedAt"])
        except Exception:
            # Absence preserves traffic. Corruption never silently discards restrictions.
            return cls(unavailable=True)

    def effective(self) -> dict[str, dict]:
        return {**defaults(), **(self.providers or {})}

    def denial(self, provider: str, endpoint: str, model: str) -> str | None:
        if self.unavailable:
            return "provider_policy_unavailable"
        rule = self.effective().get(provider)
        if rule is None:
            return None
        if not rule["enabled"]:
            return "provider_disabled"
        if endpoint in rule["rejectEndpoints"]:
            return "provider_endpoint_rejected"
        if canonical_model(provider, model) in rule["rejectModels"]:
            return "provider_model_rejected"
        return None

    def merge(self, body: dict) -> dict[str, dict]:
        if set(body) != {"providers"}:
            raise ValueError("Configuration must contain only providers.")
        return {**self.effective(), **parse_providers(body["providers"])}


def admission_error(state, provider: str, endpoint: str, model: str, *, auto: bool = False) -> web.Response | None:
    code = state.provider_policy.denial(provider, endpoint, model)
    if code is None:
        return None
    status = 503 if code == "provider_policy_unavailable" or auto else 403
    if auto and code != "provider_policy_unavailable":
        code = "auto_target_not_eligible"
    message = ("Stored provider configuration is unavailable; repair it before admitting new requests."
               if code == "provider_policy_unavailable" else
               f"Provider policy denies {canonical_model(provider, model)} for {endpoint}."
               + (" The configured auto target is not eligible; select another target." if auto else ""))
    return web.json_response(json_error(message, status, code, "invalid_request_error")[0], status=status)


def credential_status(state, provider: str) -> str:
    if provider == "ollama":
        return "not_required"
    if provider == "codex":
        return "configured" if state.codex and getattr(state.codex, "slots", ()) else "missing_credentials"
    return "configured" if state.config.api_key(provider) else "missing_credentials"


def config_document(state) -> dict:
    from .codex import codex_image_host_model
    from .realtime import MODEL
    entries = {}
    builtin = {"openai": {"openai/gpt-6-luna", "openai/gpt-image-2"},
               "codex": {"codex/gpt-image-2", canonical_model("codex", codex_image_host_model()), canonical_model("codex", MODEL)}}
    for provider, rule in state.provider_policy.effective().items():
        models = {canonical_model(provider, e.id) for e in state.config.models.values() if e.provider == provider}
        entries[provider] = {**rule, "supportedEndpoints": list(SUPPORTED[provider]),
                             "models": sorted(models | builtin.get(provider, set())),
                             "credentialStatus": credential_status(state, provider)}
    data = {"providers": entries, "configured": state.provider_policy.providers is not None,
            "updatedAt": state.provider_policy.updated_at}
    data["etag"] = '"' + hashlib.sha256(stable_json(data).encode()).hexdigest() + '"'
    return data


def config_response(state) -> web.Response:
    if state.provider_policy.unavailable:
        return admission_error(state, "openai", DECISIONS, "gpt-6-luna")
    data = config_document(state)
    return web.json_response(data, headers={"ETag": data["etag"], "Cache-Control": "no-store"})


def config_error(state, message: str, status: int, code: str) -> web.Response:
    return web.json_response(json_error(message, status, code)[0], status=status,
                             headers={"ETag": config_document(state)["etag"], "Cache-Control": "no-store"})


def register(app: web.Application, state_key, read_body) -> None:
    state = app[state_key]
    state.provider_policy = ProviderPolicy.load(initialize_store(state.telemetry))

    async def get(request):
        return config_response(state)

    async def put(request):
        if state.provider_policy.unavailable:
            return config_response(state)
        if not request.headers.get("If-Match"):
            return config_error(state, "If-Match is required for configuration updates.", 428, "config_precondition_required")
        body = await read_body(request)
        if isinstance(body, web.Response):
            return body
        async with state.routing_policy_lock:
            if request.headers["If-Match"] != config_document(state)["etag"]:
                return config_error(state, "Provider configuration changed; reload before saving.", 412, "config_revision_conflict")
            try:
                providers = state.provider_policy.merge(body)
            except ValueError as exc:
                return config_error(state, str(exc), 400, "invalid_provider_config")
            try:
                updated = state.telemetry.provider_settings.replace(providers)
            except Exception:
                state.telemetry.record_health_failure("write", "provider_config")
                return config_error(state, "Provider configuration could not be persisted.", 503, "config_persistence_failed")
            state.telemetry.record_health_success("write", "provider_config")
            state.provider_policy = ProviderPolicy(providers, updated)
        return config_response(state)

    app.router.add_get("/api/config/providers", get)
    app.router.add_put("/api/config/providers", put)


def image_admission_error(state, endpoint: str, model: str) -> web.Response | None:
    from .codex import codex_image_host_model
    if model.startswith("openai/"):
        error = admission_error(state, "openai", endpoint, model)
        if error is not None:
            return error
    provider = "codex" if model.startswith("codex/") or not state.config.api_key("openai") else "openai"
    # The subscription image tool ignores a requested image ID. Enforce its
    # actual image identity and the host model used by _build_codex_image_body.
    actual = "gpt-image-2" if provider == "codex" else model
    error = admission_error(state, provider, endpoint, actual)
    if error is None and provider == "codex":
        error = admission_error(state, provider, endpoint, codex_image_host_model())
    return error


def model_payload(state, entry, base_model: str | None = None) -> dict:
    from .pi_responses import model_payload as native_payload
    payload = native_payload(entry, state.codex_catalog, base_model)
    model = base_model or entry.id
    payload["api_modes"] = [mode for mode in payload["api_modes"]
                            if not state.provider_policy.denial(entry.provider, "/v1/" + mode.replace("_", "/"), model)]
    payload["enabled"] = bool(entry.enabled and payload["api_modes"])
    return payload


def auto_capabilities(state) -> dict:
    from .pi_responses import auto_capabilities as native_auto, auto_entries
    payload = native_auto(state)
    entries = auto_entries(state)
    payload["api_modes"] = [mode for mode in payload["api_modes"]
                            if all(entry is not None and not state.provider_policy.denial(
                                entry.provider, "/v1/" + mode.replace("_", "/"), entry.id) for entry in entries)]
    payload["enabled"] = bool(payload["api_modes"])
    return payload


def realtime_payload(state) -> dict:
    from .realtime import public_model, MODEL
    payload = public_model()
    payload["enabled"] = not (state.provider_policy.denial("codex", REALTIME, MODEL)
                              or state.provider_policy.denial("codex", MINT, MODEL))
    return payload


def require_admission(handler):
    """Resolve the same request identity before a handler can dispatch upstream.

    aiohttp caches JSON and multipart reads. Saving settings does not revisit
    this check, so work admitted under an earlier snapshot continues normally.
    """
    @wraps(handler)
    async def admitted(request):
        from .app import get_state, read_json_object, route_request, responses_body_to_chat, normalize_model_id, InvalidContextVariant
        state = get_state(request)
        try:
            body = await request.post() if request.path == EDITS else await read_json_object(request)
        except Exception:
            return await handler(request)  # Preserve the owner's malformed-multipart response.
        if isinstance(body, web.Response):
            return body
        if request.path in {GENERATIONS, EDITS}:
            error = image_admission_error(state, request.path, normalize_model_id(body.get("model") or "gpt-image-2"))
        else:
            routing = state.routing_policy
            chat = responses_body_to_chat(body) if request.path == RESPONSES else body
            try:
                route = route_request(state.config, chat, routing.tier_models if routing else None,
                                      state.model_variants.snapshot(state.config, reserved_ids=state.variant_reserved_ids)[1])
            except InvalidContextVariant:
                return await handler(request)  # The owner supplies its model-not-found response.
            error = admission_error(state, route.provider, request.path, route.actual_model,
                                    auto=normalize_model_id(body.get("model")) in {"auto", "clawroute/auto"})
            if error is None and route.tier != "passthrough" and request.path not in SUPPORTED.get(route.provider, ()):
                error = config_error(state, f"The configured auto target does not support {request.path}.", 503, "auto_target_not_eligible")
        return error if error is not None else await handler(request)
    return admitted
