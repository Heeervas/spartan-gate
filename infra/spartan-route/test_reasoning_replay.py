"""Receipt boundary, not client-signing implementation snapshots."""
import asyncio
import base64
import hashlib
import importlib.util
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route import reasoning_replay as replay
from spartan_route.codex import CodexAuth, CodexCallResult, CodexRouter, CodexSlot
from spartan_route.native_responses import execute_codex_responses
from spartan_route.telemetry import TelemetryStore
import test_codex as codex_fixtures
from spartan_route.native_responses import buffer_precommit, CODEX_RESPONSES_PRECOMMIT_MAX_BYTES
import test_pi_responses as fixtures


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET="41" * 32)
        env.start()
        self.addCleanup(env.stop)
        self.policy = replay.policy({"input": "first"}, replay.REALM)
        self.auth = CodexAuth("token-one", account_id="known-account")
        self.scope = self.policy.account_scope(self.auth)

    def test_issuer_identity_survives_known_account_refresh_but_tracks_unknown_credentials(self):
        refreshed = CodexAuth("token-two", account_id="known-account")
        self.assertEqual(self.policy.account_scope(refreshed), self.scope)
        self.assertNotEqual(self.policy.account_scope(CodexAuth("token", account_id="other")), self.scope)
        self.assertNotEqual(self.policy.account_scope(CodexAuth("token-one")),
                            self.policy.account_scope(CodexAuth("token-two")))

    def test_actual_ciphertext_roundtrip_and_request_context_reset(self):
        opaque = "upstream-ciphertext-☃"
        receipt = self.policy.receipt(opaque, self.scope)
        body = {"input": [{"type": "reasoning", "encrypted_content": receipt}]}
        with replay.replay_request(body, replay.REALM) as prepared:
            self.assertEqual(prepared["input"][0]["encrypted_content"], opaque)
            self.assertEqual(replay.CURRENT.get().scope, self.scope)
        self.assertIsNone(replay.CURRENT.get())
        self.assertEqual(body["input"][0]["encrypted_content"], receipt)

    def test_noncanonical_nested_wrong_types_and_unknown_versions_are_rejected(self):
        # Even a trusted secret holder cannot accidentally issue recursively
        # interpreted receipts; MAC purpose separation is not tenant isolation.
        data = [1, replay.REALM, self.scope, "ciphertext"]
        invalid_data = ([True, *data[1:]], [2, *data[1:]], [1, replay.REALM, 5, "ciphertext"],
                        [*data[:3], "sgr:1.nested"], {"duplicate": "not-an-array"})
        raw_payloads = [json.dumps(invalid, separators=(",", ":")).encode() for invalid in invalid_data]
        raw_payloads.append(json.dumps(data).encode())  # Valid data, noncanonical JSON whitespace.
        for raw in raw_payloads:
            payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
            mac = hmac.new(self.policy.key, b"receipt\0" + payload.encode(), hashlib.sha256).hexdigest()
            with self.assertRaises(replay.ReplayError) as caught:
                self.policy.unwrap(f"sgr:1.{payload}.{mac}", replay.REALM)
            self.assertEqual(caught.exception.code, "reasoning_replay_invalid")

    def test_encoded_size_exact_and_over_limit(self):
        receipt = self.policy.receipt("ciphertext", self.scope)
        with patch.object(replay, "MAX_RECEIPT_BYTES", len(receipt)):
            self.assertEqual(self.policy.receipt("ciphertext", self.scope), receipt)
            self.assertEqual(self.policy.unwrap(receipt, replay.REALM)[0], "ciphertext")
        with patch.object(replay, "MAX_RECEIPT_BYTES", len(receipt) - 1):
            with self.assertRaises(replay.ReplayError):
                self.policy.receipt("ciphertext", self.scope)
            with self.assertRaises(replay.ReplayError):
                self.policy.unwrap(receipt, replay.REALM)
        encoded = base64.urlsafe_b64encode(b"bounded").rstrip(b"=").decode()
        with patch.object(replay, "MAX_RECEIPT_BYTES", len(encoded)):
            self.assertEqual(replay._decode(encoded), b"bounded")
            with self.assertRaises(ValueError):
                replay._decode(encoded + "A")

    def test_mixed_issuers_prefer_last_receipt_and_validate_every_item(self):
        other_scope = self.policy.account_scope(CodexAuth("other", account_id="other"))
        receipts = [self.policy.receipt("ciphertext-A", self.scope), self.policy.receipt("ciphertext-B", other_scope)]
        cases = ((receipts, other_scope, ["ciphertext-A", "ciphertext-B"]),
                 (receipts[::-1], self.scope, ["ciphertext-B", "ciphertext-A"]))
        for ordered, expected_scope, expected_raw in cases:
            body = {"input": [{"type": "reasoning", "encrypted_content": value} for value in ordered]}
            with replay.replay_request(body, replay.REALM) as prepared:
                self.assertEqual(replay.CURRENT.get().scope, expected_scope)
                self.assertEqual([i["encrypted_content"] for i in prepared["input"]], expected_raw)
            self.assertEqual([i["encrypted_content"] for i in body["input"]], ordered)
            for index in (0, 1):
                invalid = [dict(item) for item in body["input"]]
                invalid[index]["encrypted_content"] += "0"
                with self.assertRaises(replay.ReplayError) as caught:
                    replay.policy({"input": invalid}, replay.REALM)
                self.assertEqual(caught.exception.code, "reasoning_replay_invalid")

    def test_receipt_expansion_at_precommit_buffer_boundary_preserves_material_event(self):
        ciphertext = 'x' * CODEX_RESPONSES_PRECOMMIT_MAX_BYTES
        item = {'type': 'reasoning', 'encrypted_content': ciphertext}
        payload = {'type': 'response.output_item.done', 'item': item}
        async def events():
            yield payload['type'], json.dumps(payload)
        buffered, terminal = asyncio.run(buffer_precommit(events(), CodexCallResult(status=200), replay.REALM,
                                                          lambda event: self.policy.output(event, self.scope)))
        self.assertFalse(terminal)
        self.assertEqual(len(buffered), 1)
        self.assertGreater(len(buffered[0]), CODEX_RESPONSES_PRECOMMIT_MAX_BYTES)
        data = json.loads(buffered[0].decode().split('data: ', 1)[1])
        self.assertEqual(self.policy.unwrap(data['item']['encrypted_content'], replay.REALM)[0], ciphertext)
        self.assertEqual(item['encrypted_content'], ciphertext)

    def test_duplicate_events_are_deterministic_and_do_not_mutate_raw_observations(self):
        item = {"type": "reasoning", "encrypted_content": "ciphertext"}
        event = {"type": "response.output_item.done", "item": item}
        terminal = {"response": {"output": [item]}}
        first = self.policy.output(event, self.scope)["item"]["encrypted_content"]
        self.assertEqual(self.policy.output(terminal, self.scope)["response"]["output"][0]["encrypted_content"], first)
        self.assertEqual(item["encrypted_content"], "ciphertext")


class ReplaySchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = patch.dict(os.environ, {"SPARTAN_REASONING_BINDING_SECRET": "41" * 32,
                                      "CODEX_CACHE_LEASE_MODE": "keyed"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.store = TelemetryStore(":memory:")
        self.addCleanup(self.store.close)
        session = codex_fixtures.NativeResponsesTests.SequenceSession([])
        self.router = CodexRouter(session, self.store)
        self.router.slots = [CodexSlot(i, auth=CodexAuth("token-" + account, account_id=account))
                             for i, account in enumerate(("A", "B", "A"))]
        self.router.load_auth = self.load_auth
        self.policy = replay.policy({}, replay.REALM)
        self.issuer = self.router.slots[0].auth
        self.scope = self.policy.account_scope(self.issuer)
        self.receipt = self.policy.receipt("ciphertext-A-☃", self.scope)
        self.body = {"input": [{"type": "reasoning", "encrypted_content": self.receipt}]}

    async def load_auth(self, slot):
        return slot.auth

    async def test_preference_beats_conflicting_lease_without_invalidating_it_or_capping_load(self):
        router = self.router
        router._remember_lease("conversation", router.slots[1])
        lease = router.leases["conversation"]
        for slot in router.slots:
            slot.in_flight = 10 if slot.index != 1 else 0
        with replay.replay_request(self.body, replay.REALM):
            selected, _ = await router.choose_slot("conversation", set(), set())
            self.assertEqual(selected.index, 0)
            self.assertIs(router.leases["conversation"], lease)
            self.assertEqual((await router.model_selection_diagnostics(set(), set(), "model"))["operational_eligible"], 3)
        with replay.replay_request({"input": "fresh"}, replay.REALM):
            self.assertEqual((await router.choose_slot("conversation", set(), set()))[0].index, 1)
            self.assertEqual((await router.choose_slot(None, set(), set()))[0].index, 1)

    async def test_ineligible_preferred_issuer_falls_back_without_bypassing_any_gate(self):
        for gate in ("disabled", "rate", "transient", "auth", "model", "effort", "slot", "account", "unknown-credential"):
            with self.subTest(gate=gate):
                router = self.router
                for slot in router.slots:
                    slot.rate_limited_until = slot.transient_until = 0
                router.model_catalog = None
                router.slots[0].auth = self.issuer
                router.slots[2].auth = self.issuer
                await router.set_slot_routing_enabled(0, gate != "disabled")
                await router.set_slot_routing_enabled(2, gate != "disabled")
                excluded, accounts = set(), set()
                if gate in ("rate", "transient"):
                    for slot in (router.slots[0], router.slots[2]):
                        setattr(slot, "rate_limited_until" if gate == "rate" else "transient_until", time.time() + 3600)
                elif gate == "auth":
                    router.slots[0].auth = router.slots[2].auth = None
                elif gate in ("model", "effort"):
                    router.model_catalog = SimpleNamespace(supports=lambda slot, auth, model, effort:
                        slot.index == 1 or (model != "requested" if gate == "model" else effort != "high"))
                elif gate == "slot":
                    excluded = {0, 2}
                elif gate == "account":
                    accounts = {self.issuer.account_key}
                body = self.body
                if gate == "unknown-credential":
                    scope = self.policy.account_scope(CodexAuth("replaced-token"))
                    body = {"input": [{"type": "reasoning", "encrypted_content": self.policy.receipt("raw", scope)}]}
                    router.slots[1].last_selected_at = -1
                with replay.replay_request(body, replay.REALM):
                    selected = await router.choose_slot(None, excluded, accounts, "requested", "high")
                self.assertIsNotNone(selected, gate)
                self.assertEqual(selected[0].index, 1)

    async def test_retryable_issuer_failure_preserves_exact_history_and_stamps_actual_attempt(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                router = self.router
                router.leases.clear()
                for slot in router.slots:
                    slot.transient_until = slot.last_selected_at = 0
                terminal = {"type": "response.completed", "response": {"status": "completed", "output": [
                    {"type": "reasoning", "encrypted_content": "ciphertext-B"}]}}
                router.session.responses = [codex_fixtures.FakeResponse(status=500, text="retry"),
                                            codex_fixtures.FakeResponse(events=[("response.completed", terminal)])]
                router.session.calls.clear()
                history = [*self.body["input"], {"type": "function_call", "call_id": "call-1", "name": "answer", "arguments": "{}"},
                           {"type": "function_call_output", "call_id": "call-1", "output": "42"}]
                with replay.replay_request({"input": history}, replay.REALM) as prepared:
                    result = await execute_codex_responses(router, prepared, "codex/gpt-6-sol", stream, "conversation", None)
                    self.assertEqual(result.status, 200)
                    if stream:
                        self.assertIsNotNone(result.stream)
                        chunks = [chunk async for chunk in result.stream]
                        payload = json.loads(b"".join(chunks).decode().split("data: ", 1)[1])['response']
                        await result.stream_cleanup()
                    else:
                        payload = result.body
                self.assertEqual(result.slot_index, 1)
                self.assertEqual([call["headers"]["chatgpt-account-id"] for call in router.session.calls], ["A", "B"])
                expected = [{"type": "reasoning", "encrypted_content": "ciphertext-A-☃"}, *history[1:]]
                for call in router.session.calls:
                    self.assertEqual(call["json"]["input"], expected)
                self.assertEqual(history[0]["encrypted_content"], self.receipt)
                raw, scope = self.policy.unwrap(payload["output"][0]["encrypted_content"], replay.REALM)
                self.assertEqual(raw, "ciphertext-B")
                self.assertEqual(scope, self.policy.account_scope(router.slots[1].auth))
                self.assertTrue(all(slot.in_flight == 0 for slot in router.slots))

    async def test_replay_keeps_ordinary_pool_rate_limit_and_model_errors(self):
        for kind, status, code in (("pool", 503, "codex_unavailable"), ("rate", 429, "codex_429"),
                                   ("model", 503, "codex_model_unavailable")):
            with self.subTest(kind=kind):
                for slot in self.router.slots:
                    slot.rate_limited_until = time.time() + 3600 if kind == "rate" else 0
                    slot.auth = self.issuer if kind != "pool" else None
                diagnostics = {"operational_eligible": 3, "model_eligible": 0} if kind == "model" else None
                with replay.replay_request(self.body, replay.REALM):
                    result = await self.router._unavailable_result(diagnostics)
                self.assertEqual((result.status, result.error), (status, code))
                if kind == "rate":
                    self.assertGreater(int(result.headers["Retry-After"]), 0)
                else:
                    self.assertEqual(result.headers, {})


class ReceiptAdmissionTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only the existing real authenticated fixture, not its test cases.
    asyncSetUp = fixtures.PiResponsesTests.asyncSetUp
    asyncTearDown = fixtures.PiResponsesTests.asyncTearDown

    async def test_unchanged_hermes_plugin_signs_stamps_and_replays_raw_against_route(self):
        source = Path(__file__).resolve().parents[1] / 'hermes-plugins/spartan-cache-lineage/__init__.py'
        spec = importlib.util.spec_from_file_location('receipt_legacy_consumer', source)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        opaque = {'type': 'reasoning', 'id': 'rs_legacy', 'encrypted_content': 'legacy-ciphertext'}
        result = fixtures.ExecutionResult(status=200, body={'status': 'completed', 'output': [opaque]})
        base = 'http://spartan-route:18790/v1'
        env = {'HERMES_HOME': '/synthetic-hermes', 'SPARTAN_REASONING_REALM': replay.REALM, 'OPENAI_BASE_URL': base}
        with patch.dict(os.environ, env), patch('spartan_route.app.execute_responses', return_value=result) as execute:
            async def submit(items):
                updated = plugin._llm_request_middleware(request={'model': 'codex/gpt-6-sol', 'input': items},
                    session_id='legacy-session', base_url=base, api_mode='codex_responses')['request']
                body = {**updated, **updated.pop('extra_body')}
                body.pop('extra_body', None)
                response = await self.client.post('/v1/responses', headers=self.headers, json=body)
                self.assertEqual(response.status, 200)
                return await response.json()
            first = await submit('first')
            message = SimpleNamespace(codex_reasoning_items=first['output'])
            plugin._post_api_request(assistant_message=message, session_id='legacy-session', base_url=base, api_mode='codex_responses')
            self.assertIn('_spartan_binding_mac', message.codex_reasoning_items[0])
            second = await submit(message.codex_reasoning_items)
            self.assertEqual(second['output'][0]['encrypted_content'], 'legacy-ciphertext')
            replayed = execute.call_args.args[1]['input'][0]
            self.assertEqual(replayed, opaque)
            self.assertEqual(execute.call_count, 2)

    async def test_standard_admission_has_no_legacy_binding_store_dependency(self):
        result = fixtures.ExecutionResult(status=200, body={"status": "completed", "output": []})
        with patch.object(self.store, "bind_reasoning_chain", side_effect=RuntimeError("synthetic outage")) as bind, patch(
            "spartan_route.app.execute_responses", return_value=result
        ):
            response = await self.client.post("/v1/responses", headers=self.headers,
                                              json={"model": "codex/gpt-6-sol", "input": "first"})
            self.assertEqual(response.status, 200)
            bind.assert_not_called()

    async def test_missing_secret_rejects_first_standard_request_before_inference(self):
        with patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET=""), patch("spartan_route.app.execute_responses") as execute:
            response = await self.client.post("/v1/responses", headers=self.headers,
                                              json={"model": "codex/gpt-6-sol", "input": "first"})
            self.assertEqual(response.status, 503)
            self.assertEqual((await response.json())["error"]["code"], "reasoning_binding_unavailable")
            execute.assert_not_called()
