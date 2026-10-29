"""Subscription-only Realtime transport; Hermes owns playback and tool execution."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TraceConfig, WSMsgType, web
from aiohttp.client_exceptions import ClientError, WSServerHandshakeError
from aiohttp.helpers import get_env_proxy_for_url
from yarl import URL

MODEL = "gpt-realtime-2.1"
MINT_URL = "https://api.openai.com/v1/realtime/client_secrets"
WS_URL = "wss://api.openai.com/v1/realtime"
RT_KEY = web.AppKey("realtime", object)
CLIENT_KEY = web.AppKey("realtime_client", object)
VOICE_PATHS = frozenset({"/v1/realtime", "/v1/realtime/client_secrets", "/api/realtime/capabilities"})


class RealtimeError(Exception):
    def __init__(self, code: str, status: int = 400, *, limit: dict[str, Any] | None = None):
        super().__init__(code)
        self.code, self.status, self.limit = code, status, limit
        self.event_id: str | None = None

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"type": "realtime_error", "code": self.code,
                                 "message": self.code.replace("_", " ")}
        if self.limit is not None:
            error["limit"] = self.limit
        return {"error": error}


def bound(name: str, observed: int, configured: int, status: int = 413) -> None:
    if observed > configured:
        raise RealtimeError("realtime_limit_exceeded", status, limit={
            "name": name, "configured": configured, "observed": observed,
            "recovery": "reduce_request_or_wait_for_capacity"})


def finite(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class VoiceClient:
    id: str
    token_sha256: str = field(repr=False)
    slot: int


@dataclass(frozen=True)
class RealtimeConfig:
    enabled: bool = False
    clients: tuple[VoiceClient, ...] = ()
    model: str = MODEL
    # Conservative operational bounds, documented against probe measurements.
    max_sessions: int = 4
    per_client: int = 1
    per_account: int = 2
    frame_bytes: int = 1024 * 1024
    mint_bytes: int = 256 * 1024
    ticket_seconds: float = 30
    mint_seconds: float = 25
    handshake_seconds: float = 25
    send_seconds: float = 5
    idle_seconds: float = 1800
    session_seconds: float = 3600
    shutdown_seconds: float = 5

    def __post_init__(self) -> None:
        if self.model != MODEL:
            raise ValueError("unsupported_realtime_model")
        if self.enabled and not self.clients:
            raise ValueError("realtime_clients_required")
        ids, hashes = set(), set()
        for client in self.clients:
            if (not isinstance(client.id, str) or not isinstance(client.token_sha256, str)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", client.id)
                    or not re.fullmatch(r"[a-f0-9]{64}", client.token_sha256)
                    or type(client.slot) is not int or not 0 <= client.slot <= 1023
                    or client.id in ids or client.token_sha256 in hashes):
                raise ValueError("invalid_realtime_client")
            ids.add(client.id)
            hashes.add(client.token_sha256)
        if len(self.clients) > 64:
            raise ValueError("too_many_realtime_clients")
        for name, maximum in (("max_sessions", 16), ("per_client", 16), ("per_account", 16),
                              ("frame_bytes", 1024 * 1024), ("mint_bytes", 256 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError("invalid_realtime_limit")
        for name in ("ticket_seconds", "mint_seconds", "handshake_seconds", "send_seconds",
                     "idle_seconds", "session_seconds", "shutdown_seconds"):
            value = getattr(self, name)
            if not finite(value) or not 0 < value <= 3600:
                raise ValueError("invalid_realtime_timeout")

    @classmethod
    def from_env(cls, global_token: str | None, env: Mapping[str, str] | None = None) -> RealtimeConfig:
        env = os.environ if env is None else env
        enabled = env.get("SPARTAN_ROUTE_REALTIME_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("invalid_realtime_enabled")
        if enabled == "false":
            return cls()
        if env.get("SPARTAN_ROUTE_REALTIME_MODEL") != MODEL:
            raise ValueError("explicit_verified_realtime_model_required")
        path = env.get("SPARTAN_ROUTE_REALTIME_CLIENTS_FILE", "")
        if not path:
            raise ValueError("realtime_clients_file_required")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            raise ValueError("realtime_clients_file_unavailable") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65536:
                raise ValueError("unsafe_realtime_clients_file")
            with os.fdopen(os.dup(fd), encoding="utf-8") as handle:
                try:
                    body = strict_loads(handle.read(65537))
                except (ValueError, UnicodeError):
                    raise ValueError("invalid_realtime_clients_file") from None
        finally:
            os.close(fd)
        if not isinstance(body, dict) or set(body) != {"clients"} or not isinstance(body["clients"], list):
            raise ValueError("invalid_realtime_clients_file")
        try:
            clients = tuple(VoiceClient(**entry) for entry in body["clients"])
        except (TypeError, ValueError):
            raise ValueError("invalid_realtime_clients_file") from None
        if global_token and any(hmac.compare_digest(c.token_sha256, digest(global_token)) for c in clients):
            raise ValueError("realtime_requires_independent_client_credential")
        return cls(enabled=True, clients=clients)

    def client(self, authorization: str) -> VoiceClient:
        token = bearer(authorization)
        fingerprint = digest(token)
        for client in self.clients:
            if hmac.compare_digest(client.token_sha256, fingerprint):
                return client
        raise RealtimeError("voice_credential_required", 401)


def bearer(value: str) -> str:
    if not value.lower().startswith("bearer "):
        raise RealtimeError("voice_credential_required", 401)
    token = value[7:]
    if not 32 <= len(token) <= 512 or not token.isascii() or any(c.isspace() for c in token):
        raise RealtimeError("voice_credential_required", 401)
    return token


def object_keys(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise RealtimeError("unsupported_realtime_configuration")
    return value


def validate_tools(tools: Any) -> None:
    if not isinstance(tools, list):
        raise RealtimeError("function_tools_only")
    bound("tools", len(tools), 128)
    for tool in tools:
        value = object_keys(tool, {"type", "name", "description", "parameters", "strict"})
        if (value.get("type") != "function" or not isinstance(value.get("name"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value["name"])):
            raise RealtimeError("function_tools_only")
        if "parameters" in value and not isinstance(value["parameters"], dict):
            raise RealtimeError("invalid_function_schema")


def validate_session(value: Any, *, mint: bool = False, response: bool = False) -> None:
    common = {"instructions", "tools", "tool_choice", "output_modalities", "max_output_tokens", "audio"}
    allowed = common | ({"conversation", "metadata", "input"} if response else {"type", "model", "reasoning", "truncation"})
    obj = object_keys(value, allowed)
    if mint and (obj.get("type") != "realtime" or obj.get("model") != MODEL):
        raise RealtimeError("explicit_realtime_model_required")
    if "model" in obj and obj["model"] != MODEL:
        raise RealtimeError("realtime_model_mismatch")
    if "type" in obj and obj["type"] != "realtime":
        raise RealtimeError("realtime_session_required")
    if "tools" in obj:
        validate_tools(obj["tools"])
    if "tool_choice" in obj:
        choice = obj["tool_choice"]
        if isinstance(choice, dict):
            choice = object_keys(choice, {"type", "name"})
            if choice.get("type") != "function" or not isinstance(choice.get("name"), str):
                raise RealtimeError("function_tools_only")
        elif choice not in ("auto", "none", "required"):
            raise RealtimeError("function_tools_only")
    if "instructions" in obj and not isinstance(obj["instructions"], str):
        raise RealtimeError("invalid_realtime_instructions")
    if "output_modalities" in obj and obj["output_modalities"] != ["audio"]:
        raise RealtimeError("audio_output_required")
    if "max_output_tokens" in obj:
        tokens = obj["max_output_tokens"]
        if type(tokens) is not int or not 1 <= tokens <= 4096:
            raise RealtimeError("realtime_output_token_limit")
    if "audio" in obj:
        audio = object_keys(obj["audio"], {"output"} if response else {"input", "output"})
        if "input" in audio:
            inp = object_keys(audio["input"], {"format", "transcription", "noise_reduction", "turn_detection"})
            if "format" in inp and inp["format"] != {"type": "audio/pcm", "rate": 24000}:
                raise RealtimeError("pcm24k_required")
            if inp.get("transcription") is not None:
                tr = object_keys(inp["transcription"], {"model", "language", "prompt"})
                if "model" in tr and tr["model"] != "gpt-4o-mini-transcribe":
                    raise RealtimeError("unverified_transcription_model")
            if inp.get("noise_reduction") is not None:
                noise = object_keys(inp["noise_reduction"], {"type"})
                if noise.get("type") not in ("near_field", "far_field"):
                    raise RealtimeError("unsupported_noise_reduction")
            if inp.get("turn_detection") is not None:
                vad = object_keys(inp["turn_detection"], {"type", "threshold", "prefix_padding_ms", "silence_duration_ms", "create_response", "interrupt_response", "idle_timeout_ms"})
                if "type" in vad and vad["type"] != "server_vad":
                    raise RealtimeError("server_vad_required")
        if "output" in audio:
            out = object_keys(audio["output"], {"format", "voice", "speed"})
            if "format" in out and out["format"] != {"type": "audio/pcm", "rate": 24000}:
                raise RealtimeError("pcm24k_required")
            if "voice" in out and out["voice"] != "cedar":
                raise RealtimeError("unverified_realtime_voice")
    if "reasoning" in obj:
        reasoning = object_keys(obj["reasoning"], {"effort"})
        if reasoning.get("effort") not in ("low", "medium", "high"):
            raise RealtimeError("invalid_realtime_reasoning")


CLIENT_EVENTS = {"session.update", "input_audio_buffer.append", "input_audio_buffer.commit",
                 "input_audio_buffer.clear", "response.create", "response.cancel",
                 "conversation.item.create", "conversation.item.delete", "conversation.item.truncate"}


def strict_loads(raw: str | bytes | bytearray) -> Any:
    def reject_constant(_value: str) -> Any:
        raise ValueError("nonfinite_json")
    def number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite_json")
        return parsed
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, parse_constant=reject_constant, parse_float=number, object_pairs_hook=unique)


def client_event(raw: str) -> dict[str, Any]:
    try:
        event = strict_loads(raw)
    except (ValueError, RecursionError):
        raise RealtimeError("invalid_realtime_json") from None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str) or event["type"] not in CLIENT_EVENTS:
        raise RealtimeError("unsupported_realtime_event")
    if event["type"] == "session.update":
        validate_session(event.get("session"))
    elif event["type"] == "response.create":
        validate_session(event.get("response", {}), response=True)
    # Tool execution is never a Route action; ordinary conversation/audio items
    # stay byte-for-byte intact and are validated by the provider protocol.
    return event


@dataclass
class Reservation:
    id: str
    client: VoiceClient
    created: float
    state: str = "minting"
    account: str | None = field(default=None, repr=False)
    slot: Any = field(default=None, repr=False)
    upstream_secret: str | None = field(default=None, repr=False)
    deadline: float = 0
    ticket_digest: str | None = field(default=None, repr=False)
    timer: asyncio.TimerHandle | None = field(default=None, repr=False)
    upstream: Any = field(default=None, repr=False)
    downstream: Any = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    metrics: dict[str, Any] = field(default_factory=dict)
    released: bool = False


class Reservations:
    """All transitions are synchronous on the event loop; no await in admission."""
    def __init__(self, config: RealtimeConfig):
        self.config = config
        self.entries: dict[str, Reservation] = {}
        self.tickets: dict[str, str] = {}
        self.closing = False

    def reserve(self, client: VoiceClient) -> Reservation:
        if self.closing:
            raise RealtimeError("realtime_shutting_down", 503)
        bound("sessions", len(self.entries) + 1, self.config.max_sessions, 429)
        bound("client_sessions", sum(r.client.id == client.id for r in self.entries.values()) + 1, self.config.per_client, 429)
        reservation = Reservation(uuid.uuid4().hex, client, time.monotonic(), task=asyncio.current_task())
        self.entries[reservation.id] = reservation
        return reservation

    def bind(self, reservation: Reservation, slot: Any, account: str) -> None:
        bound("account_sessions", sum(r.account == account for r in self.entries.values()) + 1, self.config.per_account, 429)
        reservation.slot, reservation.account = slot, account

    def issue(self, reservation: Reservation, secret: str, expires_at: Any) -> tuple[str, int]:
        if not finite(expires_at):
            raise RealtimeError("upstream_expiration_invalid", 502)
        ttl = min(self.config.ticket_seconds, expires_at - time.time())
        if ttl <= 1:
            raise RealtimeError("upstream_secret_expired", 502)
        token = secrets.token_urlsafe(32)
        reservation.state, reservation.upstream_secret = "issued", secret
        reservation.deadline = time.monotonic() + ttl
        reservation.ticket_digest = digest(token)
        self.tickets[reservation.ticket_digest] = reservation.id
        reservation.task = None
        reservation.timer = asyncio.get_running_loop().call_later(ttl, self.release, reservation)
        return token, math.floor(time.time() + ttl)

    def redeem(self, token: str, model: str) -> Reservation:
        rid = self.tickets.get(digest(token))
        reservation = self.entries.get(rid) if rid else None
        if reservation is None or reservation.state != "issued":
            raise RealtimeError("realtime_ticket_invalid", 401)
        if time.monotonic() >= reservation.deadline:
            self.release(reservation)
            raise RealtimeError("realtime_ticket_invalid", 401)
        if model != self.config.model:
            raise RealtimeError("realtime_model_mismatch")
        self.tickets.pop(reservation.ticket_digest, None)
        if reservation.timer:
            reservation.timer.cancel()
        reservation.state = "connecting"
        reservation.task = asyncio.current_task()
        return reservation

    def release(self, reservation: Reservation) -> None:
        if reservation.released:
            return
        reservation.released = True
        if reservation.timer:
            reservation.timer.cancel()
        if reservation.ticket_digest:
            self.tickets.pop(reservation.ticket_digest, None)
        reservation.upstream_secret = None
        self.entries.pop(reservation.id, None)


def provider_error(status: int, body: Any = None) -> RealtimeError:
    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    known = {"insufficient_quota": ("realtime_quota_exhausted", 429),
             "rate_limit_exceeded": ("realtime_rate_limited", 429),
             "model_not_found": ("realtime_upstream_model_unavailable", 502),
             "invalid_api_key": ("realtime_upstream_authentication", 502)}
    if isinstance(code, str) and code in known:
        name, local_status = known[code]
        return RealtimeError(name, local_status)
    names = {400: "realtime_upstream_configuration_rejected", 401: "realtime_upstream_authentication",
             403: "realtime_upstream_forbidden_unclassified", 429: "realtime_upstream_limit_unclassified"}
    return RealtimeError(names.get(status, "realtime_upstream_unavailable"),
                         status if status in {400, 403, 429} else 502)


async def read_json(stream: Any, maximum: int) -> dict[str, Any]:
    data = bytearray()
    async for chunk in stream.iter_chunked(8192):
        bound("json_bytes", len(data) + len(chunk), maximum)
        data.extend(chunk)
    try:
        body = strict_loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise RealtimeError("invalid_realtime_json") from None
    if not isinstance(body, dict):
        raise RealtimeError("invalid_realtime_json")
    return body


def checked_proxy() -> dict[str, Any]:
    try:
        proxy, auth = get_env_proxy_for_url(URL(MINT_URL))
    except LookupError:
        raise RealtimeError("realtime_https_proxy_required", 503) from None
    return {"proxy": proxy, "proxy_auth": auth}


def public_model() -> dict[str, Any]:
    return {"id": MODEL, "object": "model", "created": 0, "owned_by": "openai",
            "tool_capable": True, "multimodal": True,
            "capabilities": {"api": "realtime", "input": ["audio", "text"], "output": ["audio"],
                             "transport": ["websocket"], "function_tools": True, "cancel": True,
                             "truncate": True, "routing": "explicit_model_only",
                             "authentication": "subscription-only", "account_access": "requires_authenticated_check"}}


class RealtimeService:
    def __init__(self, config: RealtimeConfig):
        self.config = config
        self.pool = Reservations(config)
        self.codex: Any = None
        self.http: Any = None
        self.observations: dict[str, dict[str, Any]] = {}

    async def start(self, codex: Any) -> None:
        self.codex = codex
        if not self.config.enabled:
            return
        async def reject_redirect(*_args: Any) -> None:
            raise RealtimeError("realtime_upstream_redirect_denied", 502)
        trace = TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        self.http = ClientSession(trust_env=True, cookie_jar=DummyCookieJar(), trace_configs=[trace],
                                  timeout=ClientTimeout(total=self.config.handshake_seconds))

    def observe(self, reservation: Reservation, stage: str, error: str | None = None) -> None:
        self.observations[reservation.client.id] = {"observed_at": int(time.time()), "stage": stage,
                                                  "error": error, "billing": "unknown"}

    def record(self, reservation: Reservation, reason: str) -> None:
        # One bounded content-free record per closed/rejected session. Never use
        # the textual request logger, which may be configured to retain prompts.
        import sys
        payload = {"realtime_id": reservation.id, "route": "subscription-only", "model": MODEL,
                   "close_reason": reason, "duration_ms": round((time.monotonic() - reservation.created) * 1000),
                   "billing": "unknown", **reservation.metrics}
        try:
            print(json.dumps(payload, separators=(",", ":")), file=sys.stderr)
        except Exception:
            pass  # Telemetry must not affect transport or cleanup.

    def slot_ready(self, client: VoiceClient) -> Any:
        if self.codex is None:
            raise RealtimeError("realtime_oauth_unavailable", 503)
        slots = [s for s in self.codex.slots if s.index == client.slot and s.path and not s.token]
        if len(slots) != 1:
            raise RealtimeError("realtime_authorized_slot_unavailable", 503)
        slot = slots[0]
        status = self.codex.slot_routing_status(slot)
        if (status.get("routingEnabled") is not True or status.get("requestedRoutingEnabled") is not True
                or status.get("identityMismatch") or status.get("identityUnverified")):
            raise RealtimeError("realtime_authorized_slot_disabled", 403)
        for value in (slot.rate_limited_until, slot.transient_until, slot.auth_retry_at):
            if not finite(value):
                raise RealtimeError("realtime_slot_policy_invalid", 503)
            if value > time.time():
                raise RealtimeError("realtime_authorized_slot_cooldown", 429)
        return slot

    async def quota_ready(self, client: VoiceClient, slot: Any, auth: Any, account: str | None = None) -> None:
        if auth is None:
            raise RealtimeError("realtime_oauth_unavailable", 503)
        quota = getattr(self.codex, "quota", None)
        admitted = quota is None or await quota.admit(slot, auth)
        if (self.slot_ready(client) is not slot or slot.auth is not auth
                or (account is not None and slot.account_key() != account)):
            raise RealtimeError("realtime_account_identity_changed", 409)
        if not admitted:
            raise RealtimeError("realtime_quota_paused", 503)

    async def mint(self, request: web.Request) -> web.Response:
        client: VoiceClient = request[CLIENT_KEY]
        reservation = self.pool.reserve(client)
        try:
            async with asyncio.timeout(self.config.mint_seconds):
                body = await read_json(request.content, self.config.mint_bytes)
                object_keys(body, {"session"})
                validate_session(body.get("session"), mint=True)
                proxy = checked_proxy()  # fail before any OAuth refresh
                slot = self.slot_ready(client)
                auth = await self.codex.load_auth(slot)
                if auth is None or not auth.account_key or not slot.auth_persistence_healthy:
                    raise RealtimeError("realtime_oauth_unavailable", 503)
                # Recheck policy after auth identity is known; no pool selection.
                await self.quota_ready(client, slot, auth)
                self.pool.bind(reservation, slot, auth.account_key)
                config = body["session"]
                config.setdefault("output_modalities", ["audio"])
                config.setdefault("max_output_tokens", 4096)
                config.setdefault("audio", {}).setdefault("output", {}).setdefault("voice", "cedar")
                async with self.http.post(MINT_URL, json={"session": config},
                                          headers={"Authorization": f"Bearer {auth.access_token}"},
                                          allow_redirects=False, **proxy) as response:
                    if response.status != 200:
                        try:
                            failure = await read_json(response.content, self.config.mint_bytes)
                        except RealtimeError:
                            failure = None
                        raise provider_error(response.status, failure)
                    try:
                        payload = await read_json(response.content, self.config.mint_bytes)
                    except RealtimeError:
                        raise RealtimeError("realtime_upstream_payload_invalid", 502) from None
                nested = payload.get("client_secret")
                data = nested if isinstance(nested, dict) else payload
                secret = data.get("value")
                if not isinstance(secret, str) or not secret or len(secret) > 8192 or any(c.isspace() for c in secret):
                    raise RealtimeError("realtime_upstream_secret_invalid", 502)
                ticket, expiry = self.pool.issue(reservation, secret, data.get("expires_at"))
                reservation.metrics["mint_ms"] = round((time.monotonic() - reservation.created) * 1000)
                self.observe(reservation, "mint_accepted")
                return web.json_response({"value": ticket, "expires_at": expiry}, headers={"Cache-Control": "no-store"})
        except BaseException as exc:
            self.pool.release(reservation)
            if isinstance(exc, asyncio.CancelledError):
                raise
            failure = self.failure(exc)
            self.observe(reservation, "mint_failed", failure.code)
            self.record(reservation, failure.code)
            raise failure from None

    @staticmethod
    def failure(exc: BaseException) -> RealtimeError:
        if isinstance(exc, RealtimeError):
            return exc
        if isinstance(exc, WSServerHandshakeError):
            return provider_error(exc.status)
        if isinstance(exc, TimeoutError):
            return RealtimeError("realtime_deadline_exceeded", 504)
        if isinstance(exc, ClientError):
            return RealtimeError("realtime_transport_unavailable", 502)
        return RealtimeError("realtime_internal_error", 500)

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        if list(request.query.keys()) != ["model"] or len(request.query.getall("model")) != 1:
            raise RealtimeError("explicit_realtime_model_required")
        # Check a valid upgrade before consuming a ticket or opening upstream.
        downstream = web.WebSocketResponse(max_msg_size=self.config.frame_bytes, heartbeat=20,
                                            compress=False, timeout=1)
        if not downstream.can_prepare(request).ok:
            raise RealtimeError("websocket_upgrade_required", 426)
        reservation = self.pool.redeem(bearer(request.headers.get("Authorization", "")), request.query["model"])
        upstream = None
        close_reason = "normal"
        close_code = 1000
        try:
            slot = self.slot_ready(reservation.client)
            if slot is not reservation.slot or slot.account_key() != reservation.account:
                raise RealtimeError("realtime_account_identity_changed", 409)
            await self.quota_ready(reservation.client, slot, slot.auth, reservation.account)
            proxy = checked_proxy()
            timeout = min(self.config.handshake_seconds, reservation.deadline - time.monotonic())
            if timeout <= 0:
                raise RealtimeError("realtime_ticket_invalid", 401)
            started = time.monotonic()
            async with asyncio.timeout(timeout):
                upstream = await self.http.ws_connect(WS_URL + "?model=" + MODEL,
                    headers={"Authorization": f"Bearer {reservation.upstream_secret}"},
                    max_msg_size=self.config.frame_bytes, heartbeat=20, compress=0, **proxy)
                reservation.upstream_secret = None
                reservation.upstream, reservation.downstream = upstream, downstream
                reservation.metrics["handshake_ms"] = round((time.monotonic() - started) * 1000)
                await downstream.prepare(request)
            reservation.state = "active"
            self.observe(reservation, "websocket_open")
            async with asyncio.timeout(self.config.session_seconds):
                close_reason = await self.relay(reservation)
            return downstream
        except BaseException as exc:
            close_reason = "cancelled" if isinstance(exc, asyncio.CancelledError) else self.failure(exc).code
            if isinstance(exc, asyncio.CancelledError):
                raise
            failure = self.failure(exc)
            close_code = 1009 if failure.status == 413 else 1008 if failure.status in {400, 401, 403, 409} else 1011
            self.observe(reservation, "connection_failed", failure.code)
            if not downstream.prepared:
                raise failure from None
            try:
                async with asyncio.timeout(self.config.send_seconds):
                    payload = {"type": "error", **failure.payload()}
                    if failure.event_id is not None:
                        payload["event_id"] = failure.event_id
                    await downstream.send_json(payload)
            except Exception:
                pass
            return downstream
        finally:
            try:
                async with asyncio.timeout(2):
                    sockets = [s.close(code=1000 if close_reason == "normal" else (close_code if close_code != 1000 else 1011)) for s in (upstream, downstream) if s is not None]
                    if sockets:
                        await asyncio.gather(*sockets, return_exceptions=True)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self.pool.release(reservation)
            self.record(reservation, close_reason)

    async def relay(self, reservation: Reservation) -> str:
        last_activity = time.monotonic()
        cancel_at: float | None = None

        async def forward(source: Any, target: Any, *, to_provider: bool) -> str:
            nonlocal last_activity, cancel_at
            while True:
                message = await source.receive()
                if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED}:
                    return "normal" if source.close_code in {1000, 1001} else "realtime_peer_disconnected"
                if message.type == WSMsgType.ERROR and getattr(message.data, "code", None) == 1009:
                    raise RealtimeError("realtime_limit_exceeded", 413, limit={
                        "name": "frame_bytes", "configured": self.config.frame_bytes,
                        "observed": "exceeds_configured_limit", "recovery": "send_smaller_frames"})
                if message.type != WSMsgType.TEXT:
                    raise RealtimeError("realtime_invalid_frame")
                size = len(message.data.encode("utf-8"))
                bound("frame_bytes", size, self.config.frame_bytes)
                if to_provider:
                    event = client_event(message.data)
                else:
                    try:
                        event = strict_loads(message.data)
                    except (ValueError, RecursionError):
                        raise RealtimeError("realtime_upstream_invalid_event", 502) from None
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise RealtimeError("realtime_upstream_invalid_event", 502)
                    if event["type"] == "error":
                        error = event.get("error")
                        # VAD can finish/cancel a response before the client's
                        # barge-in cancel arrives. OpenAI defines this exact
                        # condition as harmless to the session. Do not turn it
                        # into a terminal error (or expose provider messages).
                        if (isinstance(error, dict)
                                and error.get("type") == "invalid_request_error"
                                and error.get("code") == "response_cancel_not_active"):
                            key = "inactive_cancel_count"
                            reservation.metrics[key] = reservation.metrics.get(key, 0) + 1
                            cancel_at = None
                            last_activity = time.monotonic()
                            continue
                        failure = provider_error(400, event)
                        if isinstance(event.get("event_id"), str):
                            failure.event_id = event["event_id"]
                        # One sanitized error from the connection owner, no replay.
                        raise failure
                    if event["type"] in {"session.created", "session.updated"}:
                        session = event.get("session")
                        if not isinstance(session, dict) or session.get("model") != MODEL:
                            raise RealtimeError("realtime_upstream_model_mismatch", 502)
                        if any(key in session for key in ("client_secret", "access_token", "refresh_token")):
                            raise RealtimeError("realtime_upstream_credential_field", 502)
                        self.observe(reservation, "session_accepted")
                    if event["type"] == "response.output_audio.delta":
                        reservation.metrics.setdefault("first_audio_ms", round((time.monotonic() - reservation.created) * 1000))
                        self.observe(reservation, "audio_output_observed")
                    if event["type"] == "response.done":
                        response = event.get("response", {})
                        if isinstance(response, dict):
                            if cancel_at is not None and response.get("status") == "cancelled":
                                reservation.metrics["cancel_terminal_ms"] = round((time.monotonic() - cancel_at) * 1000)
                                cancel_at = None
                            usage = response.get("usage")
                            if isinstance(usage, dict):
                                for key in ("input_tokens", "output_tokens", "total_tokens"):
                                    value = usage.get(key)
                                    if type(value) is int and value >= 0:
                                        reservation.metrics[key] = reservation.metrics.get(key, 0) + value
                if to_provider and event["type"] == "response.cancel":
                    cancel_at = time.monotonic()
                started = time.monotonic()
                async with asyncio.timeout(self.config.send_seconds):
                    await target.send_str(message.data)
                if to_provider and event["type"] == "response.cancel":
                    reservation.metrics["cancel_forward_ms"] = round((time.monotonic() - started) * 1000)
                key = "client_bytes" if to_provider else "provider_bytes"
                reservation.metrics[key] = reservation.metrics.get(key, 0) + size
                last_activity = time.monotonic()

        async def idle() -> str:
            while True:
                await asyncio.sleep(self.config.idle_seconds)
                if time.monotonic() - last_activity >= self.config.idle_seconds:
                    raise RealtimeError("realtime_idle_deadline", 408)

        tasks = [asyncio.create_task(forward(reservation.downstream, reservation.upstream, to_provider=True)),
                 asyncio.create_task(forward(reservation.upstream, reservation.downstream, to_provider=False)),
                 asyncio.create_task(idle())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            results = [task.result() for task in done]
            return next((r for r in results if r != "normal"), "normal")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def capabilities(self, request: web.Request) -> web.Response:
        client = request[CLIENT_KEY]
        return web.json_response({"model": public_model(), "observation": self.observations.get(client.id),
                                  "route": "subscription-only", "billing": "unknown",
                                  "limits": {k: getattr(self.config, k) for k in (
                                      "max_sessions", "per_client", "per_account", "frame_bytes", "mint_bytes",
                                      "ticket_seconds", "mint_seconds", "handshake_seconds", "send_seconds",
                                      "idle_seconds", "session_seconds", "shutdown_seconds")}},
                                 headers={"Cache-Control": "no-store"})

    async def shutdown(self) -> None:
        self.pool.closing = True
        tasks = {r.task for r in self.pool.entries.values() if r.task is not None and r.task is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.config.shutdown_seconds)
            for task in done:
                if not task.cancelled():
                    task.exception()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for reservation in list(self.pool.entries.values()):
            self.pool.release(reservation)
        if self.http:
            await self.http.close()


async def voice_dispatch(request: web.Request, handler: Any) -> web.StreamResponse:
    service: RealtimeService = request.app[RT_KEY]
    try:
        if not service.config.enabled or service.pool.closing:
            raise RealtimeError("realtime_disabled", 503)
        if request.path != "/v1/realtime":
            request[CLIENT_KEY] = service.config.client(request.headers.get("Authorization", ""))
        from .app import STATE_KEY, get_state
        if request.path == "/v1/realtime/client_secrets" and STATE_KEY in request.app:
            from .provider_policy import admission_error, MINT, REALTIME
            # Mint admits the whole session. Existing tickets and sockets keep
            # their admission when settings change; no API key participates.
            state = get_state(request)
            for endpoint in (MINT, REALTIME):
                error = admission_error(state, "codex", endpoint, service.config.model)
                if error is not None:
                    return error
        return await handler(request)
    except RealtimeError as exc:
        return web.json_response(exc.payload(), status=exc.status, headers={"Cache-Control": "no-store"})


def register(app: web.Application, config: RealtimeConfig) -> RealtimeService:
    service = RealtimeService(config)
    app[RT_KEY] = service
    app.router.add_post("/v1/realtime/client_secrets", service.mint)
    app.router.add_get("/v1/realtime", service.websocket)
    app.router.add_get("/api/realtime/capabilities", service.capabilities)
    return service
