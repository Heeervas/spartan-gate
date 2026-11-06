#!/usr/bin/env python3
"""Build-time patcher: drop Hermes workloads to configured numeric UID/GID."""

from __future__ import annotations

import sys
from pathlib import Path


MAIN_WRAPPER_TARGET = Path("/opt/hermes/docker/main-wrapper.sh")
DASHBOARD_TARGET = Path("/etc/s6-overlay/s6-rc.d/dashboard/run")
RECONCILE_TARGET = Path("/etc/cont-init.d/02-reconcile-profiles")
PROFILE_SCANNER_TARGET = Path("/etc/s6-overlay/s6-rc.d/profile-gateways/run")
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

DASHBOARD_DROP_OLD = '''# Skip the drop when already non-root.
[ "$(id -u)" = 0 ] || exec hermes dashboard --host "$dash_host" --port "$dash_port" --no-open
exec s6-setuidgid hermes hermes dashboard \\
    --host "$dash_host" --port "$dash_port" --no-open
'''
DASHBOARD_DROP_OLD_LEGACY = '''exec s6-setuidgid hermes hermes dashboard \\
    --host "$dash_host" --port "$dash_port" --no-open $insecure
'''
DASHBOARD_DROP_NEW = f'''{DASHBOARD_MARKER}
if [ "$(id -u)" != 0 ]; then
    exec hermes dashboard --host "$dash_host" --port "$dash_port" --no-open
fi
if [ -n "${{SPARTAN_HERMES_RUN_UID:-}}${{SPARTAN_HERMES_RUN_GID:-}}" ]; then
    _spartan_run_uid="${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}"
    _spartan_run_gid="${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}"
    exec /command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" hermes dashboard \\
        --host "$dash_host" --port "$dash_port" --no-open $insecure
fi
exec /command/s6-setuidgid hermes hermes dashboard \\
    --host "$dash_host" --port "$dash_port" --no-open
'''
DASHBOARD_DROP_NEW_LEGACY = f'''{DASHBOARD_MARKER}
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
    if DASHBOARD_DROP_OLD in source:
        return source.replace(DASHBOARD_DROP_OLD, DASHBOARD_DROP_NEW, 1)
    if DASHBOARD_DROP_OLD_LEGACY in source:
        return source.replace(DASHBOARD_DROP_OLD_LEGACY, DASHBOARD_DROP_NEW_LEGACY, 1)
    else:
        raise ValueError("dashboard privilege-drop anchor not found")


def _patch_profile_drop(source: str, command: str) -> str:
    """The new upstream scanner and reconciler must use the same numeric owner."""
    old = f'exec s6-setuidgid hermes {command}\n'
    new = f'''# Spartan Gate: unprivileged profile supervision uses the runtime data owner.
_spartan_run_uid="${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}"
_spartan_run_gid="${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}"
exec /command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" {command}
'''
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("profile supervision privilege-drop anchor changed")
    return source.replace(old, new, 1)


def patch_reconcile_source(source: str) -> str:
    old = 'chown hermes:hermes "$GATEWAY_SCANDIR"\n'
    new = ('# Each cont-init hook is a fresh process; stage2 shell locals are not inherited.\n'
           'SPARTAN_HERMES_RUNTIME_OWNER="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}:${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"\n'
           'chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$GATEWAY_SCANDIR"\n')
    if new not in source:
        if source.count(old) != 1:
            raise ValueError("unprivileged gateway scandir ownership anchor changed")
        source = source.replace(old, new, 1)
    return _patch_profile_drop(source, '/opt/hermes/.venv/bin/python -m hermes_cli.container_boot')


def patch_profile_scanner_source(source: str) -> str:
    return _patch_profile_drop(source, 's6-svscan "$scandir"')


def main() -> None:
    try:
        targets = (
            (MAIN_WRAPPER_TARGET, patch_main_wrapper_source),
            (DASHBOARD_TARGET, patch_dashboard_source),
            (RECONCILE_TARGET, patch_reconcile_source),
            (PROFILE_SCANNER_TARGET, patch_profile_scanner_source),
        )
        # No partial multi-file adaptation when upstream changes a later target.
        prepared = [(path, path.read_text(encoding="utf-8"), patcher) for path, patcher in targets]
        updates = [(path, original, patcher(original)) for path, original, patcher in prepared]
        for path, original, updated in updates:
            if updated != original:
                path.write_text(updated, encoding="utf-8")
            print(f"Verified runtime identity: {path}")
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
