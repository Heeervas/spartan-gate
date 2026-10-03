"""Bounded subscription transport; full request replay and account policy stay upstream."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from aiohttp import ClientSession, ClientTimeout, TCPConnector, TraceConfig, WSMsgType, WSServerHandshakeError
from yarl import URL

MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_CONNECTIONS = 32
MAX_ACCOUNT_CONNECTIONS = 4
IDLE_SECONDS = 240
MAX_AGE_SECONDS = 50 * 60
SEND_SECONDS = 15
READ_SECONDS = 300
CLOSE_SECONDS = 1
TERMINAL = {"response.completed", "response.failed", "response.incomplete", "response.cancelled"}
SIDEBAND = {"codex.rate_limits", "codex.response.metadata", "responsesapi.websocket_timing"}
START_EVENTS = {"response.created", "response.in_progress"}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def proxy_for_websocket(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    raw = env.get("https_proxy") or env.get("HTTPS_PROXY") or ""
    try:
        url = URL(raw)
        if url.scheme != "http" or not url.host or url.user or url.password or url.query_string or url.fragment or url.path not in {"", "/"}:
            raise ValueError()
        _ = url.port
    except (ValueError, TypeError):
        raise ValueError("A valid explicit HTTP egress proxy is required for Codex WebSockets") from None
    return str(url)


def websocket_frame(body: dict[str, Any]) -> str:
    # The subscription protocol retains stream=True (unlike some public API examples).
    frame: dict[str, Any] = {"type": "response.create"}
    for key in ("model", "stream", "service_tier"):
        if key in body:
            frame[key] = body[key]
    frame.update({key: value for key, value in body.items() if key not in frame and key != "previous_response_id"})
    return compact_json(frame)


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def observe_event(details: dict[str, Any], started: float, event: str | None, payload: dict[str, Any]) -> None:
    name = event or payload.get("type")
    details.setdefault("first_event_ms", elapsed_ms(started))
    if name in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta", "response.reasoning.delta"}:
        if payload.get("delta"):
            details.setdefault("first_reasoning_ms", elapsed_ms(started))
    if name == "response.output_text.delta" and payload.get("delta"):
        details.setdefault("first_text_ms", elapsed_ms(started))
        details.setdefault("first_output_ms", elapsed_ms(started))
    if name in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"} and payload.get("delta"):
        details.setdefault("first_tool_ms", elapsed_ms(started))
        details.setdefault("first_output_ms", elapsed_ms(started))


def create_client_session(*, limit: int = 100) -> ClientSession:
    trace = TraceConfig()

    async def queued_start(_session, context, _params):
        if isinstance(context.trace_request_ctx, dict):
            context.trace_request_ctx["queued_at"] = time.monotonic()

    async def queued_end(_session, context, _params):
        data = context.trace_request_ctx
        if isinstance(data, dict) and "queued_at" in data:
            data["details"]["pool_wait_ms"] = elapsed_ms(data.pop("queued_at"))

    async def connect_start(_session, context, _params):
        if isinstance(context.trace_request_ctx, dict):
            context.trace_request_ctx["connecting_at"] = time.monotonic()

    async def connect_end(_session, context, _params):
        data = context.trace_request_ctx
        if isinstance(data, dict) and "connecting_at" in data:
            data["details"]["connection_ms"] = elapsed_ms(data.pop("connecting_at"))

    async def reused(_session, context, _params):
        if isinstance(context.trace_request_ctx, dict):
            context.trace_request_ctx["details"]["connection_reused"] = True

    trace.on_connection_queued_start.append(queued_start)
    trace.on_connection_queued_end.append(queued_end)
    trace.on_connection_create_start.append(connect_start)
    trace.on_connection_create_end.append(connect_end)
    trace.on_connection_reuseconn.append(reused)
    session = ClientSession(
        connector=TCPConnector(limit=limit, keepalive_timeout=IDLE_SECONDS, ttl_dns_cache=300),
        trust_env=True, trace_configs=[trace], json_serialize=compact_json,
    )
    session._spartan_traced = True
    return session


class TransportError(Exception):
    def __init__(self, code: str, *, status: int = 502, retry_allowed: bool = False, error: dict[str, Any] | None = None, rejected: bool = False):
        super().__init__(code)
        self.code, self.status, self.retry_allowed = code, status, retry_allowed
        self.error = error or {"code": code, "type": "upstream_error", "message": "Codex transport failed"}
        self.rejected = rejected


class FailureResponse:
    def __init__(self, error: TransportError, details: dict[str, Any]):
        self.status = error.status
        self.transport_details = details
        self.retry_allowed = error.retry_allowed
        self.is_websocket = details["kind"] == "websocket"
        self._error = error.error

    async def text(self) -> str:
        return compact_json({"error": self._error})

    def release(self) -> None:
        pass


@dataclass(eq=False)
class Connection:
    key: tuple[str, ...]
    account: str
    born: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    ws: Any = None
    state: str = "connecting"
    retire: bool = False
    idle_task: asyncio.Task | None = None


class WebSocketResponse:
    status = 200
    is_websocket = True
    retry_allowed = False

    def __init__(self, owner: CodexTransport, connection: Connection, details: dict[str, Any], started: float, deadline: float):
        self.owner, self.connection = owner, connection
        self.transport_details, self.started, self.deadline = details, started, deadline
        self.buffered: tuple[str, str] | None = None
        self.content = self
        self.terminal = False
        self.success = False
        self.material = False
        self.released = False
        self.first_response = False
        self.sidebands = 0

    async def next_event(self) -> tuple[str, str]:
        while True:
            timeout = READ_SECONDS if self.first_response else min(READ_SECONDS, self.deadline - time.monotonic())
            if timeout <= 0:
                raise TransportError("codex_ws_first_event_timeout")
            try:
                message = await asyncio.wait_for(self.connection.ws.receive(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TransportError("codex_ws_read_timeout") from None
            if message.type != WSMsgType.TEXT:
                if message.type in {WSMsgType.PING, WSMsgType.PONG}:
                    continue
                raise TransportError("codex_ws_disconnected")
            try:
                payload = json.loads(message.data)
            except (TypeError, ValueError):
                raise TransportError("codex_ws_invalid_event") from None
            if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                raise TransportError("codex_ws_invalid_event")
            event = payload["type"]
            if event in SIDEBAND:
                self.sidebands += 1
                if self.sidebands > 256:
                    raise TransportError("codex_ws_sideband_overflow")
                continue
            if event == "error":
                raw = payload.get("error") if isinstance(payload.get("error"), dict) else payload
                code = str(raw.get("code") or "codex_ws_error")
                if not re.fullmatch(r"[\w.-]{1,64}", code, flags=re.ASCII):
                    code = "codex_ws_error"
                status = payload.get("status", payload.get("status_code"))
                valid_status = not isinstance(status, bool) and isinstance(status, int) and 400 <= status <= 599
                if not valid_status:
                    status = 502
                definitive = valid_status and not self.material and status in {401, 429, 500, 502, 503}
                safe = {"code": code, "type": "upstream_error", "message": "Codex upstream rejected the request"}
                for field_name in ("resets_at", "resets_in_seconds"):
                    value = raw.get(field_name)
                    if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= (10**11 if field_name == "resets_at" else 604800):
                        safe[field_name] = value
                wrapped_headers = payload.get("headers")
                if isinstance(wrapped_headers, dict) and "resets_in_seconds" not in safe:
                    value = wrapped_headers.get("retry-after", wrapped_headers.get("Retry-After"))
                    if isinstance(value, str) and re.fullmatch(r"\d{1,6}", value) and int(value) <= 604800:
                        safe["resets_in_seconds"] = int(value)
                self.retry_allowed = definitive
                raise TransportError(code, status=status, retry_allowed=definitive, error=safe, rejected=valid_status)
            if not event.startswith("response."):
                raise TransportError("codex_ws_unknown_event")
            self.first_response = True
            if event not in START_EVENTS and event not in TERMINAL:
                self.material = True
            observe_event(self.transport_details, self.started, event, payload)
            if event in TERMINAL:
                self.terminal = True
                response = payload.get("response")
                self.success = event == "response.completed" and isinstance(response, dict) and response.get("status") == "completed"
            return event, compact_json(payload)

    async def iter_events(self) -> AsyncIterator[tuple[str, str]]:
        try:
            if self.buffered is not None:
                event, data = self.buffered
                self.buffered = None
                yield event, data
            while not self.terminal:
                yield await self.next_event()
        except BaseException:
            self.success = False
            raise

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        async for event, data in self.iter_events():
            yield f"event: {event}\ndata: {data}\n\n".encode()

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.owner.release(self.connection, reusable=self.terminal and self.success)


class CodexTransport:
    def __init__(self, session: ClientSession, mode: str = "http", *, proxy: str | None = None):
        if mode not in {"http", "auto", "websocket"}:
            raise ValueError("SPARTAN_ROUTE_CODEX_TRANSPORT must be http, auto, or websocket")
        self.session, self.mode, self.proxy = session, mode, proxy
        self.entries: list[Connection] = []
        self.closers: set[asyncio.Task] = set()
        self.unsupported: dict[str, float] = {}
        self.generations: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.closing = False

    async def _close(self, entry: Connection) -> None:
        try:
            if entry.idle_task is not None:
                entry.idle_task.cancel()
                await asyncio.gather(entry.idle_task, return_exceptions=True)
                entry.idle_task = None
            if entry.ws is not None:
                await asyncio.wait_for(entry.ws.close(), CLOSE_SECONDS)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            if entry in self.entries:
                self.entries.remove(entry)

    def discard(self, entry: Connection) -> None:
        if entry.state != "closing":
            entry.state = "closing"
            task = asyncio.create_task(self._close(entry))
            self.closers.add(task)
            task.add_done_callback(self.closers.discard)

    def release(self, entry: Connection, *, reusable: bool) -> None:
        if reusable and not entry.retire and not self.closing and not entry.ws.closed:
            entry.state, entry.touched = "idle", time.monotonic()
            entry.idle_task = asyncio.create_task(self._idle(entry))
        else:
            self.discard(entry)

    async def _idle(self, entry: Connection) -> None:
        """Service peer pings and expire idle connections even without new traffic."""
        try:
            deadline = min(entry.touched + IDLE_SECONDS, entry.born + MAX_AGE_SECONDS)
            sidebands = 0
            while entry.state == "idle":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = await asyncio.wait_for(entry.ws.receive(), remaining)
                if message.type in {WSMsgType.PING, WSMsgType.PONG}:
                    continue
                if message.type == WSMsgType.TEXT:
                    payload = json.loads(message.data)
                    sidebands += 1
                    if isinstance(payload, dict) and payload.get("type") in SIDEBAND and sidebands <= 256:
                        continue
                break
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        # _close owns joining this task; do not cancel ourselves.
        if entry.state == "idle":
            self.discard(entry)

    async def reserve(self, key: tuple[str, ...], account: str) -> tuple[Connection | None, bool]:
        async with self.lock:
            if self.closing:
                return None, False
            now = time.monotonic()
            for entry in list(self.entries):
                if entry.state == "idle" and (entry.ws.closed or now - entry.touched > IDLE_SECONDS or now - entry.born > MAX_AGE_SECONDS):
                    self.discard(entry)
            generation = key[1]
            if self.generations.get(account) != generation:
                self.generations[account] = generation
                for entry in list(self.entries):
                    if entry.account == account and entry.key[1] != generation:
                        entry.retire = True
                        if entry.state == "idle":
                            self.discard(entry)
            for entry in self.entries:
                if entry.key == key and entry.state == "idle":
                    entry.state = "busy"
                    if entry.idle_task is not None:
                        entry.idle_task.cancel()
                        try:
                            await asyncio.gather(entry.idle_task, return_exceptions=True)
                        except BaseException:
                            self.discard(entry)
                            raise
                        entry.idle_task = None
                    return entry, True
            account_entries = [entry for entry in self.entries if entry.account == account]
            if len(self.entries) >= MAX_CONNECTIONS or len(account_entries) >= MAX_ACCOUNT_CONNECTIONS:
                candidates = account_entries if len(account_entries) >= MAX_ACCOUNT_CONNECTIONS else self.entries
                idle = [entry for entry in candidates if entry.state == "idle"]
                if idle:
                    self.discard(min(idle, key=lambda entry: entry.touched))
                return None, False
            entry = Connection(key, account)
            self.entries.append(entry)  # Connecting reservations count before the first await.
            return entry, False

    async def http(self, url: str, body: dict[str, Any], headers: dict[str, str], started: float, deadline: float, fallback: str | None = None) -> Any:
        details = {"version": 1, "kind": "http", "latency_basis": "http_headers", "connection_reused": False, "pool_wait_ms": 0, "_started_monotonic": started}
        if fallback:
            details["fallback_reason"] = fallback
        kwargs: dict[str, Any] = {}
        if getattr(self.session, "_spartan_traced", False):
            kwargs["trace_request_ctx"] = {"details": details}
        response = await asyncio.wait_for(self.session.post(url, json=body, headers=headers, timeout=ClientTimeout(total=None), **kwargs), max(0, deadline - time.monotonic()))
        details["headers_ms"] = elapsed_ms(started)
        response.transport_details = details
        response.transport_started = started
        response.retry_allowed = True
        return response

    async def open(self, url: str, body: dict[str, Any], headers: dict[str, str], *, account: str, scope: str | None, timeout: float) -> Any:
        started = time.monotonic()
        deadline = started + timeout
        if self.mode == "http":
            return await self.http(url, body, headers, started, deadline)
        details: dict[str, Any] = {"version": 1, "kind": "websocket", "latency_basis": "websocket_events", "connection_reused": False, "pool_wait_ms": 0, "_started_monotonic": started}
        try:
            proxy = proxy_for_websocket({"https_proxy": self.proxy} if self.proxy is not None else None)
        except ValueError:
            return FailureResponse(TransportError("codex_ws_proxy_invalid", status=503), details)
        encoded = websocket_frame(body)
        details["preparation_ms"] = elapsed_ms(started)
        if len(encoded.encode()) > MAX_REQUEST_BYTES:
            if self.mode == "auto":
                return await self.http(url, body, headers, started, deadline, "request_size")
            return FailureResponse(TransportError("codex_ws_request_too_large", status=413), details)
        fingerprint = hashlib.sha256(headers.get("Authorization", "").encode()).hexdigest()
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        cache_key = str(body.get("prompt_cache_key") or "")
        key = (account, fingerprint, str(body.get("model") or ""), digest(scope or cache_key), digest(cache_key), proxy)
        if self.mode == "auto" and self.unsupported.get(fingerprint, 0) > started:
            return await self.http(url, body, headers, started, deadline, "unsupported")
        entry, reused = await self.reserve(key, account)
        if entry is None:
            if self.mode == "auto":
                return await self.http(url, body, headers, started, deadline, "pool_busy")
            return FailureResponse(TransportError("codex_ws_pool_busy", status=503), details)
        entry.retire = not bool(scope or cache_key)
        details["connection_reused"] = reused
        sent = False
        response: WebSocketResponse | None = None
        try:
            if not reused:
                connection_started = time.monotonic()
                entry.ws = await asyncio.wait_for(self.session.ws_connect(
                    url.replace("https:", "wss:", 1), headers=headers, proxy=proxy,
                    ssl=True, max_msg_size=MAX_FRAME_BYTES, heartbeat=20, compress=0,
                ), min(10, max(0, deadline - time.monotonic())))
                details["connection_ms"] = elapsed_ms(connection_started)
                if self.closing or entry.state == "closing" or entry not in self.entries:
                    await asyncio.wait_for(entry.ws.close(), CLOSE_SECONDS)
                    return FailureResponse(TransportError("codex_ws_shutting_down", status=503), details)
            entry.state = "busy"
            response = WebSocketResponse(self, entry, details, started, deadline)
            sent = True  # A failed send may already have reached the backend.
            await asyncio.wait_for(entry.ws.send_str(encoded), min(SEND_SECONDS, max(0, deadline - time.monotonic())))
            response.buffered = await response.next_event()
            return response
        except WSServerHandshakeError as exc:
            self.discard(entry)
            if self.mode == "auto" and exc.status in {404, 405, 426}:
                self.unsupported[fingerprint] = time.monotonic() + 600
                return await self.http(url, body, headers, started, deadline, "unsupported")
            safe = {"code": "codex_ws_handshake_rejected", "type": "upstream_error", "message": "Codex upstream rejected the connection"}
            retry_after = (exc.headers or {}).get("Retry-After", "")
            if isinstance(retry_after, str) and re.fullmatch(r"\d{1,6}", retry_after) and int(retry_after) <= 604800:
                safe["resets_in_seconds"] = int(retry_after)
            return FailureResponse(TransportError("codex_ws_handshake_rejected", status=exc.status, retry_allowed=exc.status in {401, 429, 500, 502, 503}, error=safe, rejected=True), details)
        except TransportError as exc:
            self.discard(entry)
            return FailureResponse(exc, details)
        except asyncio.CancelledError:
            self.discard(entry)
            raise
        except Exception:
            self.discard(entry)
            if not sent and self.mode == "auto":
                return await self.http(url, body, headers, started, deadline, "connection_failed")
            return FailureResponse(TransportError("codex_ws_send_or_connect_failed"), details)

    async def close(self) -> None:
        self.closing = True
        for entry in list(self.entries):
            self.discard(entry)
        if self.closers:
            await asyncio.gather(*list(self.closers), return_exceptions=True)
