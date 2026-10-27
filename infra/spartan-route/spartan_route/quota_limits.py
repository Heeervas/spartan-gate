"""Account quota policy, independent of presentation caches and manual disables.

Only confirmed provider observations can admit capped accounts. Reset expiry is
not evidence of replenishment. The existing usage controller owns refresh tasks.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from datetime import datetime
from typing import Any

from .slot_policy import SlotPolicyUnavailable

WINDOWS = ("weekly", "fiveHour")
FRESH_SECONDS = 60
RETRY_SECONDS = 5


def validate_limits(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict) or set(value) != set(WINDOWS):
        raise ValueError("weekly and fiveHour must be the only fields")
    for cap in value.values():
        if cap is not None and (type(cap) not in (int, float) or not 0 < cap <= 100 or not math.isfinite(cap)):
            raise ValueError("limits must be numbers greater than 0 and at most 100, or null")
    return dict(value)


class QuotaLimitStore:
    """Single writer; publish only committed snapshots, never telemetry fallbacks."""

    def __init__(self, connection: Any, lock: Any):
        self.connection, self.lock = connection, lock
        with lock, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS codex_account_quota_limits (
                account_key TEXT PRIMARY KEY, weekly REAL, five_hour REAL,
                CHECK (weekly IS NULL OR (weekly > 0 AND weekly <= 100)),
                CHECK (five_hour IS NULL OR (five_hour > 0 AND five_hour <= 100)))""")
        try:
            rows = connection.execute("SELECT * FROM codex_account_quota_limits").fetchall()
            self._limits = {row["account_key"]: validate_limits(
                {"weekly": row["weekly"], "fiveHour": row["five_hour"]}) for row in rows}
        except Exception as exc:
            raise SlotPolicyUnavailable("quota_policy_unavailable") from exc

    def limits(self, account: str | None) -> dict[str, float | None]:
        return dict(self._limits.get(account, dict.fromkeys(WINDOWS)))

    def set(self, account: str, value: Any) -> dict[str, float | None]:
        limits = validate_limits(value)
        with self.lock:
            candidate = {**self._limits, account: limits}
            with self.connection:
                self.connection.execute("""INSERT INTO codex_account_quota_limits VALUES (?, ?, ?)
                    ON CONFLICT(account_key) DO UPDATE SET weekly=excluded.weekly, five_hour=excluded.five_hour""",
                    (account, limits["weekly"], limits["fiveHour"]))
            self._limits = candidate
        return dict(limits)


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


def evaluate(limits: dict[str, Any], observation: Any, now: float) -> dict[str, Any]:
    reasons = []
    for name, cap in limits.items():
        if cap is not None:
            reason = window_reason(observation.get(name) if isinstance(observation, dict) else None, cap, now)
            if reason:
                reasons.append(f"{name}: {reason}")
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
        result = evaluate(self.store.limits(account), self.observations.get(account), time.time())
        if result["paused"]:
            for slot in self.router.slots:
                if slot.account_key() == account:
                    self.router._invalidate_slot_leases(slot)
        return result

    def observe(self, account: str, windows: dict[str, Any]) -> None:
        # Called with validated live windows, never sanitized/persisted dashboard rows.
        self.observations[account] = copy.deepcopy(windows)
        self.status(account)
        self.router._notify_availability_changed()

    def needs_refresh(self, account: str | None) -> bool:
        limits = self.store.limits(account)
        windows = self.observations.get(account, {})
        for name, cap in limits.items():
            if cap is not None and window_reason(windows.get(name), cap, time.time()) not in (None, "consumed limit reached"):
                return True
        return False

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


def quota_health(slot: Any) -> dict[str, Any] | None:
    if not bool(slot.get("routingEnabled", True)):
        return {"healthStatus": "disabled", "healthReasons": ["Routing disabled by operator"], "healthCached": False}
    policy = slot.get("quotaPolicy", {})
    if policy.get("paused"):
        return {"healthStatus": "paused", "healthReasons": policy["reasons"], "healthCached": False}
    return None


def observe_usage(router: Any, generation: int, account: str, item: dict[str, Any]) -> dict[str, Any]:
    if generation == int(getattr(router, "_usage_generation", 0)) and hasattr(router, "quota"):
        router.quota.observe(account, {name: item["row"][name] for name in WINDOWS})
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
    from .app import get_state, read_json_object, response_json_error

    state = get_state(request)
    router = state.codex
    if router is None:
        return response_json_error("Codex router is not ready", 503, "codex_unavailable")
    body = await read_json_object(request)
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
    return web.json_response({"accountKey": key, "quotaLimits": saved, "quotaPolicy": router.quota.status(key)})
