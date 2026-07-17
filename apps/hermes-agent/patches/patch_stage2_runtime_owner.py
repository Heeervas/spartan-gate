#!/usr/bin/env python3
"""Narrow stage2 patch for Spartan numeric identity and runtime-owned state."""

from __future__ import annotations

import re
import sys
from pathlib import Path


TARGET = Path("/opt/hermes/docker/stage2-hook.sh")
MIGRATION_MARKER = "# Spartan Gate patch: migrate route config before profile reconciliation"
RUNTIME_IDENTITY_MARKER = "# Spartan Gate patch: runtime Hermes identity"
AS_HERMES_MARKER = "# Spartan Gate patch: run as Hermes runtime identity"
RUNTIME_DIR_MARKER = "# Spartan Gate patch: runtime state directories"
MIGRATION_ANCHOR = "# --- Sync bundled skills ---"
MANAGED_DIR_CORE_ENTRIES = (
    "cron",
    "sessions",
    "logs",
    "profiles",
    "pairing",
    "platforms/pairing",
)
MANAGED_DIR_REQUIRED_ENTRIES = ("kanban", ".local/state/hermes")

MIGRATION_BLOCK = """# Spartan Gate patch: migrate route config before profile reconciliation
# Upstream docker_config_migrate.py has already run above. This local migrator
# then converts persisted Spartan Route provider aliases before
# /etc/cont-init.d/02-reconcile-profiles reads the profiles.
if [ -f "$INSTALL_DIR/patches/patch_clawroute_named_provider.py" ]; then
    as_hermes "$INSTALL_DIR/.venv/bin/python" \
        "$INSTALL_DIR/patches/patch_clawroute_named_provider.py"
fi

"""

RUNTIME_IDENTITY_BLOCK = """# Spartan Gate patch: runtime Hermes identity
# Keep /etc/passwd immutable and drop every workload to the configured numeric
# data owner instead of mutating the baked hermes account.
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
HERMES_UID=""
HERMES_GID=""
PUID=""
PGID=""

"""


def patch_managed_dir_allowlist(source: str) -> str:
    """Add only Spartan-owned runtime directories to the ownership allowlist."""

    found_managed_loop = False

    def replace_loop(match: re.Match[str]) -> str:
        nonlocal found_managed_loop
        original = match.group(0)
        if found_managed_loop:
            return original
        entries = match.group("entries").replace("\\", " ").split()
        if not all(entry in entries for entry in MANAGED_DIR_CORE_ENTRIES):
            return original
        found_managed_loop = True
        updated = list(entries)
        for entry in MANAGED_DIR_REQUIRED_ENTRIES:
            if entry not in updated:
                updated.append(entry)
        return original if updated == entries else f"for sub in {' '.join(updated)}; do"

    patched = re.sub(r"for sub in (?P<entries>.*?); do", replace_loop, source, flags=re.DOTALL)
    if not found_managed_loop:
        raise ValueError("managed data directory allowlist anchor not found")
    return patched


def patch_source(source: str) -> str:
    patched = source

    as_hermes_anchor = 'as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }\n'
    as_hermes_replacement = f"""as_hermes() {{
    {AS_HERMES_MARKER}
    [ "$(id -u)" = 0 ] || {{ "$@"; return; }}
    /command/s6-applyuidgid -u "$SPARTAN_HERMES_RUN_UID" -g "$SPARTAN_HERMES_RUN_GID" "$@"
}}
"""
    if AS_HERMES_MARKER not in patched:
        if as_hermes_anchor not in patched:
            raise ValueError("as_hermes privilege-drop anchor not found")
        patched = patched.replace(as_hermes_anchor, as_hermes_replacement, 1)

    if RUNTIME_IDENTITY_MARKER not in patched:
        identity_anchor = """validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

"""
        if identity_anchor not in patched:
            raise ValueError("runtime identity validation anchor not found")
        patched = patched.replace(identity_anchor, identity_anchor + RUNTIME_IDENTITY_BLOCK, 1)

    patched = patched.replace("actual_hermes_uid=$(id -u hermes)", 'actual_hermes_uid="$SPARTAN_HERMES_RUN_UID"')
    patched = patched.replace(
        'echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to hermes ($actual_hermes_uid)"',
        'echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to $SPARTAN_HERMES_RUNTIME_OWNER"',
    )
    patched = patched.replace("chown -R hermes:hermes", 'chown -R "$SPARTAN_HERMES_RUNTIME_OWNER"')
    patched = patched.replace("chown hermes:hermes", 'chown "$SPARTAN_HERMES_RUNTIME_OWNER"')
    patched = patched.replace(
        '    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python"',
        '    as_hermes "$INSTALL_DIR/.venv/bin/python"',
    )

    patched = patch_managed_dir_allowlist(patched)

    if RUNTIME_DIR_MARKER not in patched:
        seed_anchor = "as_hermes mkdir -p "
        if seed_anchor not in patched:
            raise ValueError("runtime directory seed anchor not found")
        patched = patched.replace(seed_anchor, RUNTIME_DIR_MARKER + "\n" + seed_anchor, 1)

        final_dir = '    "$HERMES_HOME/lazy-packages"\n'
        expanded_dirs = (
            '    "$HERMES_HOME/lazy-packages" \\\n'
            '    "$HERMES_HOME/kanban" \\\n'
            '    "$HERMES_HOME/.local/state/hermes/gateway-locks"\n'
        )
        if final_dir not in patched:
            raise ValueError("runtime directory list tail not found")
        patched = patched.replace(final_dir, expanded_dirs, 1)

    if MIGRATION_MARKER not in patched:
        if MIGRATION_ANCHOR not in patched:
            raise ValueError("pre-reconcile migration anchor not found")
        patched = patched.replace(MIGRATION_ANCHOR, MIGRATION_BLOCK + MIGRATION_ANCHOR, 1)

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
        print(f"SKIP: {TARGET} already has Spartan runtime ownership")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - numeric runtime ownership and route migration enabled")


if __name__ == "__main__":
    main()
