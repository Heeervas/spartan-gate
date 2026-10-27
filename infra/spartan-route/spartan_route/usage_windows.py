"""Provider window decoding shared by usage presentation and recovery."""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

FIVE_HOURS_SECONDS = 18_000
SEVEN_DAYS_SECONDS = 604_800


def usage_account_row(router: Any, account_key: str, slots: list, persisted_reset: Any) -> dict[str, Any]:
    paths = [path for path in (router.slot_path_label(slot) for slot in slots) if path]
    return {"accountKey": account_key, "slotIndexes": [slot.index for slot in slots],
            "slotPaths": list(dict.fromkeys(paths)), "source": "live", "stale": False,
            "fiveHour": None, "weekly": None, "resetCredits": persisted_reset}


def valid_live_usage_window(source: dict[str, Any]) -> tuple[float, str] | None:
    used = source.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    try:
        used = float(used)
    except OverflowError:
        return None
    if not math.isfinite(used) or not 0 <= used <= 100:
        return None
    reset_at = source.get("reset_at")
    if isinstance(reset_at, bool):
        return None
    if isinstance(reset_at, (int, float)):
        try:
            reset_at = float(reset_at)
        except OverflowError:
            return None
        if not math.isfinite(reset_at) or reset_at <= 0:
            return None
        try:
            reset_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(reset_at))
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(reset_at, str):
        return None
    try:
        parsed_reset = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_reset.tzinfo is None:
        return None
    return used, reset_at


def valid_recovery_duration(duration: Any) -> bool:
    return type(duration) is int and duration in (FIVE_HOURS_SECONDS, SEVEN_DAYS_SECONDS)


def live_usage_allows_recovery(payload: dict[str, Any]) -> bool:
    rate_limit = payload.get("rate_limit")
    if rate_limit is not None and not isinstance(rate_limit, dict):
        return False
    sources = (payload, rate_limit or {})
    saw_weekly = False
    for source in sources:
        for position in ("primary", "secondary"):
            window = source.get(f"{position}_window")
            if window is None:
                continue
            if not isinstance(window, dict):
                return False
            duration = window.get("limit_window_seconds")
            if duration is None:
                seconds = FIVE_HOURS_SECONDS if position == "primary" else SEVEN_DAYS_SECONDS
            else:
                if not valid_recovery_duration(duration):
                    return False
                seconds = duration
            validated = valid_live_usage_window(window)
            if validated is None or validated[0] >= 100:
                return False
            saw_weekly = saw_weekly or seconds == SEVEN_DAYS_SECONDS
    return saw_weekly


def normalize_usage_windows(payload: dict[str, Any]) -> dict[str, tuple[dict[str, Any], int]]:
    """Recognize exact integer durations; use position only when duration is absent."""
    rate_limit = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else {}
    candidates = (
        ("primary", payload.get("primary_window")),
        ("secondary", payload.get("secondary_window")),
        ("primary", rate_limit.get("primary_window")),
        ("secondary", rate_limit.get("secondary_window")),
    )
    windows: dict[str, tuple[dict[str, Any], int]] = {}
    for position, candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        duration = candidate.get("limit_window_seconds")
        if "limit_window_seconds" not in candidate:
            window_name = "fiveHour" if position == "primary" else "weekly"
            seconds = FIVE_HOURS_SECONDS if position == "primary" else SEVEN_DAYS_SECONDS
        elif valid_recovery_duration(duration):
            seconds = duration
            window_name = "fiveHour" if seconds == FIVE_HOURS_SECONDS else "weekly"
        else:
            continue
        windows.setdefault(window_name, (candidate, seconds))
    return windows
