"""Offline synthetic dashboard benchmark; never opens an operator database."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import random
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import json
import statistics
import time

from spartan_route.telemetry import RoutingLogEntry, TelemetryStore


def legacy_benchmark() -> None:
    store = TelemetryStore(":memory:")
    try:
        with store._lock, store._conn:
            store._conn.executemany(
                """INSERT INTO routing_log (
                    timestamp, original_model, routed_model, actual_model, tier,
                    session_id, turn_id, request_id, prompt_preview, response_time_ms, context_info
                ) VALUES ('2026-10-20T12:00:00Z', 'auto', 'codex/test',
                          'codex/test', 'complex', ?, ?, ?, ?, ?, '{"usage_seen":true}')""",
                ((f"session-{session}", f"turn-{turn}", f"request-{session}-{turn}-{request}",
                  "needle" if turn % 10 == 0 else "other", 10 + turn % 500)
                 for session in range(30) for turn in range(1000) for request in range(2)),
            )
        for label, filters in (("unfiltered", {}), ("filtered", {"prompt": "needle"})):
            times = []
            for _ in range(5):
                started = time.perf_counter()
                payload = store.live_sessions(session_limit=10, turn_limit=5, **filters)
                times.append((time.perf_counter() - started) * 1000)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            print(json.dumps({"case": label, "rows": 60000,
                "medianMs": round(statistics.median(times), 2),
                "returnedRequests": sum(s["metrics"]["requests"] for s in payload["sessions"]),
                "payloadSha256": digest}))
        for label in ("today-cold", "today-warm", "today-appends"):
            times = []
            for index in range(5):
                if label == "today-appends":
                    store.log_routing(RoutingLogEntry(
                        timestamp="2026-10-20T12:00:00Z", original_model="auto",
                        routed_model="codex/test", actual_model="codex/test", tier="complex",
                        context_info='{"usage_seen":true}',
                    ))
                started = time.perf_counter()
                payload = store.today_summary("2026-10-20")
                times.append((time.perf_counter() - started) * 1000)
                if label == "today-cold":
                    break
            print(json.dumps({"case": label, "medianMs": round(statistics.median(times), 2),
                              "loggedRequests": payload["loggedRequests"]}))
    finally:
        store.close()


def payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def representative_rows(rows: int, width: int, seed: int, day: str):
    """Skew sessions and whole-turn lengths; wide JSON stays outside metadata pages."""
    rng = random.Random(seed)
    emitted = turn = 0
    padding = "synthetic-context-" * (width // 18 + 1)
    while emitted < rows:
        session = 0 if rng.random() < .65 else rng.randrange(1, 40)
        count = min(rng.choice((1, 2, 3, 8, 32)), rows - emitted)
        needle = turn % 13 == 0
        context = json.dumps({"usage_seen": True, "synthetic": padding[:width], "turn": turn})
        for request in range(count):
            yield (f"{day}T12:00:00Z", f"session-{session}", f"turn-{turn}",
                   f"request-{emitted}", "needle" if needle else "other",
                   rng.randrange(10, 5000), context, 100 + request, request % 3, 10 + request)
            emitted += 1
        turn += 1


def populate(store: TelemetryStore, rows: int, width: int, seed: int, day: str) -> dict:
    digest = hashlib.sha256()
    turns, sessions = set(), set()
    def measured_rows():
        for row in representative_rows(rows, width, seed, day):
            digest.update(json.dumps(row, separators=(",", ":")).encode())
            turns.add(row[2])
            sessions.add(row[1])
            yield row
    with store._lock, store._conn:
        store._conn.executemany("""INSERT INTO routing_log (
            timestamp, session_id, turn_id, request_id, prompt_preview,
            response_time_ms, context_info, input_tokens, cached_input_tokens, output_tokens,
            original_model, routed_model, actual_model, tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto', 'codex/test', 'codex/test', 'complex')""", measured_rows())
    return {"rows": rows, "contextBytes": width, "seed": seed, "day": day,
            "turns": len(turns), "sessions": len(sessions), "fixtureSha256": digest.hexdigest()}


def measure_queries(store: TelemetryStore, day: str, rows: int, deadline: float, timings: dict, digests: dict) -> None:
    for temperature in ("reopened", "warm"):
        for case in ("unfiltered", "filtered", "summary"):
            if time.monotonic() >= deadline:
                raise TimeoutError("benchmark elapsed budget reached between queries; reduce dataset/repeats")
            started = time.perf_counter()
            if case == "summary":
                payload = store.today_summary(day)
                if payload["loggedRequests"] != rows:
                    raise AssertionError("summary lost fixture rows")
            else:
                filters = {"prompt": "needle"} if case == "filtered" else {}
                payload = store.live_sessions(session_limit=10, turn_limit=5, **filters)
            timings[f"{temperature}-{case}"].append((time.perf_counter() - started) * 1000)
            digest = payload_digest(payload)
            if case in digests and digests[case] != digest:
                raise AssertionError(f"{case} changed across reopened/warm repetitions")
            digests[case] = digest


def representative_benchmark(rows: int, width: int, seed: int, repeats: int, seconds: float) -> dict:
    if not (100 <= rows <= 50000 and 128 <= width <= 16384 and 3 <= repeats <= 30 and 1 <= seconds <= 300):
        raise ValueError("budget: rows 100..50000, width 128..16384, repeats 3..30, seconds 1..300")
    if rows * width > 128 * 1024 * 1024:
        raise ValueError("dataset context budget exceeds 128 MiB")
    deadline = time.monotonic() + seconds
    day = datetime.now(timezone.utc).date().isoformat()
    timings = {f"{temperature}-{case}": [] for temperature in ("reopened", "warm")
               for case in ("unfiltered", "filtered", "summary")}
    digests = {}
    with tempfile.TemporaryDirectory(prefix="route-benchmark-") as directory:
        path = str(Path(directory) / "synthetic.db")
        store = TelemetryStore(path)
        try:
            dataset = populate(store, rows, width, seed, day)
        finally:
            store.close()
        size = sum(p.stat().st_size for p in Path(directory).iterdir() if p.is_file())
        for _repeat in range(repeats):
            store = TelemetryStore(path)
            try:
                measure_queries(store, day, rows, deadline, timings, digests)
            finally:
                store.close()
        return {"mode": "representative-file", "environment": {
                    "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                    "platform": platform.platform(), "cpuCount": os.cpu_count()},
                "dataset": {**dataset, "databaseFamilyBytes": size,
                    "sessionSkew": "65% session-0, remainder 39 sessions", "requestsPerTurnChoices": [1, 2, 3, 8, 32]},
                "measurement": {"repeats": repeats, "elapsedSeconds": round(seconds - (deadline - time.monotonic()), 3),
                    "budgetSeconds": seconds, "coldDefinition": "reopened store and derived cache; OS cache uncontrolled",
                    "concurrency": "none; isolated characterization, not contention acceptance",
                    "deadline": "cooperative between bounded queries; not a hard query timeout"},
                "payloadSha256": digests,
                "timings": {key: {"samplesMs": values, "medianMs": statistics.median(values),
                    "minMs": min(values), "maxMs": max(values)} for key, values in timings.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--representative", action="store_true")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--context-bytes", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=7126)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.representative:
        print(json.dumps(representative_benchmark(args.rows, args.context_bytes, args.seed, args.repeats, args.seconds), sort_keys=True))
    else:
        legacy_benchmark()


if __name__ == "__main__":
    main()
