import asyncio
import unittest
from unittest.mock import patch

import test_codex as fixtures
import test_native_cache_transport as cache_fixtures
from spartan_route.app import ExecutionResult, copy_codex_telemetry, sync_codex_stream_result, write_execution_response
from spartan_route.native_cache import NativeCacheDiagnostics, provider_cache_fields
from spartan_route.native_responses import execute_codex_responses


class NativeCacheCleanupTests(unittest.TestCase):
    def test_prepare_failure_and_cancelled_write_finalize_before_logging(self):
        for phase in ("prepare", "write"):
            with self.subTest(phase=phase):
                router = fixtures.NativeResponsesTests.router([cache_fixtures.NativeCacheTransportTests.response(), cache_fixtures.NativeCacheTransportTests.response()])
                cache = NativeCacheDiagnostics()
                logged = []

                class Sink:
                    def __init__(self, **kwargs):
                        pass

                    async def prepare(self, request):
                        if phase == "prepare":
                            raise ConnectionResetError()

                    async def write(self, chunk):
                        raise asyncio.CancelledError()

                    async def write_eof(self):
                        pass

                async def run():
                    source = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", True, "session", "cache", cache)
                    target = ExecutionResult(200)
                    target.stream = sync_codex_stream_result(source, target)

                    async def cleanup():
                        await source.stream_cleanup()
                        copy_codex_telemetry(source, target)

                    target.stream_cleanup = cleanup
                    with patch("spartan_route.app.web.StreamResponse", Sink):
                        try:
                            await write_execution_response(None, target, on_done=lambda: logged.append(target.native_cache))
                        except (ConnectionResetError, asyncio.CancelledError):
                            pass
                    await cleanup()  # Repeated cleanup must not change accounting.
                    following = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", False, "session", "cache", cache)
                    return following

                following = asyncio.run(run())
                self.assertEqual(len(logged), 1)
                self.assertFalse(logged[0]["completed"])
                self.assertEqual(len(logged[0]["attempts"]), 1)
                self.assertEqual(following.native_cache["comparison"], "no_predecessor")
                self.assertFalse(following.native_cache["overlap"])
                self.assertTrue(all(slot.in_flight == 0 for slot in router.slots))

    def test_precommit_output_time_does_not_include_consumer_delay(self):
        router = fixtures.NativeResponsesTests.router([cache_fixtures.NativeCacheTransportTests.response()])

        async def run():
            result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6-astra", True, "session", "cache")
            self.assertIsNotNone(result.first_output_at)
            await asyncio.sleep(0.05)
            async for _ in result.stream:
                pass
            return result

        result = asyncio.run(run())
        self.assertLess(result.native_cache["ttft_ms"], 50)

    def test_documented_reasons_are_preserved_without_free_text(self):
        for reason in ("input_changed", "prompt_cache_key_changed", "service_tier_changed", "text_format_changed", "reasoning_effort_changed", "verbosity_changed", "context_compacted"):
            _, metadata = provider_cache_fields({"prompt_cache_diagnostics": {"type": "cache_miss", "reason": reason}})
            self.assertEqual(metadata["reason"], reason)
        _, metadata = provider_cache_fields({"prompt_cache_diagnostics": {"type": "cache_miss", "reason": "secret text"}})
        self.assertEqual(metadata["reason"], "other")


if __name__ == "__main__":
    unittest.main()
