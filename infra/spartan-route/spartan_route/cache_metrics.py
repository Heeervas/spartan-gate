"""Versioned native-cache counters shared by incremental and detailed reads."""
from __future__ import annotations

import copy
from typing import Any

from .native_cache import COMPARISONS


def empty_metrics() -> dict[str, Any]:
    return {
        "version": 2, "requests": 0, "usageRequests": 0, "inputTokens": 0, "cachedInputTokens": 0,
        "cacheReadKnownRequests": 0, "cacheReadInputTokens": 0,
        "overlapRequests": 0, "completedRequests": 0, "cacheWriteKnownRequests": 0, "cacheWriteTokens": 0,
        "developerBoundaryRequests": 0, "developerBoundaryReusedRequests": 0, "changedSuffixRequests": 0,
        "ttftKnownRequests": 0, "ttftSumMs": 0, "experimentalRequests": 0,
        "comparisons": {name: 0 for name in sorted(COMPARISONS)},
        "changedComponents": {name: 0 for name in ("instructions", "tools", "settings", "input")},
        "providerStatuses": {name: 0 for name in ("cache_hit", "cache_miss", "comparison_response_not_found", "unavailable")},
    }


def count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def add_request(metrics: dict[str, Any], context: dict[str, Any], input_tokens: int, cached_tokens: int) -> bool:
    native = context.get("native_cache")
    if not isinstance(native, dict) or native.get("version") != 2:
        return False
    metrics["requests"] += 1
    if context.get("usage_seen") is True:
        metrics["usageRequests"] += 1
        metrics["inputTokens"] += count(input_tokens)
        if native.get("cache_read_known") is True:
            metrics["cachedInputTokens"] += count(cached_tokens)
            metrics["cacheReadInputTokens"] += count(input_tokens)
            metrics["cacheReadKnownRequests"] += 1
    comparison = native.get("comparison")
    metrics["comparisons"][comparison if comparison in COMPARISONS else "unknown"] += 1
    metrics["overlapRequests"] += int(native.get("overlap") is True)
    metrics["completedRequests"] += int(native.get("completed") is True)
    for component in set(native.get("changed_components") or []):
        if component in metrics["changedComponents"]:
            metrics["changedComponents"][component] += 1
    writes = native.get("cache_write_tokens")
    if isinstance(writes, int) and not isinstance(writes, bool) and writes >= 0:
        metrics["cacheWriteKnownRequests"] += 1
        metrics["cacheWriteTokens"] += writes
    boundary = native.get("developer_boundary")
    if isinstance(boundary, dict):
        metrics["developerBoundaryRequests"] += int(count(boundary.get("items")) > 0)
        reliable = native.get("completed") is True and native.get("overlap") is False and native.get("comparison") in {"replay", "append", "changed"}
        metrics["developerBoundaryReusedRequests"] += int(reliable and boundary.get("reused") is True)
        metrics["changedSuffixRequests"] += int(reliable and boundary.get("changed_suffix") is True)
    provider = native.get("provider")
    if isinstance(provider, dict) and provider.get("type") in metrics["providerStatuses"]:
        metrics["providerStatuses"][provider["type"]] += 1
    ttft = native.get("ttft_ms")
    if isinstance(ttft, int) and not isinstance(ttft, bool) and ttft >= 0:
        metrics["ttftKnownRequests"] += 1
        metrics["ttftSumMs"] += ttft
    metrics["experimentalRequests"] += int(bool(native.get("experiment")))
    return True


def merge_metrics(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in target.items():
        if key == "version":
            continue
        if isinstance(value, dict):
            other = source.get(key) or {}
            for name in value:
                value[name] += count(other.get(name))
        else:
            target[key] += count(source.get(key))


def public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(metrics)
    if not result["cacheWriteKnownRequests"]:
        result["cacheWriteTokens"] = None
    result["cacheHitPercent"] = (100 * result["cachedInputTokens"] / result["cacheReadInputTokens"] if result["cacheReadInputTokens"] else None)
    result["usageMissingRequests"] = result["requests"] - result["usageRequests"]
    result["cacheReadMissingRequests"] = result["usageRequests"] - result["cacheReadKnownRequests"]
    result["averageTtftMs"] = (result["ttftSumMs"] / result["ttftKnownRequests"] if result["ttftKnownRequests"] else None)
    return result
