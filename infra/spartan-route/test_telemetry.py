from __future__ import annotations

import sqlite3
import sys
import tempfile
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
                upstream_response_time_ms=120,
                total_response_time_ms=900,
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
                upstream_response_time_ms=250,
                total_response_time_ms=1200,
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
            self.assertEqual(rows[0]["avg_response_time_ms"], 185)
            self.assertEqual(rows[0]["max_response_time_ms"], 250)
            self.assertEqual(rows[0]["split_latency_requests"], 2)
            self.assertEqual(rows[0]["avg_total_response_time_ms"], 1050)
            self.assertEqual(rows[0]["max_total_response_time_ms"], 1200)

            today = store.today_summary("2026-06-26")
            self.assertEqual(today["loggedRequests"], 2)
            self.assertEqual(today["usageSeenRequests"], 1)
            self.assertEqual(today["latencyMs"]["avg"], 185)
            self.assertEqual(today["latencyMs"]["p50"], 120)
            self.assertEqual(today["latencyMs"]["p90"], 250)
            self.assertEqual(today["totalLatencyMs"]["avg"], 1050)
            self.assertEqual(today["usageMissingRequests"], 1)

            live = store.live_sessions()
            self.assertEqual(live["sessions"][0]["id"], "session")
            self.assertEqual(live["sessions"][0]["metrics"]["cachedInputTokens"], 750)
            self.assertEqual(live["sessions"][0]["metrics"]["latencyMs"]["avg"], 250)
            self.assertEqual(live["sessions"][0]["metrics"]["totalLatencyMs"]["avg"], 1200)
            self.assertEqual(live["sessions"][0]["turns"][0]["id"], "turn")
            self.assertNotIn("requests", live["sessions"][0]["turns"][0])
        finally:
            store.close()

    def test_cache_likely_and_low_metrics_are_reported(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            entries = [
                ("request-a", 100000, 90000, '{"usage_seen":true,"cache_likely":true,"cache_low":false}'),
                ("request-b", 80000, 0, '{"usage_seen":true,"cache_likely":true,"cache_low":true}'),
                ("request-c", 12000, 0, '{"usage_seen":true,"cache_likely":false,"cache_low":false}'),
            ]
            for request_id, input_tokens, cached_tokens, context_info in entries:
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-06-26T10:00:00.000Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=50,
                    response_time_ms=1200,
                    upstream_response_time_ms=250,
                    total_response_time_ms=1200,
                    session_id="session",
                    request_id=request_id,
                    turn_id="turn",
                    context_info=context_info,
                ))

            rows = store.daily_cache_rows()
            self.assertEqual(rows[0]["requests"], 3)
            self.assertEqual(rows[0]["cache_likely_requests"], 2)
            self.assertEqual(rows[0]["cache_likely_input_tokens"], 180000)
            self.assertEqual(rows[0]["cache_likely_cached_input_tokens"], 90000)
            self.assertEqual(rows[0]["cache_likely_hit_percent"], 50.0)
            self.assertEqual(rows[0]["cache_low_requests"], 1)
            self.assertEqual(rows[0]["cache_low_input_tokens"], 80000)

            today = store.today_summary("2026-06-26")
            self.assertEqual(today["cacheLikelyRequests"], 2)
            self.assertEqual(today["cacheLikelyInputTokens"], 180000)
            self.assertEqual(today["cacheLikelyCachedInputTokens"], 90000)
            self.assertEqual(today["cacheLikelyHitPercent"], 50.0)
            self.assertEqual(today["cacheLowRequests"], 1)
            self.assertEqual(today["cacheLowInputTokens"], 80000)

            live = store.live_sessions()
            metrics = live["sessions"][0]["metrics"]
            self.assertEqual(metrics["cacheLikelyRequests"], 2)
            self.assertEqual(metrics["cacheLikelyInputTokens"], 180000)
            self.assertEqual(metrics["cacheLikelyCachedInputTokens"], 90000)
            self.assertEqual(metrics["cacheLikelyHitPercent"], 50.0)
            self.assertEqual(metrics["cacheLowRequests"], 1)
            self.assertEqual(metrics["cacheLowInputTokens"], 80000)
        finally:
            store.close()

    def test_legacy_latency_rows_fall_back_to_response_time(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-25T09:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=500,
                response_time_ms=1300,
                context_info='{"usage_seen":true}',
            ))
            rows = store.daily_cache_rows()
            self.assertEqual(rows[0]["avg_response_time_ms"], 1300)
            self.assertEqual(rows[0]["avg_total_response_time_ms"], 1300)
            self.assertEqual(rows[0]["legacy_latency_requests"], 1)
            self.assertEqual(rows[0]["split_latency_requests"], 0)
            today = store.today_summary("2026-06-25")
            self.assertEqual(today["splitLatencyRequests"], 0)
            self.assertEqual(today["firstResponseLatencyMs"], today["latencyMs"])
        finally:
            store.close()

    def test_split_first_response_latency_ignores_legacy_rows_when_available(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-27T09:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=500,
                response_time_ms=13000,
                context_info='{"usage_seen":true}',
            ))
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-27T10:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=800,
                response_time_ms=120,
                upstream_response_time_ms=120,
                total_response_time_ms=900,
                session_id="session",
                turn_id="turn",
                request_id="request-a",
                context_info='{"usage_seen":true}',
            ))
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-27T11:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=900,
                response_time_ms=250,
                upstream_response_time_ms=250,
                total_response_time_ms=1200,
                session_id="session",
                turn_id="turn",
                request_id="request-b",
                context_info='{"usage_seen":true}',
            ))

            rows = store.daily_cache_rows()
            self.assertEqual(rows[0]["avg_response_time_ms"], 185)
            self.assertEqual(rows[0]["max_response_time_ms"], 250)
            self.assertEqual(rows[0]["split_latency_requests"], 2)
            self.assertEqual(rows[0]["legacy_latency_requests"], 1)

            today = store.today_summary("2026-06-27")
            self.assertEqual(today["legacyLatencyRequests"], 1)
            self.assertEqual(today["splitLatencyRequests"], 2)
            self.assertEqual(today["firstResponseLatencyMs"]["avg"], 185)
            self.assertEqual(today["firstResponseLatencyMs"]["p50"], 120)
            self.assertEqual(today["firstResponseLatencyMs"]["p90"], 250)
            self.assertGreater(today["latencyMs"]["avg"], today["firstResponseLatencyMs"]["avg"])

            live = store.live_sessions()
            metrics = live["sessions"][0]["metrics"]
            self.assertEqual(metrics["splitLatencyRequests"], 2)
            self.assertEqual(metrics["firstResponseLatencyMs"]["avg"], 185)
        finally:
            store.close()

    def test_stream_error_only_day_is_visible_without_usage_tokens(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-06-28T09:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=60000,
                cached_input_tokens=0,
                output_tokens=0,
                response_time_ms=1200,
                upstream_response_time_ms=1200,
                total_response_time_ms=180000,
                session_id="session-stream",
                request_id="request-stream",
                turn_id="turn-stream",
                error="codex_stream_timeout",
                context_info='{"usage_seen":false}',
            ))

            rows = store.daily_cache_rows()
            self.assertEqual(rows[0]["day"], "2026-06-28")
            self.assertEqual(rows[0]["requests"], 0)
            self.assertEqual(rows[0]["logged_requests"], 1)
            self.assertEqual(rows[0]["input_tokens"], 0)
            self.assertEqual(rows[0]["cached_input_tokens"], 0)
            self.assertEqual(rows[0]["errors"], 1)
            self.assertEqual(rows[0]["usage_missing_requests"], 1)
            self.assertEqual(rows[0]["split_latency_requests"], 1)

            live = store.live_sessions()
            metrics = live["sessions"][0]["metrics"]
            self.assertEqual(metrics["requests"], 1)
            self.assertEqual(metrics["errors"], 1)
            self.assertEqual(metrics["usageSeenRequests"], 0)
            self.assertEqual(metrics["usageMissingRequests"], 1)
            self.assertEqual(metrics["inputTokens"], 0)
            self.assertEqual(metrics["cachedInputTokens"], 0)
            self.assertEqual(metrics["latencyMs"]["avg"], 1200)
            self.assertEqual(metrics["totalLatencyMs"]["avg"], 180000)
        finally:
            store.close()

    def test_existing_database_gets_latency_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "spartan-route.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE routing_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        original_model TEXT NOT NULL,
                        routed_model TEXT NOT NULL,
                        actual_model TEXT NOT NULL,
                        tier TEXT NOT NULL,
                        classification_reason TEXT NOT NULL DEFAULT '',
                        confidence REAL NOT NULL DEFAULT 0,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        original_cost_usd REAL NOT NULL DEFAULT 0,
                        actual_cost_usd REAL NOT NULL DEFAULT 0,
                        savings_usd REAL NOT NULL DEFAULT 0,
                        escalated INTEGER NOT NULL DEFAULT 0,
                        escalation_chain TEXT NOT NULL DEFAULT '[]',
                        response_time_ms INTEGER NOT NULL DEFAULT 0,
                        had_tool_calls INTEGER NOT NULL DEFAULT 0,
                        is_dry_run INTEGER NOT NULL DEFAULT 0,
                        is_override INTEGER NOT NULL DEFAULT 0,
                        session_id TEXT,
                        error TEXT,
                        prompt_preview TEXT,
                        context_info TEXT,
                        cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                        request_api_kind TEXT NOT NULL DEFAULT 'chat_completions',
                        requested_reasoning_effort TEXT,
                        selected_codex_slot_index INTEGER,
                        selected_codex_account_key TEXT,
                        request_id TEXT,
                        turn_id TEXT
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            store = TelemetryStore(db_path)
            try:
                columns = {
                    row["name"]
                    for row in store._conn.execute("PRAGMA table_info(routing_log)").fetchall()
                }
                self.assertIn("upstream_response_time_ms", columns)
                self.assertIn("total_response_time_ms", columns)
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-06-25T09:00:00.000Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    response_time_ms=75,
                    upstream_response_time_ms=75,
                    total_response_time_ms=900,
                    context_info='{"usage_seen":true}',
                ))
            finally:
                store.close()

    def test_live_sessions_and_turns_are_recent_first(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            entries = [
                ("session-a", "turn-old", "2026-06-26T09:00:00.000Z", 100, 0),
                ("session-b", "turn-newer", "2026-06-26T10:00:00.000Z", 200, 0),
                ("session-a", "turn-latest", "2026-06-26T11:00:00.000Z", 300, 100),
            ]
            for index, (session_id, turn_id, timestamp, input_tokens, cached_tokens) in enumerate(entries):
                store.log_routing(RoutingLogEntry(
                    timestamp=timestamp,
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=10,
                    response_time_ms=100 + index,
                    session_id=session_id,
                    request_id=f"request-{index}",
                    turn_id=turn_id,
                    context_info='{"usage_seen":true}',
                ))

            live = store.live_sessions(session_limit=5, turn_limit=5)
            self.assertEqual([session["id"] for session in live["sessions"]], ["session-a", "session-b"])
            self.assertEqual([turn["id"] for turn in live["sessions"][0]["turns"]], ["turn-latest", "turn-old"])
            self.assertEqual(live["sessions"][0]["turns"][0]["metrics"]["cachedInputTokens"], 100)
        finally:
            store.close()

    def test_slot_routing_overrides_round_trip(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.set_slot_routing_enabled(
                2,
                False,
                account_key="acct",
                slot_path_hash="path-hash",
                auth_fingerprint_hash="auth-hash",
            )
            overrides = store.slot_routing_overrides()
            self.assertFalse(overrides[2]["routingEnabled"])
            self.assertEqual(overrides[2]["accountKey"], "acct")
            self.assertEqual(store.slot_routing_override(2)["slotPathHash"], "path-hash")
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
