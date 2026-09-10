"""Read-only gates for cache experiments; missing evidence never means success."""
from __future__ import annotations

import collections
import math
import random
import sqlite3
import time
import json
from datetime import datetime, timezone

from .cache_experiments import MODEL
from .quota_report import quota_report


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def native_rows(conn: sqlite3.Connection, epoch: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = []
    for raw in conn.execute("""SELECT timestamp, actual_model, input_tokens, cached_input_tokens, output_tokens,
                              error, actual_tool_call_count, context_info FROM routing_log
                              WHERE actual_model=? AND CASE WHEN json_valid(context_info)
                              THEN json_extract(context_info, '$.native_cache.epoch')=? ELSE 0 END
                              ORDER BY timestamp""", (MODEL, epoch)):
        row = dict(raw)
        row["native"] = json.loads(row.pop("context_info"))["native_cache"]
        rows.append(row)
    return rows


def usable(row: dict) -> bool:
    native = row["native"]
    return (native.get("version") == 2 and native.get("completed") is True and native.get("overlap") is False
            and native.get("cache_read_known") is True and not row.get("error")
            and row["input_tokens"] > 0 and 0 <= row["cached_input_tokens"] <= row["input_tokens"]
            and native.get("session_scope") is not None)


def baseline_report(rows: list[dict], *, started: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    ordinary = [r for r in rows if not r["native"].get("experiment")]
    valid = [r for r in ordinary if usable(r)]
    patterns = [r for r in valid if (r["native"].get("developer_boundary") or {}).get("changed_suffix") is True]
    accounts = collections.defaultdict(lambda: dict(input=0, cached=0, requests=0, patterns=0))
    for row in valid:
        account = row["native"].get("account_key")
        if not account:
            continue
        item = accounts[account]
        item["input"] += row["input_tokens"]
        item["cached"] += row["cached_input_tokens"]
        item["requests"] += 1
        item["patterns"] += bool((row["native"].get("developer_boundary") or {}).get("changed_suffix"))
    hours = max(0, now - started) / 3600
    recurring = len(patterns) >= 10 and len({r["native"]["session_scope"] for r in patterns}) >= 2
    ready = hours >= 24 and recurring and sum(a["patterns"] > 0 for a in accounts.values()) >= 2
    return dict(status="applicable" if ready else "inapplicable" if hours >= 48 and not recurring else "waiting",
                applicable=ready, hours=round(hours, 2), requests=len(ordinary), known_completed_requests=len(valid),
                changed_suffix_requests=len(patterns), accounts=dict(accounts),
                session_scopes=sorted({r["native"]["session_scope"] for r in patterns}),
                candidates={scope: sorted({r["native"]["candidate_signature"] for r in patterns if r["native"]["session_scope"] == scope and r["native"].get("candidate_signature")})[:8] for scope in {r["native"]["session_scope"] for r in patterns}},
                candidate_accounts={scope: {
                    account: sorted({r["native"]["candidate_signature"] for r in patterns if r["native"]["session_scope"] == scope
                                     and r["native"].get("account_key") == account and r["native"].get("candidate_signature")})[:8]
                    for account in accounts} for scope in {r["native"]["session_scope"] for r in patterns}},
                reason=None if ready else "Need 24 hours, recurring stable-developer/changed-suffix requests, and two observed accounts")


def probe_benefit(cases: list[dict]) -> dict:
    if len(cases) != 12 or [r.get("case") for r in cases] != list(range(1, 13)):
        return dict(verified=False, reason="Twelve successful ordered cases are required")
    gains = []
    for offset in (0, 6):
        group = cases[offset:offset + 6]
        if any(not r.get("completed") or r.get("cache_read_known") is not True or r.get("input_tokens", 0) <= 0 for r in group):
            return dict(verified=False, reason="Missing completion or cache-read evidence")
        control, treatment = group[2], group[5]
        # Replays establish that the transport can observe a warm cache. An
        # accepted marker alone does not establish subscription support.
        if group[1].get("cached_input_tokens", 0) <= 0 or group[4].get("cached_input_tokens", 0) <= 0:
            return dict(verified=False, reason="Warm replay was not observed on both arms")
        c = 1 - control["cached_input_tokens"] / control["input_tokens"]
        t = 1 - treatment["cached_input_tokens"] / treatment["input_tokens"]
        gains.append((c - t) / c if c > 0 else 0)
    return dict(verified=min(gains) >= .10, relative_uncached_reduction=gains,
                reason=None if min(gains) >= .10 else "Changed-suffix probes did not improve both accounts by 10%")


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def stratum(row: dict) -> tuple:
    native = row["native"]
    gap, age = native.get("predecessor_gap_ms"), native.get("observed_chain_age_ms")
    return (native.get("account_key"), int(math.log2(max(1, row["input_tokens"]))),
            None if gap is None else 0 if gap < 60000 else 1 if gap < 300000 else 2 if gap < 1800000 else 3,
            None if age is None else 0 if age < 300000 else 1 if age < 1800000 else 2)


def quota_evidence(quotas: dict) -> dict:
    grouped = {arm: collections.defaultdict(list) for arm in ("control", "breakpoint")}
    for arm in grouped:
        for sample in quotas[arm]["samples"]:
            grouped[arm][sample["account_key"]].append(sample)
    accounts = set(grouped["control"]) & set(grouped["breakpoint"])
    comparisons = []
    comparable = len(accounts) >= 2 and all(set(group) == accounts for group in grouped.values())
    for account in sorted(accounts):
        bounds, mixes, sizes = {}, {}, {}
        for arm in grouped:
            samples = grouped[arm][account]
            total = sum(s["input_tokens"] + s["output_tokens"] for s in samples)
            units = sum(s["units"] for s in samples)
            uncertainty = sum(2 * s["multiplier"] for s in samples)
            bounds[arm] = [(max(0, units - uncertainty) * 1e6 / total), (units + uncertainty) * 1e6 / total]
            mixes[arm] = sum(s["output_tokens"] for s in samples) / total
            sizes[arm] = sum(s["input_tokens"] for s in samples) / sum(s["requests"] for s in samples)
            comparable = comparable and len(samples) >= 2
        similar_mix = abs(mixes["control"] - mixes["breakpoint"]) <= .01 and .8 <= sizes["breakpoint"] / max(1, sizes["control"]) <= 1.2
        supports = similar_mix and bounds["breakpoint"][1] < bounds["control"][0]
        comparisons.append(dict(account=account, units_per_million_bounds=bounds, similar_mix=similar_mix, supports_savings=supports))
    return dict(supports_savings=bool(comparable and comparisons and all(c["supports_savings"] for c in comparisons)),
                method="same accounts; >=2 spans/account/arm; +/-2 percentage points per span; comparable output share and mean input size",
                accounts=comparisons, arms=quotas)


def cohort_report(rows: list[dict], run_id: str, *, quota: dict | None = None, reservation_issues: int = 0) -> dict:
    failed_treatment_attempts = 0
    for row in rows:
        for attempt in row["native"].get("attempts") or [row["native"]]:
            experiment = attempt.get("experiment") or {}
            if (experiment.get("run_id") == run_id and experiment.get("kind") == "cohort"
                    and experiment.get("arm") == "breakpoint" and not experiment.get("skipped")
                    and (attempt.get("completed") is not True or attempt.get("cache_read_known") is not True)):
                failed_treatment_attempts += 1
    selected = [r for r in rows if (r["native"].get("experiment") or {}).get("kind") == "cohort"
                and r["native"]["experiment"].get("run_id") == run_id]
    arms = {name: [r for r in selected if r["native"]["experiment"].get("arm") == name
                   and not r["native"]["experiment"].get("skipped")] for name in ("control", "breakpoint")}
    valid = {name: [r for r in group if usable(r)] for name, group in arms.items()}
    common = set.intersection(*(set(stratum(r) for r in group) for group in valid.values()))
    common = {s for s in common if s[0] and s[2] is not None and s[3] is not None}
    matched = {name: [r for r in group if stratum(r) in common] for name, group in valid.items()}
    counts = {name: dict(requests=len(group), sessions=len({r["native"]["session_scope"] for r in group}),
                        excluded_requests=len(arms[name]) - len(group)) for name, group in matched.items()}
    result = dict(status="inconclusive", expand=False, arms=counts, common_strata=len(common),
                  confidence_interval=None, quota=quota, failed_treatment_attempts=failed_treatment_attempts,
                  reservation_issues=reservation_issues, reasons=[])
    if failed_treatment_attempts or reservation_issues:
        result["reasons"].append("Failed, unresolved or unlogged experiment attempts require investigation")
    if any(c["requests"] < 100 or c["sessions"] < 10 for c in counts.values()):
        result["reasons"].append("Need 100 known-cache requests and 10 completed sessions per arm in shared strata")
        return result
    weights = collections.Counter(stratum(r) for group in matched.values() for r in group)

    def rate(group):
        totals = collections.defaultdict(lambda: [0, 0])
        for row in group:
            value = totals[stratum(row)]
            value[0] += row["input_tokens"] - row["cached_input_tokens"]
            value[1] += row["input_tokens"]
        if set(totals) != common:
            return None
        return sum(weights[s] * totals[s][0] / totals[s][1] for s in common) / sum(weights.values())

    rates = {name: rate(group) for name, group in matched.items()}
    if not rates["control"]:
        result["reasons"].append("Control has no measured uncached input to improve")
        return result
    clusters = {name: collections.defaultdict(list) for name in matched}
    for name, group in matched.items():
        for row in group:
            clusters[name][row["native"]["session_scope"]].append(row)
    randomizer, ratios = random.Random(20260910), []
    for _ in range(1000):
        sampled = {}
        for name in matched:
            sessions = list(clusters[name].values())
            sampled[name] = rate([r for session in randomizer.choices(sessions, k=len(sessions)) for r in session])
        if sampled["control"] and sampled["breakpoint"] is not None:
            ratios.append(sampled["breakpoint"] / sampled["control"])
    ratio = rates["breakpoint"] / rates["control"]
    interval = [percentile(ratios, .025), percentile(ratios, .975)] if len(ratios) >= 950 else None
    result.update(normalized_uncached_fraction=rates, relative_reduction=1 - ratio, confidence_interval=interval,
                  bootstrap_valid=len(ratios), confidence_method="1000 session-cluster resamples; fixed pooled request-stratum weights")
    if ratio > .90 or interval is None or interval[1] >= 1:
        result["reasons"].append("Require 10% uncached reduction with a 95% interval excluding no benefit")
    latency = {name: percentile([r["native"]["ttft_ms"] for r in group if isinstance(r["native"].get("ttft_ms"), (int, float))], .95)
               for name, group in matched.items()}
    result["p95_ttft_ms"] = latency
    if any(any(not isinstance(r["native"].get("ttft_ms"), (int, float)) for r in group) for group in matched.values()) or not latency["control"] or latency["breakpoint"] > 1.1 * latency["control"]:
        result["reasons"].append("Require complete provider TTFT coverage and at most 10% p95 regression")
    if any(not r["native"].get("completed") or r.get("error") or r["native"].get("cache_read_known") is not True for r in arms["breakpoint"]):
        result["reasons"].append("Treatment contains failures, incomplete streams, or missing cache readings")
    if not quota or not quota.get("supports_savings"):
        result["reasons"].append("Model-and-arm-isolated quota spans do not yet support savings")
    # Router telemetry cannot assess correctness of tool arguments or tasks.
    # A separate explicit review is mandatory even after numerical gates pass.
    result["numerical_gates_pass"] = not result["reasons"]
    result["reasons"].append("Task/tool-argument/replay quality review is required before expansion")
    result["status"] = "review_required" if result["numerical_gates_pass"] else "inconclusive"
    return result


def report(conn: sqlite3.Connection, *, epoch: str, started: float, now: float | None = None, run_id: str | None = None) -> dict:
    now = time.time() if now is None else now
    rows = native_rows(conn, epoch)
    since = iso(started)
    result = dict(version=1, epoch=epoch, generated_at=iso(now), baseline=baseline_report(rows, started=started, now=now))
    if run_id:
        quotas = {arm: quota_report(conn, MODEL, since, cohort=(run_id, arm)) for arm in ("control", "breakpoint")}
        evidence = quota_evidence(quotas)
        accounted = set()
        for row in rows:
            for attempt in row["native"].get("attempts") or [row["native"]]:
                experiment = attempt.get("experiment") or {}
                if (experiment.get("run_id") == run_id and attempt.get("completed") is True
                        and attempt.get("cache_read_known") is True and row["native"].get("completed") is True):
                    accounted.add(experiment.get("attempt_id"))
        reservations = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_experiment_reservations'").fetchone():
            reservations = list(conn.execute("SELECT * FROM cache_experiment_reservations WHERE run_id=?", (run_id,)))
        issues = sum(r["outcome"] != "completed" or r["attempt_id"] not in accounted for r in reservations)
        if not reservations:
            issues += 1
        result["cohort"] = cohort_report(rows, run_id, quota=evidence, reservation_issues=issues)
    return result
