import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_codex as fixtures
import test_native_cache_transport as native_fixtures
from spartan_route.cache_experiments import CacheExperiments, MODEL, ProbeRejected, make_probe_manifest, probe_fixture
from spartan_route.native_cache import NativeCacheDiagnostics
from spartan_route.native_responses import execute_codex_responses
from spartan_route.telemetry import TelemetryStore


class ExperimentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = TelemetryStore(":memory:")
        self.addCleanup(self.store.close)
        self.cache = NativeCacheDiagnostics()
        self.controller = CacheExperiments(self.path, self.cache.epoch, self.store, clock=lambda: 1000)
        self.router = fixtures.NativeResponsesTests.router([native_fixtures.NativeCacheTransportTests.response()])
        self.router.load_auth = AsyncMock(side_effect=lambda slot: slot.auth)
        self.run_id = "a" * 32
        manifest = make_probe_manifest(self.cache.epoch, self.run_id, [s.auth.account_key for s in self.router.slots], 1000)
        self.manifest = self.path / "cache-canary.json"
        self.manifest.write_text(json.dumps(manifest))
        self.manifest.chmod(0o600)

    def ticket(self):
        body = probe_fixture(self.run_id, 1)
        return body, self.controller.admit(f"{self.run_id}:1", body, MODEL)

    def execute(self, body, ticket, stream=False):
        return execute_codex_responses(self.router, body, MODEL, stream, "session", "cache", self.cache, self.controller, ticket)

    def test_probe_failure_is_one_attempt_and_no_cross_account_fallback(self):
        self.router.session.responses = [fixtures.FakeResponse(status=500, text='{"error":{"code":"server_error"}}')]
        body, ticket = self.ticket()
        result = asyncio.run(self.execute(body, ticket))
        self.assertEqual(result.status, 500)
        self.assertEqual(len(self.router.session.calls), 1)
        self.assertEqual(result.native_cache["experiment"]["kind"], "probe")
        self.assertIsNone(self.store._conn.execute("SELECT finished_at FROM cache_probe_reservations").fetchone()[0])
        with self.assertRaises(ProbeRejected):
            self.controller.admit(f"{self.run_id}:2", probe_fixture(self.run_id, 2), MODEL)

    def test_unstarted_stream_cleanup_stops_probe_and_releases_slot(self):
        async def run():
            body, ticket = self.ticket()
            result = await self.execute(body, ticket, True)
            await result.stream_cleanup()
            await result.stream_cleanup()
            return result
        result = asyncio.run(run())
        self.assertFalse(result.native_cache["completed"])
        self.assertTrue(all(s.in_flight == 0 for s in self.router.slots))
        self.assertEqual(self.controller._stopped, "probe_failed")

    def test_stop_or_expiry_during_auth_load_prevents_send(self):
        for reason in ("stop", "expiry", "removed", "account_swap"):
            with self.subTest(reason=reason):
                self.controller._stopped = None
                self.controller._pending = None
                self.controller._used.clear()
                self.store._conn.execute("DELETE FROM cache_probe_reservations")
                self.controller.clock = lambda: 1000
                if not self.manifest.exists():
                    self.manifest.write_text(json.dumps(make_probe_manifest(self.cache.epoch, self.run_id, [s.auth.account_key for s in self.router.slots], 1000)))
                    self.manifest.chmod(0o600)
                body, ticket = self.ticket()
                async def choose(*args):
                    if reason == "stop": self.controller.stop("operator_stop")
                    if reason == "expiry": self.controller.clock = lambda: 5000
                    if reason == "removed": self.manifest.unlink()
                    index = 1 if reason == "account_swap" else 0
                    return self.router.slots[index], self.router.slots[index].auth
                self.router.choose_slot = choose
                result = asyncio.run(self.execute(body, ticket))
                self.assertEqual(result.status, 503)
                self.assertEqual(self.router.session.calls, [])
                self.assertEqual(self.store._conn.execute("SELECT outcome FROM cache_probe_reservations").fetchone()[0], "not_sent")

    def test_comparison_id_is_bound_to_own_cold_case(self):
        body, ticket = self.ticket()
        self.controller.finish(ticket, success=True, response_id="resp_own")
        body = probe_fixture(self.run_id, 2)
        body["prompt_cache_options"] = {"comparison_response_id": "resp_other"}
        with self.assertRaisesRegex(ProbeRejected, "invalid_comparison"):
            self.controller.admit(f"{self.run_id}:2", body, MODEL)
        self.controller._stopped = None
        body["prompt_cache_options"]["comparison_response_id"] = "resp_own"
        self.controller.admit(f"{self.run_id}:2", body, MODEL)

    def test_cohort_reservation_failure_skips_mutation_and_failure_stops_run(self):
        body = {"input": "hello"}
        changed = {"input": "hello", "test_marker": True}
        metadata = {"kind": "cohort", "run_id": self.run_id, "arm": "breakpoint"}
        with patch.object(self.controller, "cohort_body", return_value=(changed, metadata)), patch.object(self.store, "reserve_cache_experiment", side_effect=RuntimeError("storage")):
            result = asyncio.run(self.execute(body, None))
        self.assertEqual(result.status, 200)
        self.assertNotIn("test_marker", self.router.session.calls[0]["json"])
        self.assertTrue(result.native_cache["experiment"]["skipped"])
        self.controller._cohort_stopped = False
        attempt = self.controller.reserve_cohort(metadata, "account")
        self.controller.finish_cohort(attempt, False)
        self.assertTrue(self.controller._cohort_stopped)
        self.assertIsNone(self.store._conn.execute("SELECT finished_at FROM cache_experiment_reservations").fetchone()[0])

    def test_candidate_changes_on_key_prefix_tools_and_settings_but_not_suffix(self):
        import copy
        body = probe_fixture(self.run_id, 1)
        signature = self.cache.candidate_signature(body, "key")
        changed = copy.deepcopy(body)
        changed["input"][-1]["content"][0]["text"] = "changed suffix"
        self.assertEqual(signature, self.cache.candidate_signature(changed, "key"))
        self.assertNotEqual(signature, self.cache.candidate_signature(body, "new-key"))
        for item in ({**body, "tools": []}, {**body, "reasoning": {"effort": "high"}}):
            self.assertNotEqual(signature, self.cache.candidate_signature(item, "key"))
        changed["input"][0]["content"][0]["text"] = "changed prefix"
        self.assertNotEqual(signature, self.cache.candidate_signature(changed, "key"))


if __name__ == "__main__":
    unittest.main()
