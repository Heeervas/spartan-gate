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
from unittest.mock import patch

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
    def test_websocket_event_latency_does_not_pollute_http_header_rollups(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            for kind, header, total in (("http", 100, 1000), ("websocket", None, 400)):
                entry = routing_entry(datetime.now(timezone.utc).isoformat(), kind)
                entry = RoutingLogEntry(**{**entry.__dict__, "response_time_ms": header if header is not None else 0,
                    "upstream_response_time_ms": header, "total_response_time_ms": total})
                store.log_routing(entry)
            for rebuild in (False, True):
                if rebuild:
                    store.enforce_routing_retention(force_rebuild=True)
                rows = store.daily_cache_rows()
                self.assertEqual(rows[0]["avg_response_time_ms"], 100)
                self.assertEqual(rows[0]["avg_total_response_time_ms"], 700)
        finally:
            store.close()

    def test_codex_context_metadata_survives_restart_and_rejects_partial_invalid_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "route.db")
            store = TelemetryStore(path)
            try:
                store.save_codex_model_contexts({"codex/gpt-6.1-sol": 872000})
                for invalid in (True, 0, 2_000_001, 2.5, "872000"):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        store.save_codex_model_contexts({"codex/gpt-6.1-sol": 300000, "codex/other": invalid})
                self.assertEqual(store.codex_model_contexts(), {"codex/gpt-6.1-sol": 872000})
            finally:
                store.close()
            restored = TelemetryStore(path)
            try:
                self.assertEqual(restored.codex_model_contexts(), {"codex/gpt-6.1-sol": 872000})
                restored.save_codex_model_contexts({"codex/gpt-6.1-sol": 400000})
                self.assertEqual(restored.codex_model_contexts(), {"codex/gpt-6.1-sol": 400000})
            finally:
                restored.close()

    def test_reasoning_tokens_and_chain_realm_are_persisted_without_double_counting(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            entry = routing_entry("2026-07-30T10:00:00Z", "reasoning")
            entry = RoutingLogEntry(**{**entry.__dict__, "output_tokens": 20, "reasoning_tokens": 12})
            store.log_routing(entry)
            row = store._conn.execute(
                "SELECT output_tokens, reasoning_tokens FROM routing_log WHERE request_id = ?",
                ("reasoning",),
            ).fetchone()
            self.assertEqual((row["output_tokens"], row["reasoning_tokens"]), (20, 12))
            self.assertTrue(store.bind_reasoning_chain("a" * 64, "codex_subscription"))
            self.assertTrue(store.bind_reasoning_chain("a" * 64, "codex_subscription"))
            self.assertFalse(store.bind_reasoning_chain("a" * 64, "openai_api"))
            self.assertEqual(store.reasoning_realm_for_chain("a" * 64), "codex_subscription")
        finally:
            store.close()

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

    def test_daily_rollup_tracks_exact_metrics_without_reading_routing_log(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            entries = [
                RoutingLogEntry(
                    timestamp="2026-07-19T08:00:00Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    input_tokens=1000,
                    cached_input_tokens=500,
                    output_tokens=100,
                    response_time_ms=1000,
                    upstream_response_time_ms=250,
                    total_response_time_ms=1200,
                    context_info='{"usage_seen":true,"cache_likely":true,"cache_low":false}',
                ),
                RoutingLogEntry(
                    timestamp="2026-07-19T09:00:00Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    input_tokens=2000,
                    cached_input_tokens=0,
                    output_tokens=200,
                    response_time_ms=500,
                    context_info='{"usage_seen":true,"cache_likely":false,"cache_low":true}',
                ),
                RoutingLogEntry(
                    timestamp="2026-07-19T10:00:00Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    input_tokens=9000,
                    cached_input_tokens=9000,
                    error="codex_stream_timeout",
                    context_info="{malformed",
                ),
                RoutingLogEntry(
                    timestamp="2026-07-19T11:00:00Z",
                    original_model="clawroute/auto",
                    routed_model="openai/gpt-test",
                    actual_model="openai/gpt-test",
                    tier="complex",
                    input_tokens=7000,
                    cached_input_tokens=7000,
                    response_time_ms=9999,
                    context_info='{"usage_seen":true,"cache_likely":true}',
                ),
            ]
            for entry in entries:
                store.log_routing(entry)

            rollup_columns = {
                "usage_requests",
                "usage_input_tokens",
                "usage_cached_input_tokens",
                "cache_likely_requests",
                "cache_likely_input_tokens",
                "cache_likely_cached_input_tokens",
                "cache_low_requests",
                "cache_low_input_tokens",
                "error_requests",
                "response_time_sum_ms",
                "response_time_count",
                "response_time_max_ms",
                "upstream_response_time_sum_ms",
                "upstream_response_time_count",
                "upstream_response_time_max_ms",
                "total_response_time_sum_ms",
                "total_response_time_count",
                "total_response_time_max_ms",
                "split_latency_requests",
                "legacy_latency_requests",
            }
            info = {
                row["name"]: dict(row)
                for row in store._conn.execute(
                    "PRAGMA table_info(routing_daily_rollups)"
                ).fetchall()
            }
            self.assertTrue(rollup_columns.issubset(info))
            for name in rollup_columns:
                self.assertEqual(info[name]["type"], "INTEGER")
                self.assertEqual(info[name]["notnull"], 1)
                self.assertEqual(str(info[name]["dflt_value"]).strip("'()"), "0")

            raw = dict(store._conn.execute(
                """
                SELECT *
                FROM routing_daily_rollups
                WHERE actual_model = 'codex/gpt-5.5'
                """
            ).fetchone())
            self.assertEqual(raw["requests"], 3)
            self.assertEqual(raw["usage_requests"], 2)
            self.assertEqual(raw["usage_input_tokens"], 3000)
            self.assertEqual(raw["usage_cached_input_tokens"], 500)
            self.assertEqual(raw["cache_likely_requests"], 1)
            self.assertEqual(raw["cache_likely_input_tokens"], 1000)
            self.assertEqual(raw["cache_likely_cached_input_tokens"], 500)
            self.assertEqual(raw["cache_low_requests"], 1)
            self.assertEqual(raw["cache_low_input_tokens"], 2000)
            self.assertEqual(raw["error_requests"], 1)
            self.assertEqual(raw["response_time_sum_ms"], 1500)
            self.assertEqual(raw["response_time_count"], 2)
            self.assertEqual(raw["response_time_max_ms"], 1000)
            self.assertEqual(raw["upstream_response_time_sum_ms"], 250)
            self.assertEqual(raw["upstream_response_time_count"], 1)
            self.assertEqual(raw["upstream_response_time_max_ms"], 250)
            self.assertEqual(raw["total_response_time_sum_ms"], 1700)
            self.assertEqual(raw["total_response_time_count"], 2)
            self.assertEqual(raw["total_response_time_max_ms"], 1200)
            self.assertEqual(raw["split_latency_requests"], 1)
            self.assertEqual(raw["legacy_latency_requests"], 2)

            traced: list[str] = []
            store._conn.set_trace_callback(traced.append)
            rows = store.daily_cache_rows()
            store._conn.set_trace_callback(None)

            self.assertFalse(
                any("FROM ROUTING_LOG" in statement.upper() for statement in traced),
                traced,
            )
            self.assertEqual([{k: v for k, v in row.items() if k not in {"nativeCache", "legacyCacheHeuristics"}} for row in rows], [{
                "day": "2026-07-19",
                "requests": 2,
                "logged_requests": 3,
                "input_tokens": 3000,
                "cached_input_tokens": 500,
                "cache_likely_requests": 1,
                "cache_likely_input_tokens": 1000,
                "cache_likely_cached_input_tokens": 500,
                "cache_low_requests": 1,
                "cache_low_input_tokens": 2000,
                "errors": 1,
                "usage_missing_requests": 1,
                "split_latency_requests": 1,
                "legacy_latency_requests": 2,
                "cache_likely_hit_percent": 50.0,
                "avg_response_time_ms": 250,
                "max_response_time_ms": 250,
                "avg_total_response_time_ms": 850,
                "max_total_response_time_ms": 1200,
            }])
        finally:
            store.close()

    def test_incremental_and_rebuilt_rollups_share_sqlite_utc_date_semantics(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-07-01T00:30:00+02:00",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.5",
                actual_model="codex/gpt-5.5",
                tier="complex",
                input_tokens=100,
                cached_input_tokens=25,
                response_time_ms=100,
                context_info='{"usage_seen":true}',
            ))
            incremental = [
                row["day"]
                for row in store._conn.execute(
                    "SELECT day FROM routing_daily_rollups"
                ).fetchall()
            ]
            self.assertEqual(incremental, ["2026-06-30"])

            before = store.daily_cache_rows()
            with store._lock:
                store._conn.execute("BEGIN IMMEDIATE")
                store._rebuild_rollups_unlocked()
                store._conn.commit()
            rebuilt_days = [
                row["day"]
                for row in store._conn.execute(
                    "SELECT day FROM routing_daily_rollups"
                ).fetchall()
            ]
            self.assertEqual(rebuilt_days, ["2026-06-30"])
            self.assertEqual(store.daily_cache_rows(), before)
        finally:
            store.close()

    def test_old_rollup_schema_is_altered_and_force_rebuilt_before_first_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "spartan-route.db")
            initial = TelemetryStore(db_path)
            initial.close()

            retained_at = datetime.now(timezone.utc).replace(microsecond=0)
            retained_timestamp = retained_at.isoformat().replace("+00:00", "Z")
            retained_day = retained_at.date().isoformat()
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO routing_log (
                        timestamp, original_model, routed_model, actual_model, tier,
                        input_tokens, cached_input_tokens, output_tokens,
                        response_time_ms, upstream_response_time_ms, total_response_time_ms,
                        error, context_info
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        retained_timestamp,
                        "clawroute/auto",
                        "codex/gpt-5.5",
                        "codex/gpt-5.5",
                        "complex",
                        1200,
                        600,
                        100,
                        800,
                        200,
                        900,
                        "",
                        '{"usage_seen":true,"cache_likely":true}',
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO routing_daily_rollups (
                        day, actual_model, tier, request_api_kind,
                        requested_reasoning_effort, requests, input_tokens,
                        cached_input_tokens, output_tokens
                    ) VALUES ('1999-01-01', 'codex/stale', 'complex',
                              'chat_completions', '', 99, 99, 99, 99)
                    """
                )
                conn.commit()
            finally:
                conn.close()

            migrated = TelemetryStore(db_path)
            try:
                columns = {
                    row["name"]
                    for row in migrated._conn.execute(
                        "PRAGMA table_info(routing_daily_rollups)"
                    ).fetchall()
                }
                self.assertIn("usage_requests", columns)
                self.assertIn("total_response_time_max_ms", columns)
                raw = dict(migrated._conn.execute(
                    "SELECT * FROM routing_daily_rollups"
                ).fetchone())
                self.assertEqual(raw["day"], retained_day)
                self.assertEqual(raw["requests"], 1)
                self.assertEqual(raw["usage_requests"], 1)
                self.assertEqual(raw["usage_input_tokens"], 1200)
                self.assertEqual(raw["usage_cached_input_tokens"], 600)
                self.assertEqual(raw["cache_likely_requests"], 1)
                self.assertEqual(raw["response_time_sum_ms"], 800)
                self.assertEqual(raw["upstream_response_time_sum_ms"], 200)
                self.assertEqual(raw["total_response_time_sum_ms"], 900)
            finally:
                migrated.close()

    def test_existing_database_gets_additive_routing_columns(self) -> None:
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
                self.assertIn("actual_tool_call_count", columns)
                self.assertIn("actual_tool_names", columns)
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-06-25T09:00:00.000Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.5",
                    actual_model="codex/gpt-5.5",
                    tier="complex",
                    response_time_ms=75,
                    upstream_response_time_ms=75,
                    total_response_time_ms=900,
                    actual_tool_call_count=1,
                    actual_tool_names=["read_file"],
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
    def test_age_pruning_deletes_in_bounded_statements(self) -> None:
        store = TelemetryStore(":memory:", retention_days=30, routing_row_cap=10_000)
        try:
            for index in range(600):
                store.log_routing(
                    routing_entry("2026-05-01T00:00:00Z", f"old-{index}")
                )
            store.log_routing(routing_entry("2026-07-01T00:00:00Z", "retained"))

            traced: list[str] = []
            store._conn.set_trace_callback(traced.append)
            report = store.enforce_routing_retention(
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
                force_rebuild=True,
            )
            store._conn.set_trace_callback(None)

            delete_statements = [
                statement
                for statement in traced
                if statement.lstrip().upper().startswith("DELETE FROM ROUTING_LOG")
            ]
            self.assertGreater(len(delete_statements), 1)
            self.assertTrue(
                all("WHERE ID IN" in statement.upper() for statement in delete_statements)
            )
            self.assertEqual(report["agePrunedRows"], 600)
            self.assertEqual(report["rowsAfter"], 1)
        finally:
            store.close()

    def test_oldest_retained_timestamp_preserves_timezone_order_without_sorting(self) -> None:
        store = TelemetryStore(":memory:", retention_days=30, routing_row_cap=10)
        try:
            earlier = "2026-07-01T01:00:00+02:00"
            store.log_routing(routing_entry(earlier, "offset-earlier"))
            store.log_routing(routing_entry("2026-07-01T00:30:00Z", "lexical-earlier"))

            traced: list[str] = []
            store._conn.set_trace_callback(traced.append)
            report = store.enforce_routing_retention(
                now=datetime(2026, 7, 2, tzinfo=timezone.utc),
                force_rebuild=True,
            )
            store._conn.set_trace_callback(None)

            self.assertEqual(report["oldestRetainedAt"], earlier)
            oldest_queries = [
                statement
                for statement in traced
                if "oldest" not in statement.lower()
                and "SELECT" in statement.upper()
                and "timestamp" in statement
                and "FROM routing_log" in statement
            ]
            self.assertFalse(any("ORDER BY julianday" in statement for statement in oldest_queries))
        finally:
            store.close()

    def test_row_cap_aggregation_avoids_sqlite_group_sort_spills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "spartan-route.db")
            store = TelemetryStore(db_path, retention_days=30, routing_row_cap=50_000)
            try:
                padding = "x" * 1024
                context = (
                    '{"usage_seen":true,"cache_likely":true,'
                    f'"cache_low":false,"padding":"{padding}"}}'
                )
                store._conn.executemany(
                    """
                    INSERT INTO routing_log (
                        timestamp, original_model, routed_model, actual_model, tier,
                        input_tokens, cached_input_tokens, output_tokens,
                        response_time_ms, upstream_response_time_ms, total_response_time_ms,
                        context_info, request_api_kind, requested_reasoning_effort
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            f"2026-07-{(index % 30) + 1:02d}T{index % 24:02d}:00:00Z",
                            "clawroute/auto",
                            "codex/gpt-5.5",
                            "codex/gpt-5.5",
                            "complex",
                            100,
                            25,
                            10,
                            100,
                            50,
                            100,
                            context,
                            "chat_completions",
                            "high",
                        )
                        for index in range(50_000)
                    ),
                )
                store._conn.commit()
                store._conn.execute("PRAGMA temp_store=FILE")

                traced: list[str] = []
                store._conn.set_trace_callback(traced.append)
                report = store.enforce_routing_retention(
                    now=datetime(2026, 7, 31, tzinfo=timezone.utc),
                    force_rebuild=True,
                )
                rows = store.daily_cache_rows()
                store._conn.set_trace_callback(None)

                self.assertEqual(sum(row["logged_requests"] for row in rows), 50_000)
                self.assertEqual(sum(row["requests"] for row in rows), 50_000)
                self.assertEqual(sum(row["input_tokens"] for row in rows), 5_000_000)
                self.assertEqual(report["rowsAfter"], 50_000)
                rollup = store._conn.execute(
                    "SELECT count(*) AS groups, sum(requests) AS requests FROM routing_daily_rollups"
                ).fetchone()
                self.assertEqual(int(rollup["requests"]), 50_000)
                self.assertLessEqual(int(rollup["groups"]), 30)

                spill_sql = [
                    statement
                    for statement in traced
                    if "routing_log" in statement
                    and ("GROUP BY" in statement.upper() or "ORDER BY DAY" in statement.upper())
                ]
                self.assertEqual(spill_sql, [])
                source_reads = {
                    statement
                    for statement in traced
                    if statement.lstrip().upper().startswith("SELECT")
                    and "FROM routing_log" in statement
                    and ("date(timestamp)" in statement or "substr(timestamp" in statement)
                }
                self.assertEqual(len(source_reads), 1)
                for statement in source_reads:
                    with self.subTest(statement=statement.splitlines()[1].strip()):
                        plan = store._conn.execute(
                            f"EXPLAIN QUERY PLAN {statement}"
                        ).fetchall()
                        self.assertFalse(
                            any("TEMP B-TREE" in str(row[3]).upper() for row in plan),
                            plan,
                        )
            finally:
                store.close()

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


    def test_live_sessions_expose_reasoning_and_actual_tool_activity(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-07-19T10:00:00.000Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.6",
                actual_model="codex/gpt-5.6",
                tier="complex",
                input_tokens=1000,
                cached_input_tokens=750,
                output_tokens=50,
                had_tool_calls=True,
                actual_tool_call_count=2,
                actual_tool_names=["write_file", "read_file", "read_file"],
                session_id="session-friendly",
                request_id="request-friendly",
                turn_id="turn-friendly",
                prompt_preview="Improve the mobile dashboard",
                requested_reasoning_effort="high",
                context_info='{"usage_seen":true}',
            ))

            session = store.live_sessions()["sessions"][0]
            metrics = session["metrics"]
            self.assertEqual(metrics["actualToolCalls"], 2)
            self.assertEqual(metrics["toolNames"], ["read_file", "write_file"])
            self.assertEqual(metrics["toolEnabledRequests"], 1)
            self.assertEqual(metrics["toolTelemetryKnownRequests"], 1)
            self.assertEqual(metrics["reasoningEfforts"], {"high": 1})

            request = store.turn_requests("turn-friendly")["requests"][0]
            self.assertTrue(request["toolsAvailable"])
            self.assertEqual(request["actualToolCallCount"], 2)
            self.assertEqual(request["actualToolNames"], ["read_file", "write_file"])
            self.assertEqual(request["requestedReasoningEffort"], "high")
        finally:
            store.close()

    def test_live_session_filters_search_retained_rows_and_return_whole_turns(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            store.log_routing(RoutingLogEntry(
                timestamp="2026-07-01T09:00:00Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.6-sol",
                actual_model="codex/gpt-5.6-sol",
                tier="complex",
                session_id="old-session",
                turn_id="matching-turn",
                request_id="matching-request",
                prompt_preview="Budget 100%_literal review",
                requested_reasoning_effort=None,
                context_info='{"usage_seen":true}',
            ))
            store.log_routing(RoutingLogEntry(
                timestamp="2026-07-01T09:01:00Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.4-mini",
                actual_model="codex/gpt-5.4-mini",
                tier="simple",
                session_id="old-session",
                turn_id="matching-turn",
                request_id="context-request",
                prompt_preview="Companion request",
                requested_reasoning_effort="high",
                context_info='{"usage_seen":true}',
            ))
            for index in range(25):
                store.log_routing(RoutingLogEntry(
                    timestamp=f"2026-07-02T{index % 24:02d}:00:00Z",
                    original_model="clawroute/auto",
                    routed_model="codex/gpt-5.6-terra",
                    actual_model="codex/gpt-5.6-terra",
                    tier="complex",
                    session_id=f"new-session-{index}",
                    turn_id=f"new-turn-{index}",
                    request_id=f"new-request-{index}",
                    prompt_preview="Budget 100XXliteral review",
                    requested_reasoning_effort="high",
                    context_info='{"usage_seen":true}',
                ))

            payload = store.live_sessions(
                session_limit=1,
                turn_limit=1,
                model="codex/gpt-5.6-sol",
                reasoning="default",
                prompt="100%_literal",
            )

            self.assertEqual(payload["matchingSessions"], 1)
            self.assertEqual(payload["matchingTurns"], 1)
            self.assertEqual(payload["sessions"][0]["id"], "old-session")
            self.assertEqual(payload["sessions"][0]["metrics"]["requests"], 2)
            self.assertEqual(
                payload["sessions"][0]["models"],
                ["codex/gpt-5.4-mini", "codex/gpt-5.6-sol"],
            )
            self.assertEqual(payload["appliedFilters"]["prompt"], "100%_literal")
            self.assertIn("default", payload["filterOptions"]["reasoningEfforts"])
        finally:
            store.close()

    def test_live_session_filter_order_and_total_use_matching_rows_before_limits(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            def log(session_id: str, turn_id: str, model: str, prompt: str) -> None:
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-07-20T10:00:00Z",
                    original_model="clawroute/auto",
                    routed_model=model,
                    actual_model=model,
                    tier="complex",
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=f"{session_id}-{turn_id}-{prompt}",
                    prompt_preview=prompt,
                    context_info='{"usage_seen":true}',
                ))

            log("older-match", "older-turn", "codex/gpt-5.6-sol", "match")
            log("newer-match", "newer-turn-1", "codex/gpt-5.6-sol", "match")
            log("newer-match", "newer-turn-2", "codex/gpt-5.6-sol", "match")
            log("older-match", "older-turn", "codex/gpt-5.4-mini", "later companion")

            payload = store.live_sessions(
                session_limit=2,
                turn_limit=1,
                model="codex/gpt-5.6-sol",
            )

            self.assertEqual(payload["matchingSessions"], 2)
            self.assertEqual(payload["matchingTurns"], 3)
            self.assertEqual([row["id"] for row in payload["sessions"]], ["newer-match", "older-match"])
            self.assertEqual(payload["sessions"][0]["turnCount"], 2)
            self.assertTrue(payload["sessions"][0]["hasMoreTurns"])
            self.assertEqual(payload["sessions"][1]["metrics"]["requests"], 2)
        finally:
            store.close()

    def test_weekly_model_estimate_uses_account_identity_and_reports_coverage(self) -> None:
        store = TelemetryStore(":memory:")
        account_key = "a" * 16
        try:
            rows = [
                ("usage", "codex/gpt-5.6-sol", 100, 10, '{"usage_seen":true}', account_key, 3),
                ("missing", "codex/gpt-5.6-sol", 50, 5, '{"usage_seen":false}', account_key, 4),
                ("unattributed", "codex/gpt-5.6-terra", 20, 2, '{"usage_seen":true}', None, 2),
            ]
            for request_id, model, input_tokens, output_tokens, context, key, slot in rows:
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-07-06T12:00:00Z",
                    original_model="clawroute/auto",
                    routed_model=model,
                    actual_model=model,
                    tier="complex",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    selected_codex_account_key=key,
                    selected_codex_slot_index=slot,
                    request_id=request_id,
                    context_info=context,
                ))
            snapshot = {
                "account_key": account_key,
                "slot_index": 0,
                "window": "weekly",
                "used_percent": 50,
                "reset_at": "2026-07-08T00:00:00Z",
                "window_minutes": 10080,
            }
            with patch("spartan_route.telemetry.now_iso", return_value="2026-07-07T00:00:00Z"):
                store.upsert_usage_snapshots([snapshot], current_account_keys={account_key})

            estimate = store.codex_usage_estimate({account_key})
            self.assertTrue(estimate["available"])
            self.assertEqual(estimate["sampleAccounts"], 1)
            self.assertEqual(estimate["sampleTokens"], 110)
            self.assertEqual(estimate["excludedAccountRequests"], 1)
            self.assertEqual(estimate["excludedAccountTokens"], 55)
            self.assertEqual(estimate["unattributedRequests"], 1)
            self.assertEqual(estimate["unattributedTokens"], 22)
            self.assertEqual(estimate["models"][0]["model"], "codex/gpt-5.6-sol")
            self.assertAlmostEqual(estimate["models"][0]["expectedUnits"], 50.0)

            store.set_codex_capacity_multiplier(account_key, 5)
            estimate = store.codex_usage_estimate({account_key})
            self.assertAlmostEqual(estimate["actualQuotaUnits"], 250.0)
            self.assertAlmostEqual(estimate["models"][0]["expectedUnits"], 250.0)

            store.log_routing(RoutingLogEntry(
                timestamp="2026-07-13T12:00:00Z",
                original_model="clawroute/auto",
                routed_model="codex/gpt-5.6-luna",
                actual_model="codex/gpt-5.6-luna",
                tier="complex",
                input_tokens=200,
                output_tokens=20,
                selected_codex_account_key=account_key,
                selected_codex_slot_index=1,
                request_id="new-window",
                context_info='{"usage_seen":true}',
            ))
            with patch("spartan_route.telemetry.now_iso", return_value="2026-07-14T00:00:00Z"):
                store.upsert_usage_snapshots([{
                    **snapshot,
                    "slot_index": 1,
                    "reset_at": "2026-07-15T00:00:00Z",
                    "used_percent": 25,
                }], current_account_keys={account_key})
            estimate = store.codex_usage_estimate({account_key})
            self.assertEqual([row["model"] for row in estimate["models"]], ["codex/gpt-5.6-luna"])
            self.assertEqual(
                store._conn.execute(
                    "SELECT count(*) FROM codex_window_model_usage WHERE account_key = ?",
                    (account_key,),
                ).fetchone()[0],
                1,
            )
        finally:
            store.close()

    def test_efficiency_groups_model_reasoning_with_account_weighted_rate_and_sessions(self) -> None:
        store = TelemetryStore(":memory:")
        account_a = "a" * 16
        account_b = "b" * 16
        try:
            rows = [
                (account_a, "codex/gpt-5.6-sol", "high", "shared", 900_000, 100_000),
                (account_b, "codex/gpt-5.6-sol", "high", "shared", 900_000, 100_000),
                (account_b, "codex/gpt-5.6-terra", "low", "terra-session", 900_000, 100_000),
            ]
            for index, (account, model, reasoning, session, input_tokens, output_tokens) in enumerate(rows):
                store.log_routing(RoutingLogEntry(
                    timestamp="2026-07-06T12:00:00Z",
                    original_model="clawroute/auto",
                    routed_model=model,
                    actual_model=model,
                    tier="complex",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    selected_codex_account_key=account,
                    session_id=session,
                    request_id=f"request-{index}",
                    requested_reasoning_effort=reasoning,
                    context_info='{"usage_seen":true}',
                ))
            store.set_codex_capacity_multiplier(account_b, 5)
            with patch("spartan_route.telemetry.now_iso", return_value="2026-07-07T00:00:00Z"):
                store.upsert_usage_snapshots([
                    {
                        "account_key": account_a, "slot_index": 0, "window": "weekly",
                        "used_percent": 10, "reset_at": "2026-07-08T00:00:00Z", "window_minutes": 10080,
                    },
                    {
                        "account_key": account_b, "slot_index": 1, "window": "weekly",
                        "used_percent": 10, "reset_at": "2026-07-08T00:00:00Z", "window_minutes": 10080,
                    },
                ], current_account_keys={account_a, account_b})

            estimate = store.codex_usage_estimate({account_a, account_b})
            rows_by_group = {(row["model"], row["reasoning"]): row for row in estimate["efficiency"]}
            sol = rows_by_group[("codex/gpt-5.6-sol", "high")]
            self.assertEqual(sol["sessions"], 1)
            self.assertEqual(sol["totalTokens"], 2_000_000)
            self.assertAlmostEqual(sol["expectedUnits"], 35.0)
            self.assertAlmostEqual(sol["percentPerMillionTokens"], 17.5)
            self.assertAlmostEqual(sol["averageMillionTokensPerSession"], 2.0)
            terra = rows_by_group[("codex/gpt-5.6-terra", "low")]
            self.assertAlmostEqual(terra["percentPerMillionTokens"], 25.0)
            self.assertAlmostEqual(terra["averageMillionTokensPerSession"], 1.0)
        finally:
            store.close()

    def test_capacity_multiplier_persists_and_x1_deletes_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "spartan-route.db")
            account_key = "b" * 16
            store = TelemetryStore(db_path)
            store.set_codex_capacity_multiplier(account_key, 5)
            store.close()

            store = TelemetryStore(db_path)
            try:
                self.assertEqual(store.codex_capacity_multiplier(account_key), 5)
                store.set_codex_capacity_multiplier(account_key, 1)
                self.assertEqual(store.codex_capacity_multiplier(account_key), 1)
                self.assertEqual(
                    store._conn.execute("SELECT count(*) FROM codex_account_capacity_overrides").fetchone()[0],
                    0,
                )
            finally:
                store.close()

    def test_weekly_snapshot_replaces_obsolete_current_windows_but_keeps_history(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            base = {
                "account_key": "acct",
                "slot_index": 0,
                "used_percent": 20,
                "reset_at": "2026-07-25T10:00:00Z",
            }
            store.upsert_usage_snapshots([
                {**base, "window": "fiveHour", "window_minutes": 300},
                {**base, "window": "weekly", "window_minutes": 10080},
            ])
            store.upsert_usage_snapshots([
                {**base, "window": "weekly", "window_minutes": 10080, "used_percent": 40},
            ])

            self.assertEqual(
                [row["window"] for row in store.usage_snapshots()],
                ["weekly"],
            )
            self.assertEqual(
                store._conn.execute(
                    "SELECT count(*) FROM codex_window_usage_coverage WHERE account_key = 'acct'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                store._conn.execute("SELECT count(*) FROM codex_usage_snapshot_history").fetchone()[0],
                3,
            )
        finally:
            store.close()


    def test_window_usage_prunes_accounts_outside_current_configuration(self) -> None:
        store = TelemetryStore(":memory:")
        try:
            def snapshot(account_key: str) -> dict[str, object]:
                return {
                    "account_key": account_key,
                    "slot_index": 0,
                    "window": "weekly",
                    "used_percent": 10,
                    "reset_at": "2026-07-25T10:00:00Z",
                    "window_minutes": 10080,
                }

            store.upsert_usage_snapshots([snapshot("b" * 16)], current_account_keys={"b" * 16})
            store.upsert_usage_snapshots([snapshot("a" * 16)], current_account_keys={"a" * 16})

            rows = store._conn.execute(
                "SELECT DISTINCT account_key FROM codex_window_usage_coverage WHERE account_key != ''"
            ).fetchall()
            self.assertEqual([row["account_key"] for row in rows], ["a" * 16])

            store.upsert_usage_snapshots([], current_account_keys=set())
            self.assertEqual(
                store._conn.execute("SELECT count(*) FROM codex_window_model_usage").fetchone()[0],
                0,
            )
            self.assertEqual(
                store._conn.execute("SELECT count(*) FROM codex_window_usage_coverage").fetchone()[0],
                0,
            )
            self.assertEqual(
                store._conn.execute("SELECT count(*) FROM codex_window_efficiency").fetchone()[0],
                0,
            )
        finally:
            store.close()

    def test_focused_usage_update_preserves_other_accounts_and_global_coverage(self) -> None:
        store = TelemetryStore(":memory:")
        account_a = "a" * 16
        account_b = "b" * 16
        try:
            def snapshot(account_key: str, slot_index: int, used: float) -> dict[str, object]:
                return {
                    "account_key": account_key,
                    "slot_index": slot_index,
                    "window": "weekly",
                    "used_percent": used,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                }

            store.upsert_usage_snapshots(
                [snapshot(account_a, 0, 10), snapshot(account_b, 1, 20)],
                current_account_keys={account_a, account_b},
            )
            before_other = {
                table: [
                    tuple(row)
                    for row in store._conn.execute(
                        f"SELECT * FROM {table} WHERE account_key = ? ORDER BY rowid",
                        (account_b,),
                    ).fetchall()
                ]
                for table in (
                    "codex_window_model_usage",
                    "codex_window_efficiency",
                    "codex_window_usage_coverage",
                )
            }
            before_global = [
                tuple(row)
                for row in store._conn.execute(
                    """
                    SELECT * FROM codex_window_usage_coverage
                    WHERE account_key = ''
                    ORDER BY rowid
                    """
                ).fetchall()
            ]

            store.upsert_usage_snapshots(
                [snapshot(account_a, 0, 30)],
                current_account_keys={account_a},
                full_pool_refresh=False,
            )

            for table, expected in before_other.items():
                self.assertEqual(
                    [
                        tuple(row)
                        for row in store._conn.execute(
                            f"SELECT * FROM {table} WHERE account_key = ? ORDER BY rowid",
                            (account_b,),
                        ).fetchall()
                    ],
                    expected,
                )
            self.assertEqual(
                [
                    tuple(row)
                    for row in store._conn.execute(
                        """
                        SELECT * FROM codex_window_usage_coverage
                        WHERE account_key = ''
                        ORDER BY rowid
                        """
                    ).fetchall()
                ],
                before_global,
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
        self.health_events: list[tuple[str, str, str]] = []
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

    def record_health_failure(self, category: str, operation: str) -> None:
        self.health_events.append(("failure", category, operation))

    def record_health_success(self, category: str, operation: str) -> None:
        self.health_events.append(("success", category, operation))


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
        self.assertEqual(
            store.health_events,
            [
                ("failure", "write", "routing_log"),
                ("success", "write", "routing_log"),
                ("success", "maintenance", "retention"),
            ],
        )

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
