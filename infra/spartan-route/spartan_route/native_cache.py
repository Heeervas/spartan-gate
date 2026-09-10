"""Bounded, metadata-only comparisons of final native Responses requests.

These are structured comparisons, not a tokenizer or proof of provider cache
residency. Nothing in this module changes an inference request.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

VERSION = 2
MAX_CONTEXT_BYTES = 8192
COMPARISONS = frozenset({"no_predecessor", "unattributed", "replay", "append", "changed", "unknown"})


class ObservationLimit(ValueError):
    pass


class Budget:
    def __init__(self, limit: int):
        self.remaining = limit
        self.nodes = 65536

    def bytes(self, value: bytes) -> bytes:
        self.remaining -= len(value)
        if self.remaining < 0:
            raise ObservationLimit()
        return value


def canonical_chunks(value: Any, budget: Budget, depth: int = 0):
    """Canonical object keys; exact scalars and ordered arrays. No large copy."""
    budget.nodes -= 1
    if depth > 64 or budget.nodes < 0:
        raise ObservationLimit()
    if isinstance(value, str):
        yield budget.bytes(b'"')
        for offset in range(0, len(value), 8192):
            escaped = json.dumps(value[offset:offset + 8192], ensure_ascii=True)[1:-1]
            yield budget.bytes(escaped.encode("ascii"))
        yield budget.bytes(b'"')
    elif isinstance(value, dict):
        if len(value) > budget.nodes or any(not isinstance(k, str) for k in value):
            raise ObservationLimit()
        yield budget.bytes(b"{")
        for index, key in enumerate(sorted(value)):
            if index:
                yield budget.bytes(b",")
            yield from canonical_chunks(key, budget, depth + 1)
            yield budget.bytes(b":")
            yield from canonical_chunks(value[key], budget, depth + 1)
        yield budget.bytes(b"}")
    elif isinstance(value, list):
        if len(value) > budget.nodes:
            raise ObservationLimit()
        yield budget.bytes(b"[")
        for index, item in enumerate(value):
            if index:
                yield budget.bytes(b",")
            yield from canonical_chunks(item, budget, depth + 1)
        yield budget.bytes(b"]")
    else:
        yield budget.bytes(json.dumps(value, allow_nan=False, separators=(",", ":")).encode("ascii"))


def developer_boundary(body: dict[str, Any]) -> int:
    """The initial developer group's final existing input_text block only."""
    items = body.get("input")
    if not isinstance(items, list):
        return 0
    count = 0
    for item in items:
        if not isinstance(item, dict) or item.get("role") != "developer":
            break
        count += 1
    if not count:
        return 0
    content = items[count - 1].get("content")
    if (not isinstance(content, list) or not content or not isinstance(content[-1], dict)
            or content[-1].get("type") != "input_text" or not isinstance(content[-1].get("text"), str)):
        return 0
    return count


@dataclass
class LedgerEntry:
    generation: int
    touched: float
    active: int = 0
    successful: dict[str, Any] | None = None
    completed: float | None = None
    first_sent: float = 0.0
    success_count: int = 0
    overlap_generation: int = 0


@dataclass
class Observation:
    metadata: dict[str, Any]
    sent: float
    scope: str | None = None
    entry: LedgerEntry | None = None
    generation: int = 0
    shape: dict[str, Any] | None = None
    finished: bool = False
    first_output: float | None = None
    overlap_generation: int = 0

    def output(self, payload: dict[str, Any], event: str, now: float) -> None:
        if self.first_output is None and event in {
            "response.output_text.delta", "response.function_call_arguments.delta", "response.custom_tool_call_input.delta",
        } and isinstance(payload.get("delta"), str) and payload["delta"]:
            self.first_output = now


class NativeCacheDiagnostics:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 max_payload_bytes: int = 8 * 1024 * 1024, max_scopes: int = 512,
                 max_state_bytes: int = 8 * 1024 * 1024, idle_seconds: float = 1800):
        self.clock = clock
        self.epoch = secrets.token_hex(8)
        self.started_at = time.time()
        self._secret = secrets.token_bytes(32)
        self.max_payload_bytes = max_payload_bytes
        # Each retained entry is constant-size and charged a conservative 16KiB.
        self.max_scopes = min(max_scopes, max_state_bytes // 16384)
        self.idle_seconds = idle_seconds
        self._entries: OrderedDict[str, LedgerEntry] = OrderedDict()
        self._generation = 0

    def digest(self, value: Any, budget: Budget | None = None) -> str:
        digest = hmac.new(self._secret, digestmod=hashlib.sha256)
        for chunk in canonical_chunks(value, budget or Budget(self.max_payload_bytes)):
            digest.update(chunk)
        return digest.hexdigest()

    def _shape(self, body: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        budget = Budget(self.max_payload_bytes)
        if len(body) > 65536 or (isinstance(body.get("input"), list) and len(body["input"]) > 65536):
            raise ObservationLimit()
        settings = {k: v for k, v in body.items() if k not in {"instructions", "tools", "input", "prompt_cache_key"}}
        options = settings.get("prompt_cache_options")
        if isinstance(options, dict):
            options = {k: v for k, v in options.items() if k != "comparison_response_id"}
            if options:
                settings["prompt_cache_options"] = options
            else:
                settings.pop("prompt_cache_options")
        shape = {key: self.digest({key: body[key]} if key in body else {}, budget) for key in ("instructions", "tools")}
        shape["settings"] = self.digest(settings, budget)
        items = body.get("input")
        if not isinstance(items, list):
            shape.update(input=self.digest({"input": items}, budget), items=None, prefix=None, boundary=None, boundary_items=0)
            return shape
        boundary = developer_boundary(body)
        prior_count = previous.get("items") if previous else None
        rolling = hmac.new(self._secret, b"native-input-items-v2", hashlib.sha256)
        prefix = rolling.hexdigest() if prior_count == 0 else None
        boundary_digest = None
        if len(items) > 65536:
            raise ObservationLimit()
        for index, item in enumerate(items, 1):
            rolling.update(b"\x00")
            for chunk in canonical_chunks(item, budget):
                rolling.update(chunk)
            if index == prior_count:
                prefix = rolling.hexdigest()
            if index == boundary:
                boundary_digest = rolling.hexdigest()
        shape.update(input=rolling.hexdigest(), items=len(items), prefix=prefix,
                     boundary=boundary_digest, boundary_items=boundary)
        return shape

    def candidate_signature(self, body: dict[str, Any], cache_key: str | None, shape: dict[str, Any] | None = None) -> str | None:
        try:
            shape = self._shape(body, None) if shape is None else shape
            if not shape.get("boundary") or not cache_key:
                return None
            return self.digest([shape["instructions"], shape["tools"], shape["settings"], shape["boundary"], cache_key], Budget(16384))
        except Exception:
            return None

    def session_scope(self, realm: str, chain: str | None) -> str | None:
        try:
            return self.digest([realm, chain], Budget(16384)) if chain else None
        except Exception:
            return None

    def begin(self, body: dict[str, Any], *, endpoint: str, realm: str, model: str,
              account: str | None, chain: str | None, cache_key: str | None,
              slot: int | None = None, credential_scope: str | None = None) -> Observation:
        now = self.clock()
        observation = Observation({"version": VERSION, "epoch": self.epoch, "comparison": "unknown",
                                   "sent_at": time.time(), "slot": slot, "account_known": bool(account), "account_key": account,
                                   "overlap": False, "cache_write_tokens": None, "ttft_ms": None}, now)
        try:
            for key, entry in list(self._entries.items()):
                if now - entry.touched >= self.idle_seconds:
                    self._entries.pop(key, None)
            if chain:
                observation.metadata["session_scope"] = self.session_scope(realm, chain)
            identity = account or credential_scope
            if not chain or not cache_key or not identity or self.max_scopes < 1:
                observation.metadata["comparison"] = "unattributed"
                return observation
            scope = self.digest([endpoint, realm, model, identity, chain, cache_key], Budget(16384))
            entry = self._entries.get(scope)
            previous = entry.successful if entry else None
            shape = self._shape(body, previous)
            if entry is None:
                while len(self._entries) >= self.max_scopes:
                    self._entries.popitem(last=False)
                entry = LedgerEntry(0, now, first_sent=now)
                self._entries[scope] = entry
            self._generation += 1
            observation.scope, observation.entry = scope, entry
            observation.generation, observation.shape = self._generation, shape
            entry.generation, entry.touched = self._generation, now
            self._entries.move_to_end(scope)
            observation.metadata.update(
                overlap=entry.active > 0, predecessor_gap_ms=(max(0, int((now - entry.completed) * 1000)) if entry.completed is not None else None),
                observed_chain_age_ms=max(0, int((now - entry.first_sent) * 1000)),
                completed_predecessors=entry.success_count,
                input_items=shape["items"],
                candidate_signature=self.candidate_signature(body, cache_key, shape),
                hashes={k: shape[k] for k in ("instructions", "tools", "settings", "input")},
                provider_comparison_requested=bool(isinstance(body.get("prompt_cache_options"), dict) and body["prompt_cache_options"].get("comparison_response_id")),
            )
            if entry.active:
                entry.overlap_generation += 1
            observation.overlap_generation = entry.overlap_generation
            entry.active += 1
            changed = [k for k in ("instructions", "tools", "settings", "input") if previous and shape[k] != previous[k]]
            comparison = "no_predecessor"
            if previous:
                if not changed:
                    comparison = "replay"
                elif changed == ["input"] and shape["prefix"] is not None and shape["prefix"] == previous["input"]:
                    comparison = "append"
                else:
                    comparison = "changed"
            boundary_reused = bool(previous and shape["boundary"] and shape["boundary"] == previous["boundary"]
                                   and all(shape[k] == previous[k] for k in ("instructions", "tools", "settings")))
            observation.metadata.update(comparison=comparison, changed_components=changed,
                                        developer_boundary={"items": shape["boundary_items"], "digest": shape["boundary"],
                                                            "reused": boundary_reused, "changed_suffix": boundary_reused and comparison == "changed"})
        except Exception:
            observation.metadata.update(comparison="unknown", reason="observation_limit")
            observation.shape = None
        return observation

    def finish(self, observation: Observation, *, success: bool,
               cache_write_tokens: int | None = None, provider_diagnostics: dict[str, Any] | None = None,
               cache_read_known: bool = False, status: int | None = None, error: str | None = None,
               first_output_at: float | None = None) -> dict[str, Any]:
        try:
            if not observation.finished:
                observation.finished = True
                now = self.clock()
                if first_output_at is not None:
                    observation.first_output = first_output_at
                observation.metadata.update(completed=bool(success), completed_at=time.time(),
                                            cache_write_tokens=cache_write_tokens, cache_read_known=cache_read_known,
                                            status=status, error_code=safe_error_code(error),
                                            ttft_ms=(max(0, int((observation.first_output - observation.sent) * 1000)) if observation.first_output is not None else None))
                if provider_diagnostics:
                    observation.metadata["provider"] = provider_diagnostics
                entry = observation.entry
                if entry is not None:
                    observation.metadata["overlap"] = bool(observation.metadata["overlap"] or entry.overlap_generation != observation.overlap_generation)
                    if self._entries.get(observation.scope) is not entry:
                        observation.metadata.update(comparison="unknown", reason="scope_evicted", overlap=None)
                    entry.active = max(0, entry.active - 1)
                    if (success and observation.shape is not None and entry.generation == observation.generation
                            and self._entries.get(observation.scope) is entry):
                        entry.successful = observation.shape
                        entry.completed, entry.touched = now, now
                        entry.success_count += 1
            metadata = copy.deepcopy(observation.metadata)
            if len(json.dumps(metadata)) > MAX_CONTEXT_BYTES:
                return {"version": VERSION, "epoch": self.epoch, "comparison": "unknown", "reason": "metadata_limit"}
            return metadata
        except Exception:
            return {"version": VERSION, "epoch": self.epoch, "comparison": "unknown", "reason": "diagnostic_failure"}


def request_diagnostics(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return independent, bounded snapshots, including attempts that failed."""
    result = copy.deepcopy(attempts[-1]) if attempts else {"version": VERSION, "comparison": "unknown"}
    result["attempts"] = [{k: v for k, v in attempt.items() if k not in {"hashes", "developer_boundary", "attempts"}}
                          for attempt in attempts[:3]]
    if len(json.dumps(result)) > MAX_CONTEXT_BYTES:
        return {"version": VERSION, "comparison": "unknown", "reason": "metadata_limit"}
    return result


def observe_output_chunk(observation: Observation, chunk: bytes, now: float) -> None:
    if observation.first_output is not None or len(chunk) > 1024 * 1024:
        return
    try:
        for line in chunk.splitlines():
            if line.startswith(b"data:"):
                payload = json.loads(line[5:])
                if isinstance(payload, dict):
                    observation.output(payload, str(payload.get("type") or ""), now)
    except Exception:
        pass


def safe_error_code(value: str | None) -> str | None:
    if not value:
        return None
    code = value.split(":", 1)[0] if isinstance(value, str) else "other"
    return code if code in {
        "codex_error", "server_error", "server_is_overloaded", "rate_limit_exceeded", "codex_stream_error",
        "response_failed", "response_cancelled", "response_incomplete", "codex_empty_response",
        "codex_stream_timeout", "invalid_request_error", "unauthorized", "provider_request_failed",
        "codex_unavailable", "codex_auth_unavailable", "codex_rate_limited", "request_aborted",
    } else "other"


def cache_read_known(usage: dict[str, Any]) -> bool:
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is None:
        cached = usage.get("cached_tokens")
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    return (isinstance(cached, int) and not isinstance(cached, bool)
            and isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
            and 0 <= cached <= input_tokens)


def provider_cache_fields(payload: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """Allowlisted provider metadata only; missing counters stay nullable."""
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return None, None
    usage = response.get("usage")
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
    writes = details.get("cache_write_tokens") if isinstance(details, dict) else None
    if not isinstance(writes, int) or isinstance(writes, bool) or writes < 0:
        writes = None
    raw = response.get("prompt_cache_diagnostics")
    diagnostics = None
    if isinstance(raw, dict) and raw.get("type") in {"cache_hit", "cache_miss", "comparison_response_not_found", "unavailable"}:
        diagnostics = {"type": raw["type"]}
        for key in ("comparison_reusable_tokens", "cache_missed_tokens"):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                diagnostics[key] = value
        reason = raw.get("reason")
        if isinstance(reason, str):
            diagnostics["reason"] = reason if reason in {"tools_changed", "model_changed", "prefix_mismatch", "cache_expired", "settings_changed", "prefix_changed", "cache_not_found", "input_changed", "prompt_cache_key_changed", "service_tier_changed", "text_format_changed", "reasoning_effort_changed", "verbosity_changed", "context_compacted"} else "other"
    return writes, diagnostics
