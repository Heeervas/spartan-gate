"""Offline synthetic dashboard benchmark; never opens an operator database."""
from __future__ import annotations

import hashlib
import json
import statistics
import time

from spartan_route.telemetry import RoutingLogEntry, TelemetryStore


def main() -> None:
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


if __name__ == "__main__":
    main()
