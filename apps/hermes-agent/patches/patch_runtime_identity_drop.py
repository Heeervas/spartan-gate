#!/usr/bin/env python3
"""Build-time patcher: drop Hermes workloads to configured numeric UID/GID."""

from __future__ import annotations

import sys
from pathlib import Path


MAIN_WRAPPER_TARGET = Path("/opt/hermes/docker/main-wrapper.sh")
DASHBOARD_TARGET = Path("/etc/s6-overlay/s6-rc.d/dashboard/run")
MAIN_MARKER = "# Spartan Gate patch: main runtime identity"
DASHBOARD_MARKER = "# Spartan Gate patch: dashboard runtime identity"

MAIN_DROP_OLD = 'drop() { [ "$(id -u)" = 0 ] && set -- s6-setuidgid hermes "$@"; exec "$@"; }\n'
MAIN_DROP_NEW = f'''drop() {{
    if [ "$(id -u)" = 0 ]; then
        if [ -n "${{SPARTAN_HERMES_RUN_UID:-}}${{SPARTAN_HERMES_RUN_GID:-}}" ]; then
            {MAIN_MARKER}
            _spartan_run_uid="${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}"
            _spartan_run_gid="${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}"
            set -- /command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" "$@"
        else
            set -- /command/s6-setuidgid hermes "$@"
        fi
    fi
    exec "$@"
}}
'''

DASHBOARD_DROP_OLD = '''exec s6-setuidgid hermes hermes dashboard \\
    --host "$dash_host" --port "$dash_port" --no-open $insecure
'''
DASHBOARD_DROP_NEW = f'''{DASHBOARD_MARKER}
if [ -n "${{SPARTAN_HERMES_RUN_UID:-}}${{SPARTAN_HERMES_RUN_GID:-}}" ]; then
    _spartan_run_uid="${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}"
    _spartan_run_gid="${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}"
    exec /command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" hermes dashboard \\
        --host "$dash_host" --port "$dash_port" --no-open $insecure
fi
exec /command/s6-setuidgid hermes hermes dashboard \\
    --host "$dash_host" --port "$dash_port" --no-open $insecure
'''


def patch_main_wrapper_source(source: str) -> str:
    if MAIN_MARKER in source:
        return source
    if MAIN_DROP_OLD not in source:
        raise ValueError("main-wrapper privilege-drop anchor not found")
    return source.replace(MAIN_DROP_OLD, MAIN_DROP_NEW, 1)


def patch_dashboard_source(source: str) -> str:
    if DASHBOARD_MARKER in source:
        return source
    if DASHBOARD_DROP_OLD not in source:
        raise ValueError("dashboard privilege-drop anchor not found")
    return source.replace(DASHBOARD_DROP_OLD, DASHBOARD_DROP_NEW, 1)


def patch_file(path: Path, patcher, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    source = path.read_text(encoding="utf-8")
    patched = patcher(source)
    if patched == source:
        return f"SKIP: {label} already patched"
    path.write_text(patched, encoding="utf-8")
    return f"Patched: {label}"


def main() -> None:
    try:
        print(patch_file(MAIN_WRAPPER_TARGET, patch_main_wrapper_source, str(MAIN_WRAPPER_TARGET)))
        print(patch_file(DASHBOARD_TARGET, patch_dashboard_source, str(DASHBOARD_TARGET)))
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
