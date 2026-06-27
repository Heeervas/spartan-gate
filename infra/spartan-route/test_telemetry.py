from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.telemetry import RoutingLogEntry, TelemetryStore


class TelemetryTests(unittest.TestCase):
    def test_routing_log_updates_daily_cache_and_live_sessions(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-26T09:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=50000,
                cached_input_tokens=0,
                output_tokens=0,
                response_time_ms=900,
                context_info='{"usage_seen":false}',
            ))
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-26T10:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=750,
                output_tokens=50,
                response_time_ms=1200,
                session_id="session",
                request_id="request",
                turn_id="turn",
                selected_codex_slot_index=1,
                selected_codex_account_key="acct",
                context_info='{"usage_seen":true}',
            ))

            rows = store.daily_cache_rows()
            self.assertEqual(rows[0]["day"], "2026-06-26")
            self.assertEqual(rows[0]["requests"], 1)
            self.assertEqual(rows[0]["logged_requests"], 2)
            self.assertEqual(rows[0]["input_tokens"], 1000)
            self.assertEqual(rows[0]["cached_input_tokens"], 750)
            self.assertEqual(rows[0]["usage_missing_requests"], 1)

            live = store.live_sessions()
            self.assertEqual(live["sessions"][0]["id"], "session")
            self.assertEqual(live["sessions"][0]["metrics"]["cachedInputTokens"], 750)
        finally:
            store.close()

    def test_reset_credit_snapshots_round_trip(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.upsert_reset_credits([
                {
                    "account_key": "acct",
                    "slot_index": 0,
                    "available_count": 2,
                    "details_available": True,
                    "credits": [
                        {
                            "credit_key": "credit",
                            "status": "available",
                            "reset_type": "weekly",
                            "title": "Weekly reset",
                            "granted_at": None,
                            "expires_at": "2026-06-30T00:00:00Z",
                            "redeemed_at": None,
                        }
                    ],
                }
            ])

            snapshots = store.reset_credit_snapshots()
            self.assertEqual(snapshots["acct"]["availableCount"], 2)
            self.assertEqual(snapshots["acct"]["credits"][0]["creditKey"], "credit")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
