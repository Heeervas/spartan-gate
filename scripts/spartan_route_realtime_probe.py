#!/usr/bin/env python3
"""Explicitly authorized, single-slot Realtime feasibility probe; default is offline.

Run live only through `sg exec -T spartan-route python3 - ... < this_file`.
This preserves the running service's bootstrap paths, managed store and UID.
No provider credentials, event contents, account identifiers or audio are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any

REVISION = "d23a8b257c6d6dcccb0c8af49884862adac7d17a"
MODEL = "gpt-realtime-2.1"
VOICE = "cedar"
MINT_URL = "https://api.openai.com/v1/realtime/client_secrets"
WS_URL = "wss://api.openai.com/v1/realtime"
BUDGET_SECONDS = 60
# Probe-only ceilings, not measured production recommendations. A 250 ms input
# is 12,000 PCM bytes; 1 MiB frames and 4 MiB total allow short output turns.
MAX_FRAME = 1024 * 1024
MAX_RECEIVED = 4 * MAX_FRAME
MAX_EVENTS = 1024
MAX_HTTP_BODY = 64 * 1024
TOOL = {"type": "function", "name": "route_probe_ping", "description": "Inert probe; returns ok.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}


class ProbeFailure(Exception):
    def __init__(self, code: str, status: int | None = None, *, limit: dict[str, Any] | None = None):
        self.code, self.status, self.limit = code, status, limit
        super().__init__(code)


def describe() -> dict[str, Any]:
    return {"mode": "offline", "authorization_required": True, "hermes_talk_revision": REVISION,
            "model_candidate": MODEL, "voice_candidate": VOICE, "subscription_access": "unverified",
            "operations": {"mint": 1, "websocket": 1, "response_creates": 3,
                           "synthetic_audio_ms": 250, "max_output_tokens_per_response": 128},
            "budget_seconds": BUDGET_SECONDS, "quota_and_cost_attribution": "unknown",
            "refresh": "existing Route manager, identical paths/store/UID only",
            "fallback": "none"}


def session_payload() -> dict[str, Any]:
    return {"type": "realtime", "model": MODEL, "output_modalities": ["audio"],
            "instructions": "This is an inert synthetic audio test. Say only ready when asked.",
            "max_output_tokens": 128, "tools": [TOOL], "tool_choice": "auto",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                 "noise_reduction": {"type": "near_field"},
                                 "transcription": {"model": "gpt-4o-mini-transcribe"},
                                 "turn_detection": {"type": "server_vad", "create_response": False,
                                                    "interrupt_response": True}},
                      "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": VOICE}}}


def synthetic_audio() -> str:
    # Quiet 440 Hz tone, generated in memory, never a person's recording.
    pcm = b"".join(struct.pack("<h", round(512 * math.sin(2 * math.pi * 440 * i / 24000)))
                   for i in range(6000))
    return base64.b64encode(pcm).decode("ascii")


def upstream_error(payload: Any, status: int | None = None) -> ProbeFailure:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    raw = error.get("code") if isinstance(error, dict) else None
    # Never propagate provider messages or arbitrary code strings (may echo input).
    known = {"insufficient_quota": "quota", "rate_limit_exceeded": "rate_limit",
             "model_not_found": "model_unavailable", "invalid_api_key": "upstream_authentication"}
    code = known.get(raw) if isinstance(raw, str) else None
    if not code:
        code = {401: "upstream_authentication", 403: "upstream_forbidden_unclassified",
                429: "upstream_limit_unclassified"}.get(status, "upstream_rejected")
    return ProbeFailure(code, status)


def finite_number(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def selected_slot(router: Any, index: int) -> Any:
    slots = [slot for slot in router.slots if slot.index == index and slot.path and not slot.token]
    if len(slots) != 1:
        raise ProbeFailure("configured_slot_missing")
    router.slots = slots  # restrict before load_auth; never call choose_slot.
    return slots[0]


async def load_selected_auth(router: Any, slot: Any, status: dict[str, Any]) -> Any:
    auth = await router.load_auth(slot)
    if not auth or not auth.account_key:
        raise ProbeFailure("oauth_unavailable")
    if status.get("accountKey") and status["accountKey"] != auth.account_key:
        raise ProbeFailure("account_identity_changed")
    return auth


def parse_secret(payload: dict[str, Any]) -> str:
    nested = payload.get("client_secret")
    source = nested if isinstance(nested, dict) else payload
    secret, expiry = source.get("value"), source.get("expires_at")
    if not isinstance(secret, str) or not secret or any(c.isspace() for c in secret):
        raise ProbeFailure("mint_invalid_secret")
    if not finite_number(expiry) or expiry <= time.time():
        raise ProbeFailure("mint_invalid_expiration")
    return secret


async def bounded_json(response: Any) -> dict[str, Any]:
    body = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        body.extend(chunk)
        if len(body) > MAX_HTTP_BODY:
            raise ProbeFailure("http_body_limit", response.status, limit={
                "name": "http_body_bytes", "configured": MAX_HTTP_BODY, "observed": len(body),
                "recovery": "stop_and_review_probe_ceiling"})
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise ProbeFailure("http_invalid_json", response.status) from None
    if not isinstance(value, dict):
        raise ProbeFailure("http_invalid_object", response.status)
    return value


def checked_slot(payload: dict[str, Any], index: int, path: str) -> dict[str, Any]:
    slots = payload.get("slots")
    if not isinstance(slots, list):
        raise ProbeFailure("local_status_invalid")
    matches = [s for s in slots if isinstance(s, dict) and s.get("slotIndex") == index]
    if len(matches) != 1:
        raise ProbeFailure("local_slot_unavailable")
    slot = matches[0]
    if slot.get("slotPath") != path:
        raise ProbeFailure("oauth_lock_identity_mismatch")
    if slot.get("routingEnabled") is not True or slot.get("requestedRoutingEnabled") is not True:
        raise ProbeFailure("local_slot_disabled")
    if slot.get("routingIdentityMismatch") or slot.get("routingIdentityUnverified"):
        raise ProbeFailure("local_slot_identity_unverified")
    for field in ("rateLimitedUntil", "transientUntil", "authRetryAt"):
        deadline = slot.get(field)
        if deadline is None and field == "authRetryAt":
            continue
        if not finite_number(deadline):
            raise ProbeFailure("local_status_invalid")
        if deadline > time.time():
            raise ProbeFailure("local_slot_cooldown")
    return slot


class Wire:
    def __init__(self, ws: Any, report: dict[str, Any]):
        self.ws, self.report = ws, report
        self.received, self.events = 0, 0
        self.report_start = time.monotonic()

    async def event(self) -> dict[str, Any]:
        # aiohttp WSMsgType.TEXT is 1; avoid importing optional dependencies offline.
        message = await self.ws.receive()
        if message.type != 1 or not isinstance(message.data, str):
            raise ProbeFailure("websocket_closed_or_invalid_frame")
        size = len(message.data.encode("utf-8"))
        self.received += size
        self.events += 1
        for name, observed, configured in (("frame_bytes", size, MAX_FRAME),
                                            ("received_bytes", self.received, MAX_RECEIVED),
                                            ("events", self.events, MAX_EVENTS)):
            if observed > configured:
                raise ProbeFailure("probe_receive_limit", limit={
                    "name": name, "configured": configured, "observed": observed,
                    "recovery": "stop_and_review_probe_ceiling"})
        try:
            event = json.loads(message.data)
        except ValueError:
            raise ProbeFailure("websocket_invalid_json") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ProbeFailure("websocket_invalid_event")
        if event["type"] == "error":
            raise upstream_error(event)
        self.report["received_bytes"] = self.received
        return event

    async def until(self, kind: str) -> dict[str, Any]:
        while True:
            event = await self.event()
            if event["type"] == kind:
                return event

    async def response(self, *, tool: bool = False) -> str | None:
        response_id = None
        call_id = None
        audio_bytes = 0
        while True:
            event = await self.event()
            kind = event["type"]
            if kind == "response.created":
                response_id = event.get("response", {}).get("id")
                if not isinstance(response_id, str) or not response_id:
                    raise ProbeFailure("response_id_missing")
            elif kind == "response.output_audio.delta":
                if not response_id or event.get("response_id") != response_id:
                    raise ProbeFailure("audio_response_mismatch")
                try:
                    pcm = base64.b64decode(event.get("delta", ""), validate=True)
                except (ValueError, TypeError):
                    raise ProbeFailure("invalid_output_audio") from None
                if not pcm or len(pcm) % 2:
                    raise ProbeFailure("invalid_output_audio")
                audio_bytes += len(pcm)
                self.report.setdefault("first_audio_ms", round((time.monotonic() - self.report_start) * 1000))
            elif kind == "response.function_call_arguments.done":
                if (not tool or call_id is not None or event.get("name") != TOOL["name"]
                        or not response_id or event.get("response_id") != response_id):
                    raise ProbeFailure("unexpected_tool_call")
                try:
                    args = json.loads(event.get("arguments", ""))
                except (ValueError, TypeError):
                    raise ProbeFailure("invalid_inert_tool_arguments") from None
                call_id = event.get("call_id")
                if args != {} or not isinstance(call_id, str) or not call_id:
                    raise ProbeFailure("invalid_inert_tool_call")
            elif kind == "response.done":
                response = event.get("response", {})
                if (not response_id or response.get("id") != response_id
                        or response.get("status") != "completed"):
                    raise ProbeFailure("response_not_completed")
                if tool and call_id is None:
                    raise ProbeFailure("tool_call_not_observed")
                if not tool and not audio_bytes:
                    raise ProbeFailure("audio_not_observed")
                self.report["decoded_audio_bytes"] = self.report.get("decoded_audio_bytes", 0) + audio_bytes
                return call_id

    @staticmethod
    def check_session(event: dict[str, Any], *, manual: bool) -> None:
        session = event.get("session", {})
        audio = session.get("audio", {})
        if (session.get("output_modalities") != ["audio"] or session.get("max_output_tokens") != 128
                or audio.get("input", {}).get("transcription", {}).get("model") != "gpt-4o-mini-transcribe"
                or audio.get("input", {}).get("noise_reduction", {}).get("type") != "near_field"):
            raise ProbeFailure("session_configuration_unconfirmed")
        for direction in ("input", "output"):
            if audio.get(direction, {}).get("format") != {"type": "audio/pcm", "rate": 24000}:
                raise ProbeFailure("session_audio_format_unconfirmed")
        if session.get("model") != MODEL or audio["output"].get("voice") != VOICE:
            raise ProbeFailure("session_model_or_voice_unconfirmed")
        vad = audio["input"].get("turn_detection", "missing")
        if manual:
            if vad is not None:
                raise ProbeFailure("manual_commit_not_configured")
        elif (not isinstance(vad, dict) or vad.get("type") != "server_vad"
              or vad.get("create_response") is not False or vad.get("interrupt_response") is not True):
            raise ProbeFailure("server_vad_not_configured")

    async def exercise(self) -> None:
        self.report_start = time.monotonic()
        self.report["stage"] = "session_created"
        created = await self.until("session.created")
        session = created.get("session", {})
        if session.get("model") != MODEL:
            raise ProbeFailure("session_model_mismatch")
        self.report["checks"].append("session_created")
        self.report["stage"] = "session_configuration"
        # Validate the client's server-VAD settings first, then disable VAD for
        # deterministic manual commit; never race server commits with manual ones.
        config = session_payload()
        config.pop("model")
        await self.ws.send_json({"type": "session.update", "session": config})
        self.check_session(await self.until("session.updated"), manual=False)
        await self.ws.send_json({"type": "session.update", "session": {
            "type": "realtime", "audio": {"input": {"turn_detection": None}}}})
        self.check_session(await self.until("session.updated"), manual=True)
        self.report["checks"].append("session_configuration_accepted")
        self.report["stage"] = "audio_round_trip"
        await self.ws.send_json({"type": "input_audio_buffer.append", "audio": synthetic_audio()})
        await self.ws.send_json({"type": "input_audio_buffer.commit"})
        await self.until("input_audio_buffer.committed")
        await self.ws.send_json({"type": "response.create", "response": {
            "tool_choice": "none", "instructions": "Say ready.", "max_output_tokens": 128}})
        await self.response()
        self.report["checks"].append("audio_round_trip_completed")
        self.report["stage"] = "inert_tool"
        await self.ws.send_json({"type": "response.create", "response": {
            "tool_choice": {"type": "function", "name": TOOL["name"]}, "max_output_tokens": 128}})
        call_id = await self.response(tool=True)
        # Fixed output only; no dispatch, shell, external tool or delegation runs.
        await self.ws.send_json({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": '{"ok":true}'}})
        self.report["checks"].append("inert_tool_call_and_output")
        self.report["stage"] = "tool_continuation"
        await self.ws.send_json({"type": "response.create", "response": {
            "tool_choice": "none", "instructions": "Say ready.", "max_output_tokens": 128}})
        await self.response()
        self.report["checks"].append("tool_continuation_audio_completed")


async def live(slot_index: int, report: dict[str, Any]) -> None:
    from aiohttp import ClientSession, ClientTimeout, TraceConfig, WSServerHandshakeError
    from spartan_route import codex

    # The stdin invocation executes inside the existing Route service; no mount
    # remapping, auth-root override, bootstrap copy or second refresh algorithm.
    if (Path(codex.__file__).resolve() != Path("/app/spartan_route/codex.py")
            or codex.CODEX_AUTH_STORE_ROOT != Path("/app/data/codex-auth")
            or os.environ.get("OPENAI_CODEX_TOKEN")):
        raise ProbeFailure("existing_route_runtime_required")
    report["manager_source_sha256"] = hashlib.sha256(Path(codex.__file__).read_bytes()).hexdigest()
    token = os.environ.get("CLAWROUTE_TOKEN", "").strip()
    port = os.environ.get("CLAWROUTE_PORT", "18790")
    if not token or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ProbeFailure("local_route_auth_unavailable")

    async def reject_redirect(*_args: Any) -> None:
        raise ProbeFailure("redirect_blocked")

    trace = TraceConfig()
    trace.on_request_redirect.append(reject_redirect)
    async with ClientSession(trust_env=True, trace_configs=[trace], timeout=ClientTimeout(total=30)) as http:
        router = codex.CodexRouter(http, None)
        try:
            slot = selected_slot(router, slot_index)
            report["stage"] = "local_slot_preflight"
            async with ClientSession(trust_env=False, timeout=ClientTimeout(total=5)) as local:
                async with local.get(f"http://127.0.0.1:{port}/api/codex/balancer",
                                     headers={"Authorization": f"Bearer {token}"},
                                     allow_redirects=False) as response:
                    if response.status != 200:
                        raise ProbeFailure("local_status_unavailable", response.status)
                    status = checked_slot(await bounded_json(response), slot_index, slot.path)
            report["stage"] = "oauth"
            auth = await load_selected_auth(router, slot, status)
            report["checks"].append("oauth_usable")
            report["stage"] = "mint"
            started = time.monotonic()
            async with http.post(MINT_URL, json={"session": session_payload()},
                                 headers={"Authorization": f"Bearer {auth.access_token}"},
                                 allow_redirects=False) as response:
                report["mint_http_status"] = response.status
                payload = await bounded_json(response)
                if response.status != 200:
                    raise upstream_error(payload, response.status)
                secret = parse_secret(payload)
            report["mint_ms"] = round((time.monotonic() - started) * 1000)
            report["checks"].append("mint_accepted")
            report["stage"] = "websocket_handshake"
            started = time.monotonic()
            try:
                async with http.ws_connect(f"{WS_URL}?model={MODEL}",
                                           headers={"Authorization": f"Bearer {secret}"},
                                           heartbeat=20, max_msg_size=MAX_FRAME,
                                           compress=0) as ws:
                    secret = None
                    report["handshake_ms"] = round((time.monotonic() - started) * 1000)
                    report["checks"].append("websocket_open")
                    await Wire(ws, report).exercise()
                    report["stage"] = "close"
                    await ws.close()
                    if ws.close_code != 1000:
                        raise ProbeFailure("unclean_websocket_close")
                    report["checks"].append("clean_close")
            except WSServerHandshakeError as exc:
                raise upstream_error({}, exc.status) from None
            finally:
                secret = None
        finally:
            await router.cleanup()


async def run(slot: int) -> dict[str, Any]:
    report: dict[str, Any] = {"mode": "live", "route": "subscription-only", "model_candidate": MODEL,
                              "voice_candidate": VOICE, "quota_and_cost_attribution": "unknown",
                              "checks": [], "stage": "runtime_preflight", "success": False}
    started = time.monotonic()
    try:
        async with asyncio.timeout(BUDGET_SECONDS):
            await live(slot, report)
        report["success"] = True
        report["stage"] = "complete"
    except ProbeFailure as exc:
        report["error"] = exc.code
        if exc.status is not None:
            report["http_status"] = exc.status
        if exc.limit is not None:
            report["limit"] = exc.limit
    except TimeoutError:
        report["error"] = "probe_deadline"
        report["limit"] = {"name": "operation_seconds", "configured": BUDGET_SECONDS,
                           "observed": round(time.monotonic() - started, 3),
                           "recovery": "stop_and_review_failed_stage"}
    except Exception:
        # Do not render exceptions: aiohttp exceptions may contain auth headers,
        # URLs, account-store paths or provider-controlled response text.
        report["error"] = "probe_internal_or_transport_error"
    if report["stage"] == "mint":
        report["endpoint"] = MINT_URL
    elif report["stage"] not in {"runtime_preflight", "local_slot_preflight", "oauth"}:
        report["endpoint"] = WS_URL
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--slot", type=int)
    parser.add_argument("--approved-account-use", action="store_true",
                        help="Execution receipt only; obtain explicit account-specific approval first")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(describe(), sort_keys=True))
        return 0
    if not args.approved_account_use or args.slot is None or args.slot < 0:
        print(json.dumps({"success": False, "error": "explicit_slot_and_approval_required"}))
        return 2
    report = asyncio.run(run(args.slot))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
