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
from .cache_metrics import empty_metrics as empty_native_metrics, add_request as add_native_request, merge_metrics as merge_native_metrics, public_metrics as public_native_metrics

DEFAULT_RETENTION_DAYS = 30
ROUTING_DAILY_ROLLUP_COLUMNS = {
    "usage_requests": "INTEGER NOT NULL DEFAULT 0",
    "usage_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "usage_cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_likely_requests": "INTEGER NOT NULL DEFAULT 0",
    "cache_likely_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_likely_cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_low_requests": "INTEGER NOT NULL DEFAULT 0",
    "cache_low_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "error_requests": "INTEGER NOT NULL DEFAULT 0",
    "response_time_sum_ms": "INTEGER NOT NULL DEFAULT 0",
    "response_time_count": "INTEGER NOT NULL DEFAULT 0",
    "response_time_max_ms": "INTEGER NOT NULL DEFAULT 0",
    "upstream_response_time_sum_ms": "INTEGER NOT NULL DEFAULT 0",
    "upstream_response_time_count": "INTEGER NOT NULL DEFAULT 0",
    "upstream_response_time_max_ms": "INTEGER NOT NULL DEFAULT 0",
    "total_response_time_sum_ms": "INTEGER NOT NULL DEFAULT 0",
    "total_response_time_count": "INTEGER NOT NULL DEFAULT 0",
    "total_response_time_max_ms": "INTEGER NOT NULL DEFAULT 0",
    "split_latency_requests": "INTEGER NOT NULL DEFAULT 0",
    "legacy_latency_requests": "INTEGER NOT NULL DEFAULT 0",
}
DEFAULT_ROUTING_ROW_CAP = 50_000
DEFAULT_QUEUE_CAPACITY = 1024
DEFAULT_RETENTION_BATCH_SIZE = 256
ROUTING_RETENTION_DELETE_BATCH_SIZE = 256
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
    reasoning_tokens: int | None = None
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

    def reserve_cache_experiment(self, attempt_id: str, experiment: dict[str, Any], account: str, now: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache_experiment_reservations(attempt_id,run_id,arm,account_key,reserved_at) VALUES (?,?,?,?,?)",
                (attempt_id, experiment["run_id"], experiment["arm"], account, now),
            )

    def finish_cache_experiment(self, attempt_id: str, now: float, success: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE cache_experiment_reservations SET finished_at=?,outcome=? WHERE attempt_id=? AND outcome='unresolved'",
                (now if success else None, "completed" if success else "unresolved", attempt_id),
            )

    def reserve_cache_probe(self, run_id: str, case: int, account: str, now: float, estimated: int) -> None:
        # Reservations are durable before egress and separate from measured usage.
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache_probe_reservations(run_id,case_index,account_key,reserved_at,estimated_input_tokens) VALUES (?,?,?,?,?)",
                (run_id, case, account, now, estimated),
            )

    def finish_cache_probe(self, run_id: str, case: int, now: float, success: bool, sent: bool) -> None:
        # A failed/aborted upstream call may still consume delayed quota. Its
        # reservation remains open; never invent a completion or zero usage.
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE cache_probe_reservations SET finished_at=?, outcome=? WHERE run_id=? AND case_index=? AND outcome='unresolved'",
                (now if success or not sent else None, "completed" if success else "not_sent" if not sent else "unresolved", run_id, case),
            )

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
                    reasoning_tokens INTEGER,
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
                CREATE TABLE IF NOT EXISTS cache_experiment_reservations (
                    attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, arm TEXT NOT NULL,
                    account_key TEXT NOT NULL, reserved_at REAL NOT NULL,
                    finished_at REAL, outcome TEXT NOT NULL DEFAULT 'unresolved'
                );
                CREATE TABLE IF NOT EXISTS cache_probe_reservations (
                    run_id TEXT NOT NULL, case_index INTEGER NOT NULL CHECK(case_index BETWEEN 1 AND 12),
                    account_key TEXT NOT NULL, reserved_at REAL NOT NULL,
                    estimated_input_tokens INTEGER NOT NULL CHECK(estimated_input_tokens > 0),
                    finished_at REAL, outcome TEXT NOT NULL DEFAULT 'unresolved',
                    PRIMARY KEY(run_id, case_index)
                );
                CREATE TABLE IF NOT EXISTS native_cache_daily_rollups (
                    day TEXT NOT NULL, actual_model TEXT NOT NULL, account_key TEXT NOT NULL,
                    diagnostic_version INTEGER NOT NULL CHECK(diagnostic_version = 2),
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY(day, actual_model, account_key, diagnostic_version)
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
                CREATE TABLE IF NOT EXISTS codex_account_capacity_overrides (
                    account_key TEXT PRIMARY KEY,
                    capacity_multiplier INTEGER NOT NULL CHECK (capacity_multiplier = 5),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_window_model_usage (
                    account_key TEXT NOT NULL,
                    window TEXT NOT NULL,
                    reset_at TEXT NOT NULL,
                    actual_model TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (account_key, window, reset_at, actual_model)
                );
                CREATE TABLE IF NOT EXISTS codex_window_efficiency (
                    account_key TEXT NOT NULL,
                    window TEXT NOT NULL,
                    reset_at TEXT NOT NULL,
                    actual_model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    has_session INTEGER NOT NULL DEFAULT 0,
                    window_start TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (account_key, window, reset_at, actual_model, reasoning_effort, session_key)
                );
                CREATE TABLE IF NOT EXISTS codex_window_usage_coverage (
                    account_key TEXT NOT NULL,
                    window TEXT NOT NULL,
                    reset_at TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    attributed_requests INTEGER NOT NULL DEFAULT 0,
                    attributed_input_tokens INTEGER NOT NULL DEFAULT 0,
                    attributed_output_tokens INTEGER NOT NULL DEFAULT 0,
                    excluded_requests INTEGER NOT NULL DEFAULT 0,
                    excluded_input_tokens INTEGER NOT NULL DEFAULT 0,
                    excluded_output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (account_key, window, reset_at)
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
                CREATE TABLE IF NOT EXISTS codex_model_context_metadata (
                    model_id TEXT PRIMARY KEY,
                    max_context INTEGER NOT NULL CHECK (
                        typeof(max_context) = 'integer' AND max_context BETWEEN 1 AND 2000000
                    ),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reasoning_chain_bindings (
                    chain_id TEXT PRIMARY KEY,
                    realm TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routing_log_session ON routing_log(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_routing_log_turn ON routing_log(turn_id, id);
                CREATE INDEX IF NOT EXISTS idx_routing_log_time ON routing_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_routing_log_codex_account_time
                    ON routing_log(selected_codex_account_key, timestamp);
                """
            )
            self._ensure_columns_unlocked(
                "routing_daily_rollups",
                ROUTING_DAILY_ROLLUP_COLUMNS,
            )
            self._ensure_columns_unlocked(
                "routing_log",
                {
                    "upstream_response_time_ms": "INTEGER",
                    "total_response_time_ms": "INTEGER",
                    "actual_tool_call_count": "INTEGER",
                    "actual_tool_names": "TEXT",
                    "reasoning_tokens": "INTEGER",
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
        context = self._row_context({"context_info": entry.context_info})
        usage_seen = self._sqlite_json_flag(context.get("usage_seen"))
        cache_likely = self._sqlite_json_flag(context.get("cache_likely"))
        cache_low = self._sqlite_json_flag(context.get("cache_low"))
        response = int(entry.response_time_ms or 0)
        upstream = int(entry.upstream_response_time_ms or 0)
        total = int(entry.total_response_time_ms or 0) or response
        legacy_latency = entry.upstream_response_time_ms is None and entry.total_response_time_ms is None

        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO routing_log (
                    timestamp, original_model, routed_model, actual_model, tier,
                    classification_reason, confidence, input_tokens, cached_input_tokens,
                    output_tokens, reasoning_tokens, original_cost_usd, actual_cost_usd, savings_usd,
                    escalated, escalation_chain, response_time_ms, upstream_response_time_ms,
                    total_response_time_ms, had_tool_calls, actual_tool_call_count,
                    actual_tool_names,
                    is_dry_run, is_override, session_id, error, prompt_preview,
                    context_info, request_api_kind, requested_reasoning_effort,
                    selected_codex_slot_index, selected_codex_account_key, request_id, turn_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp, entry.original_model, entry.routed_model, entry.actual_model, entry.tier,
                    entry.classification_reason, entry.confidence, entry.input_tokens, entry.cached_input_tokens,
                    entry.output_tokens, entry.reasoning_tokens, entry.original_cost_usd, entry.actual_cost_usd, entry.savings_usd,
                    int(entry.escalated), entry.escalation_chain, entry.response_time_ms,
                    entry.upstream_response_time_ms, entry.total_response_time_ms, int(entry.had_tool_calls),
                    entry.actual_tool_call_count,
                    json.dumps(sorted(set(entry.actual_tool_names or [])), separators=(",", ":")) if entry.actual_tool_names is not None else None,
                    int(entry.is_dry_run), int(entry.is_override), entry.session_id, entry.error, entry.prompt_preview,
                    entry.context_info, entry.request_api_kind, entry.requested_reasoning_effort,
                    entry.selected_codex_slot_index, entry.selected_codex_account_key, entry.request_id, entry.turn_id,
                ),
            )
            day = self._conn.execute(
                "SELECT date(timestamp) FROM routing_log WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()[0]
            self._conn.execute(
                """
                INSERT INTO routing_daily_rollups (
                    day, actual_model, tier, request_api_kind, requested_reasoning_effort,
                    requests, input_tokens, cached_input_tokens, output_tokens,
                    usage_requests, usage_input_tokens, usage_cached_input_tokens,
                    cache_likely_requests, cache_likely_input_tokens,
                    cache_likely_cached_input_tokens, cache_low_requests,
                    cache_low_input_tokens, error_requests, response_time_sum_ms,
                    response_time_count, response_time_max_ms,
                    upstream_response_time_sum_ms, upstream_response_time_count,
                    upstream_response_time_max_ms, total_response_time_sum_ms,
                    total_response_time_count, total_response_time_max_ms,
                    split_latency_requests, legacy_latency_requests
                ) VALUES (
                    ?, ?, ?, ?, ?, 1, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(day, actual_model, tier, request_api_kind, requested_reasoning_effort)
                DO UPDATE SET
                    requests = requests + 1,
                    input_tokens = input_tokens + excluded.input_tokens,
                    cached_input_tokens = cached_input_tokens + excluded.cached_input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    usage_requests = usage_requests + excluded.usage_requests,
                    usage_input_tokens = usage_input_tokens + excluded.usage_input_tokens,
                    usage_cached_input_tokens = usage_cached_input_tokens + excluded.usage_cached_input_tokens,
                    cache_likely_requests = cache_likely_requests + excluded.cache_likely_requests,
                    cache_likely_input_tokens = cache_likely_input_tokens + excluded.cache_likely_input_tokens,
                    cache_likely_cached_input_tokens = cache_likely_cached_input_tokens + excluded.cache_likely_cached_input_tokens,
                    cache_low_requests = cache_low_requests + excluded.cache_low_requests,
                    cache_low_input_tokens = cache_low_input_tokens + excluded.cache_low_input_tokens,
                    error_requests = error_requests + excluded.error_requests,
                    response_time_sum_ms = response_time_sum_ms + excluded.response_time_sum_ms,
                    response_time_count = response_time_count + excluded.response_time_count,
                    response_time_max_ms = max(response_time_max_ms, excluded.response_time_max_ms),
                    upstream_response_time_sum_ms = upstream_response_time_sum_ms + excluded.upstream_response_time_sum_ms,
                    upstream_response_time_count = upstream_response_time_count + excluded.upstream_response_time_count,
                    upstream_response_time_max_ms = max(upstream_response_time_max_ms, excluded.upstream_response_time_max_ms),
                    total_response_time_sum_ms = total_response_time_sum_ms + excluded.total_response_time_sum_ms,
                    total_response_time_count = total_response_time_count + excluded.total_response_time_count,
                    total_response_time_max_ms = max(total_response_time_max_ms, excluded.total_response_time_max_ms),
                    split_latency_requests = split_latency_requests + excluded.split_latency_requests,
                    legacy_latency_requests = legacy_latency_requests + excluded.legacy_latency_requests
                """,
                (
                    day, entry.actual_model, entry.tier, entry.request_api_kind,
                    entry.requested_reasoning_effort or "", entry.input_tokens,
                    entry.cached_input_tokens, entry.output_tokens, int(usage_seen),
                    entry.input_tokens if usage_seen else 0,
                    entry.cached_input_tokens if usage_seen else 0,
                    int(cache_likely), entry.input_tokens if cache_likely else 0,
                    entry.cached_input_tokens if cache_likely else 0,
                    int(cache_low), entry.input_tokens if cache_low else 0,
                    int(entry.error not in (None, "")), response,
                    int(response != 0), response, upstream, int(upstream != 0),
                    upstream, total, int(total != 0), total,
                    int(upstream != 0), int(legacy_latency),
                ),
            )
            self._increment_native_cache_unlocked(day, entry.actual_model, entry.selected_codex_account_key or "",
                                                   context, entry.input_tokens, entry.cached_input_tokens)
            self._conn.commit()

    def _increment_native_cache_unlocked(self, day: str, model: str, account: str, context: dict[str, Any],
                                        input_tokens: int, cached_tokens: int) -> None:
        native = context.get("native_cache")
        if not isinstance(native, dict) or native.get("version") != 2:
            return
        row = self._conn.execute(
            "SELECT metrics_json FROM native_cache_daily_rollups WHERE day=? AND actual_model=? AND account_key=? AND diagnostic_version=2",
            (day, model, account),
        ).fetchone()
        metrics = json.loads(row[0]) if row else empty_native_metrics()
        add_native_request(metrics, context, input_tokens, cached_tokens)
        self._conn.execute(
            "INSERT OR REPLACE INTO native_cache_daily_rollups VALUES (?, ?, ?, 2, ?)",
            (day, model, account, json.dumps(metrics, separators=(",", ":"))),
        )

    def native_cache_daily_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, actual_model, account_key, metrics_json FROM native_cache_daily_rollups WHERE diagnostic_version=2 ORDER BY day, actual_model, account_key"
            ).fetchall()
        return [{"day": row["day"], "actualModel": row["actual_model"], "accountKey": row["account_key"] or None,
                 "metrics": public_native_metrics(json.loads(row["metrics_json"]))} for row in rows]

    def reasoning_realm_for_chain(self, chain_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT realm FROM reasoning_chain_bindings WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()
        return str(row["realm"]) if row is not None else None

    def bind_reasoning_chain(self, chain_id: str, realm: str) -> bool:
        timestamp = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO reasoning_chain_bindings (chain_id, realm, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (chain_id, realm, timestamp, timestamp),
            )
            row = self._conn.execute(
                "SELECT realm FROM reasoning_chain_bindings WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()
            if row is None or str(row["realm"]) != realm:
                return False
            self._conn.execute(
                "UPDATE reasoning_chain_bindings SET updated_at = ? WHERE chain_id = ?",
                (timestamp, chain_id),
            )
        return True

    def _delete_routing_rows_in_batches_unlocked(
        self,
        *,
        predicate: str,
        parameters: tuple[Any, ...],
    ) -> int:
        last_seen_id = 0
        deleted = 0

        while True:
            rows = self._conn.execute(
                f"""
                SELECT id
                FROM routing_log
                WHERE id > ?
                  AND ({predicate})
                ORDER BY id
                LIMIT ?
                """,
                (last_seen_id, *parameters, ROUTING_RETENTION_DELETE_BATCH_SIZE),
            ).fetchall()
            if not rows:
                break

            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            cursor = self._conn.execute(
                f"DELETE FROM routing_log WHERE id IN ({placeholders})",
                ids,
            )
            deleted += max(0, int(cursor.rowcount))
            last_seen_id = ids[-1]

        return deleted

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
                age_pruned = self._delete_routing_rows_in_batches_unlocked(
                    predicate=(
                        "julianday(timestamp) IS NULL OR "
                        "julianday(timestamp) < julianday(?)"
                    ),
                    parameters=(cutoff_text,),
                )

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
                    row_cap_pruned = self._delete_routing_rows_in_batches_unlocked(
                        predicate="id < ?",
                        parameters=(int(threshold["id"]),),
                    )

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
        native_rollups: dict[tuple[str, str, str], dict[str, Any]] = {}
        rollups: dict[tuple[str, str, str, str, str], list[Any]] = {}
        cursor = self._conn.execute(
            """
            SELECT date(timestamp) AS day,
                   actual_model,
                   tier,
                   request_api_kind,
                   coalesce(requested_reasoning_effort, '') AS requested_reasoning_effort,
                   input_tokens,
                   cached_input_tokens,
                   output_tokens,
                   response_time_ms,
                   upstream_response_time_ms,
                   total_response_time_ms,
                   error,
                   context_info,
                   selected_codex_account_key
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
                aggregate = [*key, *([0] * 24)]
                rollups[key] = aggregate
            input_tokens = int(row["input_tokens"] or 0)
            cached_tokens = int(row["cached_input_tokens"] or 0)
            response = int(row["response_time_ms"] or 0)
            upstream_raw = row["upstream_response_time_ms"]
            total_raw = row["total_response_time_ms"]
            upstream = int(upstream_raw or 0)
            total = int(total_raw or 0) or response
            context = self._row_context(row)
            native = context.get("native_cache")
            if isinstance(native, dict) and native.get("version") == 2:
                native_key = (str(row["day"]), str(row["actual_model"]), row["selected_codex_account_key"] or "")
                native_metrics = native_rollups.setdefault(native_key, empty_native_metrics())
                add_native_request(native_metrics, context, input_tokens, cached_tokens)
            usage_seen = self._sqlite_json_flag(context.get("usage_seen"))
            cache_likely = self._sqlite_json_flag(context.get("cache_likely"))
            cache_low = self._sqlite_json_flag(context.get("cache_low"))

            aggregate[5] += 1
            aggregate[6] += input_tokens
            aggregate[7] += cached_tokens
            aggregate[8] += int(row["output_tokens"] or 0)
            aggregate[9] += int(usage_seen)
            aggregate[10] += input_tokens if usage_seen else 0
            aggregate[11] += cached_tokens if usage_seen else 0
            aggregate[12] += int(cache_likely)
            aggregate[13] += input_tokens if cache_likely else 0
            aggregate[14] += cached_tokens if cache_likely else 0
            aggregate[15] += int(cache_low)
            aggregate[16] += input_tokens if cache_low else 0
            aggregate[17] += int(row["error"] not in (None, ""))
            aggregate[18] += response
            aggregate[19] += int(response != 0)
            aggregate[20] = max(aggregate[20], response)
            aggregate[21] += upstream
            aggregate[22] += int(upstream != 0)
            aggregate[23] = max(aggregate[23], upstream)
            aggregate[24] += total
            aggregate[25] += int(total != 0)
            aggregate[26] = max(aggregate[26], total)
            aggregate[27] += int(upstream != 0)
            aggregate[28] += int(upstream_raw is None and total_raw is None)

        self._conn.execute("DELETE FROM native_cache_daily_rollups")
        self._conn.executemany(
            "INSERT INTO native_cache_daily_rollups VALUES (?, ?, ?, 2, ?)",
            [(*key, json.dumps(metrics, separators=(",", ":"))) for key, metrics in native_rollups.items()],
        )
        self._conn.execute("DELETE FROM routing_daily_rollups")
        if rollups:
            self._conn.executemany(
                """
                INSERT INTO routing_daily_rollups (
                    day, actual_model, tier, request_api_kind, requested_reasoning_effort,
                    requests, input_tokens, cached_input_tokens, output_tokens,
                    usage_requests, usage_input_tokens, usage_cached_input_tokens,
                    cache_likely_requests, cache_likely_input_tokens,
                    cache_likely_cached_input_tokens, cache_low_requests,
                    cache_low_input_tokens, error_requests, response_time_sum_ms,
                    response_time_count, response_time_max_ms,
                    upstream_response_time_sum_ms, upstream_response_time_count,
                    upstream_response_time_max_ms, total_response_time_sum_ms,
                    total_response_time_count, total_response_time_max_ms,
                    split_latency_requests, legacy_latency_requests
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rollups.values(),
            )

    def daily_cache_rows(self) -> list[dict[str, Any]]:
        by_day: dict[str | None, dict[str, Any]] = {}
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT day, requests, usage_requests, usage_input_tokens,
                       usage_cached_input_tokens, cache_likely_requests,
                       cache_likely_input_tokens, cache_likely_cached_input_tokens,
                       cache_low_requests, cache_low_input_tokens, error_requests,
                       response_time_sum_ms, response_time_count, response_time_max_ms,
                       upstream_response_time_sum_ms, upstream_response_time_count,
                       upstream_response_time_max_ms, total_response_time_sum_ms,
                       total_response_time_count, total_response_time_max_ms,
                       split_latency_requests, legacy_latency_requests
                FROM routing_daily_rollups
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

                logged = int(row["requests"] or 0)
                usage_requests = int(row["usage_requests"] or 0)
                aggregate["logged_requests"] += logged
                aggregate["requests"] += usage_requests
                aggregate["input_tokens"] += int(row["usage_input_tokens"] or 0)
                aggregate["cached_input_tokens"] += int(row["usage_cached_input_tokens"] or 0)
                aggregate["usage_missing_requests"] += logged - usage_requests
                for target, source in (
                    ("cache_likely_requests", "cache_likely_requests"),
                    ("cache_likely_input_tokens", "cache_likely_input_tokens"),
                    ("cache_likely_cached_input_tokens", "cache_likely_cached_input_tokens"),
                    ("cache_low_requests", "cache_low_requests"),
                    ("cache_low_input_tokens", "cache_low_input_tokens"),
                    ("errors", "error_requests"),
                    ("split_latency_requests", "split_latency_requests"),
                    ("legacy_latency_requests", "legacy_latency_requests"),
                ):
                    aggregate[target] += int(row[source] or 0)

                for aggregate_prefix, column_prefix in (
                    ("response", "response"),
                    ("upstream", "upstream_response"),
                    ("total", "total_response"),
                ):
                    count = int(row[f"{column_prefix}_time_count"] or 0)
                    aggregate[f"_{aggregate_prefix}_sum"] += int(
                        row[f"{column_prefix}_time_sum_ms"] or 0
                    )
                    aggregate[f"_{aggregate_prefix}_count"] += count
                    if count:
                        maximum = int(row[f"{column_prefix}_time_max_ms"] or 0)
                        aggregate[f"_{aggregate_prefix}_max"] = max(
                            aggregate[f"_{aggregate_prefix}_max"] or maximum,
                            maximum,
                        )

        native_by_day: dict[str, dict[str, Any]] = {}
        for native_row in self.native_cache_daily_rows():
            if native_row["actualModel"].startswith("codex/"):
                target = native_by_day.setdefault(native_row["day"], empty_native_metrics())
                merge_native_metrics(target, native_row["metrics"])
        rows = []
        for day in sorted(by_day, key=lambda value: (value is not None, value or "")):
            aggregate = by_day[day]
            aggregate["nativeCache"] = public_native_metrics(native_by_day.get(day, empty_native_metrics()))
            aggregate["legacyCacheHeuristics"] = True
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
        native_metrics = empty_native_metrics()
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
            add_native_request(native_metrics, context, int(item.get("input_tokens") or 0), int(item.get("cached_input_tokens") or 0))
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
            "nativeCache": public_native_metrics(native_metrics),
            "legacyCacheHeuristics": True,
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

    def routing_filter_options(self) -> dict[str, list[str]]:
        with self._lock:
            model_rows = self._conn.execute(
                """
                SELECT DISTINCT actual_model
                FROM routing_daily_rollups
                WHERE requests > 0
                ORDER BY actual_model
                """
            ).fetchall()
            reasoning_rows = self._conn.execute(
                """
                SELECT DISTINCT requested_reasoning_effort
                FROM routing_daily_rollups
                WHERE requests > 0
                ORDER BY requested_reasoning_effort
                """
            ).fetchall()
        reasoning = [
            str(row["requested_reasoning_effort"]).strip().lower() or "default"
            for row in reasoning_rows
        ]
        return {
            "models": [str(row["actual_model"]) for row in model_rows],
            "reasoningEfforts": sorted(set(reasoning), key=lambda value: (value != "default", value)),
        }

    def live_sessions(
        self,
        limit: int = 30,
        session_limit: int | None = None,
        turn_limit: int = 20,
        *,
        model: str | None = None,
        reasoning: str | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(100, int(session_limit if session_limit is not None else limit)))
        safe_turn_limit = max(1, min(100, int(turn_limit)))
        filters = {
            "model": (model or "").strip(),
            "reasoning": (reasoning or "").strip().lower(),
            "prompt": (prompt or "").strip(),
        }
        clauses = ["session_id IS NOT NULL"]
        params: list[Any] = []
        if filters["model"]:
            clauses.append("actual_model = ?")
            params.append(filters["model"])
        if filters["reasoning"] == "default":
            clauses.append("trim(coalesce(requested_reasoning_effort, '')) = ''")
        elif filters["reasoning"]:
            clauses.append("lower(trim(coalesce(requested_reasoning_effort, ''))) = ?")
            params.append(filters["reasoning"])
        if filters["prompt"]:
            escaped = filters["prompt"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("lower(coalesce(prompt_preview, '')) LIKE lower(?) ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where_sql = " AND ".join(clauses)
        selection_sql = f"""
            WITH matching_turns AS (
                SELECT
                    session_id,
                    coalesce(nullif(turn_id, ''), nullif(request_id, ''), 'unknown') AS effective_turn_id,
                    max(id) AS last_match_id
                FROM routing_log
                WHERE {where_sql}
                GROUP BY session_id, effective_turn_id
            ),
            ranked_turns AS (
                SELECT
                    session_id,
                    effective_turn_id,
                    last_match_id,
                    row_number() OVER (
                        PARTITION BY session_id ORDER BY last_match_id DESC
                    ) AS turn_rank,
                    count(*) OVER (PARTITION BY session_id) AS matching_turn_count,
                    count(*) OVER () AS total_matching_turns
                FROM matching_turns
            ),
            ranked_sessions AS (
                SELECT session_id, max(last_match_id) AS last_match_id
                FROM matching_turns
                GROUP BY session_id
            ),
            limited_sessions AS (
                SELECT
                    session_id,
                    last_match_id,
                    count(*) OVER () AS total_sessions
                FROM ranked_sessions
                ORDER BY last_match_id DESC
                LIMIT ?
            )
            SELECT
                turns.session_id,
                turns.effective_turn_id,
                turns.last_match_id,
                turns.matching_turn_count,
                turns.total_matching_turns,
                sessions.total_sessions
            FROM ranked_turns turns
            JOIN limited_sessions sessions USING(session_id)
            WHERE turns.turn_rank <= ?
            ORDER BY sessions.last_match_id DESC, turns.last_match_id DESC
        """
        with self._lock:
            selected_rows = self._conn.execute(
                selection_sql,
                (*params, safe_limit, safe_turn_limit),
            ).fetchall()
            selected = {
                (str(row["session_id"]), str(row["effective_turn_id"])): dict(row)
                for row in selected_rows
            }
            session_ids = list(dict.fromkeys(str(row["session_id"]) for row in selected_rows))
            if session_ids:
                placeholders = ",".join("?" for _ in session_ids)
                rows = self._conn.execute(
                    f"SELECT * FROM routing_log WHERE session_id IN ({placeholders}) ORDER BY id DESC",
                    session_ids,
                ).fetchall()
            else:
                rows = []

        sessions: dict[str, dict[str, Any]] = {}
        for raw_row in rows:
            item = dict(raw_row)
            sid = str(item.get("session_id") or "unknown")
            turn_id = str(item.get("turn_id") or item.get("request_id") or "unknown")
            if (sid, turn_id) not in selected:
                continue
            match_meta = selected[(sid, turn_id)]
            session = sessions.setdefault(sid, {
                "id": sid,
                "startedAt": item["timestamp"],
                "lastAt": item["timestamp"],
                "lastId": item["id"],
                "matchLastId": int(match_meta["last_match_id"]),
                "metrics": self._empty_metrics(),
                "models": set(),
                "tiers": set(),
                "turns": {},
            })
            session["startedAt"] = min(session["startedAt"], item["timestamp"])
            session["lastAt"] = max(session["lastAt"], item["timestamp"])
            session["lastId"] = max(session["lastId"], item["id"])
            session["matchLastId"] = max(session["matchLastId"], int(match_meta["last_match_id"]))
            self._add_metrics(session["metrics"], item)
            session["models"].add(item["actual_model"])
            session["tiers"].add(item["tier"])
            turn = session["turns"].setdefault(turn_id, {
                "id": turn_id,
                "startedAt": item["timestamp"],
                "lastAt": item["timestamp"],
                "lastId": item["id"],
                "matchLastId": int(match_meta["last_match_id"]),
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

        values = sorted(sessions.values(), key=lambda session: session["matchLastId"], reverse=True)
        selected_meta = {str(row["session_id"]): dict(row) for row in selected_rows}
        for session in values:
            self._finalize_metrics(session["metrics"])
            turns = sorted(session["turns"].values(), key=lambda turn: turn["matchLastId"], reverse=True)
            matching_turn_count = int(selected_meta[session["id"]]["matching_turn_count"])
            session["turnCount"] = matching_turn_count
            session["hasMoreTurns"] = matching_turn_count > len(turns)
            session["turns"] = turns
            session["models"] = sorted(session["models"])
            session["tiers"] = sorted(session["tiers"])
            session.pop("lastId", None)
            session.pop("matchLastId", None)
            for turn in session["turns"]:
                self._finalize_metrics(turn["metrics"])
                turn["models"] = sorted(turn["models"])
                turn["tiers"] = sorted(turn["tiers"])
                turn.pop("lastId", None)
                turn.pop("matchLastId", None)
        total_sessions = int(selected_rows[0]["total_sessions"]) if selected_rows else 0
        total_matching_turns = int(selected_rows[0]["total_matching_turns"]) if selected_rows else 0
        return {
            "sessions": values,
            "hasMoreSessions": total_sessions > safe_limit,
            "matchingSessions": total_sessions,
            "matchingTurns": total_matching_turns,
            "retentionDays": self.retention_days,
            "filterOptions": self.routing_filter_options(),
            "appliedFilters": filters,
        }

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

    def codex_model_contexts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT model_id, max_context FROM codex_model_context_metadata"
            ).fetchall()
        return {
            row["model_id"]: row["max_context"] for row in rows
            if isinstance(row["model_id"], str) and row["model_id"].startswith("codex/")
            and type(row["max_context"]) is int and 1 <= row["max_context"] <= 2_000_000
        }

    def save_codex_model_contexts(self, contexts: dict[str, int]) -> None:
        if any(
            not isinstance(model_id, str) or not model_id.startswith("codex/")
            or type(context) is not int or not 1 <= context <= 2_000_000
            for model_id, context in contexts.items()
        ):
            raise ValueError("invalid_codex_context_metadata")
        updated_at = now_iso()
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO codex_model_context_metadata(model_id, max_context, updated_at)
                VALUES (?, ?, ?) ON CONFLICT(model_id) DO UPDATE SET
                max_context=excluded.max_context, updated_at=excluded.updated_at""",
                [(model_id, context, updated_at) for model_id, context in contexts.items()],
            )

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

    @staticmethod
    def _window_start(reset_at: str, window_minutes: int) -> str:
        text = str(reset_at).replace("Z", "+00:00")
        reset = datetime.fromisoformat(text)
        if reset.tzinfo is None:
            reset = reset.replace(tzinfo=timezone.utc)
        return (reset.astimezone(timezone.utc) - timedelta(minutes=int(window_minutes))).isoformat()

    def _replace_window_usage_unlocked(
        self,
        record: dict[str, Any],
        observed_at: str,
    ) -> None:
        account_key = str(record["account_key"])
        window = str(record["window"])
        reset_at = str(record["reset_at"])
        window_start = self._window_start(reset_at, int(record["window_minutes"]))
        self._conn.execute(
            "DELETE FROM codex_window_model_usage WHERE account_key = ? AND window = ?",
            (account_key, window),
        )
        self._conn.execute(
            "DELETE FROM codex_window_usage_coverage WHERE account_key = ? AND window = ?",
            (account_key, window),
        )
        self._conn.execute(
            "DELETE FROM codex_window_efficiency WHERE account_key = ? AND window = ?",
            (account_key, window),
        )
        usage_seen = """
            CASE
                WHEN json_valid(context_info)
                THEN coalesce(json_extract(context_info, '$.usage_seen'), 0)
                ELSE 0
            END = 1
        """
        model_rows = self._conn.execute(
            f"""
            SELECT
                actual_model,
                count(*) AS requests,
                coalesce(sum(input_tokens), 0) AS input_tokens,
                coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
                coalesce(sum(output_tokens), 0) AS output_tokens
            FROM routing_log
            WHERE selected_codex_account_key = ?
              AND actual_model LIKE 'codex/%'
              AND julianday(timestamp) >= julianday(?)
              AND julianday(timestamp) <= julianday(?)
              AND {usage_seen}
            GROUP BY actual_model
            """,
            (account_key, window_start, observed_at),
        ).fetchall()
        self._conn.executemany(
            """
            INSERT INTO codex_window_model_usage (
                account_key, window, reset_at, actual_model, window_start,
                observed_at, requests, input_tokens, cached_input_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    account_key,
                    window,
                    reset_at,
                    row["actual_model"],
                    window_start,
                    observed_at,
                    row["requests"],
                    row["input_tokens"],
                    row["cached_input_tokens"],
                    row["output_tokens"],
                )
                for row in model_rows
            ],
        )
        efficiency_rows = self._conn.execute(
            f"""
            SELECT
                actual_model,
                coalesce(nullif(lower(trim(requested_reasoning_effort)), ''), 'default') AS reasoning_effort,
                CASE
                    WHEN nullif(trim(session_id), '') IS NOT NULL THEN 'session:' || session_id
                    ELSE 'request:' || coalesce(nullif(request_id, ''), cast(id AS TEXT))
                END AS session_key,
                CASE WHEN nullif(trim(session_id), '') IS NOT NULL THEN 1 ELSE 0 END AS has_session,
                count(*) AS requests,
                coalesce(sum(input_tokens), 0) AS input_tokens,
                coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
                coalesce(sum(output_tokens), 0) AS output_tokens
            FROM routing_log
            WHERE selected_codex_account_key = ?
              AND actual_model LIKE 'codex/%'
              AND julianday(timestamp) >= julianday(?)
              AND julianday(timestamp) <= julianday(?)
              AND {usage_seen}
            GROUP BY actual_model, reasoning_effort, session_key, has_session
            """,
            (account_key, window_start, observed_at),
        ).fetchall()
        self._conn.executemany(
            """
            INSERT INTO codex_window_efficiency (
                account_key, window, reset_at, actual_model, reasoning_effort,
                session_key, has_session, window_start, observed_at, requests,
                input_tokens, cached_input_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    account_key, window, reset_at, row["actual_model"],
                    row["reasoning_effort"], row["session_key"], row["has_session"],
                    window_start, observed_at, row["requests"], row["input_tokens"],
                    row["cached_input_tokens"], row["output_tokens"],
                )
                for row in efficiency_rows
            ],
        )
        coverage = self._conn.execute(
            f"""
            SELECT
                coalesce(sum(CASE WHEN {usage_seen} THEN 1 ELSE 0 END), 0) AS attributed_requests,
                coalesce(sum(CASE WHEN {usage_seen} THEN input_tokens ELSE 0 END), 0) AS attributed_input_tokens,
                coalesce(sum(CASE WHEN {usage_seen} THEN output_tokens ELSE 0 END), 0) AS attributed_output_tokens,
                coalesce(sum(CASE WHEN NOT ({usage_seen}) THEN 1 ELSE 0 END), 0) AS excluded_requests,
                coalesce(sum(CASE WHEN NOT ({usage_seen}) THEN input_tokens ELSE 0 END), 0) AS excluded_input_tokens,
                coalesce(sum(CASE WHEN NOT ({usage_seen}) THEN output_tokens ELSE 0 END), 0) AS excluded_output_tokens
            FROM routing_log
            WHERE selected_codex_account_key = ?
              AND actual_model LIKE 'codex/%'
              AND julianday(timestamp) >= julianday(?)
              AND julianday(timestamp) <= julianday(?)
            """,
            (account_key, window_start, observed_at),
        ).fetchone()
        self._conn.execute(
            """
            INSERT INTO codex_window_usage_coverage (
                account_key, window, reset_at, window_start, observed_at,
                attributed_requests, attributed_input_tokens, attributed_output_tokens,
                excluded_requests, excluded_input_tokens, excluded_output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_key,
                window,
                reset_at,
                window_start,
                observed_at,
                coverage["attributed_requests"],
                coverage["attributed_input_tokens"],
                coverage["attributed_output_tokens"],
                coverage["excluded_requests"],
                coverage["excluded_input_tokens"],
                coverage["excluded_output_tokens"],
            ),
        )

    def _replace_global_unattributed_unlocked(
        self,
        account_keys: set[str],
        observed_at: str,
    ) -> None:
        self._conn.execute(
            "DELETE FROM codex_window_usage_coverage WHERE account_key = '' AND window = 'weekly'"
        )
        if not account_keys:
            return
        placeholders = ",".join("?" for _ in account_keys)
        snapshots = self._conn.execute(
            f"""
            SELECT reset_at, window_minutes, updated_at
            FROM codex_usage_snapshots
            WHERE window = 'weekly' AND account_key IN ({placeholders})
            """,
            sorted(account_keys),
        ).fetchall()
        if not snapshots:
            return
        ranges = [
            (self._window_start(row["reset_at"], int(row["window_minutes"])), str(row["updated_at"]))
            for row in snapshots
        ]
        range_sql = " OR ".join(
            "(julianday(timestamp) >= julianday(?) AND julianday(timestamp) <= julianday(?))"
            for _ in ranges
        )
        params = [value for pair in ranges for value in pair]
        coverage = self._conn.execute(
            f"""
            SELECT
                count(*) AS requests,
                coalesce(sum(input_tokens), 0) AS input_tokens,
                coalesce(sum(output_tokens), 0) AS output_tokens
            FROM routing_log
            WHERE nullif(selected_codex_account_key, '') IS NULL
              AND actual_model LIKE 'codex/%'
              AND ({range_sql})
            """,
            params,
        ).fetchone()
        self._conn.execute(
            """
            INSERT INTO codex_window_usage_coverage (
                account_key, window, reset_at, window_start, observed_at,
                attributed_requests, attributed_input_tokens, attributed_output_tokens,
                excluded_requests, excluded_input_tokens, excluded_output_tokens
            ) VALUES ('', 'weekly', 'current-union', ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (
                min(start for start, _end in ranges),
                max(end for _start, end in ranges),
                coverage["requests"],
                coverage["input_tokens"],
                coverage["output_tokens"],
            ),
        )

    def _prune_window_usage_unlocked(
        self,
        current_account_keys: set[str],
        refreshed_windows: dict[str, set[str]],
        *,
        full_pool_refresh: bool,
    ) -> None:
        tables = (
            "codex_window_model_usage",
            "codex_window_efficiency",
            "codex_window_usage_coverage",
        )
        current_keys = sorted(current_account_keys)
        for table in tables:
            if full_pool_refresh:
                if current_keys:
                    placeholders = ",".join("?" for _ in current_keys)
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE account_key != '' AND account_key NOT IN ({placeholders})",
                        current_keys,
                    )
                else:
                    self._conn.execute(f"DELETE FROM {table} WHERE account_key != ''")
            for account_key, windows in refreshed_windows.items():
                ordered_windows = sorted(windows)
                if ordered_windows:
                    placeholders = ",".join("?" for _ in ordered_windows)
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE account_key = ? AND window NOT IN ({placeholders})",
                        (account_key, *ordered_windows),
                    )
                else:
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE account_key = ?",
                        (account_key,),
                    )

    def upsert_usage_snapshots(
        self,
        records: list[dict[str, Any]],
        *,
        current_account_keys: set[str] | None = None,
        full_pool_refresh: bool = True,
    ) -> None:
        if not records and current_account_keys is None:
            return
        observed_at = now_iso()
        authoritative_accounts = {
            str(record["account_key"])
            for record in records
            if record.get("window") == "weekly"
        }
        refreshed_windows: dict[str, set[str]] = {}
        for record in records:
            refreshed_windows.setdefault(str(record["account_key"]), set()).add(str(record["window"]))
        current_keys = set(authoritative_accounts if current_account_keys is None else current_account_keys)
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
                    self._replace_window_usage_unlocked(record, observed_at)
                self._prune_window_usage_unlocked(
                    current_keys,
                    refreshed_windows,
                    full_pool_refresh=full_pool_refresh,
                )
                if full_pool_refresh:
                    self._replace_global_unattributed_unlocked(current_keys, observed_at)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def codex_capacity_multiplier(self, account_key: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT capacity_multiplier FROM codex_account_capacity_overrides WHERE account_key = ?",
                (account_key,),
            ).fetchone()
        return int(row["capacity_multiplier"]) if row is not None else 1

    def set_codex_capacity_multiplier(self, account_key: str, multiplier: int) -> None:
        if multiplier not in {1, 5} or isinstance(multiplier, bool):
            raise ValueError("capacity multiplier must be 1 or 5")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if multiplier == 1:
                    self._conn.execute(
                        "DELETE FROM codex_account_capacity_overrides WHERE account_key = ?",
                        (account_key,),
                    )
                else:
                    self._conn.execute(
                        """
                        INSERT INTO codex_account_capacity_overrides (
                            account_key, capacity_multiplier, updated_at
                        ) VALUES (?, 5, ?)
                        ON CONFLICT(account_key) DO UPDATE SET
                            capacity_multiplier = 5,
                            updated_at = excluded.updated_at
                        """,
                        (account_key, now_iso()),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def codex_usage_estimate(self, active_account_keys: set[str]) -> dict[str, Any]:
        keys = sorted({key for key in active_account_keys if key})
        unavailable = {
            "available": False,
            "source": "calibrated_total_tokens",
            "unavailableReason": "no_current_weekly_accounts",
            "models": [],
            "efficiency": [],
        }
        if not keys:
            return unavailable
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            snapshots = [dict(row) for row in self._conn.execute(
                f"""
                SELECT * FROM codex_usage_snapshots
                WHERE window = 'weekly' AND account_key IN ({placeholders})
                """,
                keys,
            ).fetchall()]
            model_rows = [dict(row) for row in self._conn.execute(
                f"""
                SELECT * FROM codex_window_model_usage
                WHERE window = 'weekly' AND account_key IN ({placeholders})
                """,
                keys,
            ).fetchall()]
            efficiency_rows = [dict(row) for row in self._conn.execute(
                f"""
                SELECT * FROM codex_window_efficiency
                WHERE window = 'weekly' AND account_key IN ({placeholders})
                """,
                keys,
            ).fetchall()]
            coverage_rows = [dict(row) for row in self._conn.execute(
                f"""
                SELECT * FROM codex_window_usage_coverage
                WHERE window = 'weekly' AND (account_key IN ({placeholders}) OR account_key = '')
                """,
                keys,
            ).fetchall()]
            override_rows = self._conn.execute(
                f"""
                SELECT account_key, capacity_multiplier
                FROM codex_account_capacity_overrides
                WHERE account_key IN ({placeholders})
                """,
                keys,
            ).fetchall()
        multipliers = {str(row["account_key"]): int(row["capacity_multiplier"]) for row in override_rows}
        snapshots_by_account = {str(row["account_key"]): row for row in snapshots}
        current_models = [
            row for row in model_rows
            if (snapshot := snapshots_by_account.get(str(row["account_key"])))
            and row["reset_at"] == snapshot["reset_at"]
        ]
        current_efficiency = [
            row for row in efficiency_rows
            if (snapshot := snapshots_by_account.get(str(row["account_key"])))
            and row["reset_at"] == snapshot["reset_at"]
        ]
        current_coverage = [
            row for row in coverage_rows
            if row["account_key"]
            and (snapshot := snapshots_by_account.get(str(row["account_key"])))
            and row["reset_at"] == snapshot["reset_at"]
        ]
        coverage_by_account = {str(row["account_key"]): row for row in current_coverage}
        actual_units = 0.0
        sample_tokens = 0
        sample_accounts = 0
        account_rates: dict[str, float] = {}
        for account_key, snapshot in snapshots_by_account.items():
            coverage = coverage_by_account.get(account_key)
            attributed = (
                int(coverage["attributed_input_tokens"]) + int(coverage["attributed_output_tokens"])
                if coverage else 0
            )
            used = float(snapshot["used_percent"] or 0)
            if attributed > 0 and used > 0:
                sample_accounts += 1
                sample_tokens += attributed
                account_units = used * multipliers.get(account_key, 1)
                actual_units += account_units
                account_rates[account_key] = account_units * 1_000_000 / attributed
        rate = actual_units * 1_000_000 / sample_tokens if sample_tokens else None
        by_model: dict[str, dict[str, Any]] = {}
        for row in current_models:
            model = str(row["actual_model"])
            aggregate = by_model.setdefault(model, {
                "model": model,
                "requests": 0,
                "inputTokens": 0,
                "cachedInputTokens": 0,
                "outputTokens": 0,
            })
            aggregate["requests"] += int(row["requests"])
            aggregate["inputTokens"] += int(row["input_tokens"])
            aggregate["cachedInputTokens"] += int(row["cached_input_tokens"])
            aggregate["outputTokens"] += int(row["output_tokens"])
        models = []
        for aggregate in by_model.values():
            total_tokens = aggregate["inputTokens"] + aggregate["outputTokens"]
            expected_units = total_tokens / 1_000_000 * rate if rate is not None else None
            models.append({
                **aggregate,
                "totalTokens": total_tokens,
                "expectedUnits": expected_units,
                "x1EquivalentPercent": expected_units,
                "x5EquivalentPercent": expected_units / 5 if expected_units is not None else None,
            })
        models.sort(key=lambda row: row["totalTokens"], reverse=True)
        by_efficiency: dict[tuple[str, str], dict[str, Any]] = {}
        for row in current_efficiency:
            group_key = (str(row["actual_model"]), str(row["reasoning_effort"]))
            aggregate = by_efficiency.setdefault(group_key, {
                "model": group_key[0],
                "reasoning": group_key[1],
                "requests": 0,
                "inputTokens": 0,
                "cachedInputTokens": 0,
                "outputTokens": 0,
                "calibratedTokens": 0,
                "expectedUnits": 0.0,
                "sessionTokens": 0,
                "requestsWithoutSession": 0,
                "_sessions": set(),
            })
            input_tokens = int(row["input_tokens"])
            output_tokens = int(row["output_tokens"])
            total_tokens = input_tokens + output_tokens
            requests = int(row["requests"])
            aggregate["requests"] += requests
            aggregate["inputTokens"] += input_tokens
            aggregate["cachedInputTokens"] += int(row["cached_input_tokens"])
            aggregate["outputTokens"] += output_tokens
            if int(row["has_session"]):
                aggregate["_sessions"].add(str(row["session_key"]))
                aggregate["sessionTokens"] += total_tokens
            else:
                aggregate["requestsWithoutSession"] += requests
            account_rate = account_rates.get(str(row["account_key"]))
            if account_rate is not None:
                aggregate["calibratedTokens"] += total_tokens
                aggregate["expectedUnits"] += total_tokens / 1_000_000 * account_rate
        efficiency = []
        for aggregate in by_efficiency.values():
            sessions = len(aggregate.pop("_sessions"))
            total_tokens = aggregate["inputTokens"] + aggregate["outputTokens"]
            calibrated_tokens = aggregate["calibratedTokens"]
            expected_units = aggregate["expectedUnits"] if calibrated_tokens else None
            efficiency.append({
                **aggregate,
                "totalTokens": total_tokens,
                "sessions": sessions,
                "percentPerMillionTokens": (
                    expected_units * 1_000_000 / calibrated_tokens
                    if expected_units is not None and calibrated_tokens else None
                ),
                "averageMillionTokensPerSession": (
                    aggregate["sessionTokens"] / 1_000_000 / sessions if sessions else None
                ),
                "averageUnitsPerSession": (
                    expected_units / sessions if expected_units is not None and sessions else None
                ),
                "cachePercent": (
                    aggregate["cachedInputTokens"] * 100 / aggregate["inputTokens"]
                    if aggregate["inputTokens"] else 0
                ),
            })
        efficiency.sort(key=lambda row: row["totalTokens"], reverse=True)
        global_coverage = next((row for row in coverage_rows if row["account_key"] == ""), None)
        attributed_requests = sum(int(row["attributed_requests"]) for row in current_coverage)
        attributed_tokens = sum(
            int(row["attributed_input_tokens"]) + int(row["attributed_output_tokens"])
            for row in current_coverage
        )
        excluded_requests = sum(int(row["excluded_requests"]) for row in current_coverage)
        excluded_tokens = sum(
            int(row["excluded_input_tokens"]) + int(row["excluded_output_tokens"])
            for row in current_coverage
        )
        unavailable_reason = None
        if not snapshots:
            unavailable_reason = "no_current_weekly_snapshots"
        elif not sample_tokens:
            unavailable_reason = "no_attributed_usage_tokens"
        elif actual_units <= 0:
            unavailable_reason = "no_positive_weekly_usage"
        bounds = current_coverage or current_models
        return {
            "available": unavailable_reason is None,
            "source": "calibrated_total_tokens",
            "unavailableReason": unavailable_reason,
            "actualQuotaUnits": actual_units,
            "quotaUnitsPerMillionTokens": rate,
            "sampleAccounts": sample_accounts,
            "sampleTokens": sample_tokens,
            "attributedRequests": attributed_requests,
            "attributedTokens": attributed_tokens,
            "excludedAccountRequests": excluded_requests,
            "excludedAccountTokens": excluded_tokens,
            "unattributedRequests": int(global_coverage["excluded_requests"]) if global_coverage else 0,
            "unattributedTokens": (
                int(global_coverage["excluded_input_tokens"]) + int(global_coverage["excluded_output_tokens"])
                if global_coverage else 0
            ),
            "windowStart": min((str(row["window_start"]) for row in bounds), default=None),
            "observedAt": max((str(row["observed_at"]) for row in bounds), default=None),
            "models": models,
            "efficiency": efficiency,
        }

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
            "codexInputTokens": 0,
            "codexOutputTokens": 0,
            "codexUsageSeenRequests": 0,
            "errors": 0,
            "usageSeenRequests": 0,
            "usageMissingRequests": 0,
            "cacheLikelyRequests": 0,
            "nativeCache": empty_native_metrics(),
            "legacyCacheHeuristics": True,
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
            "toolTelemetryPartialRequests": 0,
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
        add_native_request(metrics["nativeCache"], context, int(row.get("input_tokens") or 0), int(row.get("cached_input_tokens") or 0))
        if row.get("had_tool_calls"):
            metrics["toolEnabledRequests"] += 1
        actual_tool_call_count, tool_complete = self._tool_telemetry(row)
        if actual_tool_call_count is None:
            metrics["toolTelemetryUnknownRequests"] += 1
        else:
            metrics["toolTelemetryKnownRequests" if tool_complete else "toolTelemetryPartialRequests"] += 1
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
            if str(row.get("actual_model") or "").startswith("codex/"):
                metrics["codexInputTokens"] += int(row.get("input_tokens") or 0)
                metrics["codexOutputTokens"] += int(row.get("output_tokens") or 0)
                metrics["codexUsageSeenRequests"] += 1
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
        metrics["nativeCache"] = public_native_metrics(metrics["nativeCache"])
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
            "actualToolCallCount": self._tool_telemetry(row)[0],
            "toolTelemetryComplete": self._tool_telemetry(row)[1],
            "actualToolNames": self._tool_names_from_row(row),
            "requestedReasoningEffort": row.get("requested_reasoning_effort"),
            "context": context,
        }

    def _tool_telemetry(self, row: dict[str, Any]) -> tuple[int | None, bool]:
        count = row.get("actual_tool_call_count")
        context = self._row_context(row)
        if context.get("tool_telemetry_version") == 2:
            complete = context.get("tool_telemetry_complete") is True
            return (count if complete or count else None), complete
        if row.get("request_api_kind") == "responses" and str(row.get("actual_model", "")).startswith("codex/"):
            # Old native telemetry ignored item events. Retain stored evidence,
            # but never display its unverified zeros as factual counts.
            return (count if count else None), False
        return count, count is not None

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
