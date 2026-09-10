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


def quota_report(conn: sqlite3.Connection, model: str, since: str) -> dict:
    conn.row_factory = sqlite3.Row
    cutoff = timestamp(since)
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
                """SELECT actual_model, input_tokens, cached_input_tokens, output_tokens,
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
            if not valid or unknown:
                finish(active)
                active = None
                continue
            if active is None:
                active = dict(start=before["observed_at"], slot=after["slot_index"],
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
