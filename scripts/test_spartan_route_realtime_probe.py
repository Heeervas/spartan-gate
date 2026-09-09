from __future__ import annotations

import asyncio
import base64
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).with_name("spartan_route_realtime_probe.py")
spec = importlib.util.spec_from_file_location("realtime_probe", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def event(kind, **values):
    return {"type": kind, **values}


def completed(rid, *, audio=False, tool=False):
    result = [event("response.created", response={"id": rid})]
    if audio:
        result.append(event("response.output_audio.delta", response_id=rid,
                            delta=base64.b64encode(b"\x01\x00" * 10).decode()))
    if tool:
        result.append(event("response.function_call_arguments.done", response_id=rid,
                            name="route_probe_ping", call_id="inert-call", arguments="{}"))
    result.append(event("response.done", response={"id": rid, "status": "completed"}))
    return result


class Socket:
    def __init__(self, events):
        self.events = list(events)
        self.sent = []

    async def receive(self):
        if not self.events:
            return SimpleNamespace(type=8, data=None)
        value = self.events.pop(0)
        return SimpleNamespace(type=1, data=json.dumps(value))

    async def send_json(self, value):
        self.sent.append(copy.deepcopy(value))


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_and_missing_approval_are_offline_without_dependencies(self):
        for args, code in (([], 0), (["--live", "--slot", "0"], 2),
                           (["--live", "--approved-account-use"], 2)):
            result = subprocess.run([sys.executable, "-I", str(SOURCE), *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            value = json.loads(result.stdout)
            if code == 0:
                self.assertEqual(value["subscription_access"], "unverified")
                self.assertEqual(value["operations"]["mint"], 1)
            else:
                self.assertEqual(value["error"], "explicit_slot_and_approval_required")

    @unittest.skipUnless(importlib.util.find_spec("aiohttp"), "requires Route aiohttp dependency")
    def test_websocket_explicitly_reuses_https_proxy_and_never_goes_direct(self):
        with patch.dict(os.environ, {"https_proxy": "http://proxy:8888", "HOME": "/nonexistent"}, clear=True):
            kwargs = probe.websocket_proxy_kwargs()
            self.assertEqual(str(kwargs["proxy"]), "http://proxy:8888")
            self.assertIsNone(kwargs["proxy_auth"])
        for env in ({}, {"https_proxy": "http://proxy:8888", "no_proxy": "api.openai.com"}):
            with patch.dict(os.environ, env, clear=True), self.assertRaises(probe.ProbeFailure):
                probe.websocket_proxy_kwargs()

    def test_transport_categories_do_not_render_exception_contents(self):
        exc = type("ClientConnectorError", (Exception,), {})("secret-url-and-headers")
        self.assertEqual(probe.transport_error_code(exc), "transport_connect_failed")
        self.assertEqual(probe.transport_error_code(RuntimeError("private")), "probe_internal_or_transport_error")

    def test_synthetic_audio_is_bounded_pcm(self):
        pcm = base64.b64decode(probe.synthetic_audio(), validate=True)
        self.assertEqual(len(pcm), 24000 * 2 // 4)
        self.assertNotEqual(pcm, b"\0" * len(pcm))

    def test_secret_shape_and_expiry_fail_closed(self):
        for nested in (False, True):
            value = {"value": "ephemeral-fixture", "expires_at": time.time() + 60}
            self.assertEqual(probe.parse_secret({"client_secret": value} if nested else value),
                             "ephemeral-fixture")
        for expiry in (None, True, 0, float("nan"), float("inf"), 10 ** 1000, "tomorrow"):
            with self.subTest(expiry=expiry), self.assertRaises(probe.ProbeFailure):
                probe.parse_secret({"value": "fixture", "expires_at": expiry})

    def status(self):
        return {"slots": [{"slotIndex": 2, "slotPath": "/fixture/auth.json",
                           "routingEnabled": True, "requestedRoutingEnabled": True,
                           "rateLimitedUntil": 0, "transientUntil": 0, "authRetryAt": None}]}

    def test_disabled_cooling_wrong_identity_and_malformed_slots_fail(self):
        status = self.status()
        self.assertEqual(probe.checked_slot(status, 2, "/fixture/auth.json")["slotIndex"], 2)
        for field, value in (("routingEnabled", False), ("requestedRoutingEnabled", False),
                             ("routingIdentityMismatch", True), ("routingIdentityUnverified", True),
                             ("rateLimitedUntil", time.time() + 60), ("transientUntil", float("nan")),
                             ("authRetryAt", True), ("slotPath", "/different/auth.json")):
            changed = copy.deepcopy(status)
            changed["slots"][0][field] = value
            with self.subTest(field=field), self.assertRaises(probe.ProbeFailure):
                probe.checked_slot(changed, 2, "/fixture/auth.json")

    async def test_only_selected_slot_loads_and_account_identity_cannot_change(self):
        slots = [SimpleNamespace(index=i, path=f"/fixture/{i}/auth.json", token=None) for i in range(3)]
        router = SimpleNamespace(slots=slots, load_auth=AsyncMock(return_value=SimpleNamespace(account_key="fixture-account")))
        slot = probe.selected_slot(router, 2)
        self.assertEqual(router.slots, [slots[2]])
        auth = await probe.load_selected_auth(router, slot, {"accountKey": "fixture-account"})
        self.assertEqual(auth.account_key, "fixture-account")
        router.load_auth.assert_awaited_once_with(slots[2])
        with self.assertRaisesRegex(probe.ProbeFailure, "account_identity_changed"):
            await probe.load_selected_auth(router, slot, {"accountKey": "different-account"})
        router.load_auth.return_value = None
        with self.assertRaisesRegex(probe.ProbeFailure, "oauth_unavailable"):
            await probe.load_selected_auth(router, slot, {})
        self.assertEqual(router.slots, [slots[2]])

    async def test_static_or_wrong_slots_are_rejected_before_auth(self):
        router = SimpleNamespace(slots=[SimpleNamespace(index=0, path=None, token="fixture-static")],
                                 load_auth=AsyncMock())
        for index in (0, 99):
            with self.assertRaises(probe.ProbeFailure):
                probe.selected_slot(router, index)
        router.load_auth.assert_not_awaited()

    def test_provider_messages_and_unknown_codes_are_never_reported(self):
        sentinel = "secret-account-and-private-prompt"
        for status in (401, 403, 429, 500):
            failure = probe.upstream_error({"error": {"message": sentinel, "code": sentinel}}, status)
            self.assertNotIn(sentinel, failure.code)
            self.assertNotIn("entitlement", failure.code)
        self.assertEqual(probe.upstream_error({"error": {"code": "insufficient_quota"}}).code, "quota")

    async def test_audio_tools_and_continuation_sequence(self):
        configured = probe.session_payload()
        manual = copy.deepcopy(configured)
        manual["audio"]["input"]["turn_detection"] = None
        events = [event("session.created", session={"model": probe.MODEL}),
                  event("session.updated", session=configured), event("session.updated", session=manual),
                  event("input_audio_buffer.committed")]
        events += completed("first", audio=True) + completed("tool", tool=True) + completed("last", audio=True)
        ws = Socket(events)
        report = {"checks": []}
        await probe.Wire(ws, report).exercise()
        self.assertEqual(report["decoded_audio_bytes"], 40)
        self.assertIn("tool_continuation_audio_completed", report["checks"])
        commands = [message["type"] for message in ws.sent]
        self.assertEqual(commands, ["session.update", "session.update", "input_audio_buffer.append",
                                   "input_audio_buffer.commit", "response.create", "response.create",
                                   "conversation.item.create", "response.create"])
        self.assertEqual(ws.sent[-2]["item"]["call_id"], "inert-call")
        self.assertEqual(ws.sent[-2]["item"]["output"], '{"ok":true}')
        self.assertNotIn("inert-call", json.dumps(report))

    async def test_text_only_response_does_not_prove_audio(self):
        with self.assertRaisesRegex(probe.ProbeFailure, "audio_not_observed"):
            await probe.Wire(Socket(completed("text-only")), {}).response()

    async def test_wrong_response_audio_and_malformed_pcm_fail(self):
        for updates in ({"response_id": "foreign"}, {"delta": "!bad!"},
                        {"delta": base64.b64encode(b"x").decode()}):
            events = completed("first", audio=True)
            events[1].update(updates)
            with self.subTest(updates=updates), self.assertRaises(probe.ProbeFailure):
                await probe.Wire(Socket(events), {}).response()

    async def test_unexpected_or_argument_bearing_tools_never_execute(self):
        for updates in ({"name": "shell"}, {"arguments": '{"command":"private"}'},
                        {"response_id": "foreign"}):
            events = completed("tool", tool=True)
            events[1].update(updates)
            ws = Socket(events)
            with self.subTest(updates=updates), self.assertRaises(probe.ProbeFailure):
                await probe.Wire(ws, {}).response(tool=True)
            self.assertEqual(ws.sent, [])

    def test_session_ack_requires_model_voice_codec_and_vad(self):
        original = event("session.updated", session=probe.session_payload())
        probe.Wire.check_session(original, manual=False)
        for direction in ("input", "output"):
            changed = copy.deepcopy(original)
            changed["session"]["audio"][direction]["format"]["rate"] = 48000
            with self.assertRaises(probe.ProbeFailure):
                probe.Wire.check_session(changed, manual=False)
        for field, value in (("output_modalities", ["text"]), ("max_output_tokens", "inf")):
            changed = copy.deepcopy(original)
            changed["session"][field] = value
            with self.assertRaises(probe.ProbeFailure):
                probe.Wire.check_session(changed, manual=False)
        changed = copy.deepcopy(original)
        changed["session"]["audio"]["input"]["transcription"]["model"] = "different-model"
        with self.assertRaises(probe.ProbeFailure):
            probe.Wire.check_session(changed, manual=False)
        with self.assertRaises(probe.ProbeFailure):
            probe.Wire.check_session(original, manual=True)

    async def test_transport_error_content_redacted_and_budget_bounded(self):
        with patch.object(probe, "live", AsyncMock(side_effect=RuntimeError("Bearer private-token"))):
            result = await probe.run(2)
        self.assertFalse(result["success"])
        self.assertNotIn("private-token", json.dumps(result))
        async def stuck(*args):
            await asyncio.Event().wait()
        with patch.object(probe, "live", stuck), patch.object(probe, "BUDGET_SECONDS", 0.01):
            result = await probe.run(2)
        self.assertEqual(result["error"], "probe_deadline")

    async def test_receive_budget_and_disconnect_fail_closed(self):
        with patch.object(probe, "MAX_FRAME", 5), self.assertRaises(probe.ProbeFailure):
            await probe.Wire(Socket([event("session.created")]), {}).event()
        with self.assertRaises(probe.ProbeFailure):
            await probe.Wire(Socket([]), {}).event()

    async def test_bounded_http_json_handles_fragmentation_and_oversize(self):
        class Content:
            async def iter_chunked(self, size):
                yield b'{"val'
                yield b'ue":1}'
        response = SimpleNamespace(content=Content(), status=200)
        self.assertEqual(await probe.bounded_json(response), {"value": 1})
        with patch.object(probe, "MAX_HTTP_BODY", 4), self.assertRaises(probe.ProbeFailure):
            await probe.bounded_json(response)


if __name__ == "__main__":
    unittest.main()
