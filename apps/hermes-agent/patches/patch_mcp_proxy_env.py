#!/usr/bin/env python3
"""Build-time patcher: harden stdio MCP subprocess environments.

Hermes filters stdio subprocess environments in ``tools/mcp_tool.py``.
Without the proxy variables used inside the isolated Docker network,
``npx ...@latest`` MCP servers can hang before JSON-RPC startup.
Recent MCP SDK releases also require every environment value to be a string,
while existing Hermes YAML may contain boolean or numeric scalars.

Designed to be idempotent and to fail loudly if the anchor changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path("/opt/hermes/tools/mcp_tool.py")

MARKER = "# Spartan Gate patch: preserve proxy env vars for stdio MCP subprocesses"
ENV_VALUE_MARKER = "# Spartan Gate patch: normalize explicit MCP env scalars to strings"
PROXY_TOKENS = (
    '"http_proxy"',
    '"https_proxy"',
    '"HTTP_PROXY"',
    '"HTTPS_PROXY"',
    '"no_proxy"',
    '"NO_PROXY"',
    '"all_proxy"',
    '"ALL_PROXY"',
)

OLD_BLOCK = '''# Environment variables that are safe to pass to stdio subprocesses
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
})'''

NEW_BLOCK = '''# Environment variables that are safe to pass to stdio subprocesses
# Spartan Gate patch: preserve proxy env vars for stdio MCP subprocesses
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY",
})'''

OLD_ENV_UPDATE = '''    if user_env:
        env.update(user_env)
    return env'''

NEW_ENV_UPDATE = '''    if user_env:
        # Spartan Gate patch: normalize explicit MCP env scalars to strings
        for key, value in user_env.items():
            if not isinstance(key, str):
                raise ValueError("MCP environment variable names must be strings")
            if isinstance(value, bool):
                normalized = "true" if value else "false"
            elif isinstance(value, (str, int, float)):
                normalized = str(value)
            else:
                raise ValueError(
                    f"MCP environment variable {key!r} must be a string, boolean, or number"
                )
            env[key] = normalized
    return env'''


def patch_source(source: str) -> str:
    patched = source
    if MARKER not in patched and not all(token in patched for token in PROXY_TOKENS):
        if OLD_BLOCK not in patched:
            raise ValueError("safe stdio environment anchor not found")
        patched = patched.replace(OLD_BLOCK, NEW_BLOCK, 1)
    if ENV_VALUE_MARKER not in patched:
        if OLD_ENV_UPDATE not in patched:
            raise ValueError("explicit stdio environment anchor not found")
        patched = patched.replace(OLD_ENV_UPDATE, NEW_ENV_UPDATE, 1)
    return patched


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
        print(f"Already patched: {TARGET}")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(
        f"Patched: {TARGET} — proxy env preserved and explicit scalar values normalized"
    )


if __name__ == "__main__":
    main()