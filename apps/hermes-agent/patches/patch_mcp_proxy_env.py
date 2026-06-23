#!/usr/bin/env python3
"""Build-time patcher: preserve proxy env vars for stdio MCP subprocesses.

Hermes filters stdio subprocess environments in ``tools/mcp_tool.py``.
Without the proxy variables used inside the isolated Docker network,
``npx ...@latest`` MCP servers can hang before JSON-RPC startup.

Designed to be idempotent and to fail loudly if the anchor changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path("/opt/hermes/tools/mcp_tool.py")

MARKER = "# Spartan Gate patch: preserve proxy env vars for stdio MCP subprocesses"
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


def main() -> None:
    if not TARGET.exists():
        print(f"FATAL: {TARGET} not found", file=sys.stderr)
        sys.exit(1)

    source = TARGET.read_text(encoding="utf-8")

    if MARKER in source or all(token in source for token in PROXY_TOKENS):
        print(f"Already patched: {TARGET}")
        return

    if OLD_BLOCK not in source:
        print(f"FATAL: anchor block not found in {TARGET}", file=sys.stderr)
        sys.exit(1)

    TARGET.write_text(source.replace(OLD_BLOCK, NEW_BLOCK, 1), encoding="utf-8")
    print(f"Patched: {TARGET} — proxy env vars preserved for stdio MCP subprocesses")


if __name__ == "__main__":
    main()