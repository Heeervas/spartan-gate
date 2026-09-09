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
            if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", client.id)
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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65536:
                raise ValueError("unsafe_realtime_clients_file")
            with os.fdopen(os.dup(fd), encoding="utf-8") as handle:
                body = json.loads(handle.read(65537))
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
    if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
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
                if noise.get("type") not in {"near_field", "far_field"}:
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
        if reasoning.get("effort") not in {"low", "medium", "high"}:
            raise RealtimeError("invalid_realtime_reasoning")


CLIENT_EVENTS = {"session.update", "input_audio_buffer.append", "input_audio_buffer.commit",
                 "input_audio_buffer.clear", "response.create", "response.cancel",
                 "conversation.item.create", "conversation.item.delete", "conversation.item.truncate"}


def client_event(raw: str) -> dict[str, Any]:
    try:
        event = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise RealtimeError("invalid_realtime_json") from None
    if not isinstance(event, dict) or event.get("type") not in CLIENT_EVENTS:
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
