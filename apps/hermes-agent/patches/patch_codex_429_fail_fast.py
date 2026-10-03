#!/usr/bin/env python3
"""Fail fast on Spartan Route's exact Codex capacity signal."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path("/opt/hermes")
CLASSIFIER = ROOT / "agent/error_classifier.py"
LOOP = ROOT / "agent/conversation_loop.py"
FINALIZER = ROOT / "agent/turn_finalizer.py"
MARKER = "Spartan Gate patch: exact Codex capacity signal"


def patch_classifier(source: str) -> str:
    if MARKER in source:
        return source
    anchor = "\n\ndef _extract_error_code(body: dict) -> str:\n"
    if source.count(anchor) != 1:
        raise ValueError("error body helper anchor changed")
    helper = f'''\n\ndef is_spartan_codex_capacity_error(error: Exception) -> bool:
    """Match only HTTP 429 with a top-level error.code of codex_429."""
    # {MARKER}
    if _extract_status_code(error) != 429:
        return False
    body = _extract_error_body(error)
    error_payload = body.get("error")
    return (
        isinstance(error_payload, dict)
        and error_payload.get("code") == "codex_429"
    )


def spartan_codex_capacity_summary(error: Exception, *, now: float | None = None) -> str | None:
    """Return a bounded terminal summary for the exact Spartan capacity signal."""
    if not is_spartan_codex_capacity_error(error):
        return None
    import math
    import time

    payload = _extract_error_body(error)["error"]
    seconds = payload.get("resets_in_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        reset_at = payload.get("resets_at")
        if isinstance(reset_at, bool) or not isinstance(reset_at, (int, float)):
            seconds = None
        else:
            reset_seconds = float(reset_at)
            if reset_seconds > 10_000_000_000:
                reset_seconds /= 1000
            seconds = reset_seconds - (time.time() if now is None else now)
    if isinstance(seconds, (int, float)) and math.isfinite(seconds):
        bounded_seconds = math.ceil(seconds)
        if 1 <= bounded_seconds <= 604_800:
            return f"Codex capacity is exhausted. Retry in about {{bounded_seconds}} seconds."
    return "Codex capacity is exhausted. Retry this request later."
'''
    return source.replace(anchor, helper + anchor, 1)


def patch_loop(source: str) -> str:
    if MARKER in source:
        return source

    import_anchor = (
        "from agent.error_classifier import FailoverReason, classify_api_error\n"
    )
    import_replacement = '''from agent.error_classifier import (
    FailoverReason,
    classify_api_error,
    spartan_codex_capacity_summary,
)
'''
    if source.count(import_anchor) != 1:
        raise ValueError("error classifier import anchor changed")
    patched = source.replace(import_anchor, import_replacement, 1)

    state_anchor = "    failed = False\n"
    if patched.count(state_anchor) != 1:
        raise ValueError("turn failure state anchor changed")
    patched = patched.replace(
        state_anchor,
        state_anchor + "    _turn_failure_reason = None\n",
        1,
    )

    recovery_anchor = '''
                if (
                    classified.reason == FailoverReason.billing
'''
    if patched.count(recovery_anchor) != 1:
        raise ValueError("pre-recovery anchor changed")
    terminal = f'''
                # {MARKER}. This must remain before every credential-pool,
                # retry-delay, and fallback branch: the router has already
                # exhausted the eligible Codex capacity for this request.
                _spartan_capacity_summary = spartan_codex_capacity_summary(api_error)
                if _spartan_capacity_summary is not None:
                    final_response = _spartan_capacity_summary
                    failed = True
                    _turn_failure_reason = FailoverReason.rate_limit.value
                    _turn_exit_reason = "spartan_codex_429"
                    messages.append({{"role": "assistant", "content": final_response}})
                    break
'''
    patched = patched.replace(recovery_anchor, terminal + recovery_anchor, 1)

    finalizer_anchor = "        _turn_exit_reason=_turn_exit_reason,\n"
    if patched.count(finalizer_anchor) != 1:
        raise ValueError("turn finalizer call anchor changed")
    return patched.replace(
        finalizer_anchor,
        finalizer_anchor + "        failure_reason=_turn_failure_reason,\n",
        1,
    )


def patch_finalizer(source: str) -> str:
    if MARKER in source:
        return source
    signature_anchor = "    _turn_exit_reason,\n    _pending_verification_response=None,\n"
    if source.count(signature_anchor) != 1:
        raise ValueError("turn finalizer signature anchor changed")
    patched = source.replace(
        signature_anchor,
        "    _turn_exit_reason,\n    failure_reason=None,\n"
        "    _pending_verification_response=None,\n",
        1,
    )
    result_anchor = '''
    if agent._tool_guardrail_halt_decision is not None:
'''
    if patched.count(result_anchor) != 1:
        raise ValueError("turn result anchor changed")
    addition = f'''
    # {MARKER}: expose a bounded machine-readable reason without serializing
    # the provider body or exception text into the caller-facing result.
    if failure_reason is not None:
        result["failure_reason"] = failure_reason
'''
    return patched.replace(result_anchor, addition + result_anchor, 1)


def patch_api_error(source: str) -> str:
    """Use upstream's terminal-result path, including durable failed-turn closure."""
    if MARKER in source:
        return source
    anchor = "    _recovered, active_system_prompt = recover_before_classification(\n"
    if source.count(anchor) != 1:
        raise ValueError("pre-recovery API error anchor changed")
    block = f'''    # {MARKER}: stop before recovery, rotation, fallback or backoff.
    from agent.error_classifier import spartan_codex_capacity_summary
    summary = spartan_codex_capacity_summary(api_error)
    if summary is not None:
        return _verdict("return", {{
            "final_response": summary, "messages": messages, "api_calls": api_call_count,
            "completed": False, "failed": True, "error": summary,
            "failure_reason": "rate_limit", "failure_retryable": False,
        }})

'''
    return source.replace(anchor, block + anchor, 1)


def patch_files(paths: dict[Path, str] | None = None) -> bool:
    targets = paths or {
        CLASSIFIER: "classifier",
        LOOP: "loop",
        FINALIZER: "finalizer",
    }
    modular = ROOT / "agent/turn_api_error.py"
    if paths is None and modular.is_file():
        targets = {CLASSIFIER: "classifier", modular: "api_error"}
    transforms = {
        "classifier": patch_classifier,
        "loop": patch_loop,
        "finalizer": patch_finalizer,
        "api_error": patch_api_error,
    }
    originals: dict[Path, str] = {}
    patched: dict[Path, str] = {}
    for path, kind in targets.items():
        if not path.is_file():
            raise ValueError(f"target not found: {path}")
        originals[path] = path.read_text(encoding="utf-8")
        patched[path] = transforms[kind](originals[path])

    changed = False
    for path in targets:
        if patched[path] != originals[path]:
            path.write_text(patched[path], encoding="utf-8")
            changed = True
    return changed


def main() -> None:
    try:
        changed = patch_files()
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("Patched exact Codex 429 handling" if changed else "SKIP: already patched")


if __name__ == "__main__":
    main()
