"""Process-local, content-free telemetry. Never reads or writes storage."""
from __future__ import annotations

import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

WINDOW_SECONDS = 900
MAX_COUNTER = 2**53 - 1
FIELDS = ("requests", "errors", "usageKnownRequests", "inputTokens", "outputTokens",
          "cacheKnownRequests", "cacheEligibleInputTokens", "cachedInputTokens",
          "durationKnownRequests", "totalDurationMs")


def counter(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_COUNTER else None


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_pair(result: Any) -> tuple[int, int, int]:
    native = result.native_cache or {}
    input_tokens, cached = counter(result.input_tokens), counter(result.cached_input_tokens)
    known = (result.usage_seen is True and native.get("version") == 2
             and native.get("cache_read_known") is True)
    if not known or input_tokens is None or cached is None or cached > input_tokens:
        return 0, 0, 0
    return 1, input_tokens, cached


def measurements(result: Any, duration_ms: int | None) -> tuple[int, ...]:
    usage_known = result.usage_seen is True
    input_tokens, output_tokens = counter(result.input_tokens), counter(result.output_tokens)
    duration = counter(duration_ms)
    # Estimates and compatibility cache zeroes are not provider measurements.
    return (1, int(result.status >= 400 or bool(result.error)), int(usage_known),
            (input_tokens or 0) if usage_known else 0, (output_tokens or 0) if usage_known else 0,
            *cache_pair(result), int(duration is not None), duration or 0)


class LiveTelemetry:
    """One event-loop owner; at most 900 fixed-shape buckets, no request objects."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.started_at = timestamp()
        self.started = clock()
        self.last_observed: float | None = None
        self.last_observed_at: str | None = None
        self.observation_failures = 0
        self.buckets: deque = deque(maxlen=WINDOW_SECONDS)

    def observe(self, result: Any, duration_ms: int | None) -> None:
        # Optional diagnostics must never turn a successful response into failure.
        try:
            self._observe(result, duration_ms)
        except Exception:
            self.observation_failures = min(MAX_COUNTER, self.observation_failures + 1)

    def _observe(self, result: Any, duration_ms: int | None) -> None:
        now = self.clock()
        second = int(now)
        additions = measurements(result, duration_ms)
        if not self.buckets or self.buckets[-1][0] != second:
            self.buckets.append((second, dict.fromkeys(FIELDS, 0), False))
        _, values, saturated = self.buckets.pop()
        for key, value in zip(FIELDS, additions):
            total = values[key] + value
            saturated |= total > MAX_COUNTER
            values[key] = min(MAX_COUNTER, total)
        self.buckets.append((second, values, saturated))
        self.last_observed, self.last_observed_at = now, timestamp()

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        while self.buckets and int(now) - self.buckets[0][0] >= WINDOW_SECONDS:
            self.buckets.popleft()
        metrics = dict.fromkeys(FIELDS, 0)
        saturated = False
        for _, values, overflow in self.buckets:
            saturated |= overflow
            for key in FIELDS:
                total = metrics[key] + values[key]
                saturated |= total > MAX_COUNTER
                metrics[key] = min(MAX_COUNTER, total)
        eligible = metrics["cacheEligibleInputTokens"]
        metrics["cacheHitPercent"] = 100 * metrics["cachedInputTokens"] / eligible if eligible and not saturated else None
        durations = metrics["durationKnownRequests"]
        metrics["averageDurationMs"] = metrics["totalDurationMs"] / durations if durations and not saturated else None
        return {"version": 1, "source": "memory", "epoch": self.epoch, "startedAt": self.started_at,
                "windowSeconds": WINDOW_SECONDS, "windowResolutionSeconds": 1,
                "scope": "finalized_chat_responses", "saturated": saturated,
                "lastObservedAt": self.last_observed_at,
                "lastObservedAgeSeconds": max(0.0, now - self.last_observed) if self.last_observed is not None else None,
                "uptimeSeconds": max(0.0, now - self.started), "observationFailures": self.observation_failures,
                "metrics": metrics}


async def api_live_telemetry(request: web.Request) -> web.Response:
    from .app import get_state
    state = get_state(request)
    payload = state.live_telemetry.snapshot()
    payload["historicalTelemetryEnabled"] = state.telemetry.optional_telemetry_enabled
    return web.json_response(payload, headers={"Cache-Control": "no-store"})
