from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_app as app_fixtures
import test_codex as fixtures
from spartan_route.app import execute_provider_responses
from spartan_route.codex import (
    CodexAuth, CodexRouter, CodexSlot, build_codex_request_body,
    resolve_prompt_cache_key,
)
from spartan_route.native_responses import execute_codex_responses, open_response


class CodexCacheAffinityTests(unittest.TestCase):
    @staticmethod
    def response():
        return fixtures.FakeResponse(events=[
            ("response.output_text.done", {"item_id": "msg_1", "text": "OK"}),
            ("response.completed", {
            "type": "response.completed", "response": {
                "status": "completed", "output": [{
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "OK"}],
                }],
                "usage": {"input_tokens": 3000, "output_tokens": 1,
                          "input_tokens_details": {"cached_tokens": 2000}},
            },
        })])

    def test_both_transports_send_final_key_as_session_header(self):
        for native in (False, True):
            for stream in (False, True):
                with self.subTest(native=native, stream=stream):
                    router = fixtures.NativeResponsesTests.router(
                        [self.response() for _ in range(3)])

                    async def run():
                        for scope in ("session:\N{SNOWMAN}\nprivate\0lineage:0",
                                      "session:\N{SNOWMAN}\nprivate\0lineage:0",
                                      "session:\N{SNOWMAN}\nprivate\0lineage:1"):
                            if native:
                                result = await execute_codex_responses(
                                    router, {"input": "continue"},
                                    "codex/gpt-6.1-sol", stream, "session", scope)
                            else:
                                key = resolve_prompt_cache_key({}, "session", scope)
                                body = build_codex_request_body(
                                    {"messages": [{"role": "user", "content": "continue"}]},
                                    "gpt-6.1-sol", key)
                                # Exercise the actual compatibility transport,
                                # not RetryRouter's stub implementation.
                                result = await CodexRouter._single_chat_call(
                                    router, body, "codex/gpt-6.1-sol", "gpt-6.1-sol",
                                    stream, router.slots[0], router.slots[0].auth)
                            if result.stream:
                                async for _ in result.stream:
                                    pass
                            self.assertEqual(result.status, 200)

                    asyncio.run(run())
                    calls = router.session.calls
                    keys = [call["json"]["prompt_cache_key"] for call in calls]
                    self.assertEqual(keys[0], keys[1])
                    self.assertNotEqual(keys[0], keys[2])
                    for call, key in zip(calls, keys):
                        self.assertEqual(call["headers"].get("session-id"), key)
                        self.assertTrue(key.isascii())
                        self.assertNotIn("\n", key)
                        self.assertNotIn("\0", key)
                        self.assertNotIn("session-id", call["json"])
                        self.assertEqual(call["headers"]["chatgpt-account-id"], "acct-a")

    def test_requests_without_cache_key_omit_session_header(self):
        for native in (False, True):
            with self.subTest(native=native):
                router = fixtures.NativeResponsesTests.router([self.response()])
                body = {"model": "gpt-6.1-sol", "input": [], "stream": True}
                if native:
                    opened = asyncio.run(open_response(router, body, CodexAuth("token")))
                    opened.response.release()
                else:
                    asyncio.run(CodexRouter._single_chat_call(
                        router, body, "codex/gpt-6.1-sol", "gpt-6.1-sol",
                        False, CodexSlot(0), CodexAuth("token")))
                self.assertNotIn("session-id", router.session.calls[0]["headers"])

    def test_retry_preserves_affinity_with_selected_account_auth(self):
        router = fixtures.NativeResponsesTests.router([
            fixtures.FakeResponse(status=500, text='{"error":{"code":"server_error"}}'),
            self.response(),
        ])
        result = asyncio.run(execute_codex_responses(
            router, {"input": "continue"}, "codex/gpt-6.1-sol",
            False, "session", "cache-scope"))
        self.assertEqual(result.status, 200)
        first, second = router.session.calls
        self.assertEqual(first["headers"].get("session-id"),
                         first["json"]["prompt_cache_key"])
        self.assertEqual(first["headers"].get("session-id"),
                         second["headers"].get("session-id"))
        self.assertEqual(first["headers"]["Authorization"], "Bearer token-a")
        self.assertEqual(second["headers"]["Authorization"], "Bearer token-b")
        self.assertNotEqual(first["headers"]["chatgpt-account-id"],
                            second["headers"]["chatgpt-account-id"])

    def test_external_provider_does_not_receive_subscription_affinity(self):
        fixture = app_fixtures.AppCacheDiagnosticsTests()
        state = fixture.state()
        self.addCleanup(state.telemetry.close)
        state.session = app_fixtures.FakeOpenAISession({
            "object": "response", "status": "completed", "output": [],
        })
        route = replace(fixture.route(), provider="openai", actual_model="openai/gpt-6-astra")
        with patch.object(type(state.config), "api_key", return_value="api-token"), patch(
                "spartan_route.app.provider_base_url", return_value="https://provider.invalid/v1"):
            result = asyncio.run(execute_provider_responses(
                state, {"input": "continue"}, route, False, "cache-scope"))
        self.assertEqual(result.status, 200)
        headers = state.session.calls[0]["headers"]
        self.assertNotIn("session-id", headers)
        self.assertNotIn("chatgpt-account-id", headers)


if __name__ == "__main__":
    unittest.main()
