"""Opt-in cache experiments. Files in the private data mount are the authority.

Ordinary requests cannot choose an account or create a probe run. A manifest is
bound to one Route process, twelve fixed synthetic cases, and a shared budget.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_cache import Budget, canonical_chunks, developer_boundary

MODEL = "codex/gpt-6-astra"
PROBE_HEADER = "X-Spartan-Cache-Probe"
MAX_ATTEMPTS = 12
MAX_INPUT_TOKENS = 100000
RUN_ID = re.compile(r"[a-f0-9]{32}\Z")


class ProbeRejected(ValueError):
    pass


def canonical_digest(value: Any) -> str:
    digest = hashlib.sha256()
    for chunk in canonical_chunks(value, Budget(32768)):
        digest.update(chunk)
    return digest.hexdigest()


def probe_fixture(run_id: str, case: int) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id) or type(case) is not int or not 1 <= case <= MAX_ATTEMPTS:
        raise ProbeRejected("invalid_case")
    account = (case - 1) // 6
    local = (case - 1) % 6
    arm = "control" if local < 3 else "breakpoint"
    group = f"{run_id}-{account}-{arm}"
    prefix = f"Synthetic cache fixture {group}.\n" + "This is fixed synthetic context for a cache measurement. " * 220
    content = {"type": "input_text", "text": prefix}
    if arm == "breakpoint":
        content["prompt_cache_breakpoint"] = {"mode": "explicit"}
    return {
        "model": MODEL, "stream": True, "reasoning": {"effort": "low"},
        "prompt_cache_key": f"spartan-cache-{group}", "user": f"spartan-cache-{group}",
        "input": [{"role": "developer", "content": [content]},
                  {"role": "user", "content": [{"type": "input_text", "text": "Reply with exactly OK. Suffix " + ("B" if local % 3 == 2 else "A")}]}],
    }


def estimate_input(body: dict[str, Any]) -> int:
    # Conservative character estimate for these fixed, plain-text fixtures.
    # This is explicitly an estimate, not a provider tokenizer or output cap.
    return math.ceil(len(json.dumps(body, ensure_ascii=True)) / 3)


def make_probe_manifest(epoch: str, run_id: str, accounts: list[str], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    return {
        "version": 1, "epoch": epoch, "run_id": run_id, "model": MODEL,
        "expires_at": now + 3600, "accounts": accounts,
        "max_attempts": MAX_ATTEMPTS, "max_estimated_input_tokens": MAX_INPUT_TOKENS,
        "cases": [{"case": case, "fingerprint": canonical_digest(probe_fixture(run_id, case)),
                   "estimated_input_tokens": estimate_input(probe_fixture(run_id, case))} for case in range(1, 13)],
    }


def read_private_manifest(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 32768:
            raise ProbeRejected("unsafe_manifest")
        data = os.read(descriptor, 32769)
        if len(data) > 32768:
            raise ProbeRejected("unsafe_manifest")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ProbeRejected("invalid_manifest")
        return value
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ProbeTicket:
    run_id: str
    case: int
    account: str
    estimated_input_tokens: int

    def metadata(self) -> dict[str, Any]:
        return {"kind": "probe", "run_id": self.run_id, "case": self.case,
                "arm": "control" if (self.case - 1) % 6 < 3 else "breakpoint"}


class CacheExperiments:
    def __init__(self, directory: Path, epoch: str, telemetry: Any, *, clock=time.time):
        self.directory, self.epoch, self.telemetry, self.clock = directory, epoch, telemetry, clock
        self._manifest_digest: str | None = None
        self._used: set[int] = set()
        self._tokens = 0
        self._pending: ProbeTicket | None = None
        self._response_ids: dict[int, str] = {}
        self._cohort_stopped = False
        self._stopped: str | None = None

    def stop(self, reason: str) -> None:
        self._stopped = reason

    def admit(self, header: str, body: dict[str, Any], actual_model: str) -> ProbeTicket:
        """Synchronous reservation: no interleaving between check and commit."""
        if self._stopped:
            raise ProbeRejected("run_stopped")
        try:
            run_id, raw_case = header.split(":")
            if not RUN_ID.fullmatch(run_id) or not raw_case.isdecimal() or len(raw_case) > 2:
                raise ProbeRejected("invalid_probe_header")
            case = int(raw_case)
            manifest = read_private_manifest(self.directory / "cache-canary.json")
            now = self.clock()
            expires = manifest.get("expires_at")
            accounts = manifest.get("accounts")
            if (manifest.get("version") != 1 or manifest.get("epoch") != self.epoch
                    or manifest.get("run_id") != run_id or manifest.get("model") != MODEL or actual_model != MODEL
                    or not isinstance(expires, (int, float)) or isinstance(expires, bool)
                    or not math.isfinite(expires) or not now < expires <= now + 3600
                    or not isinstance(accounts, list) or len(accounts) != 2 or accounts[0] == accounts[1]
                    or any(not isinstance(a, str) or not 1 <= len(a) <= 256 for a in accounts)):
                raise ProbeRejected("invalid_manifest")
            maximum, tokens = manifest.get("max_attempts"), manifest.get("max_estimated_input_tokens")
            if type(maximum) is not int or not 1 <= maximum <= 12 or type(tokens) is not int or not 1 <= tokens <= 100000:
                raise ProbeRejected("invalid_budget")
            expected_manifest = make_probe_manifest(self.epoch, run_id, accounts, now)
            if manifest.get("cases") != expected_manifest["cases"]:
                raise ProbeRejected("invalid_cases")
            digest = canonical_digest(manifest)
            if self._manifest_digest is not None and digest != self._manifest_digest:
                raise ProbeRejected("manifest_changed")
            # Only the comparison ID may vary from the fixed synthetic body.
            comparable = dict(body)
            options = comparable.pop("prompt_cache_options", None)
            if options is not None:
                if (not isinstance(options, dict) or set(options) != {"comparison_response_id"}
                        or not isinstance(options["comparison_response_id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", options["comparison_response_id"])
                        or (case - 1) % 3 == 0
                        or options["comparison_response_id"] != self._response_ids.get(1 + 3 * ((case - 1) // 3))):
                    raise ProbeRejected("invalid_comparison")
            fixture = probe_fixture(run_id, case)
            if canonical_digest(comparable) != canonical_digest(fixture):
                raise ProbeRejected("fixture_mismatch")
            estimated = estimate_input(body)
            if self._pending is not None or case != len(self._used) + 1 or len(self._used) >= maximum or self._tokens + estimated > tokens:
                raise ProbeRejected("probe_budget_exhausted")
            ticket = ProbeTicket(run_id, case, accounts[(case - 1) // 6], estimated)
            # Durable reservation also excludes ambiguous/aborted requests from
            # later quota calibration. Storage failure prevents probe egress.
            self.telemetry.reserve_cache_probe(run_id, case, ticket.account, now, estimated)
            self._manifest_digest = digest
            self._used.add(case)
            self._tokens += estimated
            self._pending = ticket
            return ticket
        except Exception as exc:
            self.stop("admission_failed")
            if isinstance(exc, ProbeRejected):
                raise
            raise ProbeRejected("probe_unavailable") from None

    async def choose(self, router: Any, ticket: ProbeTicket):
        excluded: set[int] = set()
        for slot in router.slots:
            auth = await router.load_auth(slot)
            if auth is None or auth.account_key != ticket.account:
                excluded.add(slot.index)
        chosen = await router.choose_slot(None, excluded, set())
        manifest = read_private_manifest(self.directory / "cache-canary.json")
        if (self._stopped or self._pending != ticket or canonical_digest(manifest) != self._manifest_digest
                or not self.clock() < manifest["expires_at"]):
            raise ProbeRejected("probe_authorization_expired")
        if chosen is None or chosen[1].account_key != ticket.account:
            self.stop("account_unavailable")
            raise ProbeRejected("probe_account_unavailable")
        return chosen

    def finish(self, ticket: ProbeTicket, *, success: bool, sent: bool = True, response_id: str | None = None) -> None:
        if self._pending != ticket:
            return
        self._pending = None
        if success and (ticket.case - 1) % 3 == 0 and isinstance(response_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", response_id):
            self._response_ids[ticket.case] = response_id
        if not success:
            self.stop("probe_failed")
        try:
            self.telemetry.finish_cache_probe(ticket.run_id, ticket.case, self.clock(), success, sent)
        except Exception:
            self.stop("probe_storage_unavailable")

    def reserve_cohort(self, metadata: dict[str, Any], account: str) -> str | None:
        if self._cohort_stopped:
            return None
        attempt_id = uuid.uuid4().hex
        try:
            self.telemetry.reserve_cache_experiment(attempt_id, metadata, account, self.clock())
            return attempt_id
        except Exception:
            self._cohort_stopped = True
            return None

    def finish_cohort(self, attempt_id: str, success: bool) -> None:
        if not success:
            self._cohort_stopped = True
        try:
            self.telemetry.finish_cache_experiment(attempt_id, self.clock(), success)
        except Exception:
            self._cohort_stopped = True

    def cohort_body(self, body: dict[str, Any], *, model: str, account: str | None,
                    session_scope: str | None, candidate_signature: Any = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """An expiring private allowlist; errors never affect ordinary inference."""
        if self._cohort_stopped:
            return body, None
        try:
            policy = read_private_manifest(self.directory / "cache-cohort.json")
            now = self.clock()
            issued, expires = policy.get("issued_at"), policy.get("expires_at")
            scopes, accounts = policy.get("session_scopes"), policy.get("accounts")
            if (policy.get("version") != 1 or policy.get("epoch") != self.epoch or policy.get("model") != MODEL
                    or model != MODEL or not RUN_ID.fullmatch(str(policy.get("run_id") or ""))
                    or type(issued) not in (int, float) or type(expires) not in (int, float)
                    or not math.isfinite(issued) or not math.isfinite(expires) or not issued <= now < expires <= issued + 7 * 86400
                    or not isinstance(scopes, list) or not 2 <= len(scopes) <= 64
                    or any(not isinstance(s, str) or not re.fullmatch(r"[a-f0-9]{64}", s) for s in scopes)
                    or not isinstance(accounts, list) or len(accounts) != 2 or accounts[0] == accounts[1]
                    or any(not isinstance(a, str) or not 1 <= len(a) <= 256 for a in accounts)
                    or session_scope not in scopes
                    or policy.get("baseline_applicable") is not True or policy.get("probe_benefit_verified") is not True):
                return body, None
            arm = "breakpoint" if int(hashlib.sha256(f"{policy['run_id']}:{session_scope}".encode()).hexdigest(), 16) % 2 else "control"
            metadata = {"kind": "cohort", "run_id": policy["run_id"], "arm": arm, "session_scope": session_scope}
            candidates = (policy.get("candidates") or {}).get(session_scope, [])
            candidate_signature = candidate_signature() if callable(candidate_signature) else candidate_signature
            if not candidate_signature or not isinstance(candidates, list) or len(candidates) > 8 or candidate_signature not in candidates:
                return body, {**metadata, "skipped": True}
            if not isinstance(body.get("input"), list) or len(body["input"]) > 65536:
                return body, {**metadata, "skipped": True}
            boundary = developer_boundary(body)
            options = body.get("prompt_cache_options") or {}
            if account not in accounts or not boundary or not isinstance(options, dict) or options.get("mode", "implicit") != "implicit":
                return body, {**metadata, "skipped": True}
            stack, nodes = [body], 0
            while stack:
                current = stack.pop()
                nodes += 1
                if nodes > 65536:
                    return body, {**metadata, "skipped": True}
                if isinstance(current, dict):
                    if "prompt_cache_breakpoint" in current:
                        return body, {**metadata, "skipped": True}
                    if len(current) + len(stack) > 65536:
                        return body, {**metadata, "skipped": True}
                    stack.extend(current.values())
                elif isinstance(current, list):
                    if len(current) + len(stack) > 65536:
                        return body, {**metadata, "skipped": True}
                    stack.extend(current)
            if arm == "control":
                return body, metadata
            updated = dict(body)
            updated["input"] = list(body["input"])
            item = dict(body["input"][boundary - 1])
            item["content"] = list(item["content"])
            item["content"][-1] = {**item["content"][-1], "prompt_cache_breakpoint": {"mode": "explicit"}}
            updated["input"][boundary - 1] = item
            return updated, metadata
        except Exception:
            return body, None
