"""Private, opt-in cache measurement helper. Default command is read-only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .cache_experiments import MODEL, PROBE_HEADER, RUN_ID, make_probe_manifest, probe_fixture, read_private_manifest
from .cache_report import report, probe_benefit
from .config import load_config
from .native_cache import cache_read_known, provider_cache_fields


class ProbeError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProbeError("Route redirect refused")


class LocalRoute:
    def __init__(self, port: int, token: str):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def open(self, path: str, body=None, probe=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if probe:
            headers[PROBE_HEADER] = probe
        return self.opener.open(urllib.request.Request(self.base + path, data=data, headers=headers), timeout=90)

    def get(self, path):
        with self.open(path) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ProbeError("Route metadata exceeded its size limit")
        return json.loads(raw)


def write_private(path: Path, value: dict, *, replace=False) -> None:
    """Publish only a complete 0600 file; exclusive by default, no symlinks."""
    data = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    if len(data) > 32768:
        raise ProbeError("Private report exceeded its size limit")
    if path.exists() or path.is_symlink():
        if not replace:
            raise ProbeError("A run file already exists; inspect it before starting another run")
        read_private_manifest(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".cache-experiment-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def consume_probe(response, *, clock=time.monotonic) -> tuple[dict, str | None]:
    deadline, total = clock() + 90, 0
    terminal, response_id = None, None
    while True:
        if clock() > deadline:
            raise ProbeError("Probe exceeded its deadline")
        line = response.readline(65537)
        if not line:
            break
        total += len(line)
        if len(line) > 65536 or total > 512 * 1024:
            raise ProbeError("Probe exceeded its response size limit")
        if not line.startswith(b"data:"):
            continue
        raw = line[5:].strip()
        if raw == b"[DONE]":
            break
        payload = json.loads(raw)
        event = payload.get("type")
        if event in {"error", "response.failed", "response.incomplete", "response.cancelled"}:
            raise ProbeError("Provider rejected or did not complete a probe; run stopped")
        if event == "response.completed":
            terminal = payload.get("response")
    if not isinstance(terminal, dict) or terminal.get("status") != "completed":
        raise ProbeError("Probe ended without a completed terminal event")
    output = terminal.get("output")
    if not isinstance(output, list) or any(not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"} for item in output):
        raise ProbeError("Unexpected probe output; no tools were executed")
    usage = terminal.get("usage") or {}
    known = cache_read_known(usage)
    if not known:
        raise ProbeError("Cache-read usage missing; run stopped")
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", usage.get("cached_tokens", 0))
    writes, diagnostics = provider_cache_fields(terminal)
    value = terminal.get("id")
    if isinstance(value, str) and len(value) <= 256:
        response_id = value
    return dict(completed=True, cache_read_known=True, input_tokens=usage["input_tokens"], cached_input_tokens=cached,
                output_tokens=usage.get("output_tokens"), cache_write_tokens=writes, provider=diagnostics), response_id


def select_accounts(baseline: dict, health: dict) -> list[str]:
    slots = health.get("slots") or (health.get("balancer") or {}).get("slots") or []
    eligible = {s.get("accountKey") for s in slots if s.get("routingEnabled") is True
                and s.get("authAvailable") is True and s.get("healthStatus") == "healthy"}
    ranked = sorted((a for a in baseline["accounts"] if a in eligible and baseline["accounts"][a]["patterns"] > 0),
                    key=lambda a: baseline["accounts"][a]["cached"] / baseline["accounts"][a]["input"])
    if len(ranked) < 2:
        raise ProbeError("Need two eligible accounts with observed baseline patterns")
    return [ranked[-1], ranked[0]]


def run_probes(route: LocalRoute, directory: Path, diagnostics: dict, baseline: dict) -> dict:
    if not baseline["applicable"]:
        raise ProbeError(baseline["reason"])
    accounts = select_accounts(baseline, route.get("/api/codex/usage"))
    run_id = secrets.token_hex(16)
    manifest = make_probe_manifest(diagnostics["epoch"], run_id, accounts)
    manifest_path, result_path = directory / "cache-canary.json", directory / "cache-probe-result.json"
    if any(p.exists() or p.is_symlink() for p in (manifest_path, result_path)):
        raise ProbeError("A previous probe run exists; inspect it before proceeding")
    result = dict(version=1, epoch=diagnostics["epoch"], run_id=run_id, accounts=accounts,
                  started_at=time.time(), status="running", cases=[])
    write_private(result_path, result)
    write_private(manifest_path, manifest)
    cold_id = None
    try:
        for case in range(1, 13):
            body = probe_fixture(run_id, case)
            if (case - 1) % 3 == 0:
                cold_id = None
            elif cold_id:
                body["prompt_cache_options"] = {"comparison_response_id": cold_id}
            # No retries: one HTTP generation request per physical case.
            with route.open("/v1/responses", body, f"{run_id}:{case}") as response:
                observation, response_id = consume_probe(response)
            if (case - 1) % 3 == 0:
                cold_id = response_id
            result["cases"].append({"case": case, **observation})
            write_private(result_path, result, replace=True)
        result["status"] = "completed"
        result["benefit"] = probe_benefit(result["cases"])
    except BaseException as exc:
        result["status"] = "stopped"
        # Do not persist exception text, upstream bodies, response IDs or tokens.
        result["error"] = type(exc).__name__
        # Revoke the file authority on client-side failure as well. A consumed
        # case is never retried, including timeouts with unknown upstream state.
        raise
    finally:
        # Revoke before the report write, which can fail independently.
        manifest_path.unlink(missing_ok=True)
        result["finished_at"] = time.time()
        write_private(result_path, result, replace=True)
    return result


def cohort_policy(diagnostics: dict, baseline: dict, probe: dict, *, now=None) -> dict:
    now = time.time() if now is None else now
    if (not baseline["applicable"] or probe.get("epoch") != diagnostics["epoch"] or probe.get("status") != "completed"
            or not probe_benefit(probe.get("cases", []))["verified"]
            or not RUN_ID.fullmatch(str(probe.get("run_id") or ""))):
        raise ProbeError("Current baseline and completed probe evidence do not permit a cohort")
    scopes = baseline["session_scopes"][:64]
    candidates = {s: sorted({candidate for account in probe["accounts"]
                            for candidate in baseline["candidate_accounts"].get(s, {}).get(account, [])})[:8] for s in scopes}
    scopes = [s for s in scopes if candidates[s]]
    counts = {name: 0 for name in ("control", "breakpoint")}
    for scope in scopes:
        arm = "breakpoint" if int(hashlib.sha256(f"{probe['run_id']}:{scope}".encode()).hexdigest(), 16) % 2 else "control"
        counts[arm] += 1
    if min(counts.values()) < 10:
        raise ProbeError("Need at least ten eligible sessions in each deterministic arm")
    return dict(version=1, epoch=diagnostics["epoch"], model=MODEL, run_id=probe["run_id"],
                issued_at=now, expires_at=now + 7 * 86400, accounts=probe["accounts"], session_scopes=scopes,
                candidates={s: candidates[s] for s in scopes}, baseline_applicable=True, probe_benefit_verified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("report", "probe", "cohort", "off"), default="report")
    parser.add_argument("--run-id", help="Include cohort evaluation for this run")
    args = parser.parse_args()
    config = load_config()
    directory = Path(config.db_path).resolve().parent
    try:
        if args.command == "off":
            paths = [directory / name for name in ("cache-canary.json", "cache-cohort.json")]
            existing = [path for path in paths if path.exists() or path.is_symlink()]
            for path in existing:
                read_private_manifest(path)
            for path in existing:
                path.unlink()
            print("Cache probes and cohort disabled; already-sent requests will finish normally.")
            return
        if not config.token:
            raise ProbeError("An authenticated local Route token is required")
        route = LocalRoute(config.port, config.token)
        diagnostics = route.get("/api/routing/live?limit=1&sessionLimit=1&turnLimit=1").get("cacheDiagnostics")
        if not diagnostics or diagnostics.get("version") != 2:
            raise ProbeError("Running Route has no v2 cache diagnostics; deploy before collecting a baseline")
        with sqlite3.connect(Path(config.db_path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            data = report(conn, epoch=diagnostics["epoch"], started=diagnostics["startedAt"], run_id=args.run_id)
        if args.command == "probe":
            data = run_probes(route, directory, diagnostics, data["baseline"])
        elif args.command == "cohort":
            probe = read_private_manifest(directory / "cache-probe-result.json")
            policy = cohort_policy(diagnostics, data["baseline"], probe)
            write_private(directory / "cache-cohort.json", policy)
            data = dict(status="enabled", run_id=policy["run_id"], expires_at=policy["expires_at"], sessions=len(policy["session_scopes"]))
        print(json.dumps(data, indent=2, allow_nan=False))
    except (Exception, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        print(f"Cache experiment stopped: {message}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
