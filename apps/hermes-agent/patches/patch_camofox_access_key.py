#!/usr/bin/env python3
"""Runtime patcher: send CAMOFOX_ACCESS_KEY from Hermes Camofox client."""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/tools/browser_camofox.py")
MARKER = "# Spartan Gate patch: CAMOFOX_ACCESS_KEY request headers"


def _replace_once(source: str, old: str, new: str) -> str:
    if old not in source:
        raise ValueError(f"expected Camofox client marker not found: {old[:80]!r}")
    return source.replace(old, new, 1)


def patch_source(source: str) -> str:
    if MARKER in source:
        return source

    source = _replace_once(
        source,
        '''def is_camofox_mode() -> bool:
    """True when Camofox backend is configured and no CDP override is active.
''',
        '''def _camofox_request_headers() -> Dict[str, str]:
    """Return bearer auth headers for protected Camofox routes."""
    access_key = os.getenv("CAMOFOX_ACCESS_KEY", "").strip()
    if not access_key:
        return {}
    return {"Authorization": f"Bearer {access_key}"}


def is_camofox_mode() -> bool:
    """True when Camofox backend is configured and no CDP override is active.
''',
    )

    replacements = [
        (
            'resp = requests.get(f"{url}/health", timeout=5)',
            'resp = requests.get(f"{url}/health", headers=_camofox_request_headers(), timeout=5)',
        ),
        (
            '''    resp = requests.post(
        f"{base}/tabs",
        json={
            "userId": session["user_id"],
            "sessionKey": session["session_key"],
            "url": url,
        },
        timeout=_DEFAULT_TIMEOUT,
    )''',
            '''    resp = requests.post(
        f"{base}/tabs",
        json={
            "userId": session["user_id"],
            "sessionKey": session["session_key"],
            "url": url,
        },
        headers=_camofox_request_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )''',
        ),
        (
            "resp = requests.post(url, json=body, timeout=timeout)",
            "resp = requests.post(url, json=body, headers=_camofox_request_headers(), timeout=timeout)",
        ),
        (
            "resp = requests.get(url, params=params, timeout=timeout)",
            "resp = requests.get(url, params=params, headers=_camofox_request_headers(), timeout=timeout)",
        ),
        (
            "resp = requests.get(url, params=params, timeout=timeout)",
            "resp = requests.get(url, params=params, headers=_camofox_request_headers(), timeout=timeout)",
        ),
        (
            "resp = requests.delete(url, json=body, timeout=timeout)",
            "resp = requests.delete(url, json=body, headers=_camofox_request_headers(), timeout=timeout)",
        ),
    ]

    for old, new in replacements:
        source = _replace_once(source, old, new)

    return f"{MARKER}\n{source}"


def main() -> None:
    if not TARGET.exists():
        print(f"FATAL: {TARGET} not found", file=sys.stderr)
        sys.exit(1)

    source = TARGET.read_text(encoding="utf-8")
    try:
        patched = patch_source(source)
    except ValueError as exc:
        print(f"FATAL: {exc} in {TARGET}", file=sys.stderr)
        sys.exit(1)

    if patched == source:
        print(f"SKIP: {TARGET} already sends CAMOFOX_ACCESS_KEY")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - CAMOFOX_ACCESS_KEY headers enabled")


if __name__ == "__main__":
    main()
