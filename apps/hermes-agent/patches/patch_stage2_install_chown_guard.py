#!/usr/bin/env python3
"""Build-time patcher: remove install-tree chown and order config migration."""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/docker/stage2-hook.sh")
MARKER = "# Spartan Gate patch: immutable install tree"
MIGRATION_MARKER = "# Spartan Gate patch: migrate configs before profile reconciliation"
ROOT_FILE_MARKER = "# Spartan Gate patch: root managed data files"
GATEWAY_LOCK_MARKER = "# Spartan Gate patch: writable gateway lock state"
RUNTIME_IDENTITY_MARKER = "# Spartan Gate patch: runtime Hermes identity"
AS_HERMES_MARKER = "# Spartan Gate patch: run as Hermes runtime identity"
MIGRATION_ANCHOR = "# --- Sync bundled skills ---"


NEW_BLOCK = '''# Spartan Gate patch: immutable install tree
# Installed code under /opt/hermes is root-owned and patched at image build
# time only, so boot must not recursively chown the venv, gateway source,
# UI bundle, or Node dependencies to the workload user.
echo "[stage2] Spartan Gate keeps Hermes install trees immutable"

'''

MIGRATION_BLOCK = '''# Spartan Gate patch: migrate configs before profile reconciliation
# This stage2 hook is /etc/cont-init.d/01-hermes-setup. Running the migration
# here guarantees /etc/cont-init.d/02-reconcile-profiles sees current config.
if [ -f "$INSTALL_DIR/patches/patch_clawroute_named_provider.py" ]; then
    as_hermes "$INSTALL_DIR/.venv/bin/python" \\
        "$INSTALL_DIR/patches/patch_clawroute_named_provider.py"
fi

'''

RUNTIME_IDENTITY_BLOCK = '''# Spartan Gate patch: runtime Hermes identity
# Optional local deployments can run Hermes workloads as the host data owner
# without mutating /etc/passwd or /etc/group. The root filesystem is read-only;
# every workload drop uses these numeric IDs directly.
SPARTAN_HERMES_RUN_UID="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}"
SPARTAN_HERMES_RUN_GID="${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"
if ! validate_uid_gid "$SPARTAN_HERMES_RUN_UID"; then
    echo "[stage2] FATAL: invalid SPARTAN_HERMES_RUN_UID=$SPARTAN_HERMES_RUN_UID" >&2
    exit 1
fi
if ! validate_uid_gid "$SPARTAN_HERMES_RUN_GID"; then
    echo "[stage2] FATAL: invalid SPARTAN_HERMES_RUN_GID=$SPARTAN_HERMES_RUN_GID" >&2
    exit 1
fi
SPARTAN_HERMES_RUNTIME_OWNER="$SPARTAN_HERMES_RUN_UID:$SPARTAN_HERMES_RUN_GID"
if [ "$SPARTAN_HERMES_RUN_UID" != "$(id -u hermes)" ] || [ "$SPARTAN_HERMES_RUN_GID" != "$(id -g hermes)" ]; then
    echo "[stage2] Hermes workloads run as $SPARTAN_HERMES_RUNTIME_OWNER"
fi
HERMES_UID=""
HERMES_GID=""
PUID=""
PGID=""

'''


def patch_source(source: str) -> str:
    patched = source
    if MARKER not in patched:
        start = patched.find("# --- Fix ownership of build trees under $INSTALL_DIR ---")
        end_marker = "\n# Always reset ownership"
        end = patched.find(end_marker, start)
        if start == -1 or end == -1:
            start = patched.find("# --- Immutable install tree ---")
            end = patched.find(end_marker, start)
        if start == -1 or end == -1:
            raise ValueError("install-tree chown or immutable marker block not found")
        patched = patched[:start] + NEW_BLOCK + patched[end:]

    if MIGRATION_MARKER not in patched:
        if MIGRATION_ANCHOR not in patched:
            raise ValueError("pre-reconcile migration anchor not found")
        patched = patched.replace(MIGRATION_ANCHOR, MIGRATION_BLOCK + MIGRATION_ANCHOR, 1)

    if ROOT_FILE_MARKER not in patched:
        root_file_anchor = (
            "    active_profile; do\n"
            "    if [ -e \"$HERMES_HOME/$f\" ]; then\n"
        )
        root_file_replacement = (
            "    active_profile \\\n"
            "    .install_method; do\n"
            "    # Spartan Gate patch: root managed data files\n"
            "    if [ -e \"$HERMES_HOME/$f\" ]; then\n"
        )
        if root_file_anchor not in patched:
            raise ValueError("root managed data file allowlist anchor not found")
        patched = patched.replace(root_file_anchor, root_file_replacement, 1)

    as_hermes_anchor = 'as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }\n'
    as_hermes_replacement = f'''as_hermes() {{
    {AS_HERMES_MARKER}
    [ "$(id -u)" = 0 ] || {{ "$@"; return; }}
    /command/s6-applyuidgid -u "$SPARTAN_HERMES_RUN_UID" -g "$SPARTAN_HERMES_RUN_GID" "$@"
}}
'''
    if AS_HERMES_MARKER not in patched:
        if as_hermes_anchor not in patched:
            raise ValueError("as_hermes privilege-drop anchor not found")
        patched = patched.replace(as_hermes_anchor, as_hermes_replacement, 1)

    if RUNTIME_IDENTITY_MARKER not in patched:
        runtime_identity_anchor = '''validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

'''
        if runtime_identity_anchor not in patched:
            raise ValueError("runtime identity validation anchor not found")
        patched = patched.replace(runtime_identity_anchor, runtime_identity_anchor + RUNTIME_IDENTITY_BLOCK, 1)

    patched = patched.replace("actual_hermes_uid=$(id -u hermes)", 'actual_hermes_uid="$SPARTAN_HERMES_RUN_UID"')
    patched = patched.replace(
        'echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to hermes ($actual_hermes_uid)"',
        'echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to $SPARTAN_HERMES_RUNTIME_OWNER"',
    )
    patched = patched.replace(
        '    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py" \\\n',
        '    as_hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py" \\\n',
    )
    patched = patched.replace("chown -R hermes:hermes", 'chown -R "$SPARTAN_HERMES_RUNTIME_OWNER"')
    patched = patched.replace("chown hermes:hermes", 'chown "$SPARTAN_HERMES_RUNTIME_OWNER"')

    if GATEWAY_LOCK_MARKER not in patched:
        lock_state_anchor = 'mkdir -p "$HERMES_HOME"\n'
        lock_state_replacement = (
            'mkdir -p "$HERMES_HOME"\n'
            f"{GATEWAY_LOCK_MARKER}\n"
            'mkdir -p "$HERMES_HOME/.local/state/hermes/gateway-locks"\n'
        )
        if lock_state_anchor not in patched:
            raise ValueError("gateway lock state creation anchor not found")
        patched = patched.replace(lock_state_anchor, lock_state_replacement, 1)

    managed_dir_anchor = "for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing; do"
    managed_dir_replacement = (
        "for sub in cron sessions logs hooks memories skills skins plans workspace home profiles "
        "pairing platforms/pairing kanban .local/state/hermes; do"
    )
    if managed_dir_replacement not in patched:
        if managed_dir_anchor not in patched:
            raise ValueError("managed data directory allowlist anchor not found")
        patched = patched.replace(managed_dir_anchor, managed_dir_replacement, 1)

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
        print(f"SKIP: {TARGET} already has immutable install tree and config migration")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - install-tree chown removed and config migration ordered")


if __name__ == "__main__":
    main()
