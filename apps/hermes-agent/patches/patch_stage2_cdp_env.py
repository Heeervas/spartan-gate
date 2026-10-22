#!/usr/bin/env python3
"""Build-time patcher: migrate gateway state before s6 reconciliation.

The historical filename is retained for Dockerfile compatibility. No browser
service or CDP environment is prepared here.
"""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/docker/stage2-hook.sh")
MARKER = "# Spartan Gate patch: gateway state for supervised services"
ANCHOR = "# --- Discover agent-browser's Chromium binary ---"
ALTERNATE_ANCHOR = "# --- Point agent-browser at the pinned Chromium binary ---"
END_MARKER = "# End Spartan Gate supervised gateway state"

NEW_BLOCK = '''# Spartan Gate patch: gateway state for supervised services
# Required before the native profile reconciler starts supervised gateways.
seed_legacy_autostart_profile_state() {
    [ -n "${HERMES_AUTOSTART_PROFILES:-}" ] || return 0

    old_ifs="$IFS"
    IFS=','
    for profile_name in $HERMES_AUTOSTART_PROFILES; do
        IFS="$old_ifs"
        profile_name="$(printf '%s' "$profile_name" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        IFS=','
        [ -n "$profile_name" ] || continue
        if [ "$profile_name" = "default" ]; then
            echo "[stage2] HERMES_AUTOSTART_PROFILES profile 'default' is reserved for the root profile - skipping"
            continue
        fi
        case "$profile_name" in
            *[!a-z0-9_-]*|[!a-z0-9]*)
                echo "[stage2] HERMES_AUTOSTART_PROFILES profile '$profile_name' is invalid - skipping"
                continue
                ;;
        esac

        profile_dir="$HERMES_HOME/profiles/$profile_name"
        state_file="$profile_dir/gateway_state.json"
        if [ ! -f "$profile_dir/SOUL.md" ] || [ ! -f "$profile_dir/config.yaml" ]; then
            echo "[stage2] HERMES_AUTOSTART_PROFILES profile '$profile_name' is not configured - skipping"
            continue
        fi
        if [ -f "$state_file" ]; then
            echo "[stage2] HERMES_AUTOSTART_PROFILES profile '$profile_name' already has gateway state - preserving"
            continue
        fi
        printf '{"gateway_state":"running","migrated_from":"HERMES_AUTOSTART_PROFILES"}\\n' > "$state_file"
        chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$state_file" 2>/dev/null || true
        chmod 644 "$state_file" 2>/dev/null || true
        echo "[stage2] HERMES_AUTOSTART_PROFILES profile '$profile_name' seeded for s6 start"
    done
    IFS="$old_ifs"
}

seed_spartan_default_gateway_state() {
    state_file="$HERMES_HOME/gateway_state.json"
    [ ! -f "$state_file" ] || return 0
    case "${HERMES_GATEWAY_NO_SUPERVISE:-}" in
        1|true|yes) return 0 ;;
    esac

    cmdline="$(tr '\\000' ' ' < /proc/1/cmdline 2>/dev/null || true)"
    case "$cmdline" in
        *"/opt/hermes/entrypoint-wrapper.sh gateway run"*|*"/opt/hermes/docker/main-wrapper.sh gateway run"*)
            printf '{"gateway_state":"running","migrated_from":"spartan-container-cmd"}\\n' > "$state_file"
            chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$state_file" 2>/dev/null || true
            chmod 644 "$state_file" 2>/dev/null || true
            echo "[stage2] Default gateway state seeded for s6 start from container command"
            ;;
    esac
}

seed_spartan_default_gateway_state
seed_legacy_autostart_profile_state
# End Spartan Gate supervised gateway state

'''


def _insertion_index(source: str) -> int:
    anchors = [anchor for anchor in (ANCHOR, ALTERNATE_ANCHOR) if anchor in source]
    if len(anchors) != 1 or source.count(anchors[0]) != 1:
        raise ValueError("expected exactly one supported agent-browser discovery anchor")
    return source.index(anchors[0])


def patch_source(source: str) -> str:
    index = _insertion_index(source)
    # Exact generated-block validation intentionally rejects partial/modified
    # guards instead of trusting a marker or guessing how to repair shell code.
    if MARKER in source:
        if source.count(MARKER) != 1 or source.count(NEW_BLOCK) != 1:
            raise ValueError("incomplete or modified supervised gateway state patch")
        start = source.index(NEW_BLOCK)
        if start + len(NEW_BLOCK) != index:
            raise ValueError("supervised gateway state patch is not before discovery")
        remainder = source[:start] + source[index:]
        _validate_pristine(remainder)
        return source

    _validate_pristine(source)
    patched = source[:index] + NEW_BLOCK + source[index:]
    if patched.count(NEW_BLOCK) != 1:
        raise ValueError("supervised gateway state patch postcondition failed")
    return patched


def _validate_pristine(source: str) -> None:
    if any(fragment in source for fragment in (
        "# Spartan Gate patch: browser runtime for supervised services", END_MARKER,
        "seed_legacy_autostart_profile_state", "seed_spartan_default_gateway_state",
    )):
        raise ValueError("incompatible or partial Spartan stage2 patch")


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
        print(f"SKIP: {TARGET} already prepares supervised gateway state")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - gateway state prepared before supervised services")


if __name__ == "__main__":
    main()
