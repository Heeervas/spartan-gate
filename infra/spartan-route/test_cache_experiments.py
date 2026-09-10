import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from spartan_route.cache_experiments import CacheExperiments, MODEL, ProbeRejected, make_probe_manifest, probe_fixture
from spartan_route.telemetry import TelemetryStore


class CacheExperimentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.store = TelemetryStore(":memory:")
        self.addCleanup(self.store.close)
        self.controller = CacheExperiments(self.path, "epoch", self.store, clock=lambda: 1000)
        self.run_id = "a" * 32
        self.manifest = make_probe_manifest("epoch", self.run_id, ["a", "b"], 1000)

    def write(self, value=None, name="cache-canary.json"):
        path = self.path / name
        path.write_text(json.dumps(self.manifest if value is None else value))
        path.chmod(0o600)
        return path

    def admit(self, case=1, body=None):
        return self.controller.admit(f"{self.run_id}:{case}", body or probe_fixture(self.run_id, case), MODEL)

    def test_missing_or_unsafe_manifest_never_reserves(self):
        for mode in ("missing", "public", "symlink", "oversized"):
            with self.subTest(mode=mode):
                self.controller = CacheExperiments(self.path, "epoch", self.store, clock=lambda: 1000)
                path = self.path / "cache-canary.json"
                path.unlink(missing_ok=True)
                if mode != "missing":
                    self.write()
                    if mode == "public":
                        path.chmod(0o644)
                    elif mode == "symlink":
                        path.rename(self.path / "target")
                        path.symlink_to(self.path / "target")
                    else:
                        path.write_text(" " * 32769)
                with self.assertRaises(ProbeRejected):
                    self.admit()
        self.assertEqual(self.store._conn.execute("SELECT count(*) FROM cache_probe_reservations").fetchone()[0], 0)

    def test_invalid_manifest_and_budget_fail_closed(self):
        for key, value in (("epoch", "old"), ("expires_at", 1000), ("expires_at", float("nan")),
                           ("max_attempts", 13), ("max_attempts", True), ("max_estimated_input_tokens", 100001),
                           ("max_estimated_input_tokens", -1), ("accounts", ["a", "a"]), ("cases", [])):
            with self.subTest(key=key, value=value):
                self.controller = CacheExperiments(self.path, "epoch", self.store, clock=lambda: 1000)
                self.write({**self.manifest, key: value})
                with self.assertRaises(ProbeRejected):
                    self.admit()

    def test_sequential_twelve_cases_and_durable_budget(self):
        self.write()
        for case in range(1, 13):
            ticket = self.admit(case)
            self.assertEqual(ticket.account, "a" if case <= 6 else "b")
            self.controller.finish(ticket, success=True)
        rows = self.store._conn.execute("SELECT * FROM cache_probe_reservations").fetchall()
        self.assertEqual(len(rows), 12)
        self.assertLess(sum(row["estimated_input_tokens"] for row in rows), 100000)
        with self.assertRaises(ProbeRejected):
            self.admit(12)
        restarted = CacheExperiments(self.path, "new-epoch", self.store, clock=lambda: 1000)
        with self.assertRaises(ProbeRejected):
            restarted.admit(f"{self.run_id}:1", probe_fixture(self.run_id, 1), MODEL)

    def test_concurrent_out_of_order_tampered_and_failed_calls_stop_run(self):
        for mode in ("concurrent", "order", "tools", "failure", "changed"):
            with self.subTest(mode=mode):
                self.store._conn.execute("DELETE FROM cache_probe_reservations")
                self.controller = CacheExperiments(self.path, "epoch", self.store, clock=lambda: 1000)
                self.write()
                body = probe_fixture(self.run_id, 1)
                case = 1
                if mode in ("concurrent", "failure", "changed"):
                    ticket = self.admit()
                    case, body = 2, probe_fixture(self.run_id, 2)
                    if mode == "failure":
                        self.controller.finish(ticket, success=False)
                    if mode == "changed":
                        self.controller.finish(ticket, success=True)
                        self.write({**self.manifest, "expires_at": 4000})
                elif mode == "order":
                    case, body = 2, probe_fixture(self.run_id, 2)
                else:
                    body["tools"] = [{"type": "function", "name": "danger"}]
                with self.assertRaises(ProbeRejected):
                    self.admit(case, body)
                with self.assertRaises(ProbeRejected):
                    self.admit()

    def test_failed_reservation_stays_unresolved_and_not_sent_closes(self):
        self.write()
        ticket = self.admit()
        self.controller.finish(ticket, success=False)
        row = self.store._conn.execute("SELECT finished_at,outcome FROM cache_probe_reservations").fetchone()
        self.assertEqual(tuple(row), (None, "unresolved"))
        # Duplicate cleanup cannot overwrite an ambiguous abort with success.
        self.controller.finish(ticket, success=True)
        self.assertIsNone(self.store._conn.execute("SELECT finished_at FROM cache_probe_reservations").fetchone()[0])

    def test_cohort_is_off_without_valid_policy_and_preserves_original(self):
        body = probe_fixture(self.run_id, 1)
        scope = "1" * 64
        call = lambda b=body, **kwargs: self.controller.cohort_body(b, model=MODEL, account=kwargs.get("account", "a"), session_scope=scope, candidate_signature="3" * 64)
        self.assertEqual(call(), (body, None))
        policy = dict(version=1, epoch="epoch", model=MODEL, run_id=self.run_id, issued_at=999, expires_at=2000,
                      accounts=["a", "b"], session_scopes=[scope, "2" * 64], candidates={scope: ["3" * 64]}, baseline_applicable=True, probe_benefit_verified=True)
        for scopes in ([scope, "2" * 64],):
            policy["session_scopes"] = scopes
            self.write(policy, "cache-cohort.json")
            original = copy.deepcopy(body)
            updated, metadata = call()
            self.assertEqual(body, original)
            self.assertEqual(call()[1], metadata)
            if metadata["arm"] == "breakpoint":
                del updated["input"][0]["content"][0]["prompt_cache_breakpoint"]
                self.assertEqual(updated, body)
            self.assertTrue(call(account="unknown")[1]["skipped"])
            self.assertTrue(call({"instructions": "keep", "input": []})[1]["skipped"])
            marked = probe_fixture(self.run_id, 4)
            self.assertTrue(call(marked)[1]["skipped"])
        for key, value in (("epoch", "restart"), ("expires_at", 999), ("expires_at", 99999999),
                           ("issued_at", True), ("probe_benefit_verified", False), ("accounts", ["a", "a"])):
            self.write({**policy, key: value}, "cache-cohort.json")
            self.assertEqual(call(), (body, None))


if __name__ == "__main__":
    unittest.main()
