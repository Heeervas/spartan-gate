"""Offline synthetic dashboard benchmark; never opens an operator database."""
from __future__ import annotations

import hashlib
import json
import statistics
import time

from spartan_route.telemetry import TelemetryStore


def main() -> None:
    store = TelemetryStore(":memory:")
    try:
        with store._lock, store._conn:
            store._conn.executemany(
                """INSERT INTO routing_log (
                    timestamp, original_model, routed_model, actual_model, tier,
                    session_id, turn_id, request_id, prompt_preview, context_info
                ) VALUES ('2026-10-20T12:00:00Z', 'auto', 'codex/test',
                          'codex/test', 'complex', ?, ?, ?, ?, '{"usage_seen":true}')""",
                ((f"session-{session}", f"turn-{turn}", f"request-{session}-{turn}-{request}",
                  "needle" if turn % 10 == 0 else "other")
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
    finally:
        store.close()


if __name__ == "__main__":
    main()
