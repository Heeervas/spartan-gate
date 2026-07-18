from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.telemetry import RoutingLogEntry, TelemetryQueue, TelemetryStore


def routing_entry(timestamp: str, request_id: str | None = None) -> RoutingLogEntry:
    return RoutingLogEntry(
        timestamp=timestamp,
        original_model="clawroute/auto",
        routed_model="codex/gpt-5.5",
        actual_model="codex/gpt-5.5",
        tier="complex",
        input_tokens=100,
        cached_input_tokens=25,
        output_tokens=10,
        session_id="session",
        request_id=request_id or timestamp,
        turn_id="turn",
        context_info='{"usage_seen":true}',
    )


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

    def test_routing_model_overrides_are_empty_by_default(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            self.assertEqual(store.routing_model_override_rows(), [])
        finally:
            store.close()

    def test_routing_model_overrides_round_trip_complete_mapping(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            mapping = {
                "heartbeat": "codex/gpt-5.1-mini",
                "simple": "codex/gpt-5.2",
                "moderate": "codex/gpt-5.4",
                "complex": "codex/gpt-5.6",
            }
            store.replace_routing_model_overrides(mapping)

            rows = store.routing_model_override_rows()
            self.assertEqual(
                [(row["tier"], row["model_id"]) for row in rows],
                sorted(mapping.items()),
            )
            self.assertTrue(all(row["updated_at"] for row in rows))
            self.assertEqual(len({row["updated_at"] for row in rows}), 1)
        finally:
            store.close()

    def test_routing_model_override_replace_is_atomic_and_removes_old_rows(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.replace_routing_model_overrides({
                "heartbeat": "old/heartbeat",
                "simple": "old/simple",
                "moderate": "old/moderate",
                "complex": "old/complex",
            })

            replacement = {
                "heartbeat": "new/heartbeat",
                "simple": "new/simple",
                "moderate": "new/moderate",
                "complex": "new/complex",
            }
            store.replace_routing_model_overrides(replacement)

            self.assertEqual(
                {
                    row["tier"]: row["model_id"]
                    for row in store.routing_model_override_rows()
                },
                replacement,
            )
        finally:
            store.close()

    def test_routing_model_overrides_survive_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "spartan-route.db")
            mapping = {
                "heartbeat": "codex/gpt-5.1-mini",
                "simple": "codex/gpt-5.2",
                "moderate": "codex/gpt-5.4",
                "complex": "codex/gpt-5.6",
            }
            first = TelemetryStore(db_path)
            first.replace_routing_model_overrides(mapping)
            first.close()

            reopened = TelemetryStore(db_path)
            try:
                self.assertEqual(
                    {row["tier"]: row["model_id"] for row in reopened.routing_model_override_rows()},
                    mapping,
                )
            finally:
                reopened.close()

    def test_delete_routing_model_overrides_resets_complete_set(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.replace_routing_model_overrides({
                "heartbeat": "codex/gpt-5.1-mini",
                "simple": "codex/gpt-5.2",
                "moderate": "codex/gpt-5.4",
                "complex": "codex/gpt-5.6",
            })

            store.delete_routing_model_overrides()

            self.assertEqual(store.routing_model_override_rows(), [])
            store.delete_routing_model_overrides()
            self.assertEqual(store.routing_model_override_rows(), [])
        finally:
            store.close()

    def test_routing_model_override_read_exposes_incomplete_and_extra_rows(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store._conn.executemany(
                """
                INSERT INTO routing_model_overrides (tier, model_id, updated_at)
                VALUES (?, ?, ?)
                """,
                [
                    ("heartbeat", "codex/gpt-5.1-mini", "2026-07-10T09:00:00Z"),
                    ("future", "codex/gpt-future", "2026-07-10T09:00:00Z"),
                ],
            )
            store._conn.commit()

            self.assertEqual(
                store.routing_model_override_rows(),
                [
                    {
                        "tier": "future",
                        "model_id": "codex/gpt-future",
                        "updated_at": "2026-07-10T09:00:00Z",
                    },
                    {
                        "tier": "heartbeat",
                        "model_id": "codex/gpt-5.1-mini",
                        "updated_at": "2026-07-10T09:00:00Z",
                    },
                ],
            )
        finally:
            store.close()

    def test_routing_model_override_replace_rolls_back_on_insert_failure(self) -> None:
        store = TelemetryStore(":memory:")
        original = {
            "heartbeat": "old/heartbeat",
            "simple": "old/simple",
            "moderate": "old/moderate",
            "complex": "old/complex",
        }
        try:
            store.replace_routing_model_overrides(original)
            store._conn.execute(
                """
                CREATE TRIGGER fail_routing_override_insert
                BEFORE INSERT ON routing_model_overrides
                WHEN NEW.tier = 'moderate'
                BEGIN
                    SELECT RAISE(ABORT, 'injected insert failure');
                END
                """
            )
            store._conn.commit()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected insert failure"):
                store.replace_routing_model_overrides({
                    "heartbeat": "new/heartbeat",
                    "simple": "new/simple",
                    "moderate": "new/moderate",
                    "complex": "new/complex",
                })

            self.assertEqual(
                {
                    row["tier"]: row["model_id"]
                    for row in store.routing_model_override_rows()
                },
                original,
            )
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


class TelemetryRetentionTests(unittest.TestCase):
    def test_age_and_row_cap_pruning_rebuilds_rollups_deterministically(self) -> None:
        store = TelemetryStore(":memory:", retention_days=30, routing_row_cap=3)
        try:
            for timestamp in (
                "2026-05-01T00:00:00Z",
                "2026-06-17T23:59:59Z",
                "2026-06-20T00:00:00Z",
                "2026-07-01T00:00:00Z",
                "2026-07-02T00:00:00Z",
                "2026-07-03T00:00:00Z",
            ):
                store.log_routing(routing_entry(timestamp))

            report = store.enforce_routing_retention(
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
                force_rebuild=True,
            )

            timestamps = [
                row["timestamp"]
                for row in store._conn.execute(
                    "SELECT timestamp FROM routing_log ORDER BY id"
                ).fetchall()
            ]
            self.assertEqual(
                timestamps,
                [
                    "2026-07-01T00:00:00Z",
                    "2026-07-02T00:00:00Z",
                    "2026-07-03T00:00:00Z",
                ],
            )
            self.assertEqual(report["agePrunedRows"], 2)
            self.assertEqual(report["rowCapPrunedRows"], 1)
            self.assertEqual(report["rowsAfter"], 3)
            self.assertTrue(report["rowCapShortenedHistory"])
            rollup = store._conn.execute(
                "SELECT count(*) AS days, sum(requests) AS requests FROM routing_daily_rollups"
            ).fetchone()
            self.assertEqual(dict(rollup), {"days": 3, "requests": 3})
        finally:
            store.close()

    def test_retention_rolls_back_routing_rows_when_rollup_rebuild_fails(self) -> None:
        store = TelemetryStore(":memory:", retention_days=30, routing_row_cap=10)
        try:
            store.log_routing(routing_entry("2026-05-01T00:00:00Z", "old"))
            store.log_routing(routing_entry("2026-07-01T00:00:00Z", "new"))
            store._conn.execute(
                """
                CREATE TRIGGER fail_rollup_rebuild
                BEFORE DELETE ON routing_daily_rollups
                BEGIN
                    SELECT RAISE(ABORT, 'injected rollup failure');
                END
                """
            )
            store._conn.commit()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected rollup failure"):
                store.enforce_routing_retention(
                    now=datetime(2026, 7, 18, tzinfo=timezone.utc),
                    force_rebuild=True,
                )

            self.assertEqual(
                store._conn.execute("SELECT count(*) FROM routing_log").fetchone()[0],
                2,
            )
            self.assertEqual(
                store._conn.execute(
                    "SELECT coalesce(sum(requests), 0) FROM routing_daily_rollups"
                ).fetchone()[0],
                2,
            )
        finally:
            store.close()

    def test_retention_preserves_codex_history_and_control_state(self) -> None:
        store = TelemetryStore(":memory:", retention_days=30, routing_row_cap=1)
        try:
            store.log_routing(routing_entry("2026-07-01T00:00:00Z", "first"))
            store.log_routing(routing_entry("2026-07-02T00:00:00Z", "second"))
            usage = {
                "account_key": "acct",
                "slot_index": 0,
                "window": "weekly",
                "used_percent": 10,
                "reset_at": "2026-07-20T00:00:00Z",
                "window_minutes": 10080,
            }
            store.upsert_usage_snapshots([usage])
            store.upsert_usage_snapshots([{**usage, "used_percent": 20}])
            store.set_slot_routing_enabled(0, False, account_key="acct")
            store.replace_routing_model_overrides({"complex": "codex/gpt-5.5"})
            store.upsert_reset_credits([
                {
                    "account_key": "acct",
                    "slot_index": 0,
                    "available_count": 1,
                    "details_available": False,
                    "credits": [],
                }
            ])

            store.enforce_routing_retention(
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
                force_rebuild=True,
            )

            self.assertEqual(
                store._conn.execute(
                    "SELECT count(*) FROM codex_usage_snapshot_history"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(len(store.usage_snapshots()), 1)
            self.assertFalse(store.slot_routing_override(0)["routingEnabled"])
            self.assertEqual(
                store.routing_model_override_rows()[0]["model_id"],
                "codex/gpt-5.5",
            )
            self.assertEqual(
                store.reset_credit_snapshots()["acct"]["availableCount"],
                1,
            )
        finally:
            store.close()


class QueueTestStore:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        delay: float = 0.0,
        block: bool = False,
    ) -> None:
        self.fail_first = fail_first
        self.delay = delay
        self.block = block
        self.attempts = 0
        self.written: list[RoutingLogEntry] = []
        self.prune_calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def log_routing(self, entry: RoutingLogEntry) -> None:
        self.attempts += 1
        if self.block:
            self.started.set()
            self.release.wait(timeout=1)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_first and self.attempts == 1:
            raise sqlite3.OperationalError("injected write failure")
        self.written.append(entry)

    def enforce_routing_retention(self) -> dict[str, object]:
        self.prune_calls += 1
        return self.retention_status()

    def retention_status(self) -> dict[str, object]:
        return {
            "retentionDays": 30,
            "rowCap": 50_000,
            "rowsAfter": len(self.written),
            "rowCapShortenedHistory": False,
        }


class TelemetryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturation_drops_only_telemetry(self) -> None:
        queue = TelemetryQueue(QueueTestStore(), capacity=2)

        self.assertTrue(queue.log(routing_entry("2026-07-01T00:00:00Z", "one")))
        self.assertTrue(queue.log(routing_entry("2026-07-01T00:00:01Z", "two")))
        self.assertFalse(queue.log(routing_entry("2026-07-01T00:00:02Z", "three")))

        status = queue.status()
        self.assertEqual(status["depth"], 2)
        self.assertEqual(status["capacity"], 2)
        self.assertEqual(status["drops"], 1)
        self.assertEqual(status["writeFailures"], 0)

    async def test_worker_recovers_from_database_errors_and_prunes_by_batch(self) -> None:
        store = QueueTestStore(fail_first=True)
        queue = TelemetryQueue(store, capacity=4, retention_batch_size=1)
        queue.start()
        queue.log(routing_entry("2026-07-01T00:00:00Z", "failed"))
        queue.log(routing_entry("2026-07-01T00:00:01Z", "written"))

        self.assertTrue(await queue.stop(timeout=1))

        self.assertEqual([entry.request_id for entry in store.written], ["written"])
        self.assertEqual(store.prune_calls, 1)
        self.assertEqual(queue.status()["writeFailures"], 1)
        self.assertFalse(queue.status()["workerRunning"])

    async def test_normal_shutdown_drains_accepted_entries(self) -> None:
        store = QueueTestStore(delay=0.01)
        queue = TelemetryQueue(store, capacity=4)
        queue.start()
        for index in range(3):
            queue.log(routing_entry(f"2026-07-01T00:00:0{index}Z", str(index)))

        self.assertTrue(await queue.stop(timeout=1))
        self.assertEqual([entry.request_id for entry in store.written], ["0", "1", "2"])
        self.assertEqual(queue.status()["depth"], 0)

    async def test_shutdown_timeout_is_bounded_and_counts_pending_drops(self) -> None:
        store = QueueTestStore(block=True)
        queue = TelemetryQueue(store, capacity=3)
        queue.start()
        for index in range(3):
            queue.log(routing_entry(f"2026-07-01T00:00:0{index}Z", str(index)))
        self.assertTrue(await asyncio.to_thread(store.started.wait, 1))

        started = time.monotonic()
        self.assertFalse(await queue.stop(timeout=0.02))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.25)
        self.assertEqual(queue.status()["shutdownTimeouts"], 1)
        self.assertEqual(queue.status()["drops"], 2)
        store.release.set()
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
