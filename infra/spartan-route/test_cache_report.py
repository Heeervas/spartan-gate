import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from spartan_route.cache_probe_cli import ProbeError, cohort_policy, consume_probe, write_private
from spartan_route.cache_report import baseline_report, cohort_report, probe_benefit, quota_evidence


def row(session, arm=None, *, cached=6000, account="a"):
    native = dict(version=2, completed=True, overlap=False, cache_read_known=True, account_key=account,
                  session_scope=f"{session:064x}", candidate_signature="a" * 64, predecessor_gap_ms=1000,
                  observed_chain_age_ms=10000, ttft_ms=100, developer_boundary={"changed_suffix": True})
    if arm:
        native["experiment"] = dict(kind="cohort", run_id="b" * 32, arm=arm)
    return dict(native=native, input_tokens=10000, cached_input_tokens=cached, output_tokens=100, error=None)


class CacheReportTests(unittest.TestCase):
    def test_baseline_waits_for_full_day_and_recurring_real_pattern(self):
        rows = [row(i % 2, account="a" if i % 2 else "b") for i in range(20)]
        self.assertFalse(baseline_report(rows, started=0, now=86399)["applicable"])
        self.assertTrue(baseline_report(rows, started=0, now=86400)["applicable"])
        for item in rows:
            item["native"]["developer_boundary"]["changed_suffix"] = False
        self.assertEqual(baseline_report(rows, started=0, now=172800)["status"], "inapplicable")

    def cases(self):
        return [dict(case=case, completed=True, cache_read_known=True, input_tokens=10000,
                     cached_input_tokens=8000 if (case - 1) % 6 >= 3 else 6000) for case in range(1, 13)]

    def test_probe_needs_benefit_on_both_accounts_and_known_warm_replays(self):
        cases = self.cases()
        self.assertTrue(probe_benefit(cases)["verified"])
        self.assertFalse(probe_benefit(cases[:-1])["verified"])
        cases[7]["cached_input_tokens"] = 0
        self.assertFalse(probe_benefit(cases)["verified"])
        cases = self.cases()
        cases[-1]["cached_input_tokens"] = 5000
        self.assertFalse(probe_benefit(cases)["verified"])

    def dataset(self):
        return [row(session + (100 if arm == "breakpoint" else 0), arm, cached=8000 if arm == "breakpoint" else 6000)
                for arm in ("control", "breakpoint") for session in range(10) for _ in range(10)]

    def test_matched_session_bootstrap_and_quality_gate(self):
        report = cohort_report(self.dataset(), "b" * 32, quota={"supports_savings": True})
        self.assertTrue(report["numerical_gates_pass"])
        self.assertEqual(report["status"], "review_required")
        self.assertFalse(report["expand"])
        self.assertEqual(report["confidence_interval"], [.5, .5])
        self.assertEqual(report["relative_reduction"], .5)

    def test_small_mismatched_missing_or_regressed_data_stays_off(self):
        rows = self.dataset()
        self.assertEqual(cohort_report(rows[:150], "b" * 32)["status"], "inconclusive")
        self.assertFalse(cohort_report(rows, "b" * 32)["numerical_gates_pass"])
        for item in rows[100:]:
            item["native"]["ttft_ms"] = 111
        self.assertFalse(cohort_report(rows, "b" * 32, quota={"supports_savings": True})["numerical_gates_pass"])
        for item in rows[100:]:
            item["native"]["predecessor_gap_ms"] = 99999999
        self.assertEqual(cohort_report(rows, "b" * 32)["common_strata"], 0)

    def test_cohort_creation_requires_baseline_probes_and_both_session_arms(self):
        baseline = baseline_report([row(i, account="a" if i % 2 else "b") for i in range(64)], started=0, now=86400)
        probe = dict(epoch="epoch", run_id="b" * 32, status="completed", cases=self.cases(), accounts=["a", "b"])
        policy = cohort_policy({"epoch": "epoch"}, baseline, probe, now=1000)
        self.assertEqual(policy["expires_at"], 1000 + 7 * 86400)
        self.assertEqual(len(policy["session_scopes"]), 64)
        with self.assertRaises(ProbeError):
            cohort_policy({"epoch": "restart"}, baseline, probe, now=1000)
        baseline["session_scopes"] = baseline["session_scopes"][:2]
        with self.assertRaises(ProbeError):
            cohort_policy({"epoch": "epoch"}, baseline, probe, now=1000)

    def test_quota_evidence_rejects_rounding_noise_and_account_mix(self):
        def evidence(control, treatment):
            return {arm: {"samples": [
                dict(account_key=account, units=units, multiplier=1, input_tokens=990000,
                     output_tokens=10000, requests=100)
                for account in ("a", "b") for _ in range(2)]}
                for arm, units in (("control", control), ("breakpoint", treatment))}
        self.assertFalse(quota_evidence(evidence(10, 9))["supports_savings"])
        good = evidence(100, 40)
        self.assertTrue(quota_evidence(good)["supports_savings"])
        good["breakpoint"]["samples"][0]["account_key"] = "other"
        self.assertFalse(quota_evidence(good)["supports_savings"])

    def test_failed_treatment_retry_cannot_disappear_behind_success(self):
        rows = self.dataset()
        recovered = row(999)
        recovered["native"]["attempts"] = [
            {"completed": False, "cache_read_known": False, "experiment": {"kind": "cohort", "run_id": "b" * 32, "arm": "breakpoint"}},
            {"completed": True, "cache_read_known": True}]
        report = cohort_report([*rows, recovered], "b" * 32, quota={"supports_savings": True})
        self.assertEqual(report["failed_treatment_attempts"], 1)
        self.assertFalse(report["numerical_gates_pass"])
        self.assertFalse(cohort_report(rows, "b" * 32, quota={"supports_savings": True}, reservation_issues=1)["numerical_gates_pass"])

    def test_cohort_cannot_count_sessions_on_unprobed_accounts(self):
        baseline = baseline_report([row(i, account="c" if i % 2 else "d") for i in range(64)], started=0, now=86400)
        probe = dict(epoch="epoch", run_id="b" * 32, status="completed", cases=self.cases(), accounts=["a", "b"])
        with self.assertRaises(ProbeError):
            cohort_policy({"epoch": "epoch"}, baseline, probe, now=1000)

    def test_client_report_failure_revokes_probe_before_final_write(self):
        from unittest.mock import patch
        from spartan_route.cache_probe_cli import run_probes
        class Route:
            calls = 0
            def get(self, path):
                return {"slots": [dict(accountKey=a, routingEnabled=True, authAvailable=True, healthStatus="healthy") for a in ("a", "b")]}
            def open(self, *args):
                self.calls += 1
                return io.BytesIO(b'data: {"type":"response.completed","response":{"status":"completed","output":[],"usage":{"input_tokens":1000,"input_tokens_details":{"cached_tokens":0}}}}\\n\\n')
        route = Route()
        baseline = baseline_report([row(i, account="a" if i % 2 else "b") for i in range(20)], started=0, now=86400)
        def fail_replace(path, value, *, replace=False):
            if replace:
                raise OSError("synthetic disk failure")
            write_private(path, value)
        with tempfile.TemporaryDirectory() as directory, patch("spartan_route.cache_probe_cli.write_private", side_effect=fail_replace):
            with self.assertRaises(OSError):
                run_probes(route, Path(directory), {"epoch": "epoch"}, baseline)
            self.assertFalse((Path(directory) / "cache-canary.json").exists())
            self.assertEqual(route.calls, 1)

    def test_probe_consumer_preserves_unknown_and_never_executes_tools(self):
        response = dict(type="response.completed", response=dict(id="resp_cold", status="completed", output=[],
                        usage=dict(input_tokens=10000, output_tokens=2, input_tokens_details=dict(cached_tokens=2000))))
        stream = lambda: io.BytesIO(b"data: " + json.dumps(response).encode() + b"\n\ndata: [DONE]\n\n")
        result, response_id = consume_probe(stream())
        self.assertEqual(response_id, "resp_cold")
        self.assertIsNone(result["cache_write_tokens"])
        response["response"]["output"] = [{"type": "function_call", "name": "danger", "arguments": "{}"}]
        with self.assertRaises(ProbeError):
            consume_probe(stream())
        response["response"]["output"] = []
        del response["response"]["usage"]["input_tokens_details"]
        with self.assertRaises(ProbeError):
            consume_probe(stream())

    def test_private_manifest_publication_is_exclusive_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest"
            write_private(path, {"valid": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ProbeError):
                write_private(path, {"other": True})
            write_private(path, {"next": True}, replace=True)
            self.assertEqual(json.loads(path.read_text()), {"next": True})
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                write_private(link, {"bad": True}, replace=True)
            self.assertEqual(json.loads(path.read_text()), {"next": True})


if __name__ == "__main__":
    unittest.main()
