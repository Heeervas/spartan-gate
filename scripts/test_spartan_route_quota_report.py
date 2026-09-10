import importlib.util
import sqlite3
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("quota_report", Path(__file__).with_name("spartan-route-quota-report.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QuotaReportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE codex_usage_snapshot_history (
                account_key TEXT, slot_index INTEGER, window TEXT, window_minutes INTEGER,
                observed_at TEXT, reset_at TEXT, used_percent REAL);
            CREATE TABLE codex_account_capacity_overrides (
                account_key TEXT, capacity_multiplier INTEGER, updated_at TEXT);
            CREATE TABLE routing_log (timestamp TEXT, selected_codex_account_key TEXT,
                actual_model TEXT, input_tokens INTEGER, cached_input_tokens INTEGER,
                output_tokens INTEGER, context_info TEXT);
        ''')
        for hour, percent in enumerate((0, 5, 4, 10)):
            self.conn.execute("INSERT INTO codex_usage_snapshot_history VALUES ('account', 1, 'weekly', 10080, ?, '2026-09-14T00:00:00Z', ?)",
                              (f"2026-09-07T0{hour}:00:00Z", percent))
        for hour in range(4):
            self.conn.execute("INSERT INTO routing_log VALUES (?, 'account', 'codex/gpt-6-astra', 100000, 60000, 1000, ?)",
                              (f"2026-09-07T0{hour}:00:00Z", '{"usage_seen":true}'))

    def report(self):
        return module.quota_report(self.conn, "codex/gpt-6-astra", "2026-09-07T00:00:00Z")

    def test_signed_endpoints_do_not_sum_positive_changes(self):
        report = self.report()
        self.assertEqual(report["totals"]["units"], 10)
        self.assertEqual(report["total_tokens"], 303000)
        self.assertEqual(report["totals"]["cached_input_tokens"], 180000)
        self.assertIsNone(report["category_rates"])

    def test_x5_conversion_and_reset_boundary(self):
        self.conn.execute("INSERT INTO codex_account_capacity_overrides VALUES ('account',5,'2026-09-01T00:00:00Z')")
        self.assertEqual(self.report()["totals"]["units"], 50)
        self.conn.execute("UPDATE codex_usage_snapshot_history SET reset_at='2026-09-15T00:00:00Z' WHERE used_percent=10")
        self.assertEqual(self.report()["totals"]["units"], 20)

    def test_mixed_model_missing_usage_and_unattributed_rows_are_excluded(self):
        for update in (
            "UPDATE routing_log SET actual_model='codex/gpt-5.6-sol'",
            "UPDATE routing_log SET context_info='{}'",
            "UPDATE routing_log SET selected_codex_account_key=NULL",
        ):
            with self.subTest(update=update):
                self.conn.execute("SAVEPOINT trial")
                self.conn.execute(update)
                self.assertIsNone(self.report()["units_per_million_tokens"])
                self.conn.execute("ROLLBACK TO trial")
                self.conn.execute("RELEASE trial")

    def test_retention_gap_and_zero_tokens_do_not_create_rates(self):
        self.conn.execute("DELETE FROM routing_log WHERE timestamp='2026-09-07T00:00:00Z'")
        self.assertEqual(self.report()["totals"]["units"], 5)
        self.conn.execute("UPDATE routing_log SET input_tokens=0,cached_input_tokens=0,output_tokens=0")
        self.assertIsNone(self.report()["units_per_million_tokens"])

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            module.quota_report(self.conn, "codex/gpt-6-astra", "2026-09-07")


if __name__ == "__main__":
    unittest.main()
