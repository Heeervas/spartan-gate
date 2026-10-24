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
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route import reasoning_replay as replay
from spartan_route.codex import CodexAuth, CodexCallResult
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

    def test_scope_survives_known_account_refresh_but_unknown_credentials_are_not_portable(self):
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
            self.assertTrue(replay.allows_auth(self.auth))
            self.assertFalse(replay.allows_auth(CodexAuth("other", account_id="other")))
        self.assertIsNone(replay.CURRENT.get())
        self.assertEqual(body["input"][0]["encrypted_content"], receipt)

    def test_noncanonical_nested_wrong_types_and_unknown_versions_are_rejected(self):
        # Even a trusted secret holder cannot accidentally issue recursively
        # interpreted receipts; MAC purpose separation is not tenant isolation.
        data = [1, replay.REALM, self.scope, "ciphertext"]
        for invalid in ([True, *data[1:]], [2, *data[1:]], [1, replay.REALM, 5, "ciphertext"],
                        [*data[:3], "sgr:1.nested"], {"duplicate": "not-an-array"}):
            payload = base64.urlsafe_b64encode(json.dumps(invalid, separators=(",", ":")).encode()).rstrip(b"=").decode()
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
        encoded = base64.urlsafe_b64encode(b"bounded").rstrip(b"=").decode()
        with patch.object(replay, "MAX_RECEIPT_BYTES", len(encoded)):
            self.assertEqual(replay._decode(encoded), b"bounded")
            with self.assertRaises(ValueError):
                replay._decode(encoded + "A")

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
