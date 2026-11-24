"""Account quota policy, independent of presentation caches and manual disables.

Only confirmed provider observations can admit capped accounts. Reset expiry is
not evidence of replenishment. The existing usage controller owns refresh tasks.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from datetime import datetime, timezone
from typing import Any

from .slot_policy import SlotPolicyUnavailable

WINDOWS = ("weekly", "fiveHour")
LIMITS = (*WINDOWS, "daily")
FRESH_SECONDS = 60
RETRY_SECONDS = 5
RESET_JITTER_SECONDS = 900


def validate_limits(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict) or not set(WINDOWS) <= set(value) <= set(LIMITS):
        raise ValueError("weekly and fiveHour are required; daily is the only optional field")
    for cap in value.values():
        if cap is not None and (type(cap) not in (int, float) or not 0 < cap <= 100 or not math.isfinite(cap)):
            raise ValueError("limits must be numbers greater than 0 and at most 100, or null")
    return dict(value)


class QuotaLimitStore:
    """Single writer; publish only committed snapshots, never telemetry fallbacks."""

    def __init__(self, connection: Any, lock: Any):
        self.connection, self.lock = connection, lock
        self._daily_failed: set[str] = set()
        try:
            with lock, connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS codex_account_quota_limits (
                    account_key TEXT PRIMARY KEY, weekly REAL, five_hour REAL, daily REAL,
                    CHECK (weekly IS NULL OR (weekly > 0 AND weekly <= 100)),
                    CHECK (five_hour IS NULL OR (five_hour > 0 AND five_hour <= 100)),
                    CHECK (daily IS NULL OR (daily > 0 AND daily <= 100)))""")
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(codex_account_quota_limits)")}
                if "daily" not in columns:
                    connection.execute("""ALTER TABLE codex_account_quota_limits ADD COLUMN daily REAL
                        CHECK (daily IS NULL OR (daily > 0 AND daily <= 100))""")
                connection.execute("""CREATE TABLE IF NOT EXISTS codex_account_daily_budget (
                    account_key TEXT PRIMARY KEY, slice_start REAL NOT NULL, window_reset REAL NOT NULL,
                    baseline REAL NOT NULL, carried REAL NOT NULL, last_used REAL NOT NULL)""")
                rows = connection.execute("SELECT * FROM codex_account_quota_limits").fetchall()
                self._limits = {row["account_key"]: validate_limits(
                    {"weekly": row["weekly"], "fiveHour": row["five_hour"], "daily": row["daily"]}) for row in rows}
                self._daily = {row["account_key"]: validate_daily_row(dict(row)) for row in
                    connection.execute("SELECT * FROM codex_account_daily_budget")}
        except Exception as exc:
            raise SlotPolicyUnavailable("quota_policy_unavailable") from exc

    def limits(self, account: str | None) -> dict[str, float | None]:
        return dict(self._limits.get(account, dict.fromkeys(LIMITS)))

    def set(self, account: str, value: Any) -> dict[str, float | None]:
        patch = validate_limits(value)
        with self.lock:
            limits = {**self.limits(account), **patch}
            candidate = {**self._limits, account: limits}
            with self.connection:
                self.connection.execute("""INSERT INTO codex_account_quota_limits (account_key, weekly, five_hour, daily)
                    VALUES (?, ?, ?, ?) ON CONFLICT(account_key) DO UPDATE SET weekly=excluded.weekly,
                    five_hour=excluded.five_hour, daily=excluded.daily""",
                    (account, limits["weekly"], limits["fiveHour"], limits["daily"]))
            self._limits = candidate
        return dict(limits)

    def observe_daily(self, account: str, weekly: Any, now: float) -> None:
        if window_reason(weekly, math.inf, now) is not None:
            return
        with self.lock:
            previous = self._daily.get(account)
            reset = daily_window_reset(previous, weekly)
            try:
                start, _ = daily_slice(weekly, now, reset)
            except ValueError:
                return
            row = next_daily_row(previous, weekly, start, reset)
            try:
                if row != previous:
                    with self.connection:
                        self.connection.execute("""INSERT INTO codex_account_daily_budget
                            (account_key, slice_start, window_reset, baseline, carried, last_used)
                            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account_key) DO UPDATE SET
                            slice_start=excluded.slice_start, window_reset=excluded.window_reset,
                            baseline=excluded.baseline, carried=excluded.carried, last_used=excluded.last_used""",
                            (account, row["slice_start"], row["window_reset"], row["baseline"], row["carried"], row["last_used"]))
                    self._daily = {**self._daily, account: row}
                self._daily_failed.discard(account)
            except Exception as exc:
                self._daily_failed.add(account)
                raise SlotPolicyUnavailable("quota_policy_unavailable") from exc

    def daily_budget(self, account: str | None, weekly: Any, now: float) -> dict[str, Any]:
        limit = self.limits(account)["daily"]
        budget = {"limit": float(limit) if limit is not None else None, "consumed": None,
                  "sliceStart": None, "sliceEnd": None, "status": "off" if limit is None else "unknown"}
        if window_reason(weekly, math.inf, now) is not None:
            return budget
        row = self._daily.get(account)
        reset = daily_window_reset(row, weekly)
        try:
            start, end = daily_slice(weekly, now, reset)
        except ValueError:
            return budget
        budget.update(sliceStart=iso_timestamp(start), sliceEnd=iso_timestamp(end))
        if (account in self._daily_failed or row is None or row["slice_start"] != start
                or row["window_reset"] != reset):
            return budget
        consumed = row["carried"] + max(0.0, float(weekly["usedPercent"]) - row["baseline"])
        budget["consumed"] = consumed
        if limit is not None:
            budget["status"] = "reached" if consumed >= limit else "ok"
        return budget


def validate_daily_row(row: dict[str, Any]) -> dict[str, float]:
    result = {name: row[name] for name in ("slice_start", "window_reset", "baseline", "carried", "last_used")}
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in result.values()):
        raise ValueError("invalid daily budget state")
    if not (0 <= result["baseline"] <= 100 and 0 <= result["last_used"] <= 100
            and result["carried"] >= 0 and result["window_reset"] > result["slice_start"]):
        raise ValueError("invalid daily budget state")
    return result


def daily_window_reset(previous: Any, weekly: dict[str, Any]) -> float:
    reset = timestamp(weekly["resetAt"])
    if previous is not None and abs(reset - previous["window_reset"]) <= RESET_JITTER_SECONDS:
        # Keep the original anchor: provider jitter must not move the slice grid.
        return previous["window_reset"]
    return reset


def next_daily_row(previous: Any, weekly: dict[str, Any], start: float, reset: float) -> dict[str, float]:
    used = float(weekly["usedPercent"])
    if previous is None or previous["slice_start"] != start or previous["window_reset"] != reset:
        # A weekly reset starts a fresh slice, even when the slice start is unchanged.
        baseline, carried = used, 0.0
    else:
        baseline, carried = previous["baseline"], previous["carried"]
    return {"slice_start": start, "window_reset": reset, "baseline": baseline, "carried": carried, "last_used": used}


def iso_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def daily_slice(weekly: dict[str, Any], now: float, reset: float | None = None) -> tuple[float, float]:
    seconds = weekly.get("windowSeconds", 604800)
    try:
        valid_duration = type(seconds) in (int, float) and seconds > 0 and math.isfinite(seconds)
    except OverflowError:
        valid_duration = False
    if not valid_duration:
        raise ValueError("invalid weekly window duration")
    if reset is None:
        reset = timestamp(weekly.get("resetAt"))
    start = reset - seconds
    if not math.isfinite(reset) or not start <= now < reset:
        raise ValueError("invalid weekly window bounds")
    slice_start = start + math.floor((now - start) / 86400) * 86400
    slice_end = min(slice_start + 86400, reset)
    if not slice_start <= now < slice_end:
        raise ValueError("invalid daily slice bounds")
    return slice_start, slice_end


def timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else math.nan
    except (ValueError, TypeError, AttributeError, OverflowError):
        return math.nan


def window_reason(window: Any, cap: float, now: float) -> str | None:
    if not isinstance(window, dict):
        return "usage missing"
    used = window.get("usedPercent")
    if type(used) not in (int, float) or not 0 <= used <= 100 or not math.isfinite(used):
        return "usage invalid"
    observed, reset = timestamp(window.get("updatedAt")), timestamp(window.get("resetAt"))
    if not math.isfinite(observed) or not 0 <= now - observed < FRESH_SECONDS:
        return "usage stale"
    if not math.isfinite(reset) or reset <= now:
        return "reset requires confirmed provider reading"
    return "consumed limit reached" if used >= cap else None


def evaluate(limits: dict[str, Any], observation: Any, now: float, daily: Any = None) -> dict[str, Any]:
    reasons = []
    windows = observation if isinstance(observation, dict) else {}
    for name in WINDOWS:
        cap = limits.get(name)
        if cap is not None:
            reason = window_reason(windows.get(name), cap, now)
            if reason:
                reasons.append(f"{name}: {reason}")
    if limits.get("daily") is not None:
        reason = window_reason(windows.get("weekly"), math.inf, now)
        if reason is None:
            status = daily.get("status") if isinstance(daily, dict) else None
            reason = None if status == "ok" else "daily budget reached" if status == "reached" else "usage unknown"
        if reason:
            reasons.append(f"daily: {reason}")
    return {"paused": bool(reasons), "reasons": reasons}


class QuotaAdmission:
    def __init__(self, router: Any):
        self.router = router
        self.observations: dict[str, dict[str, Any]] = {}
        self.last_attempt = -math.inf

    @property
    def store(self) -> QuotaLimitStore:
        store = getattr(self.router.telemetry, "quota_limits", None)
        if store is None:
            raise SlotPolicyUnavailable("quota_policy_unavailable")
        return store

    def status(self, account: str | None) -> dict[str, Any]:
        now = time.time()
        result = evaluate(self.store.limits(account), self.observations.get(account), now, self.daily_budget(account, now))
        if result["paused"]:
            for slot in self.router.slots:
                if slot.account_key() == account:
                    self.router._invalidate_slot_leases(slot)
        return result

    def daily_budget(self, account: str | None, now: float | None = None) -> dict[str, Any]:
        weekly = self.observations.get(account, {}).get("weekly")
        return self.store.daily_budget(account, weekly, time.time() if now is None else now)

    def observe(self, account: str, windows: dict[str, Any], *, persisted: bool = False) -> None:
        # Called with validated live windows, never sanitized/persisted dashboard rows.
        if not persisted:
            self.store.observe_daily(account, windows.get("weekly"), time.time())
        self.observations[account] = copy.deepcopy(windows)
        self.status(account)
        self.router._notify_availability_changed()

    def needs_refresh(self, account: str | None) -> bool:
        limits = self.store.limits(account)
        windows = self.observations.get(account, {})
        for name, cap in limits.items():
            window = windows.get("weekly" if name == "daily" else name)
            if cap is not None and window_reason(window, cap, time.time()) not in (None, "consumed limit reached"):
                return True
        return limits["daily"] is not None and self.daily_budget(account)["status"] == "unknown"

    async def admit(self, slot: Any, auth: Any) -> bool:
        if self.needs_refresh(auth.account_key):
            active = getattr(self.router, "_usage_refresh_task", None)
            task = active if active is not None and not active.done() else None
            if task is None and time.monotonic() - self.last_attempt >= RETRY_SECONDS:
                self.last_attempt = time.monotonic()
                task = self.router._ensure_usage_refresh()
            if task is not None:
                try:
                    await asyncio.shield(task)
                finally:
                    self.last_attempt = time.monotonic()
        # Auth/manual/cooldown can change while awaiting shared refresh.
        return self.ready(slot, auth)

    def ready(self, slot: Any, auth: Any) -> bool:
        return (slot.auth is auth and self.router.slot_routing_status(slot)["routingEnabled"]
                and slot.rate_limited_until <= time.time() and slot.transient_until <= time.time()
                and not self.status(auth.account_key)["paused"])

    def attach(self, payload: dict[str, Any]) -> None:
        for account in payload.get("accounts", []):
            key = account.get("accountKey")
            account["quotaLimits"] = self.store.limits(key)
            account["quotaPolicy"] = self.status(key)
            account["dailyBudget"] = self.daily_budget(key)


def quota_health(slot: Any) -> dict[str, Any] | None:
    if not bool(slot.get("routingEnabled", True)):
        return {"healthStatus": "disabled", "healthReasons": ["Routing disabled by operator"], "healthCached": False}
    policy = slot.get("quotaPolicy", {})
    if policy.get("paused"):
        return {"healthStatus": "paused", "healthReasons": policy["reasons"], "healthCached": False}
    return None


async def observe_usage(router: Any, generation: int, account: str, item: dict[str, Any]) -> dict[str, Any]:
    if generation == int(getattr(router, "_usage_generation", 0)) and hasattr(router, "quota"):
        windows = {name: item["row"][name] for name in WINDOWS}
        await asyncio.to_thread(router.quota.store.observe_daily, account, windows["weekly"], time.time())
        if generation == int(getattr(router, "_usage_generation", 0)):
            router.quota.observe(account, windows, persisted=True)
    return item


async def operational_admission(router: Any, candidates: list, excluded: set, excluded_accounts: set) -> list:
    quota = getattr(router, "quota", None)
    admitted = []
    for slot, auth in candidates:
        if quota is None or await quota.admit(slot, auth):
            admitted.append((slot, auth))
    # Recheck all mutable gates after the last shared refresh/auth await.
    now = time.time()
    return [(slot, auth) for slot, auth in admitted
            if slot.index not in excluded and auth.account_key not in excluded_accounts
            and router.slot_routing_status(slot)["routingEnabled"]
            and slot.rate_limited_until <= now and slot.transient_until <= now
            and slot.auth is auth and (quota is None or quota.ready(slot, auth))]


async def dispatch_admission(router: Any, auth: Any, slot: Any = None) -> Any:
    quota = getattr(router, "quota", None)
    if quota is None:  # Legacy injected routers do not run the production constructor.
        return
    if slot is None:
        slot = next((slot for slot in router.slots if slot.auth is auth), None)
    else:
        from .attempt import outcome_slot
        slot = outcome_slot(slot)
    if slot is None or not await quota.admit(slot, auth):
        from .transport import FailureResponse, TransportError
        code = "codex_quota_paused" if quota.status(auth.account_key)["paused"] else "codex_unavailable"
        return FailureResponse(TransportError(code, status=503, error={"code": code,
            "message": "Account no longer eligible for dispatch", "type": "upstream_error"}), {"kind": "http"})
    return None


async def open_image_inference(router: Any, body: dict, headers: dict, auth: Any, slot: Any) -> Any:
    from aiohttp import ClientTimeout
    from .codex import CODEX_RESPONSES_URL, codex_request_timeout_ms
    blocked = await dispatch_admission(router, auth, slot)
    if blocked is not None:
        return blocked
    return await asyncio.wait_for(router.session.post(CODEX_RESPONSES_URL, json=body, headers=headers,
        timeout=ClientTimeout(total=None)), timeout=codex_request_timeout_ms() / 1000)


async def api_account_limits(request: Any) -> Any:
    from aiohttp import web
    from .app import get_state, read_bounded_json_object, response_json_error

    state = get_state(request)
    router = state.codex
    if router is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    body = await read_bounded_json_object(request, 2048)
    if isinstance(body, web.Response):
        return body
    try:
        limits = validate_limits(body)
    except ValueError as exc:
        return response_json_error(str(exc), 400, "invalid_quota_limits", "invalid_request_error")
    key = request.match_info["account_key"]
    if key not in {slot.account_key() for slot in router.slots}:
        return response_json_error("Codex account is not currently configured", 404, "account_not_found", "invalid_request_error")
    try:
        saved = await asyncio.to_thread(router.quota.store.set, key, limits)
    except Exception:
        return response_json_error("Quota limits could not be saved; previous policy retained", 503, "quota_policy_write_failed")
    for slot in router.slots:
        if slot.account_key() == key:
            router._invalidate_slot_leases(slot)
    router._notify_availability_changed()
    return web.json_response({"accountKey": key, "quotaLimits": saved, "quotaPolicy": router.quota.status(key),
                              "dailyBudget": router.quota.daily_budget(key)})
