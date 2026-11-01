"""Pi inference correlation, passive quota views and shared refresh admission."""
from __future__ import annotations

import contextvars
import asyncio
import math
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from .pi_usage_store import COUNTERS, normalize_baseline, session_uuid

CURRENT_REQUEST: contextvars.ContextVar[Any] = contextvars.ContextVar("pi_usage_request", default=None)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def age(value: Any, now: float) -> float | None:
    try:
        return max(0.0, now - datetime.fromisoformat(str(value)).timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def future_reset(value: Any, now: float) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.tzinfo is not None and parsed.timestamp() > now
    except (ValueError, TypeError, OverflowError):
        return False


class CorrelationError(ValueError):
    pass


async def observe_chat(service: Any, headers: Any, provider: str, execute: Any) -> tuple[Any, Any]:
    """Keep correlation scoped to one inference request and stream finalization."""
    registered = await asyncio.to_thread(service.registration, headers)
    correlation = service.correlate(headers, registered)
    attempt = correlation.start(provider) if correlation and provider != "codex" else None
    token = CURRENT_REQUEST.set(correlation)
    try:
        result = await execute()
    except BaseException:
        if attempt:
            await attempt.finish(incomplete=True)
        raise
    finally:
        CURRENT_REQUEST.reset(token)

    async def finish() -> None:
        if attempt:
            incomplete = bool(result.error) or result.status >= 400 or (result.stream is not None and not result.stream_completed)
            await attempt.finish(result, incomplete=incomplete)

    return result, finish


class PiAttempt:
    def __init__(self, request: PiRequest, provider: str):
        self.request, self.provider = request, provider
        self.finished = False

    async def finish(self, result: Any = None, *, incomplete: bool = False) -> None:
        if self.finished:
            return
        self.finished = True
        seen = bool(result is not None and result.usage_seen)
        counters = dict.fromkeys(COUNTERS, 0)
        if seen:
            counters["inputTokens"] = max(0, int(result.input_tokens))
            counters["cachedInputTokens"] = min(counters["inputTokens"], max(0, int(result.cached_input_tokens)))
            counters["outputTokens"] = max(0, int(result.output_tokens))
            counters["totalTokens"] = counters["inputTokens"] + counters["outputTokens"]
            if self.provider == "codex":
                counters["codexTokens"] = counters["totalTokens"]
        try:
            await asyncio.to_thread(self.request.service.store.record, self.request.session_id, self.request.origin_id,
                                    counters, seen, incomplete or not seen)
        except Exception:  # noqa: BLE001 - telemetry is subordinate to inference
            # Telemetry never replaces an inference result or propagates private errors.
            self.request.service.write_failed = True


class PiRequest:
    def __init__(self, service: PiUsage, session_id: str, origin_id: str, sequence: int):
        self.service, self.session_id, self.origin_id, self.sequence = service, session_id, origin_id, sequence

    def start(self, provider: str) -> PiAttempt:
        return PiAttempt(self, provider)

    def affinity(self, key: str | None) -> None:
        previous = self.service.affinities.get(self.session_id)
        if previous is None or previous[0] <= self.sequence:
            self.service.affinities[self.session_id] = (self.sequence, key)
            self.service.affinities.move_to_end(self.session_id)
        while len(self.service.affinities) > 1024:
            self.service.affinities.popitem(last=False)


def public_window(row: dict[str, Any], now: float) -> dict[str, Any] | None:
    used = row.get("used_percent")
    if not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
        return None
    observed_age = age(row.get("updated_at"), now)
    if observed_age is None or observed_age > 3600 or not future_reset(row.get("reset_at"), now):
        return None
    return {"usedPercent": used, "remainingPercent": 100 - used,
            "resetAt": row.get("reset_at"), "updatedAt": row.get("updated_at"),
            "ageSeconds": observed_age, "stale": observed_age > 120}


def pool_window(accounts: list[dict[str, Any]], name: str, unknown_slots: int) -> dict[str, Any]:
    capacity = sum(100 * row["capacityMultiplier"] for row in accounts)
    known = [row for row in accounts if row[name] is not None]
    used = sum(row[name]["usedPercent"] * row["capacityMultiplier"] for row in known)
    remaining = sum(row[name]["remainingPercent"] * row["capacityMultiplier"] for row in known)
    unknown = len(accounts) - len(known) + unknown_slots
    return {"capacityUnits": capacity, "usedUnits": used, "remainingUnits": remaining,
            "usedPercent": used * 100 / capacity if capacity and not unknown else None,
            "remainingPercent": remaining * 100 / capacity if capacity and not unknown else None,
            "accounts": len(known), "unknownAccounts": unknown,
            "capacityIncomplete": bool(unknown_slots),
            "updatedAt": min((row[name]["updatedAt"] for row in known), default=None),
            "resetAt": min((row[name]["resetAt"] for row in known), default=None),
            "ageSeconds": max((row[name]["ageSeconds"] for row in known), default=None),
            "stale": any(row[name]["stale"] for row in known)}


def lease_view(router: Any, affinity: str | None, overrides: dict[int, Any], now: float) -> dict[str, Any]:
    mode, _duration, grace, _max_age, _limit = router._lease_settings()
    result = {"scope": "session" if mode == "keyed" else mode, "state": "absent",
              "slotIndex": None, "remainingSeconds": None, "expiresAt": None}
    if mode == "off" or affinity is None:
        return result
    lease = router.leases.get("__global__" if mode == "global" else affinity)
    if lease is None:
        return result
    expiry = min(lease.max_expires_at, max(lease.nominal_expires_at, lease.last_used_at + grace))
    slot = next((item for item in router.slots if item.index == lease.slot_index), None)
    valid = (slot is not None and slot.account_key() == lease.account_key
             and router.slot_routing_status(slot, overrides=overrides)["routingEnabled"]
             and slot.rate_limited_until <= now and slot.transient_until <= now)
    if now >= expiry or not valid:
        result["state"] = "expired"
        return result
    result.update(state="grace" if now >= lease.nominal_expires_at else "active",
                  slotIndex=lease.slot_index, remainingSeconds=max(0, expiry - now), expiresAt=iso(expiry))
    return result


def account_state(router: Any, indexes: list[int], now: float) -> str:
    slots = [slot for slot in router.slots if slot.index in indexes]
    quota = getattr(router, "quota", None)
    if slots and quota is not None and all(quota.status(slot.account_key())["paused"] for slot in slots):
        return "quota_paused"
    usable = [slot for slot in slots if not slot.auth_unavailable_reason]
    if any(slot.rate_limited_until <= now and slot.transient_until <= now for slot in usable):
        return "available"
    if not usable:
        return "auth_unavailable"
    return "rate_limited" if any(slot.rate_limited_until > now for slot in usable) else "transient"


def refresh_failed(task: Any) -> bool:
    """Classify this completed refresh cohort, never errors from an older cache."""
    if task.cancelled():
        return True
    try:
        payload = task.result()
    except Exception:  # noqa: BLE001 - controller errors are sanitized status only
        return True
    if not isinstance(payload, dict):
        return False
    status = payload.get("refreshProgress", {}).get("status")
    live_errors = status == "complete" and payload.get("source") in {"live", "mixed"} and bool(payload.get("slotErrors"))
    return status == "failed" or bool(payload.get("refreshError")) or live_errors


class PiUsage:
    def __init__(self, store: Any, telemetry: Any):
        self.store, self.telemetry = store, telemetry
        self.affinities: OrderedDict[str, tuple[int, str | None]] = OrderedDict()
        self.sequence = 0
        self.write_failed = False
        self.next_due = 0.0
        self.failure_count = 0
        self.last_refresh_task: Any = None

    def registration(self, headers: Any) -> Any:
        session = headers.get("X-Spartan-Pi-Session")
        if session is None:
            return None
        if self.telemetry.omit_optional():
            return False
        try:
            return self.store.get(session_uuid(session))
        except Exception:
            self.write_failed = True
            return False

    def correlate(self, headers: Any, registered: Any = ...) -> PiRequest | None:
        session, origin = headers.get("X-Spartan-Pi-Session"), headers.get("X-Spartan-Pi-Origin")
        if session is None and origin is None:
            return None
        if headers.get("X-Spartan-Pi-Client") != "1" or session is None or origin is None:
            raise CorrelationError("Pi correlation requires marker, session and origin")
        try:
            session, origin = session_uuid(session), session_uuid(origin)
        except ValueError as exc:
            raise CorrelationError(str(exc)) from None
        if registered is ...:
            registered = self.registration(headers)
        if registered is False:
            return None
        if registered is None or registered["originId"] != origin:
            raise CorrelationError("Pi session origin is not registered")
        self.sequence += 1
        correlation = PiRequest(self, session, origin, self.sequence)
        correlation.affinity(None)
        return correlation

    def accounts(self, router: Any, now: float) -> tuple[list[dict[str, Any]], set[str], dict[int, Any], list[dict[str, Any]]]:
        overrides = self.telemetry.slot_routing_overrides()
        grouped: dict[str, list[int]] = {}
        unresolved = []
        for slot in router.slots:
            key = slot.account_key()
            if not router.slot_routing_status(slot, overrides=overrides)["routingEnabled"]:
                continue
            if key:
                grouped.setdefault(key, []).append(slot.index)
            else:
                unresolved.append({"slotIndex": slot.index, "state": "identity_unknown"})
        multipliers = self.telemetry.codex_capacity_multipliers(set(grouped))
        rows = self.telemetry.usage_snapshots()
        accounts = []
        for key, slots in sorted(grouped.items(), key=lambda item: min(item[1])):
            account = {"id": f"account-{min(slots) + 1}", "slotIndexes": sorted(slots),
                       "capacityMultiplier": multipliers[key], "fiveHour": None, "weekly": None,
                       "state": account_state(router, slots, now)}
            for row in rows:
                if row["account_key"] == key and row["window"] in {"fiveHour", "weekly"}:
                    account[row["window"]] = public_window(row, now)
            accounts.append(account)
        calibrated_keys = {key for key in grouped if any(
            row["account_key"] == key and row["window"] == "weekly" and public_window(row, now) is not None
            for row in rows
        )}
        return accounts, calibrated_keys, overrides, unresolved

    def refresh_state(self, router: Any, now: float) -> dict[str, Any]:
        task = getattr(router, "_usage_refresh_task", None)
        refreshing = task is not None and not task.done()
        failed = task is not None and not refreshing and refresh_failed(task)
        if task is not None and task is not self.last_refresh_task and not refreshing:
            self.last_refresh_task = task
            self.failure_count = min(4, self.failure_count + 1) if failed else 0
            if failed:
                self.next_due = max(self.next_due, now + min(900, 60 * 2**self.failure_count))
        return {"state": "refreshing" if refreshing else "failed" if failed else "idle",
                "nextDueAt": iso(self.next_due) if self.next_due else None}

    def refresh(self, router: Any) -> None:
        self.telemetry.require_optional()
        now = time.time()
        state = self.refresh_state(router, now)
        cache = getattr(router, "_usage_cache", None) or {}
        self.next_due = max(self.next_due, cache.get("expiresAt", 0))
        if state["state"] == "refreshing" or now < self.next_due:
            return
        self.next_due = now + 60
        router.trigger_usage_refresh()

    def status(self, router: Any, session_id: str | None, refresh: dict[str, Any] | None = None) -> dict[str, Any]:
        self.telemetry.require_optional()
        now = time.time()
        accounts, keys, overrides, unresolved = self.accounts(router, now)
        estimate = self.telemetry.codex_usage_estimate(keys)
        calibration = {key: estimate.get(key) for key in (
            "available", "source", "quotaUnitsPerMillionTokens", "unavailableReason", "sampleTokens", "sampleAccounts", "observedAt")}
        session = self.store.get(session_id) if session_id else None
        rate = calibration["quotaUnitsPerMillionTokens"] if calibration["available"] else None
        if session is not None:
            session["coverage"]["trackingIncomplete"] |= self.write_failed
            session["estimatedQuotaUnits"] = session["totals"]["codexTokens"] * rate / 1e6 if rate is not None else None
        affinity = self.affinities.get(session_id, (0, None))[1]
        updated = [row[name]["updatedAt"] for row in accounts for name in ("fiveHour", "weekly") if row[name] and row[name]["updatedAt"]]
        observed = max(updated, default=None)
        return {"version": 1, "status": "not_registered" if session_id and session is None else "ok",
                "session": session, "accounts": accounts, "unresolvedSlots": unresolved,
                "pool": {name: pool_window(accounts, name, len(unresolved)) for name in ("fiveHour", "weekly")},
                "lease": lease_view(router, affinity, overrides, now), "calibration": calibration,
                "refresh": refresh if refresh is not None else self.refresh_state(router, now), "observedAt": observed, "ageSeconds": age(observed, now)}


def api_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"version": 1, "error": {"code": "pi_usage_invalid", "message": message}}, status=status)


def register_routes(app: web.Application, get_state: Any) -> None:
    async def register(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TypeError("body must be an object")
            session_id, baseline = session_uuid(body.get("sessionId")), normalize_baseline(body.get("baseline"))
            incomplete = body.get("trackingIncomplete", False)
            if type(incomplete) is not bool:
                raise TypeError("trackingIncomplete must be boolean")
            session, created = await asyncio.to_thread(get_state(request).pi_usage.store.register, session_id, baseline, incomplete)
            return web.json_response({"version": 1, "status": "registered", "session": session,
                                      "baselineConflict": session["baseline"] != baseline}, status=201 if created else 200)
        except (ValueError, TypeError):
            return api_error("Invalid Pi session registration")
        except Exception:  # noqa: BLE001 - sanitize operational failures at the API
            return api_error("Pi usage storage unavailable", 503)

    async def status(request: web.Request) -> web.Response:
        try:
            session = session_uuid(request.query["sessionId"]) if "sessionId" in request.query else None
            state = get_state(request)
            if state.codex is None:
                return api_error("Codex usage unavailable", 503)
            refresh = state.pi_usage.refresh_state(state.codex, time.time())
            payload = await asyncio.to_thread(state.pi_usage.status, state.codex, session, refresh)
            return web.json_response(payload)
        except ValueError:
            return api_error("Invalid sessionId")
        except Exception:  # noqa: BLE001 - sanitize operational failures at the API
            return api_error("Pi usage snapshot unavailable", 503)

    async def refresh(request: web.Request) -> web.Response:
        state = get_state(request)
        if state.codex is None:
            return api_error("Codex usage unavailable", 503)
        try:
            body = await request.json() if request.can_read_body else {}
            if not isinstance(body, dict):
                return api_error("body must be an object")
            session = session_uuid(body["sessionId"]) if "sessionId" in body else None
            state.pi_usage.refresh(state.codex)
            refresh_status = state.pi_usage.refresh_state(state.codex, time.time())
            payload = await asyncio.to_thread(state.pi_usage.status, state.codex, session, refresh_status)
            return web.json_response(payload, status=202 if payload["refresh"]["state"] == "refreshing" else 200)
        except (TypeError, ValueError):
            return api_error("Invalid Pi refresh request")
        except Exception:  # noqa: BLE001 - sanitize operational failures at the API
            return api_error("Pi usage refresh unavailable", 503)

    app.router.add_post("/api/pi/sessions", register)
    app.router.add_get("/api/pi/status", status)
    app.router.add_post("/api/pi/usage/refresh", refresh)
