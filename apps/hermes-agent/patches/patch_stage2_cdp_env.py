#!/usr/bin/env python3
"""Build-time patcher: expose Spartan browser env before s6 services start."""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/docker/stage2-hook.sh")
MARKER = "# Spartan Gate patch: browser runtime for supervised services"
ANCHOR = "# --- Discover agent-browser's Chromium binary ---"


NEW_BLOCK = '''# Spartan Gate patch: browser runtime for supervised services
# The profile reconciler starts supervised gateway services after this hook and
# before entrypoint-wrapper.sh runs. Materialize Spartan Gate's browser/CDP
# contract now so with-contenv injects it into supervised services.
camofox_browser_mode_enabled() {
    [ -n "${CAMOFOX_URL:-}" ]
}

mkdir -p /run/s6/container_environment

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

seed_spartan_supervise_skeleton() {
    svc_dir="$1"
    mkdir -p "$svc_dir/event" "$svc_dir/supervise" "$svc_dir/supervise/event"
    chmod 3730 "$svc_dir/event" "$svc_dir/supervise/event" 2>/dev/null || true
    chmod 755 "$svc_dir/supervise" 2>/dev/null || true
    if [ ! -p "$svc_dir/supervise/control" ]; then
        rm -f "$svc_dir/supervise/control"
        mkfifo "$svc_dir/supervise/control" 2>/dev/null || true
    fi
    chmod 660 "$svc_dir/supervise/control" 2>/dev/null || true
    chown -R "$SPARTAN_HERMES_RUNTIME_OWNER" "$svc_dir/event" "$svc_dir/supervise" 2>/dev/null || true

    if [ -d "$svc_dir/log" ]; then
        mkdir -p "$svc_dir/log/event" "$svc_dir/log/supervise" "$svc_dir/log/supervise/event"
        chmod 3730 "$svc_dir/log/event" "$svc_dir/log/supervise/event" 2>/dev/null || true
        chmod 755 "$svc_dir/log/supervise" 2>/dev/null || true
        if [ ! -p "$svc_dir/log/supervise/control" ]; then
            rm -f "$svc_dir/log/supervise/control"
            mkfifo "$svc_dir/log/supervise/control" 2>/dev/null || true
        fi
        chmod 660 "$svc_dir/log/supervise/control" 2>/dev/null || true
        chown -R "$SPARTAN_HERMES_RUNTIME_OWNER" "$svc_dir/log/event" "$svc_dir/log/supervise" 2>/dev/null || true
    fi
}

register_spartan_browserless_cdp_broker_service() {
    if camofox_browser_mode_enabled; then
        return 0
    fi
    if [ "${BROWSERLESS_CDP_BROKER_ENABLED:-true}" != "true" ]; then
        return 0
    fi
    if [ ! -f "$INSTALL_DIR/bootstrap/browserless-cdp-url.js" ] || \
            [ ! -f "$INSTALL_DIR/bootstrap/browserless-cdp-broker.py" ]; then
        return 0
    fi

    broker_port="${BROWSERLESS_CDP_BROKER_PORT:-9229}"
    broker_url="ws://127.0.0.1:${broker_port}"
    export BROWSERLESS_CDP_BROKER_PORT="$broker_port"
    export BROWSER_CDP_MAIN_URL="${BROWSER_CDP_MAIN_URL:-$broker_url}"
    export BROWSER_CDP_URL="${BROWSER_CDP_URL:-$BROWSER_CDP_MAIN_URL}"

    service_dir="/run/service/spartan-browserless-cdp-broker"
    tmp_dir="${service_dir}.tmp"
    rm -rf "$tmp_dir"
    mkdir -p "$tmp_dir/log"
    printf 'longrun\\n' > "$tmp_dir/type"
    cat > "$tmp_dir/run" <<'EOF'
#!/command/with-contenv sh
set -eu

export HOME=/opt/data
export BROWSERLESS_PROFILE="${BROWSERLESS_PROFILE:-main}"
export BROWSERLESS_CDP_BROKER_PORT="${BROWSERLESS_CDP_BROKER_PORT:-9229}"
export BROWSER_CDP_MAIN_URL="${BROWSER_CDP_MAIN_URL:-ws://127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}}"
export BROWSER_CDP_URL="${BROWSER_CDP_URL:-$BROWSER_CDP_MAIN_URL}"

if [ "$(id -u)" = 0 ]; then
    if [ -n "${SPARTAN_HERMES_RUN_UID:-}${SPARTAN_HERMES_RUN_GID:-}" ]; then
        exec /command/s6-applyuidgid \
            -u "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}" \
            -g "${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}" "$0" "$@"
    fi
    exec /command/s6-setuidgid hermes "$0" "$@"
fi

retry_seconds="${BROWSERLESS_CDP_BROKER_RETRY_SECONDS:-30}"
case "$retry_seconds" in
    ''|*[!0-9]*) retry_seconds=30 ;;
esac

while :; do
    export BROWSER_CDP_LAUNCH_URL
    if BROWSER_CDP_LAUNCH_URL="$(node /opt/hermes/bootstrap/browserless-cdp-url.js --profile "$BROWSERLESS_PROFILE")" && \
            [ -n "$BROWSER_CDP_LAUNCH_URL" ]; then
        exec /opt/hermes/.venv/bin/python /opt/hermes/bootstrap/browserless-cdp-broker.py
    fi
    echo "[broker] Browserless CDP launch URL generation failed; retrying in ${retry_seconds}s" >&2
    sleep "$retry_seconds"
done
EOF
    chmod 755 "$tmp_dir/run"
    cat > "$tmp_dir/log/run" <<'EOF'
#!/command/with-contenv sh
set -eu

: "${HERMES_HOME:=/opt/data}"
log_dir="$HERMES_HOME/logs/browserless-cdp-broker"
mkdir -p "$log_dir"
chown "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}:${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}" "$log_dir" 2>/dev/null || true
if [ "$(id -u)" = 0 ]; then
    if [ -n "${SPARTAN_HERMES_RUN_UID:-}${SPARTAN_HERMES_RUN_GID:-}" ]; then
        exec /command/s6-applyuidgid \
            -u "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}" \
            -g "${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}" s6-log 1 n10 s1000000 T "$log_dir"
    fi
    exec /command/s6-setuidgid hermes s6-log 1 n10 s1000000 T "$log_dir"
fi
exec s6-log 1 n10 s1000000 T "$log_dir"
EOF
    chmod 755 "$tmp_dir/log/run"
    seed_spartan_supervise_skeleton "$tmp_dir"
    rm -rf "$service_dir"
    mv "$tmp_dir" "$service_dir"
    echo "[stage2] Browserless CDP broker registered under s6 (${BROWSER_CDP_MAIN_URL})"
}

seed_spartan_default_gateway_state
seed_legacy_autostart_profile_state

if camofox_browser_mode_enabled; then
    unset BROWSER_CDP_URL
    unset BROWSER_CDP_MAIN_URL
    unset BROWSER_CDP_LAUNCH_URL
    echo "[stage2] CAMOFOX_URL set; Browserless CDP env skipped for supervised services"
elif [ -z "${BROWSER_CDP_LAUNCH_URL:-}" ] && [ -f "$INSTALL_DIR/bootstrap/browserless-cdp-url.js" ]; then
    browser_cdp_launch_url="$(node "$INSTALL_DIR/bootstrap/browserless-cdp-url.js" 2>/dev/null)" || browser_cdp_launch_url=""
    if [ -n "$browser_cdp_launch_url" ]; then
        export BROWSER_CDP_LAUNCH_URL="$browser_cdp_launch_url"
    else
        echo "[stage2] Warning: Browserless launch CDP URL could not be prepared"
    fi
fi

if ! camofox_browser_mode_enabled && [ "${BROWSERLESS_CDP_BROKER_ENABLED:-true}" = "true" ]; then
    export BROWSERLESS_CDP_BROKER_PORT="${BROWSERLESS_CDP_BROKER_PORT:-9229}"
    if [ -z "${BROWSER_CDP_MAIN_URL:-}" ]; then
        export BROWSER_CDP_MAIN_URL="ws://127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}"
    fi
    if ! camofox_browser_mode_enabled && [ -n "${BROWSER_CDP_MAIN_URL:-}" ]; then
        export BROWSER_CDP_URL="$BROWSER_CDP_MAIN_URL"
    fi
elif ! camofox_browser_mode_enabled && [ -z "${BROWSER_CDP_URL:-}" ] && [ -n "${BROWSER_CDP_LAUNCH_URL:-}" ]; then
    export BROWSER_CDP_URL="$BROWSER_CDP_LAUNCH_URL"
fi

rm -f /run/s6/container_environment/BROWSER_CDP_LAUNCH_URL
rm -f /run/s6/container_environment/BROWSER_CDP_MAIN_URL
rm -f /run/s6/container_environment/BROWSER_CDP_URL
rm -f /run/s6/container_environment/BROWSERLESS_CDP_BROKER_ENABLED
rm -f /run/s6/container_environment/BROWSERLESS_CDP_BROKER_PORT

if [ -n "${BROWSER_CDP_LAUNCH_URL:-}" ]; then
    printf '%s' "$BROWSER_CDP_LAUNCH_URL" > /run/s6/container_environment/BROWSER_CDP_LAUNCH_URL
fi
if [ -n "${BROWSER_CDP_MAIN_URL:-}" ]; then
    printf '%s' "$BROWSER_CDP_MAIN_URL" > /run/s6/container_environment/BROWSER_CDP_MAIN_URL
fi
if ! camofox_browser_mode_enabled && [ -n "${BROWSER_CDP_URL:-}" ]; then
    printf '%s' "$BROWSER_CDP_URL" > /run/s6/container_environment/BROWSER_CDP_URL
    echo "[stage2] Browserless CDP env prepared for supervised services"
fi
if [ -n "${BROWSERLESS_CDP_BROKER_ENABLED:-}" ]; then
    printf '%s' "$BROWSERLESS_CDP_BROKER_ENABLED" > /run/s6/container_environment/BROWSERLESS_CDP_BROKER_ENABLED
fi
if [ -n "${BROWSERLESS_CDP_BROKER_PORT:-}" ]; then
    printf '%s' "$BROWSERLESS_CDP_BROKER_PORT" > /run/s6/container_environment/BROWSERLESS_CDP_BROKER_PORT
fi

register_spartan_browserless_cdp_broker_service

'''


def patch_source(source: str) -> str:
    if MARKER in source:
        return source

    index = source.find(ANCHOR)
    if index == -1:
        raise ValueError("agent-browser discovery anchor not found")

    return source[:index] + NEW_BLOCK + source[index:]


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
        print(f"SKIP: {TARGET} already prepares Browserless CDP env")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - Spartan browser runtime prepared before supervised services")


if __name__ == "__main__":
    main()
