from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .util import now_iso

DEFAULT_RETENTION_DAYS = 30
DEFAULT_ROUTING_ROW_CAP = 50_000
DEFAULT_QUEUE_CAPACITY = 1024
DEFAULT_RETENTION_BATCH_SIZE = 256
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RoutingLogEntry:
    timestamp: str
    original_model: str
    routed_model: str
    actual_model: str
    tier: str
    classification_reason: str = ""
    confidence: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    original_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    savings_usd: float = 0.0
    escalated: bool = False
    escalation_chain: str = "[]"
    response_time_ms: int = 0
    upstream_response_time_ms: int | None = None
    total_response_time_ms: int | None = None
    had_tool_calls: bool = False
    actual_tool_call_count: int | None = None
    actual_tool_names: list[str] | None = None
    is_dry_run: bool = False
    is_override: bool = False
    session_id: str | None = None
    error: str | None = None
    prompt_preview: str | None = None
    context_info: str | None = None
    request_api_kind: str = "chat_completions"
    requested_reasoning_effort: str | None = None
    selected_codex_slot_index: int | None = None
    selected_codex_account_key: str | None = None
    request_id: str | None = None
    turn_id: str | None = None


class TelemetryStore:
    def __init__(
        self,
        db_path: str,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        routing_row_cap: int = DEFAULT_ROUTING_ROW_CAP,
    ):
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        if routing_row_cap < 1:
            raise ValueError("routing_row_cap must be positive")
        self.db_path = db_path
        self.retention_days = int(retention_days)
        self.routing_row_cap = int(routing_row_cap)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._health_failures = {
            "read": 0,
            "write": 0,
            "maintenance": 0,
        }
        self._unhealthy_operations: dict[str, set[str]] = {
            "read": set(),
            "write": set(),
            "maintenance": set(),
        }
        self._last_failure_code: str | None = None
        self._retention_status: dict[str, Any] = {}
        self._init_schema()
        try:
            self.enforce_routing_retention(force_rebuild=True)
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS routing_log (
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
                    upstream_response_time_ms INTEGER,
                    total_response_time_ms INTEGER,
                    had_tool_calls INTEGER NOT NULL DEFAULT 0,
                    actual_tool_call_count INTEGER,
                    actual_tool_names TEXT,
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
                );
                CREATE TABLE IF NOT EXISTS routing_daily_rollups (
                    day TEXT NOT NULL,
                    actual_model TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    request_api_kind TEXT NOT NULL DEFAULT 'chat_completions',
                    requested_reasoning_effort TEXT NOT NULL DEFAULT '',
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, actual_model, tier, request_api_kind, requested_reasoning_effort)
                );
                CREATE TABLE IF NOT EXISTS codex_usage_snapshots (
                    account_key TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    window TEXT NOT NULL,
                    used_percent REAL NOT NULL,
                    reset_at TEXT NOT NULL,
                    window_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_key, window)
                );
                CREATE TABLE IF NOT EXISTS codex_usage_snapshot_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_key TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    window TEXT NOT NULL,
                    used_percent REAL NOT NULL,
                    reset_at TEXT NOT NULL,
                    window_minutes INTEGER NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_reset_credit_summaries (
                    account_key TEXT PRIMARY KEY,
                    slot_index INTEGER NOT NULL,
                    available_count INTEGER,
                    details_available INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_reset_credit_items (
                    account_key TEXT NOT NULL,
                    credit_key TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reset_type TEXT,
                    title TEXT,
                    granted_at TEXT,
                    expires_at TEXT,
                    redeemed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_key, credit_key)
                );
                CREATE TABLE IF NOT EXISTS codex_slot_overrides (
                    slot_index INTEGER PRIMARY KEY,
                    routing_enabled INTEGER NOT NULL DEFAULT 1,
                    account_key TEXT,
                    slot_path_hash TEXT,
                    auth_fingerprint_hash TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS routing_model_overrides (
                    tier TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routing_log_session ON routing_log(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_routing_log_turn ON routing_log(turn_id, id);
                CREATE INDEX IF NOT EXISTS idx_routing_log_time ON routing_log(timestamp);
                """
            )
            self._ensure_columns_unlocked(
                "routing_log",
                {
                    "upstream_response_time_ms": "INTEGER",
                    "total_response_time_ms": "INTEGER",
                    "actual_tool_call_count": "INTEGER",
                    "actual_tool_names": "TEXT",
                },
            )
            self._conn.commit()

    def _ensure_columns_unlocked(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def log_routing(self, entry: RoutingLogEntry) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO routing_log (
                    timestamp, original_model, routed_model, actual_model, tier,
                    classification_reason, confidence, input_tokens, cached_input_tokens,
                    output_tokens, original_cost_usd, actual_cost_usd, savings_usd,
                    escalated, escalation_chain, response_time_ms, upstream_response_time_ms,
                    total_response_time_ms, had_tool_calls, actual_tool_call_count,
                    actual_tool_names,
                    is_dry_run, is_override, session_id, error, prompt_preview,
                    context_info, request_api_kind, requested_reasoning_effort,
                    selected_codex_slot_index, selected_codex_account_key, request_id, turn_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp, entry.original_model, entry.routed_model, entry.actual_model, entry.tier,
                    entry.classification_reason, entry.confidence, entry.input_tokens, entry.cached_input_tokens,
                    entry.output_tokens, entry.original_cost_usd, entry.actual_cost_usd, entry.savings_usd,
                    int(entry.escalated), entry.escalation_chain, entry.response_time_ms,
                    entry.upstream_response_time_ms, entry.total_response_time_ms, int(entry.had_tool_calls),
                    entry.actual_tool_call_count,
                    json.dumps(sorted(set(entry.actual_tool_names or [])), separators=(",", ":")) if entry.actual_tool_names is not None else None,
                    int(entry.is_dry_run), int(entry.is_override), entry.session_id, entry.error, entry.prompt_preview,
                    entry.context_info, entry.request_api_kind, entry.requested_reasoning_effort,
                    entry.selected_codex_slot_index, entry.selected_codex_account_key, entry.request_id, entry.turn_id,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO routing_daily_rollups (
                    day, actual_model, tier, request_api_kind, requested_reasoning_effort,
                    requests, input_tokens, cached_input_tokens, output_tokens
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(day, actual_model, tier, request_api_kind, requested_reasoning_effort)
                DO UPDATE SET
                    requests = requests + 1,
                    input_tokens = input_tokens + excluded.input_tokens,
                    cached_input_tokens = cached_input_tokens + excluded.cached_input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens
                """,
                (
                    entry.timestamp[:10],
                    entry.actual_model,
                    entry.tier,
                    entry.request_api_kind,
                    entry.requested_reasoning_effort or "",
                    entry.input_tokens,
                    entry.cached_input_tokens,
                    entry.output_tokens,
                ),
            )
            self._conn.commit()

    def enforce_routing_retention(
        self,
        *,
        now: datetime | None = None,
        force_rebuild: bool = False,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        cutoff = current - timedelta(days=self.retention_days)
        cutoff_text = cutoff.isoformat()

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                rows_before = int(
                    self._conn.execute("SELECT count(*) FROM routing_log").fetchone()[0]
                )
                age_cursor = self._conn.execute(
                    """
                    DELETE FROM routing_log
                    WHERE julianday(timestamp) IS NULL
                       OR julianday(timestamp) < julianday(?)
                    """,
                    (cutoff_text,),
                )
                age_pruned = max(0, int(age_cursor.rowcount))

                threshold = self._conn.execute(
                    """
                    SELECT id
                    FROM routing_log
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                    """,
                    (self.routing_row_cap - 1,),
                ).fetchone()
                row_cap_pruned = 0
                if threshold is not None:
                    cap_cursor = self._conn.execute(
                        "DELETE FROM routing_log WHERE id < ?",
                        (int(threshold["id"]),),
                    )
                    row_cap_pruned = max(0, int(cap_cursor.rowcount))

                rows_after = int(
                    self._conn.execute("SELECT count(*) FROM routing_log").fetchone()[0]
                )
                rebuild = bool(force_rebuild or age_pruned or row_cap_pruned)
                if rebuild:
                    self._rebuild_rollups_unlocked()

                oldest = self._conn.execute(
                    """
                    SELECT timestamp
                    FROM routing_log
                    WHERE julianday(timestamp) = (
                        SELECT min(julianday(timestamp))
                        FROM routing_log
                    )
                    LIMIT 1
                    """
                ).fetchone()
                shortened = bool(
                    rows_after >= self.routing_row_cap
                    and self._conn.execute(
                        """
                        SELECT coalesce(min(julianday(timestamp)) > julianday(?), 0)
                        FROM routing_log
                        """,
                        (cutoff_text,),
                    ).fetchone()[0]
                )
                report = {
                    "retentionDays": self.retention_days,
                    "rowCap": self.routing_row_cap,
                    "rowsBefore": rows_before,
                    "rowsAfter": rows_after,
                    "agePrunedRows": age_pruned,
                    "rowCapPrunedRows": row_cap_pruned,
                    "rowCapShortenedHistory": shortened,
                    "oldestRetainedAt": oldest["timestamp"] if oldest else None,
                    "rollupsRebuilt": rebuild,
                    "lastEnforcedAt": current.isoformat(),
                }
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._retention_status = report
            return dict(report)

    def retention_status(self) -> dict[str, Any]:
        return dict(self._retention_status)

    def record_health_failure(self, category: str, operation: str) -> None:
        if category not in self._health_failures:
            raise ValueError("invalid telemetry health category")
        with self._health_lock:
            self._health_failures[category] += 1
            self._unhealthy_operations[category].add(operation)
            self._last_failure_code = f"telemetry_{category}_failed"

    def record_health_success(self, category: str, operation: str) -> None:
        if category not in self._health_failures:
            raise ValueError("invalid telemetry health category")
        with self._health_lock:
            self._unhealthy_operations[category].discard(operation)
            if not any(self._unhealthy_operations.values()):
                self._last_failure_code = None

    def health_status(self) -> dict[str, Any]:
        with self._health_lock:
            read_healthy = not self._unhealthy_operations["read"]
            write_healthy = not self._unhealthy_operations["write"]
            maintenance_healthy = not self._unhealthy_operations["maintenance"]
            return {
                "healthy": read_healthy and write_healthy and maintenance_healthy,
                "readHealthy": read_healthy,
                "writeHealthy": write_healthy,
                "maintenanceHealthy": maintenance_healthy,
                "readFailures": self._health_failures["read"],
                "writeFailures": self._health_failures["write"],
                "maintenanceFailures": self._health_failures["maintenance"],
                "lastFailureCode": self._last_failure_code,
            }

    def _rebuild_rollups_unlocked(self) -> None:
        rollups: dict[tuple[str, str, str, str, str], list[Any]] = {}
        cursor = self._conn.execute(
            """
            SELECT substr(timestamp, 1, 10) AS day,
                   actual_model,
                   tier,
                   request_api_kind,
                   coalesce(requested_reasoning_effort, '') AS requested_reasoning_effort,
                   input_tokens,
                   cached_input_tokens,
                   output_tokens
            FROM routing_log
            """
        )
        for raw_row in cursor:
            row = dict(raw_row)
            key = (
                str(row["day"]),
                str(row["actual_model"]),
                str(row["tier"]),
                str(row["request_api_kind"]),
                str(row["requested_reasoning_effort"]),
            )
            aggregate = rollups.get(key)
            if aggregate is None:
                aggregate = [*key, 0, 0, 0, 0]
                rollups[key] = aggregate
            aggregate[5] += 1
            aggregate[6] += int(row["input_tokens"] or 0)
            aggregate[7] += int(row["cached_input_tokens"] or 0)
            aggregate[8] += int(row["output_tokens"] or 0)

        self._conn.execute("DELETE FROM routing_daily_rollups")
        if rollups:
            self._conn.executemany(
                """
                INSERT INTO routing_daily_rollups (
                    day, actual_model, tier, request_api_kind, requested_reasoning_effort,
                    requests, input_tokens, cached_input_tokens, output_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rollups.values(),
            )

    def daily_cache_rows(self) -> list[dict[str, Any]]:
        by_day: dict[str | None, dict[str, Any]] = {}
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT date(timestamp) AS day,
                       input_tokens,
                       cached_input_tokens,
                       response_time_ms,
                       upstream_response_time_ms,
                       total_response_time_ms,
                       error,
                       context_info
                FROM routing_log
                WHERE actual_model LIKE 'codex/%'
                """
            )
            for raw_row in cursor:
                row = dict(raw_row)
                day = row["day"]
                aggregate = by_day.get(day)
                if aggregate is None:
                    aggregate = {
                        "day": day,
                        "requests": 0,
                        "logged_requests": 0,
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "cache_likely_requests": 0,
                        "cache_likely_input_tokens": 0,
                        "cache_likely_cached_input_tokens": 0,
                        "cache_low_requests": 0,
                        "cache_low_input_tokens": 0,
                        "errors": 0,
                        "usage_missing_requests": 0,
                        "split_latency_requests": 0,
                        "legacy_latency_requests": 0,
                        "_response_sum": 0,
                        "_response_count": 0,
                        "_response_max": None,
                        "_upstream_sum": 0,
                        "_upstream_count": 0,
                        "_upstream_max": None,
                        "_total_sum": 0,
                        "_total_count": 0,
                        "_total_max": None,
                    }
                    by_day[day] = aggregate

                aggregate["logged_requests"] += 1
                context = self._row_context(row)
                usage_seen = self._sqlite_json_flag(context.get("usage_seen"))
                cache_likely = self._sqlite_json_flag(context.get("cache_likely"))
                cache_low = self._sqlite_json_flag(context.get("cache_low"))
                input_tokens = int(row.get("input_tokens") or 0)
                cached_tokens = int(row.get("cached_input_tokens") or 0)
                if usage_seen:
                    aggregate["requests"] += 1
                    aggregate["input_tokens"] += input_tokens
                    aggregate["cached_input_tokens"] += cached_tokens
                else:
                    aggregate["usage_missing_requests"] += 1
                if cache_likely:
                    aggregate["cache_likely_requests"] += 1
                    aggregate["cache_likely_input_tokens"] += input_tokens
                    aggregate["cache_likely_cached_input_tokens"] += cached_tokens
                if cache_low:
                    aggregate["cache_low_requests"] += 1
                    aggregate["cache_low_input_tokens"] += input_tokens
                if row.get("error") not in (None, ""):
                    aggregate["errors"] += 1

                response = int(row.get("response_time_ms") or 0)
                upstream_raw = row.get("upstream_response_time_ms")
                total_raw = row.get("total_response_time_ms")
                upstream = int(upstream_raw or 0)
                total = int(total_raw or 0) or response
                if response:
                    aggregate["_response_sum"] += response
                    aggregate["_response_count"] += 1
                    aggregate["_response_max"] = max(aggregate["_response_max"] or response, response)
                if upstream:
                    aggregate["_upstream_sum"] += upstream
                    aggregate["_upstream_count"] += 1
                    aggregate["_upstream_max"] = max(aggregate["_upstream_max"] or upstream, upstream)
                    aggregate["split_latency_requests"] += 1
                if total:
                    aggregate["_total_sum"] += total
                    aggregate["_total_count"] += 1
                    aggregate["_total_max"] = max(aggregate["_total_max"] or total, total)
                if upstream_raw is None and total_raw is None:
                    aggregate["legacy_latency_requests"] += 1

        rows = []
        for day in sorted(by_day, key=lambda value: (value is not None, value or "")):
            aggregate = by_day[day]
            likely_input = int(aggregate["cache_likely_input_tokens"])
            aggregate["cache_likely_hit_percent"] = (
                aggregate["cache_likely_cached_input_tokens"] / likely_input * 100
                if likely_input
                else 0
            )
            if aggregate["_upstream_count"]:
                latency_prefix = "_upstream"
            else:
                latency_prefix = "_response"
            latency_count = int(aggregate[f"{latency_prefix}_count"])
            aggregate["avg_response_time_ms"] = (
                aggregate[f"{latency_prefix}_sum"] / latency_count if latency_count else None
            )
            aggregate["max_response_time_ms"] = aggregate[f"{latency_prefix}_max"]
            total_count = int(aggregate["_total_count"])
            aggregate["avg_total_response_time_ms"] = (
                aggregate["_total_sum"] / total_count if total_count else None
            )
            aggregate["max_total_response_time_ms"] = aggregate["_total_max"]
            for key in tuple(aggregate):
                if key.startswith("_"):
                    del aggregate[key]
            rows.append(aggregate)
        return rows

    def today_summary(self, day: str | None = None) -> dict[str, Any]:
        current_day = day or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT input_tokens, cached_input_tokens, output_tokens, response_time_ms,
                       upstream_response_time_ms, total_response_time_ms, error, context_info
                FROM routing_log
                WHERE actual_model LIKE 'codex/%' AND date(timestamp) = ?
                ORDER BY id ASC
                """,
                (current_day,),
            ).fetchall()
        items = [dict(row) for row in rows]
        usage_seen = 0
        errors = 0
        input_tokens = cached_tokens = output_tokens = 0
        cache_likely_requests = 0
        cache_likely_input_tokens = 0
        cache_likely_cached_input_tokens = 0
        cache_low_requests = 0
        cache_low_input_tokens = 0
        latencies: list[int] = []
        split_latencies: list[int] = []
        total_latencies: list[int] = []
        legacy_latency_requests = 0
        split_latency_requests = 0
        for item in items:
            context = self._row_context(item)
            seen = bool(context.get("usage_seen"))
            if seen:
                usage_seen += 1
                input_tokens += int(item.get("input_tokens") or 0)
                cached_tokens += int(item.get("cached_input_tokens") or 0)
                output_tokens += int(item.get("output_tokens") or 0)
            if context.get("cache_likely"):
                cache_likely_requests += 1
                cache_likely_input_tokens += int(item.get("input_tokens") or 0)
                cache_likely_cached_input_tokens += int(item.get("cached_input_tokens") or 0)
            if context.get("cache_low"):
                cache_low_requests += 1
                cache_low_input_tokens += int(item.get("input_tokens") or 0)
            if item.get("error"):
                errors += 1
            latency, total_latency, legacy = self._row_latency_values(item)
            if latency > 0:
                latencies.append(latency)
                if not legacy and item.get("upstream_response_time_ms") is not None:
                    split_latencies.append(latency)
                    split_latency_requests += 1
            if total_latency > 0:
                total_latencies.append(total_latency)
            if legacy:
                legacy_latency_requests += 1
        return {
            "day": current_day,
            "loggedRequests": len(items),
            "usageSeenRequests": usage_seen,
            "usageMissingRequests": len(items) - usage_seen,
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens,
            "outputTokens": output_tokens,
            "cacheHitPercent": (cached_tokens / input_tokens * 100) if input_tokens else 0,
            "cacheLikelyRequests": cache_likely_requests,
            "cacheLikelyInputTokens": cache_likely_input_tokens,
            "cacheLikelyCachedInputTokens": cache_likely_cached_input_tokens,
            "cacheLikelyHitPercent": (
                cache_likely_cached_input_tokens / cache_likely_input_tokens * 100
                if cache_likely_input_tokens
                else 0
            ),
            "cacheLowRequests": cache_low_requests,
            "cacheLowInputTokens": cache_low_input_tokens,
            "errors": errors,
            "latencyMs": self._latency_summary(latencies),
            "firstResponseLatencyMs": self._latency_summary(split_latencies if split_latencies else latencies),
            "totalLatencyMs": self._latency_summary(total_latencies),
            "legacyLatencyRequests": legacy_latency_requests,
            "splitLatencyRequests": split_latency_requests,
        }

    def live_sessions(
        self,
        limit: int = 30,
        session_limit: int | None = None,
        turn_limit: int = 20,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(100, int(session_limit if session_limit is not None else limit)))
        safe_turn_limit = max(1, min(100, int(turn_limit)))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM routing_log
                WHERE session_id IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit * 20,),
            ).fetchall()
        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            sid = item.get("session_id") or "unknown"
            turn_id = item.get("turn_id") or item.get("request_id") or "unknown"
            session = sessions.setdefault(sid, {
                "id": sid,
                "startedAt": item["timestamp"],
                "lastAt": item["timestamp"],
                "lastId": item["id"],
                "metrics": self._empty_metrics(),
                "models": set(),
                "tiers": set(),
                "turns": {},
            })
            session["startedAt"] = min(session["startedAt"], item["timestamp"])
            session["lastAt"] = max(session["lastAt"], item["timestamp"])
            session["lastId"] = max(session["lastId"], item["id"])
            self._add_metrics(session["metrics"], item)
            session["models"].add(item["actual_model"])
            session["tiers"].add(item["tier"])
            turn = session["turns"].setdefault(turn_id, {
                "id": turn_id,
                "startedAt": item["timestamp"],
                "lastAt": item["timestamp"],
                "lastId": item["id"],
                "metrics": self._empty_metrics(),
                "models": set(),
                "tiers": set(),
                "promptPreview": item.get("prompt_preview"),
            })
            turn["startedAt"] = min(turn["startedAt"], item["timestamp"])
            turn["lastAt"] = max(turn["lastAt"], item["timestamp"])
            turn["lastId"] = max(turn["lastId"], item["id"])
            turn["promptPreview"] = turn["promptPreview"] or item.get("prompt_preview")
            self._add_metrics(turn["metrics"], item)
            turn["models"].add(item["actual_model"])
            turn["tiers"].add(item["tier"])
        values = sorted(sessions.values(), key=lambda session: session["lastId"], reverse=True)[:safe_limit]
        for session in values:
            self._finalize_metrics(session["metrics"])
            turns = sorted(session["turns"].values(), key=lambda turn: turn["lastId"], reverse=True)
            session["turnCount"] = len(turns)
            session["hasMoreTurns"] = len(turns) > safe_turn_limit
            session["turns"] = turns[:safe_turn_limit]
            session["models"] = sorted(session["models"])
            session["tiers"] = sorted(session["tiers"])
            session.pop("lastId", None)
            for turn in session["turns"]:
                self._finalize_metrics(turn["metrics"])
                turn["models"] = sorted(turn["models"])
                turn["tiers"] = sorted(turn["tiers"])
                turn.pop("lastId", None)
        return {"sessions": values, "hasMoreSessions": len(sessions) > safe_limit, "retentionDays": self.retention_days}

    def turn_requests(self, turn_id: str, limit: int = 200) -> dict[str, Any]:
        safe_limit = max(1, min(200, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routing_log WHERE turn_id = ? ORDER BY id ASC LIMIT ?",
                (turn_id, safe_limit + 1),
            ).fetchall()
        items = [self._decision_from_row(dict(row)) for row in rows]
        return {"turnId": turn_id, "requests": items[:safe_limit], "truncated": len(items) > safe_limit}

    def set_slot_routing_enabled(
        self,
        slot_index: int,
        routing_enabled: bool,
        account_key: str | None = None,
        slot_path_hash: str | None = None,
        auth_fingerprint_hash: str | None = None,
    ) -> None:
        updated_at = now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO codex_slot_overrides (
                    slot_index, routing_enabled, account_key, slot_path_hash, auth_fingerprint_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot_index) DO UPDATE SET
                    routing_enabled = excluded.routing_enabled,
                    account_key = excluded.account_key,
                    slot_path_hash = excluded.slot_path_hash,
                    auth_fingerprint_hash = excluded.auth_fingerprint_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    int(slot_index),
                    int(bool(routing_enabled)),
                    account_key,
                    slot_path_hash,
                    auth_fingerprint_hash,
                    updated_at,
                ),
            )
            self._conn.commit()

    def slot_routing_overrides(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM codex_slot_overrides ORDER BY slot_index").fetchall()
        return {int(row["slot_index"]): self._slot_override_from_row(dict(row)) for row in rows}

    def slot_routing_override(self, slot_index: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM codex_slot_overrides WHERE slot_index = ?", (int(slot_index),)).fetchone()
        return self._slot_override_from_row(dict(row)) if row else None

    def routing_model_override_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT tier, model_id, updated_at
                FROM routing_model_overrides
                ORDER BY tier
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_routing_model_overrides(self, mapping: dict[str, str]) -> str:
        updated_at = now_iso()
        rows = [
            (tier, model_id, updated_at)
            for tier, model_id in sorted(mapping.items())
        ]
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("DELETE FROM routing_model_overrides")
                self._conn.executemany(
                    """
                    INSERT INTO routing_model_overrides (tier, model_id, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    rows,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return updated_at

    def delete_routing_model_overrides(self) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("DELETE FROM routing_model_overrides")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def upsert_usage_snapshots(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        observed_at = now_iso()
        authoritative_accounts = {
            str(record["account_key"])
            for record in records
            if record.get("window") == "weekly"
        }
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for account_key in authoritative_accounts:
                    self._conn.execute(
                        "DELETE FROM codex_usage_snapshots WHERE account_key = ?",
                        (account_key,),
                    )
                for record in records:
                    self._conn.execute(
                        """
                        INSERT INTO codex_usage_snapshots (
                            account_key, slot_index, window, used_percent, reset_at, window_minutes, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(account_key, window) DO UPDATE SET
                            slot_index = excluded.slot_index,
                            used_percent = excluded.used_percent,
                            reset_at = excluded.reset_at,
                            window_minutes = excluded.window_minutes,
                            updated_at = excluded.updated_at
                        """,
                        (
                            record["account_key"], record["slot_index"], record["window"],
                            record["used_percent"], record["reset_at"], record["window_minutes"], observed_at,
                        ),
                    )
                    self._conn.execute(
                        """
                        INSERT INTO codex_usage_snapshot_history (
                            account_key, slot_index, window, used_percent, reset_at, window_minutes, observed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record["account_key"], record["slot_index"], record["window"],
                            record["used_percent"], record["reset_at"], record["window_minutes"], observed_at,
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def usage_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM codex_usage_snapshots ORDER BY slot_index, window").fetchall()
        return [dict(row) for row in rows]

    def upsert_reset_credits(self, summaries: list[dict[str, Any]]) -> None:
        updated_at = now_iso()
        with self._lock:
            for summary in summaries:
                account_key = summary["account_key"]
                slot_index = int(summary["slot_index"])
                credits = summary.get("credits") if isinstance(summary.get("credits"), list) else []
                self._conn.execute(
                    """
                    INSERT INTO codex_reset_credit_summaries (
                        account_key, slot_index, available_count, details_available, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_key) DO UPDATE SET
                        slot_index = excluded.slot_index,
                        available_count = excluded.available_count,
                        details_available = excluded.details_available,
                        updated_at = excluded.updated_at
                    """,
                    (
                        account_key,
                        slot_index,
                        summary.get("available_count"),
                        int(bool(summary.get("details_available"))),
                        updated_at,
                    ),
                )
                self._conn.execute("DELETE FROM codex_reset_credit_items WHERE account_key = ?", (account_key,))
                for credit in credits:
                    self._conn.execute(
                        """
                        INSERT INTO codex_reset_credit_items (
                            account_key, credit_key, slot_index, status, reset_type, title,
                            granted_at, expires_at, redeemed_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_key,
                            credit["credit_key"],
                            slot_index,
                            credit.get("status") or "available",
                            credit.get("reset_type"),
                            credit.get("title"),
                            credit.get("granted_at"),
                            credit.get("expires_at"),
                            credit.get("redeemed_at"),
                            updated_at,
                        ),
                    )
            self._conn.commit()

    def reset_credit_snapshots(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            summaries = self._conn.execute("SELECT * FROM codex_reset_credit_summaries").fetchall()
            items = self._conn.execute("SELECT * FROM codex_reset_credit_items ORDER BY expires_at").fetchall()
        by_account: dict[str, dict[str, Any]] = {}
        for row in summaries:
            data = dict(row)
            by_account[data["account_key"]] = {
                "accountKey": data["account_key"],
                "slotIndex": data["slot_index"],
                "availableCount": data["available_count"],
                "detailsAvailable": bool(data["details_available"]),
                "source": "persisted",
                "updatedAt": data["updated_at"],
                "credits": [],
            }
        for row in items:
            data = dict(row)
            parent = by_account.get(data["account_key"])
            if parent is not None:
                parent["credits"].append({
                    "creditKey": data["credit_key"],
                    "status": data["status"],
                    "resetType": data["reset_type"],
                    "title": data["title"],
                    "grantedAt": data["granted_at"],
                    "expiresAt": data["expires_at"],
                    "redeemedAt": data["redeemed_at"],
                })
        return by_account

    def _latency_summary(self, values: list[int]) -> dict[str, int | None]:
        if not values:
            return {"avg": None, "p50": None, "p90": None, "max": None}
        ordered = sorted(values)

        def percentile(fraction: float) -> int:
            import math

            index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
            return ordered[index]

        return {
            "avg": int(round(sum(ordered) / len(ordered))),
            "p50": percentile(0.50),
            "p90": percentile(0.90),
            "max": ordered[-1],
        }

    def _empty_metrics(self) -> dict[str, Any]:
        return {
            "requests": 0,
            "inputTokens": 0,
            "cachedInputTokens": 0,
            "outputTokens": 0,
            "errors": 0,
            "usageSeenRequests": 0,
            "usageMissingRequests": 0,
            "cacheLikelyRequests": 0,
            "cacheLikelyInputTokens": 0,
            "cacheLikelyCachedInputTokens": 0,
            "cacheLikelyHitPercent": 0,
            "cacheLowRequests": 0,
            "cacheLowInputTokens": 0,
            "legacyLatencyRequests": 0,
            "splitLatencyRequests": 0,
            "toolEnabledRequests": 0,
            "actualToolCalls": 0,
            "toolTelemetryKnownRequests": 0,
            "toolTelemetryUnknownRequests": 0,
            "_toolNames": set(),
            "_reasoningEfforts": {},
            "_latencies": [],
            "_firstResponseLatencies": [],
            "_totalLatencies": [],
        }

    def _add_metrics(self, metrics: dict[str, Any], row: dict[str, Any]) -> None:
        metrics["requests"] += 1
        context = self._row_context(row)
        if row.get("had_tool_calls"):
            metrics["toolEnabledRequests"] += 1
        actual_tool_call_count = row.get("actual_tool_call_count")
        if actual_tool_call_count is None:
            metrics["toolTelemetryUnknownRequests"] += 1
        else:
            metrics["toolTelemetryKnownRequests"] += 1
            metrics["actualToolCalls"] += int(actual_tool_call_count or 0)
        raw_tool_names = row.get("actual_tool_names")
        if raw_tool_names:
            try:
                tool_names = json.loads(raw_tool_names)
            except (TypeError, json.JSONDecodeError):
                tool_names = []
            if isinstance(tool_names, list):
                metrics["_toolNames"].update(str(name) for name in tool_names if isinstance(name, str) and name)
        reasoning_effort = str(row.get("requested_reasoning_effort") or "").strip().lower()
        if reasoning_effort:
            efforts = metrics["_reasoningEfforts"]
            efforts[reasoning_effort] = int(efforts.get(reasoning_effort) or 0) + 1
        usage_seen = bool(context.get("usage_seen"))
        if usage_seen:
            metrics["usageSeenRequests"] += 1
            metrics["inputTokens"] += int(row.get("input_tokens") or 0)
            metrics["cachedInputTokens"] += int(row.get("cached_input_tokens") or 0)
            metrics["outputTokens"] += int(row.get("output_tokens") or 0)
        else:
            metrics["usageMissingRequests"] += 1
        if context.get("cache_likely"):
            metrics["cacheLikelyRequests"] += 1
            metrics["cacheLikelyInputTokens"] += int(row.get("input_tokens") or 0)
            metrics["cacheLikelyCachedInputTokens"] += int(row.get("cached_input_tokens") or 0)
        if context.get("cache_low"):
            metrics["cacheLowRequests"] += 1
            metrics["cacheLowInputTokens"] += int(row.get("input_tokens") or 0)
        if row.get("error"):
            metrics["errors"] += 1
        latency, total_latency, legacy = self._row_latency_values(row)
        if latency > 0:
            metrics["_latencies"].append(latency)
            if not legacy and row.get("upstream_response_time_ms") is not None:
                metrics["_firstResponseLatencies"].append(latency)
                metrics["splitLatencyRequests"] += 1
        if total_latency > 0:
            metrics["_totalLatencies"].append(total_latency)
        if legacy:
            metrics["legacyLatencyRequests"] += 1

    def _finalize_metrics(self, metrics: dict[str, Any]) -> None:
        input_tokens = int(metrics.get("inputTokens") or 0)
        metrics["cacheHitPercent"] = (int(metrics.get("cachedInputTokens") or 0) / input_tokens * 100) if input_tokens else 0
        likely_input_tokens = int(metrics.get("cacheLikelyInputTokens") or 0)
        metrics["cacheLikelyHitPercent"] = (
            int(metrics.get("cacheLikelyCachedInputTokens") or 0) / likely_input_tokens * 100
            if likely_input_tokens
            else 0
        )
        latencies = metrics.pop("_latencies", [])
        first_latencies = metrics.pop("_firstResponseLatencies", [])
        metrics["latencyMs"] = self._latency_summary(latencies)
        metrics["firstResponseLatencyMs"] = self._latency_summary(first_latencies if first_latencies else latencies)
        metrics["totalLatencyMs"] = self._latency_summary(metrics.pop("_totalLatencies", []))
        metrics["toolNames"] = sorted(metrics.pop("_toolNames", set()))
        metrics["reasoningEfforts"] = dict(sorted(metrics.pop("_reasoningEfforts", {}).items()))

    def _row_usage_seen(self, row: dict[str, Any]) -> bool:
        return bool(self._row_context(row).get("usage_seen"))

    def _row_context(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("context_info"):
            try:
                context = json.loads(row["context_info"])
                return context if isinstance(context, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _sqlite_json_flag(self, value: Any) -> bool:
        return not isinstance(value, str) and value == 1

    def _row_latency_values(self, row: dict[str, Any]) -> tuple[int, int, bool]:
        response = int(row.get("response_time_ms") or 0)
        upstream = row.get("upstream_response_time_ms")
        total = row.get("total_response_time_ms")
        upstream_int = int(upstream) if upstream is not None else 0
        total_int = int(total) if total is not None else 0
        legacy = upstream is None and total is None
        return upstream_int or response, total_int or response, legacy

    def _slot_override_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "slotIndex": int(row["slot_index"]),
            "routingEnabled": bool(row["routing_enabled"]),
            "accountKey": row.get("account_key"),
            "slotPathHash": row.get("slot_path_hash"),
            "authFingerprintHash": row.get("auth_fingerprint_hash"),
            "updatedAt": row.get("updated_at"),
        }

    def _decision_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        context: Any = None
        if row.get("context_info"):
            try:
                context = json.loads(row["context_info"])
            except json.JSONDecodeError:
                context = None
        latency, total_latency, legacy_latency = self._row_latency_values(row)
        return {
            "timestamp": row["timestamp"],
            "tier": row["tier"],
            "classificationReason": row["classification_reason"],
            "confidence": row["confidence"],
            "originalModel": row["original_model"],
            "routedModel": row["routed_model"],
            "actualModel": row["actual_model"],
            "inputTokens": row["input_tokens"],
            "cachedInputTokens": row["cached_input_tokens"],
            "outputTokens": row["output_tokens"],
            "responseTimeMs": latency,
            "upstreamResponseTimeMs": row.get("upstream_response_time_ms"),
            "totalResponseTimeMs": total_latency,
            "latencySource": "legacy_response_time" if legacy_latency else "upstream_response",
            "error": row["error"],
            "requestId": row["request_id"],
            "sessionId": row["session_id"],
            "turnId": row["turn_id"],
            "promptPreview": row["prompt_preview"],
            "selectedCodexSlotIndex": row["selected_codex_slot_index"],
            "selectedCodexAccountKey": row["selected_codex_account_key"],
            "toolsAvailable": bool(row.get("had_tool_calls")),
            "actualToolCallCount": row.get("actual_tool_call_count"),
            "actualToolNames": self._tool_names_from_row(row),
            "requestedReasoningEffort": row.get("requested_reasoning_effort"),
            "context": context,
        }

    def _tool_names_from_row(self, row: dict[str, Any]) -> list[str] | None:
        raw = row.get("actual_tool_names")
        if raw is None:
            return None
        try:
            values = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return (
            sorted({str(value) for value in values if isinstance(value, str) and value})
            if isinstance(values, list)
            else []
        )


class TelemetryQueue:
    def __init__(
        self,
        store: TelemetryStore,
        *,
        capacity: int = DEFAULT_QUEUE_CAPACITY,
        retention_batch_size: int = DEFAULT_RETENTION_BATCH_SIZE,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if retention_batch_size < 1:
            raise ValueError("retention_batch_size must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        self.store = store
        self.capacity = int(capacity)
        self.retention_batch_size = int(retention_batch_size)
        self.shutdown_timeout = float(shutdown_timeout)
        self.queue: asyncio.Queue[RoutingLogEntry | None] = asyncio.Queue(maxsize=self.capacity)
        self.task: asyncio.Task[None] | None = None
        self.drops = 0
        self.write_failures = 0
        self.maintenance_failures = 0
        self.shutdown_timeouts = 0
        self._successful_writes_since_retention = 0
        self._accepting = True

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self._accepting = True
        self.task = asyncio.create_task(self._run())

    async def stop(self, timeout: float | None = None) -> bool:
        self._accepting = False
        if not self.task:
            dropped = self._discard_pending()
            self.drops += dropped
            return dropped == 0

        limit = self.shutdown_timeout if timeout is None else max(0.0, float(timeout))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        try:
            await asyncio.wait_for(self.queue.join(), timeout=limit)
            self.queue.put_nowait(None)
            remaining = max(0.001, deadline - loop.time())
            await asyncio.wait_for(self.task, timeout=remaining)
        except TimeoutError:
            self.shutdown_timeouts += 1
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
            self.drops += self._discard_pending()
            print(
                "[spartan-route] telemetry shutdown drain timed out; pending telemetry was dropped",
                file=sys.stderr,
            )
            return False

        self.task = None
        return True

    def log(self, entry: RoutingLogEntry) -> bool:
        if not self._accepting:
            self.drops += 1
            return False
        try:
            self.queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.drops += 1
            return False
        return True

    def status(self) -> dict[str, Any]:
        retention = self.store.retention_status()
        public_retention = {
            key: retention.get(key)
            for key in (
                "retentionDays",
                "rowCap",
                "rowsAfter",
                "agePrunedRows",
                "rowCapPrunedRows",
                "rowCapShortenedHistory",
                "rollupsRebuilt",
                "lastEnforcedAt",
            )
        }
        return {
            "depth": self.queue.qsize(),
            "capacity": self.capacity,
            "drops": self.drops,
            "writeFailures": self.write_failures,
            "maintenanceFailures": self.maintenance_failures,
            "shutdownTimeouts": self.shutdown_timeouts,
            "accepting": self._accepting,
            "workerRunning": bool(self.task and not self.task.done()),
            "retention": public_retention,
        }

    def _discard_pending(self) -> int:
        dropped = 0
        while True:
            try:
                entry = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            if entry is not None:
                dropped += 1
            self.queue.task_done()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            entry = await self.queue.get()
            try:
                if entry is None:
                    return
                try:
                    await loop.run_in_executor(None, self.store.log_routing, entry)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.write_failures += 1
                    self.store.record_health_failure("write", "routing_log")
                    print(
                        f"[spartan-route] telemetry write failed ({type(exc).__name__})",
                        file=sys.stderr,
                    )
                    continue
                self.store.record_health_success("write", "routing_log")

                self._successful_writes_since_retention += 1
                if self._successful_writes_since_retention >= self.retention_batch_size:
                    try:
                        await loop.run_in_executor(None, self.store.enforce_routing_retention)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.maintenance_failures += 1
                        self.store.record_health_failure("maintenance", "retention")
                        print(
                            f"[spartan-route] telemetry retention failed ({type(exc).__name__})",
                            file=sys.stderr,
                        )
                    else:
                        self.store.record_health_success("maintenance", "retention")
                        self._successful_writes_since_retention = 0
            finally:
                self.queue.task_done()
