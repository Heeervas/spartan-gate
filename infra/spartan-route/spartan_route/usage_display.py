"""Display-only projection. Never feed retained windows back to quota admission."""
from __future__ import annotations

import copy
import time
from typing import Any

from .quota_limits import window_reason


def usage_active_slots(router: Any) -> dict[str, list]:
    active: dict[str, list] = {}
    for slot in router.slots:
        key = slot.account_key()
        if key:
            active.setdefault(key, []).append(slot)
    return active


def memory_usage_snapshot(router: Any) -> dict[str, Any]:
    payload = copy.deepcopy(getattr(router, "_usage_progress_snapshot", None) or {
        "accounts": [], "slotErrors": [], "resetCreditErrors": [], "observedAt": None,
        "source": "unknown", "refreshing": False,
    })
    active = usage_active_slots(router)
    payload["accounts"] = [row for row in payload["accounts"] if row.get("accountKey") in active]
    _age_display_windows(payload["accounts"])
    payload.update(optionalTelemetryEnabled=False, partial=True)
    return payload


def memory_usage_projection(router: Any, active_slots: dict[str, list] | None = None) -> dict[str, Any]:
    """Seed a NEW cohort with observations, never a previous cohort's status/errors."""
    previous = getattr(router, "_usage_progress_snapshot", None) or {}
    active = usage_active_slots(router) if active_slots is None else active_slots
    fields = ("accountKey", "slotIndexes", "slotPaths", "source", "stale", "weekly", "fiveHour", "resetCredits")
    accounts = [copy.deepcopy({name: row[name] for name in fields if name in row})
                for row in previous.get("accounts", []) if row.get("accountKey") in active]
    _age_display_windows(accounts)
    return {"accounts": accounts, "slotErrors": [], "resetCreditErrors": [],
            "observedAt": previous.get("observedAt"), "source": "memory" if accounts else "unknown",
            "refreshing": False, "partial": False, "optionalTelemetryEnabled": False}


def _age_display_windows(accounts: list[dict]) -> None:
    for account in accounts:
        for name in ("weekly", "fiveHour"):
            window = account.get(name)
            if isinstance(window, dict):
                window["stale"] = bool(window.get("stale") or window_reason(window, 101, time.time()))


def display_usage_row(router: Any, previous: dict, live: dict) -> dict:
    row = copy.deepcopy(live)
    if router.telemetry.optional_telemetry_enabled:
        return row
    # The raw row was already consumed by observe_usage before this merge.
    for name in ("weekly", "fiveHour"):
        window = row.get(name)
        if window is None and previous.get(name) is not None:
            window = row[name] = copy.deepcopy(previous[name])
            window["stale"] = True
        elif window is not None:
            window.update(source="live", stale=False)
    return row


def replace_display_account(router: Any, accounts: list[dict], live: dict) -> list[dict]:
    key = live["accountKey"]
    previous = next((row for row in accounts if row.get("accountKey") == key), {})
    remaining = [row for row in accounts if row.get("accountKey") != key]
    remaining.append(display_usage_row(router, previous, live))
    return sorted(remaining, key=lambda row: min(row.get("slotIndexes") or [9999]))


def attach_capacity(account: dict, multiplier: int | None) -> None:
    account["capacityMultiplier"] = multiplier
    for name in ("weekly", "fiveHour"):
        window = account.get(name)
        if not isinstance(window, dict):
            continue
        window["capacityMultiplier"] = multiplier
        if multiplier is None:
            window.update(capacityUnits=None, usedUnits=None, remainingUnits=None)
        else:
            used = window["usedPercent"]
            window.update(capacityUnits=100 * multiplier, usedUnits=used * multiplier,
                          remainingUnits=max(0.0, 100.0 - used) * multiplier)


def attach_optional_metadata(telemetry: Any, payload: dict) -> None:
    enabled = telemetry.optional_telemetry_enabled
    payload.update(optionalTelemetryEnabled=enabled)
    if not enabled:
        payload["cacheDaily"] = None
