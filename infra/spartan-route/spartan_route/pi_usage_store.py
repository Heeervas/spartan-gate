"""Compact, non-expiring Pi session origins and observed attempt totals."""
from __future__ import annotations

import json
import uuid
from typing import Any

from .util import now_iso

COUNTERS = ("inputTokens", "cachedInputTokens", "outputTokens", "totalTokens", "codexTokens", "unknownQuotaTokens")
MAX_SAFE_INTEGER = 2**53 - 1


def session_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("sessionId must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("sessionId must be a UUID") from None
    if str(parsed) != value.lower():
        raise ValueError("sessionId must be a canonical UUID")
    return str(parsed)


def normalize_baseline(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError("baseline must be an object")
    result = {key: value.get(key, 0) for key in COUNTERS}
    if any(type(number) is not int or not 0 <= number <= MAX_SAFE_INTEGER for number in result.values()):
        raise ValueError("baseline counters must be nonnegative safe integers")
    if result["cachedInputTokens"] > result["inputTokens"]:
        raise ValueError("cached input exceeds inclusive input")
    if result["totalTokens"] != result["inputTokens"] + result["outputTokens"]:
        raise ValueError("totalTokens must equal inclusive input plus output")
    if result["codexTokens"] + result["unknownQuotaTokens"] > result["totalTokens"]:
        raise ValueError("provider subtotals exceed totalTokens")
    return result


class PiUsageStore:
    """Shares the telemetry connection/lock, but never its retention tables."""

    def __init__(self, connection: Any, lock: Any, telemetry: Any = None):
        self.telemetry = telemetry
        self.connection = connection
        self.lock = lock
        with lock, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS pi_usage_sessions (
                session_id TEXT PRIMARY KEY, origin_id TEXT NOT NULL UNIQUE,
                baseline TEXT NOT NULL, observed TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                usage_seen INTEGER NOT NULL DEFAULT 0,
                incomplete INTEGER NOT NULL DEFAULT 0,
                tracking_incomplete INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def register(self, session_id: str, baseline: dict[str, int], tracking_incomplete: bool = False) -> tuple[dict[str, Any], bool]:
        if self.telemetry is not None:
            self.telemetry.require_optional()
        timestamp = now_iso()
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO pi_usage_sessions
                (session_id,origin_id,baseline,observed,tracking_incomplete,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (session_id, str(uuid.uuid4()), json.dumps(baseline, sort_keys=True),
                 json.dumps(dict.fromkeys(COUNTERS, 0)), int(tracking_incomplete), timestamp, timestamp),
            )
            created = cursor.rowcount == 1
            if tracking_incomplete:
                self.connection.execute("UPDATE pi_usage_sessions SET tracking_incomplete=1 WHERE session_id=?", (session_id,))
            row = self.connection.execute("SELECT * FROM pi_usage_sessions WHERE session_id=?", (session_id,)).fetchone()
        return self._public(row), created

    def get(self, session_id: str) -> dict[str, Any] | None:
        if self.telemetry is not None:
            self.telemetry.require_optional()
        with self.lock:
            row = self.connection.execute("SELECT * FROM pi_usage_sessions WHERE session_id=?", (session_id,)).fetchone()
        return self._public(row) if row is not None else None

    def record(self, session_id: str, origin_id: str, counters: dict[str, int], seen: bool, incomplete: bool) -> None:
        if self.telemetry is not None and self.telemetry.omit_optional():
            return
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT observed FROM pi_usage_sessions WHERE session_id=? AND origin_id=?", (session_id, origin_id),
            ).fetchone()
            if row is None:
                return
            observed = json.loads(row["observed"])
            for key in COUNTERS:
                observed[key] += counters[key]
            self.connection.execute("""UPDATE pi_usage_sessions SET observed=?,attempts=attempts+1,
                usage_seen=usage_seen+?,incomplete=incomplete+?,updated_at=?
                WHERE session_id=? AND origin_id=?""",
                (json.dumps(observed), int(seen), int(incomplete), now_iso(), session_id, origin_id),
            )

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        baseline, observed = json.loads(row["baseline"]), json.loads(row["observed"])
        return {
            "sessionId": row["session_id"], "originId": row["origin_id"],
            "baseline": baseline, "observed": observed,
            "totals": {key: baseline[key] + observed[key] for key in COUNTERS},
            "coverage": {"attempts": row["attempts"], "usageSeenAttempts": row["usage_seen"],
                         "usageMissingAttempts": row["attempts"] - row["usage_seen"],
                         "incompleteAttempts": row["incomplete"], "trackingIncomplete": bool(row["tracking_incomplete"]),
                         "historicalCoverage": "client_reported_partial", "unknownProviderTokens": baseline["unknownQuotaTokens"],
                         "billingExact": False},
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }
