from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from test_app import CaptureQueue
from test_provider_policy import AUTH, ProviderFixture, rule

PATH = "/v1/decisions"


def request_body(model="openai/gpt-6-luna"):
    # These shapes follow the public reference, not a local chat schema.
    return {"model": model, "input": [
        {"role": "user", "content": [{"type": "input_json", "data": {"damage": True}},
                                        {"type": "input_image", "image_url": "data:image/png;base64,ZmFrZQ=="}]},
        {"type": "function_call", "call_id": "fixture", "name": "inspect", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "fixture", "output": "synthetic evidence"}],
        "questions": [
            {"type": "predicate", "instructions": "Is it damaged?"},
            {"type": "choice", "name": "route", "instructions": [{"type": "input_text", "text": "Select a route."}],
             "choices": [{"value": True}, {"value": "true", "description": "Distinct string"}]},
            {"type": "score", "name": "severity", "instructions": "Rate severity.",
             "levels": [{"label": "none"}, {"label": "high"}]}],
        "prompt_cache_key": "fixture-cache", "prompt_cache_options": {"ttl": "30m"}, "safety_identifier": "fixture-user"}


class DecisionsTests(ProviderFixture):
    async def send(self, body=None, headers=None):
        return await self.client.post(PATH, headers=AUTH if headers is None else headers,
                                      json=request_body() if body is None else body)

    async def test_native_loopback_inputs_answers_usage_and_normal_auth(self):
        response = {"model": "gpt-6-luna", "answers": [
            {"type": "predicate", "name": None, "probability": 0.95},
            {"type": "choice", "name": "route", "choice": True, "confidence": 0.8, "probabilities": [{"value": True, "probability": 0.8}]},
            {"type": "score", "name": "severity", "score": 0.9, "confidence": 0.9, "probabilities": []}],
            "usage": {"input_tokens": 42, "output_tokens": 0, "total_tokens": 42,
                      "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 5},
                      "output_tokens_details": {"reasoning_tokens": 0}}}
        self.response_body = response
        self.assertEqual((await self.send(headers={})).status, 401)
        capture = CaptureQueue()
        with patch.object(self.state.queue, "log", side_effect=capture.log):
            for model in ("openai/gpt-6-luna", "gpt-6-luna"):
                body = request_body(model)
                result = await self.send(body)
                self.assertEqual(result.status, 200)
                self.assertEqual(await result.json(), response)
                path, sent, authorization = self.calls[-1]
                self.assertEqual(path, PATH)
                self.assertEqual(sent, {**body, "model": "gpt-6-luna"})
                self.assertEqual(authorization, "Bearer synthetic-openai-key")
        entry = capture.entries[-1]
        self.assertEqual(entry.request_api_kind, "decisions")
        self.assertEqual((entry.input_tokens, entry.cached_input_tokens, entry.output_tokens), (42, 20, 0))
        self.assertIsNone(entry.selected_codex_slot_index)
        self.assertEqual(entry.actual_cost_usd, 0)
        self.assertIsNone(entry.prompt_preview)

    async def test_empty_input_optional_names_native_options_and_question_limit(self):
        self.response_body = {"model": "gpt-6-luna", "answers": [{"type": "refusal", "name": None}], "usage": {}}
        body = {"model": "gpt-6-luna", "input": "", "questions": [{"type": "predicate", "instructions": ""}]}
        self.assertEqual((await self.send(body)).status, 200)
        self.assertEqual(self.calls[-1][1], body)
        body["questions"] *= 400
        self.assertEqual((await self.send(body)).status, 200)
        body["questions"].append(body["questions"][0])
        before = len(self.calls)
        self.assertEqual((await self.send(body)).status, 400)
        self.assertEqual(len(self.calls), before)
        for patch_body in ({"model": "codex/gpt-6-luna"}, {"model": "openai/gpt-6-astra"}, {"input": {}}, {"questions": []}):
            self.assertEqual((await self.send({**request_body(), **patch_body})).status, 400)

    async def test_provider_endpoint_model_rejection_and_reenable(self):
        self.assertEqual((await self.send()).status, 200)
        for setting in (rule(enabled=False), rule(rejectEndpoints=[PATH]), rule(rejectModels=["gpt-6-luna"])):
            self.assertEqual((await self.save({"openai": setting})).status, 200)
            before = len(self.calls)
            self.assertEqual((await self.send()).status, 403)
            self.assertEqual(len(self.calls), before)
        await self.save({"openai": rule(), "codex": rule(enabled=False)})
        self.assertEqual((await self.send()).status, 200)
        config = await self.get()
        self.assertIn(PATH, config["providers"]["openai"]["supportedEndpoints"])
        self.assertNotIn(PATH, config["providers"]["codex"]["supportedEndpoints"])

    async def test_upstream_error_timeout_missing_key_and_recovery(self):
        self.response_status = 429
        self.response_body = {"error": {"code": "rate_limit_exceeded", "message": "Fixture limit"}}
        result = await self.send()
        self.assertEqual(result.status, 429)
        self.assertEqual(await result.json(), self.response_body)
        self.response_status = 200
        self.delay = 0.05
        with patch.dict("os.environ", SPARTAN_ROUTE_PROVIDER_TIMEOUT_MS="1"):
            result = await self.send()
            self.assertEqual(result.status, 502)
            self.assertEqual((await result.json())["error"]["code"], "provider_request_failed")
        self.delay = 0
        self.response_body = {"answers": [], "usage": {}}
        self.assertEqual((await self.send()).status, 200)
        before = len(self.calls)
        with patch.dict("os.environ", OPENAI_API_KEY=""):
            missing = await self.send()
            self.assertEqual(missing.status, 503)
            self.assertEqual((await missing.json())["error"]["code"], "provider_key_missing")
        self.assertEqual(len(self.calls), before)
        self.assertEqual((await self.send()).status, 200)

    async def test_telemetry_failure_does_not_replace_native_response(self):
        self.response_body = {"model": "gpt-6-luna", "answers": [{"type": "refusal", "name": None}], "usage": {}}
        with patch.object(self.state.queue, "log", side_effect=RuntimeError("fixture queue failure")):
            result = await self.send()
        self.assertEqual(result.status, 200)
        self.assertEqual(await result.json(), self.response_body)

    async def test_saved_policy_does_not_cancel_native_decision(self):
        self.block = True
        pending = asyncio.create_task(self.send())
        await self.dispatched.wait()
        await self.save({"openai": rule(enabled=False)})
        self.assertEqual((await self.send()).status, 403)
        self.release.set()
        self.assertEqual((await pending).status, 200)
