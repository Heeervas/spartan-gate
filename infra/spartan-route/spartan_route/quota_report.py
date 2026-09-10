#!/usr/bin/env python3
"""Read-only model-isolated quota observations; no inferred token-category prices."""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def experimental_windows(conn: sqlite3.Connection) -> list[dict]:
    windows = []
    accounted_attempts = set()
    for row in conn.execute(
        """SELECT timestamp, selected_codex_account_key, context_info FROM routing_log
           WHERE CASE WHEN json_valid(context_info) THEN
           json_type(context_info, '$.native_cache.experiment') IS NOT NULL
           OR json_type(context_info, '$.native_cache.attempts[0].experiment') IS NOT NULL
           OR json_type(context_info, '$.native_cache.attempts[1].experiment') IS NOT NULL
           OR json_type(context_info, '$.native_cache.attempts[2].experiment') IS NOT NULL
           ELSE 0 END"""
    ):
        context = json.loads(row["context_info"])
        native = context.get("native_cache") or {}
        for attempt in native.get("attempts") or [native]:
            experiment = attempt.get("experiment") or native.get("experiment")
            if not isinstance(experiment, dict):
                continue
            end = timestamp(row["timestamp"]).timestamp()
            start = attempt.get("sent_at")
            start = start if isinstance(start, (int, float)) else end
            accounted = (attempt.get("completed") is True and context.get("usage_seen") is True
                         and native.get("completed") is True and attempt.get("account_key") == row["selected_codex_account_key"]
                         and attempt.get("cache_read_known") is True)
            if accounted and experiment.get("attempt_id"):
                accounted_attempts.add(experiment["attempt_id"])
            windows.append(dict(account=attempt.get("account_key") or row["selected_codex_account_key"],
                                start=min(start, end), end=end, experiment=experiment, accounted=accounted))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_probe_reservations'").fetchone():
        for row in conn.execute("SELECT * FROM cache_probe_reservations WHERE outcome != 'not_sent'"):
            windows.append(dict(account=row["account_key"], start=row["reserved_at"], end=row["finished_at"],
                                experiment={"kind": "probe", "run_id": row["run_id"]}, accounted=False))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_experiment_reservations'").fetchone():
        for row in conn.execute("SELECT * FROM cache_experiment_reservations"):
            windows.append(dict(account=row["account_key"], start=row["reserved_at"], end=row["finished_at"],
                                experiment={"kind": "cohort", "run_id": row["run_id"], "arm": row["arm"]},
                                accounted=row["outcome"] == "completed" and row["attempt_id"] in accounted_attempts))
    return windows


def quota_report(conn: sqlite3.Connection, model: str, since: str, *, cohort: tuple[str, str] | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    cutoff = timestamp(since)
    experiment_windows = experimental_windows(conn)
    excluded_experiment_intervals = 0

    def wanted(experiment):
        return (cohort is not None and isinstance(experiment, dict) and experiment.get("kind") == "cohort"
                and experiment.get("run_id") == cohort[0] and experiment.get("arm") == cohort[1]
                and not experiment.get("skipped"))
    history = [dict(row) for row in conn.execute(
        """SELECT h.*, coalesce(o.capacity_multiplier, 1) multiplier,
                  o.updated_at capacity_updated_at
           FROM codex_usage_snapshot_history h
           LEFT JOIN codex_account_capacity_overrides o USING(account_key)
           WHERE h.window = 'weekly' ORDER BY h.account_key, h.observed_at"""
    ) if timestamp(row["observed_at"]) >= cutoff]
    samples, unresolved = [], []
    first_routing_row = conn.execute("SELECT min(timestamp) FROM routing_log").fetchone()[0]

    def finish(span):
        if not span or not span["requests"]:
            return
        span["units"] = (span.pop("end_percent") - span.pop("start_percent")) * span["multiplier"]
        # Smaller movements are dominated by integer-percentage quantization.
        target = samples if (span["units"] >= 2 * span["multiplier"]
                             and span["input_tokens"] + span["output_tokens"] > 0) else unresolved
        target.append(span)

    for _key, group in itertools.groupby(history, key=lambda row: row["account_key"]):
        rows = list(group)
        active = None
        for before, after in zip(rows, rows[1:]):
            same_window = (
                before["window_minutes"] == after["window_minutes"] == 10080
                and abs((timestamp(before["reset_at"]) - timestamp(after["reset_at"])).total_seconds()) <= 2
                and before["multiplier"] == after["multiplier"]
                and before["multiplier"] in (1, 5)
                and 0 <= before["used_percent"] < 100 and 0 <= after["used_percent"] < 100
            )
            capacity_at = after["capacity_updated_at"]
            if capacity_at and timestamp(capacity_at) > timestamp(before["observed_at"]):
                same_window = False
            usage = [dict(row) for row in conn.execute(
                """SELECT actual_model, input_tokens, cached_input_tokens, output_tokens, context_info,
                          CASE WHEN json_valid(context_info)
                               THEN json_extract(context_info, '$.usage_seen') ELSE 0 END usage_seen
                   FROM routing_log WHERE selected_codex_account_key = ?
                   AND julianday(timestamp) > julianday(?) AND julianday(timestamp) <= julianday(?)""",
                (after["account_key"], before["observed_at"], after["observed_at"]),
            )]
            covered = first_routing_row is not None and timestamp(first_routing_row) <= timestamp(before["observed_at"])
            valid = same_window and covered and all(
                row["actual_model"] == model and row["usage_seen"] == 1
                and 0 <= row["cached_input_tokens"] <= row["input_tokens"]
                and row["output_tokens"] >= 0 for row in usage
            )
            # Unknown-account requests cannot safely be assigned to any slot.
            unknown = conn.execute(
                """SELECT 1 FROM routing_log WHERE actual_model LIKE 'codex/%'
                   AND coalesce(selected_codex_account_key, '') = ''
                   AND julianday(timestamp) > julianday(?) AND julianday(timestamp) <= julianday(?) LIMIT 1""",
                (before["observed_at"], after["observed_at"]),
            ).fetchone()
            start_seconds = timestamp(before["observed_at"]).timestamp()
            end_seconds = timestamp(after["observed_at"]).timestamp()
            contaminated = any(
                (window["account"] is None or window["account"] == after["account_key"])
                and window["start"] <= end_seconds and (window["end"] is None or window["end"] >= start_seconds)
                and not (window["accounted"] and wanted(window["experiment"])) for window in experiment_windows
            )
            if cohort is not None:
                valid = valid and all(wanted((json.loads(row["context_info"] or "{}").get("native_cache") or {}).get("experiment")) for row in usage)
            if contaminated:
                excluded_experiment_intervals += 1
            if not valid or unknown or contaminated:
                finish(active)
                active = None
                continue
            if active is None:
                active = dict(start=before["observed_at"], slot=after["slot_index"], account_key=after["account_key"],
                              multiplier=after["multiplier"], start_percent=before["used_percent"],
                              requests=0, input_tokens=0, cached_input_tokens=0, output_tokens=0)
            active.update(end=after["observed_at"], end_percent=after["used_percent"])
            for row in usage:
                active["requests"] += 1
                for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    active[field] += row[field]
        finish(active)
    totals = {field: sum(row[field] for row in samples) for field in
              ("requests", "input_tokens", "cached_input_tokens", "output_tokens", "units")}
    tokens = totals["input_tokens"] + totals["output_tokens"]
    for sample in samples:
        sample["units_per_million_tokens"] = sample["units"] * 1e6 / (sample["input_tokens"] + sample["output_tokens"])
    return {
        "model": model, "since": since, "latest_snapshot": max((row["observed_at"] for row in history), default=None),
        "method": "model-only contiguous spans; signed endpoint change, not positive-delta sums",
        "totals": totals, "total_tokens": tokens,
        "units_per_million_tokens": totals["units"] * 1e6 / tokens if tokens else None,
        "tokens_per_unit": tokens / totals["units"] if totals["units"] else None,
        "cache_percent": totals["cached_input_tokens"] * 100 / totals["input_tokens"] if totals["input_tokens"] else None,
        "samples": samples, "below_resolution_spans": unresolved,
        "category_rates": None,
        "cohort": list(cohort) if cohort else None,
        "excluded_experiment_intervals": excluded_experiment_intervals,
        "caveats": [
            "Observational: use outside Route is not measurable here.",
            "Current capacity overrides are assumed unchanged before the sampled period.",
            "A whole-span rate applies only to a similar cache/output mix; category prices are not established.",
            "Integer quota readings and delayed provider updates limit precision.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path.home() / ".custom_claw/spartan-route/data/spartan-route.db")
    parser.add_argument("--model", default="codex/gpt-6-astra")
    parser.add_argument("--since", required=True, help="ISO timestamp with timezone")
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        print(json.dumps(quota_report(conn, args.model, args.since), indent=2))


if __name__ == "__main__":
    main()
