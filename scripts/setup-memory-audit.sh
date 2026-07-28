#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOL="$SCRIPT_DIR/memory-status.py"
UNIT_NAME="spartan-gate-memory-audit"
SYSTEMD_USER_DIR="${SPARTAN_MEMORY_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATUS_FILE="${SPARTAN_MEMORY_STATUS_FILE:-$RUNTIME_DIR/spartan-gate-memory-status.json}"
STATE_FILE="${SPARTAN_MEMORY_STATE_FILE:-$STATE_HOME/spartan-gate/memory-audit-state.json}"
SERVICE_FILE="$SYSTEMD_USER_DIR/$UNIT_NAME.service"
TIMER_FILE="$SYSTEMD_USER_DIR/$UNIT_NAME.timer"
SYSTEMCTL_BIN="${SPARTAN_MEMORY_SYSTEMCTL:-systemctl}"

usage() {
    cat <<'USAGE'
Usage: scripts/setup-memory-audit.sh <install|status>

  install  install/refresh the read-only per-user audit and run it once
  status   show the timer and latest published memory status
USAGE
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

resolve_command() {
    local command_name="$1"
    local resolved
    resolved="$(command -v "$command_name" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || die "required command is unavailable: $command_name"
    printf '%s\n' "$resolved"
}

unit_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '"%s"' "$value"
}

unit_path() {
    local value="$1"
    [[ "$value" == /* ]] || die "unit path is not absolute: $value"
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        die "unit path contains a line break"
    fi
    value="${value//\\/\\x5c}"
    value="${value// /\\x20}"
    value="${value//%/%%}"
    printf '%s' "$value"
}

validate_directory() {
    local directory="$1"
    if [[ -L "$directory" ]]; then
        die "refusing symlink directory: $directory"
    fi
    if [[ -e "$directory" ]]; then
        [[ -d "$directory" ]] || die "path is not a directory: $directory"
        [[ "$(stat -c '%u' "$directory")" == "$(id -u)" ]] ||
            die "directory is not owned by the current user: $directory"
    fi
}

validate_target() {
    local target="$1"
    if [[ -L "$target" ]]; then
        die "refusing symlink unit target: $target"
    fi
    if [[ -e "$target" ]]; then
        [[ -f "$target" ]] || die "unit target is not a regular file: $target"
        [[ "$(stat -c '%u' "$target")" == "$(id -u)" ]] ||
            die "unit target is not owned by the current user: $target"
    fi
}

write_atomic() {
    local target="$1"
    local content="$2"
    local temporary
    temporary="$(mktemp "${target}.tmp.XXXXXX")"
    chmod 0600 "$temporary"
    printf '%s' "$content" >"$temporary"
    mv -f -- "$temporary" "$target"
    chmod 0644 "$target"
}

install_audit() {
    [[ "$(id -u)" != "0" ]] || die "install this as the desktop user, not root"
    [[ -f "$TOOL" && ! -L "$TOOL" ]] || die "memory audit tool is missing or unsafe: $TOOL"
    unit_path "$REPO_ROOT" >/dev/null
    unit_path "$SYSTEMD_USER_DIR" >/dev/null
    unit_path "$STATUS_FILE" >/dev/null
    unit_path "$STATE_FILE" >/dev/null
    validate_directory "$SYSTEMD_USER_DIR"
    validate_directory "$(dirname "$STATUS_FILE")"
    validate_directory "$(dirname "$STATE_FILE")"

    local python_bin sadf_bin docker_bin swapon_bin journalctl_bin systemctl_resolved
    python_bin="$(resolve_command python3)"
    sadf_bin="$(resolve_command sadf)"
    docker_bin="$(resolve_command docker)"
    swapon_bin="$(resolve_command swapon)"
    journalctl_bin="$(resolve_command journalctl)"
    systemctl_resolved="$(resolve_command "$SYSTEMCTL_BIN")"

    "$systemctl_resolved" --user show-environment >/dev/null ||
        die "the per-user systemd manager is unavailable"

    validate_target "$SERVICE_FILE"
    validate_target "$TIMER_FILE"

    local service timer exec_start
    exec_start="$(unit_quote "$python_bin") $(unit_quote "$TOOL") collect"
    exec_start+=" --status-file $(unit_quote "$STATUS_FILE")"
    exec_start+=" --state-file $(unit_quote "$STATE_FILE")"
    exec_start+=" --sadf-bin $(unit_quote "$sadf_bin")"
    exec_start+=" --docker-bin $(unit_quote "$docker_bin")"
    exec_start+=" --swapon-bin $(unit_quote "$swapon_bin")"
    exec_start+=" --journalctl-bin $(unit_quote "$journalctl_bin")"

    service="[Unit]
Description=Daily Spartan Gate memory and swap audit

[Service]
Type=oneshot
WorkingDirectory=$(unit_path "$REPO_ROOT")
ExecStart=$exec_start
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$(unit_quote "$(dirname "$STATE_FILE")") $(unit_quote "$(dirname "$STATUS_FILE")")
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallArchitectures=native
UMask=0077
"

    timer="[Unit]
Description=Run the Spartan Gate memory audit every morning

[Timer]
OnCalendar=*-*-* 04:20:00
RandomizedDelaySec=10m
AccuracySec=1m
Persistent=true
Unit=$UNIT_NAME.service

[Install]
WantedBy=timers.target
"

    mkdir -p "$SYSTEMD_USER_DIR" "$(dirname "$STATE_FILE")" "$(dirname "$STATUS_FILE")"
    chmod 0700 "$(dirname "$STATE_FILE")"
    write_atomic "$SERVICE_FILE" "$service"
    write_atomic "$TIMER_FILE" "$timer"

    "$systemctl_resolved" --user daemon-reload
    "$systemctl_resolved" --user enable --now "$UNIT_NAME.timer"
    "$systemctl_resolved" --user start "$UNIT_NAME.service"

    printf 'Installed %s.timer (daily at 04:20, randomized by up to 10 minutes).\n' "$UNIT_NAME"
    printf 'Latest status: %s\n' "$STATUS_FILE"
}

show_status() {
    local systemctl_resolved python_bin
    systemctl_resolved="$(resolve_command "$SYSTEMCTL_BIN")"
    python_bin="$(resolve_command python3)"
    "$systemctl_resolved" --user status "$UNIT_NAME.timer" --no-pager
    "$python_bin" "$TOOL" show --status-file "$STATUS_FILE" --state-file "$STATE_FILE"
}

case "${1:-}" in
    install) install_audit ;;
    status) show_status ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
