#!/usr/bin/env python3
"""Narrow stage2 patch for Spartan numeric identity and runtime-owned state."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


TARGET = Path("/opt/hermes/docker/stage2-hook.sh")
MIGRATION_MARKER = "# Spartan Gate patch: migrate route config before profile reconciliation"
RUNTIME_IDENTITY_MARKER = "# Spartan Gate patch: runtime Hermes identity"
AS_HERMES_MARKER = "# Spartan Gate patch: run as Hermes runtime identity"
RUNTIME_DIR_MARKER = "# Spartan Gate patch: runtime state directories"
MIGRATION_ANCHOR = "# --- Sync bundled skills ---"
OWNERSHIP_PREFLIGHT_MARKER = "# Spartan Gate patch: preflight runtime ownership targets"
UPSTREAM_MANAGED_DIR_ENTRIES = (
    "cron",
    "sessions",
    "logs",
    "hooks",
    "memories",
    "skills",
    "skins",
    "plans",
    "workspace",
    "home",
    "profiles",
    "pairing",
    "platforms/pairing",
    "lazy-packages",
)
MANAGED_DIR_ENTRIES = (
    "cron",
    "sessions",
    "logs",
    "hooks",
    "memories",
    "skills",
    "skins",
    "plans",
    "workspace",
    "home",
    "profiles",
    "pairing",
    "platforms/pairing",
    "lazy-packages",
    "kanban",
    ".local/state/hermes",
)
MANAGED_FILE_ENTRIES = (
    "auth.json",
    "auth.lock",
    ".env",
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "hermes_state.db",
    "response_store.db",
    "response_store.db-shm",
    "response_store.db-wal",
    "gateway.pid",
    "gateway.lock",
    "gateway_state.json",
    "processes.json",
    "active_profile",
    "config.yaml",
)
RECURSIVE_CHOWN_ANCHOR = 'chown -R hermes:hermes "$target"'
TREE_OWNER_ANCHOR = (
    r'find "$target" \( ! -user hermes -o ! -group hermes \) '
    '-print -quit 2>/dev/null | grep -q .'
)
TREE_OWNER_REPLACEMENT = (
    r'find "$target" \( ! -uid "$SPARTAN_HERMES_RUN_UID" '
    r'-o ! -gid "$SPARTAN_HERMES_RUN_GID" \) '
    '-print -quit 2>/dev/null | grep -q .'
)
BOUNDARY_PRUNE = r'\( -path "$SPARTAN_HERMES_SHARED_BOUNDARY" -prune \) -o '
PHYSICAL_CHOWN_REPLACEMENT = (
    'find "$target" ' + BOUNDARY_PRUNE
    + '-exec chown -h "$SPARTAN_HERMES_RUNTIME_OWNER" {} +'
)
PRUNED_TREE_OWNER_REPLACEMENT = TREE_OWNER_REPLACEMENT.replace(
    'find "$target" ', 'find "$target" ' + BOUNDARY_PRUNE, 1,
)
# Exact guard emitted before canonical-bind support. Never trust a marker alone.
LEGACY_PREFLIGHT_SHA256 = "b21d2390bce1d5f0c4df2644c4dfa2b7cb03303c523da2984bc2f6a1aef2c8ee"
RUNTIME_CHOWN_ANCHORS = (
    'chown hermes:hermes "$HERMES_HOME"',
    'chown hermes:hermes "$HERMES_HOME/logs/gateways"',
    'chown hermes:hermes "$HERMES_HOME/$f"',
    'chown hermes:hermes "$HERMES_HOME/config.yaml"',
    'chown hermes:hermes "$HERMES_HOME/.env"',
    'chown hermes:hermes "$HERMES_HOME/auth.json"',
    'chown hermes:hermes "$HERMES_HOME/gateway_state.json"',
)

BOOTSTRAP_MKDIR_BLOCK = """# --- Bootstrap HERMES_HOME as root ---
# Create the directory (and any missing parents) while we still have root
# privileges so the chown checks below see real metadata and the later
# `s6-setuidgid hermes mkdir -p` block doesn't EACCES on root-owned
# ancestors. Without this, custom HERMES_HOME paths whose parents only
# root can create (e.g. `HERMES_HOME=/ho""" + """me/hermes/.hermes` in a Compose
# file, or any path under a fresh / not pre-populated by the image)
# fail on first boot with `mkdir: cannot create directory '/...': Permission
# denied` and the cont-init hook exits non-zero. Idempotent — `mkdir -p`
# is a no-op if the dir already exists. (#18482, salvages #18488)
mkdir -p "$HERMES_HOME"

"""

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
# Validate every explicit UID/GID family as an atomic pair before creating or
# changing any filesystem entry. Keep /etc/passwd immutable and drop workloads
# directly to the selected numeric data owner.
spartan_uid="${SPARTAN_HERMES_RUN_UID:-}"
spartan_gid="${SPARTAN_HERMES_RUN_GID:-}"
hermes_uid="${HERMES_UID:-}"
hermes_gid="${HERMES_GID:-}"
puid="${PUID:-}"
pgid="${PGID:-}"

validate_identity_pair() {
    pair_name="$1"
    pair_uid="$2"
    pair_gid="$3"
    if [ -n "$pair_uid" ] || [ -n "$pair_gid" ]; then
        if [ -z "$pair_uid" ] || [ -z "$pair_gid" ]; then
            echo "[stage2] FATAL: $pair_name UID/GID must be supplied together" >&2
            exit 1
        fi
        if ! validate_uid_gid "$pair_uid" || ! validate_uid_gid "$pair_gid"; then
            echo "[stage2] FATAL: invalid $pair_name UID/GID pair" >&2
            exit 1
        fi
    fi
}

validate_identity_pair SPARTAN_HERMES_RUN "$spartan_uid" "$spartan_gid"
validate_identity_pair HERMES "$hermes_uid" "$hermes_gid"
validate_identity_pair PUID_PGID "$puid" "$pgid"

if [ -n "$spartan_uid" ]; then
    SPARTAN_HERMES_RUN_UID="$spartan_uid"
    SPARTAN_HERMES_RUN_GID="$spartan_gid"
elif [ -n "$hermes_uid" ]; then
    SPARTAN_HERMES_RUN_UID="$hermes_uid"
    SPARTAN_HERMES_RUN_GID="$hermes_gid"
elif [ -n "$puid" ]; then
    SPARTAN_HERMES_RUN_UID="$puid"
    SPARTAN_HERMES_RUN_GID="$pgid"
else
    SPARTAN_HERMES_RUN_UID="$(id -u hermes)"
    SPARTAN_HERMES_RUN_GID="$(id -g hermes)"
fi

for explicit_pair in \
    "$spartan_uid:$spartan_gid" \
    "$hermes_uid:$hermes_gid" \
    "$puid:$pgid"; do
    [ "$explicit_pair" = ":" ] && continue
    if [ "$explicit_pair" != "$SPARTAN_HERMES_RUN_UID:$SPARTAN_HERMES_RUN_GID" ]; then
        echo "[stage2] FATAL: conflicting explicit Hermes runtime UID/GID pairs" >&2
        exit 1
    fi
done

SPARTAN_HERMES_RUNTIME_OWNER="$SPARTAN_HERMES_RUN_UID:$SPARTAN_HERMES_RUN_GID"
HERMES_UID=""
HERMES_GID=""
PUID=""
PGID=""
export SPARTAN_HERMES_RUN_UID SPARTAN_HERMES_RUN_GID SPARTAN_HERMES_RUNTIME_OWNER
export HERMES_UID HERMES_GID PUID PGID

# HERMES_HOME is a Compose-provided data mount. Require every lexical path
# component to exist as a real directory before any write or ownership change;
# this prevents a custom path from escaping through a symlinked parent.
"$INSTALL_DIR/.venv/bin/python" - "$HERMES_HOME" <<'PY'
import stat
import sys
from pathlib import Path

raw = Path(sys.argv[1])
if not raw.is_absolute() or ".." in raw.parts:
    raise SystemExit(f"[stage2] FATAL: HERMES_HOME must be an absolute normalized path: {raw}")

current = Path("/")
for component in raw.parts[1:]:
    current /= component
    try:
        metadata = current.lstat()
    except OSError as exc:
        raise SystemExit(f"[stage2] FATAL: HERMES_HOME must already exist: {current}: {exc}")
    if stat.S_ISLNK(metadata.st_mode):
        raise SystemExit(f"[stage2] FATAL: symlink in HERMES_HOME path: {current}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit(f"[stage2] FATAL: non-directory in HERMES_HOME path: {current}")
PY

"""

OWNERSHIP_PREFLIGHT_BLOCK = f'''{OWNERSHIP_PREFLIGHT_MARKER}
# Validate all targets before publishing the sole pruned ownership boundary.
# Physical traversal still owns descendant links, never their referents.
SPARTAN_HERMES_SHARED_BOUNDARY="$(
"$INSTALL_DIR/.venv/bin/python" - "$HERMES_HOME" <<'PY'
import os
import re
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
metadata = root.lstat()
if stat.S_ISLNK(metadata.st_mode):
    raise SystemExit(f"[stage2] FATAL: HERMES_HOME is a symlink: {{root}}")
root = root.resolve(strict=True)
root_device = root.lstat().st_dev

def decode_mount_path(value: str) -> Path:
    return Path(re.sub(r"\\\\([0-7]{{3}})", lambda match: chr(int(match.group(1), 8)), value))

mounts = []
try:
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        separator = fields.index("-")
        if separator < 6 or len(fields) != separator + 4:
            raise ValueError("malformed mountinfo record")
        device, mount_root, mountpoint = fields[2:5]
        if not re.fullmatch(r"[0-9]+:[0-9]+", device):
            raise ValueError("malformed mountinfo device")
        mount_root, mountpoint = decode_mount_path(mount_root), decode_mount_path(mountpoint)
        if not mountpoint.is_absolute():
            raise ValueError("non-absolute mountinfo mountpoint")
        mounts.append((mountpoint, device, mount_root, *fields[separator + 1:separator + 3]))
except (OSError, ValueError) as exc:
    raise SystemExit(f"[stage2] FATAL: cannot inspect mount boundaries: {{exc}}")

canonical = Path("/opt/data/skills/shared")
alias = Path("/opt/shared-skills")
paired = [record for record in mounts if record[0] in (canonical, alias)]
boundary = None
if paired:
    # Shell find uses the original spelling, so it must match the prune path.
    if sys.argv[1] != "/opt/data":
        raise SystemExit("[stage2] FATAL: canonical shared-skills binds require exact HERMES_HOME=/opt/data")
    if root != Path("/opt/data") or len(paired) != 2 or {{record[0] for record in paired}} != {{canonical, alias}}:
        raise SystemExit("[stage2] FATAL: incomplete canonical shared-skills bind pair")
    if not paired[0][2].is_absolute() or paired[0][1:] != paired[1][1:]:
        raise SystemExit("[stage2] FATAL: canonical shared-skills mount root/device/source mismatch")
    for mount, *_ in mounts:
        if canonical in mount.parents or alias in mount.parents:
            raise SystemExit(f"[stage2] FATAL: nested mount in shared-skills bind: {{mount}}")
    identities = []
    for path in (canonical, alias):
        for component in (path, *path.parents):
            metadata = component.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise SystemExit(f"[stage2] FATAL: non-directory or symlink in shared-skills path: {{component}}")
        metadata = path.lstat()
        # Compare stat identities to each other, not to mountinfo's device:
        # Btrfs subvolumes can expose a different synthetic stat st_dev.
        identities.append((metadata.st_dev, metadata.st_ino))
    if identities[0] != identities[1]:
        raise SystemExit("[stage2] FATAL: shared-skills binds are not the same object")
    boundary = canonical

def under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True

def inspect_target(relative: str, recursive: bool) -> None:
    target = root / relative
    if not target.exists() and not target.is_symlink():
        return
    target_metadata = target.lstat()
    if stat.S_ISLNK(target_metadata.st_mode):
        raise SystemExit(f"[stage2] FATAL: ownership target is a symlink: {{target}}")
    if target_metadata.st_dev != root_device:
        raise SystemExit(f"[stage2] FATAL: ownership target crosses a mount boundary: {{target}}")
    for mount, *_ in mounts:
        if mount == root or mount == boundary:
            continue
        if mount == target or (recursive and under(mount, target)):
            raise SystemExit(f"[stage2] FATAL: nested mount in ownership target: {{mount}}")
    if recursive and target.is_dir():
        for current, directories, files in os.walk(target, followlinks=False):
            for name in [*directories, *files]:
                child = Path(current) / name
                if child == boundary:
                    directories.remove(name)
                    continue
                child_metadata = child.lstat()
                if stat.S_ISLNK(child_metadata.st_mode):
                    # find with chown -h uses physical traversal: it changes the link's
                    # metadata and never follows its target. Working, external,
                    # and intentionally broken dependency links are therefore
                    # safe descendants of a real managed root.
                    continue
                if child_metadata.st_dev != root_device:
                    raise SystemExit(f"[stage2] FATAL: mount boundary in ownership target: {{child}}")

for entry in {MANAGED_DIR_ENTRIES!r}:
    inspect_target(entry, True)
for entry in {MANAGED_FILE_ENTRIES!r}:
    inspect_target(entry, False)
# No path is published until the complete allowlist has passed inspection.
print(boundary or "")
PY
)" || exit 1

'''


def patch_managed_dir_allowlist(source: str) -> str:
    """Add only Spartan-owned runtime directories to the ownership allowlist."""

    found_managed_loop = False

    def replace_loop(match: re.Match[str]) -> str:
        nonlocal found_managed_loop
        original = match.group(0)
        if found_managed_loop:
            return original
        entries = match.group("entries").replace("\\", " ").split()
        if tuple(entries) not in {UPSTREAM_MANAGED_DIR_ENTRIES, UPSTREAM_MANAGED_DIR_ENTRIES[:-1], MANAGED_DIR_ENTRIES}:
            return original
        found_managed_loop = True
        return f"for sub in {' '.join(MANAGED_DIR_ENTRIES)}; do"

    patched = re.sub(r"for sub in (?P<entries>.*?); do", replace_loop, source, flags=re.DOTALL)
    if not found_managed_loop:
        raise ValueError("managed data directory allowlist anchor not found")
    return patched


def validate_managed_file_allowlist(source: str) -> None:
    """Reject upstream drift in the top-level ownership file loop."""
    for match in re.finditer(r"for f in (?P<entries>.*?); do", source, flags=re.DOTALL):
        entries = tuple(match.group("entries").replace("\\", " ").split())
        if entries and entries[0] == "auth.json":
            expected = MANAGED_FILE_ENTRIES[:-1]
            if entries != expected:
                raise ValueError("managed top-level file allowlist changed")
            return
    raise ValueError("managed top-level file allowlist anchor not found")


def patch_ownership_boundary(source: str, *, require_existing: bool) -> str:
    """Upgrade only known ownership guards and physical traversal anchors."""
    anchor = "needs_chown=false\n"
    if source.count(anchor) != 1:
        raise ValueError("runtime ownership preflight anchor changed")
    if OWNERSHIP_PREFLIGHT_MARKER in source:
        if source.count(OWNERSHIP_PREFLIGHT_MARKER) != 1:
            raise ValueError("runtime ownership preflight duplicated")
        start = source.index(OWNERSHIP_PREFLIGHT_MARKER)
        end = source.index(anchor)
        existing = source[start:end]
        if existing != OWNERSHIP_PREFLIGHT_BLOCK and hashlib.sha256(existing.encode()).hexdigest() != LEGACY_PREFLIGHT_SHA256:
            raise ValueError("unknown runtime ownership preflight guard")
        source = source[:start] + OWNERSHIP_PREFLIGHT_BLOCK + source[end:]
    else:
        if require_existing:
            raise ValueError("runtime ownership preflight guard missing")
        source = source.replace(anchor, OWNERSHIP_PREFLIGHT_BLOCK + anchor, 1)
    for old, new in (
        ('chown -hR "$SPARTAN_HERMES_RUNTIME_OWNER" "$target"', PHYSICAL_CHOWN_REPLACEMENT),
        (TREE_OWNER_REPLACEMENT, PRUNED_TREE_OWNER_REPLACEMENT),
    ):
        if source.count(old) + source.count(new) != 1:
            raise ValueError("runtime ownership traversal anchor changed")
        source = source.replace(old, new, 1)
    return source


def patch_source(source: str) -> str:
    patched = source

    validate_managed_file_allowlist(patched)

    if RUNTIME_IDENTITY_MARKER not in patched:
        if BOOTSTRAP_MKDIR_BLOCK not in patched:
            raise ValueError("pre-identity HERMES_HOME bootstrap anchor not found")
        patched = patched.replace(BOOTSTRAP_MKDIR_BLOCK, "", 1)

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
    if RUNTIME_IDENTITY_MARKER not in source:
        if patched.count(RECURSIVE_CHOWN_ANCHOR) != 1:
            raise ValueError("recursive Hermes chown anchor changed")
        patched = patched.replace(
            RECURSIVE_CHOWN_ANCHOR,
            'chown -hR "$SPARTAN_HERMES_RUNTIME_OWNER" "$target"',
            1,
        )
        if patched.count(TREE_OWNER_ANCHOR) != 1:
            raise ValueError("runtime ownership scan anchor changed")
        patched = patched.replace(TREE_OWNER_ANCHOR, TREE_OWNER_REPLACEMENT, 1)
        for anchor in RUNTIME_CHOWN_ANCHORS:
            if patched.count(anchor) != 1:
                raise ValueError(f"runtime chown anchor changed: {anchor}")
            replacement = anchor.replace(
                "chown hermes:hermes",
                'chown "$SPARTAN_HERMES_RUNTIME_OWNER"',
                1,
            )
            patched = patched.replace(anchor, replacement, 1)
    patched = patched.replace(
        's6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python"',
        'as_hermes "$INSTALL_DIR/.venv/bin/python"',
    )
    patched = patched.replace(
        'chown hermes:hermes "$XDG_RUNTIME_DIR"',
        'chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$XDG_RUNTIME_DIR"',
    ).replace(
        'chgrp hermes /.fly/api', 'chgrp "$SPARTAN_HERMES_RUN_GID" /.fly/api',
    )

    patched = patch_managed_dir_allowlist(patched)

    patched = patch_ownership_boundary(patched, require_existing=RUNTIME_IDENTITY_MARKER in source)

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
            final_dir = '    "$HERMES_HOME/platforms/pairing"\n'
            expanded_dirs = (
                '    "$HERMES_HOME/platforms/pairing" \\\n'
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
