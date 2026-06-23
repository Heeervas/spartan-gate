#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/private/env/local.env"
COMPOSE_FILE="$ROOT_DIR/private/compose.local.yml"

usage() {
    cat >&2 <<'EOF'
Usage: sg-hermes-profile-new <name> [options]

Options:
  --gateway              Create/validate an autostart gateway profile.
  --manual               Create/validate a manual profile.
  --telegram             Add dedicated Telegram env references.
  --no-telegram          Leave Telegram vars empty.
  --discord              Add dedicated Discord env references.
  --no-discord           Leave Discord vars empty.
  --port <port>          Literal API_SERVER_PORT for gateway profiles.
  --autostart            Add profile to HERMES_AUTOSTART_PROFILES.
  --no-autostart         Do not change HERMES_AUTOSTART_PROFILES.
  --dry-run              Show planned changes without writing files.
  -h, --help             Show this help.
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

env_value() {
    local key="$1"
    local default="${2:-}"
    if [[ -f "$ENV_FILE" ]]; then
        local value
        value="$(awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$ENV_FILE")"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        if [[ -n "$value" ]]; then
            printf '%s\n' "$value"
            return 0
        fi
    fi
    printf '%s\n' "$default"
}

prompt_default() {
    local prompt="$1"
    local default="$2"
    local answer
    read -r -p "$prompt [$default]: " answer
    printf '%s\n' "${answer:-$default}"
}

prompt_yes_no() {
    local prompt="$1"
    local default="$2"
    local answer
    read -r -p "$prompt [$default]: " answer
    answer="${answer:-$default}"
    case "$answer" in
        y|Y|yes|YES|true|TRUE|1) return 0 ;;
        n|N|no|NO|false|FALSE|0) return 1 ;;
        *) die "invalid yes/no answer: $answer" ;;
    esac
}

profile_name=""
mode=""
telegram=""
discord=""
port=""
autostart=""
dry_run=false

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --gateway) mode="gateway" ;;
        --manual) mode="manual" ;;
        --telegram) telegram="yes" ;;
        --no-telegram) telegram="no" ;;
        --discord) discord="yes" ;;
        --no-discord) discord="no" ;;
        --port)
            shift
            [[ "$#" -gt 0 ]] || die "--port requires a value"
            port="$1"
            ;;
        --autostart) autostart="yes" ;;
        --no-autostart) autostart="no" ;;
        --dry-run) dry_run=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*) die "unknown option: $1" ;;
        *)
            [[ -z "$profile_name" ]] || die "unexpected argument: $1"
            profile_name="$1"
            ;;
    esac
    shift
done

[[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"

if [[ -z "$profile_name" ]]; then
    profile_name="$(prompt_default "Profile name" "")"
fi
[[ "$profile_name" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid profile name: $profile_name"
[[ "$profile_name" != "." && "$profile_name" != ".." ]] || die "invalid profile name: $profile_name"

if [[ -z "$mode" ]]; then
    mode="$(prompt_default "Profile mode (gateway/manual)" "gateway")"
fi
[[ "$mode" == "gateway" || "$mode" == "manual" ]] || die "mode must be gateway or manual"

if [[ -z "$telegram" ]]; then
    if prompt_yes_no "Use dedicated Telegram vars" "y"; then telegram="yes"; else telegram="no"; fi
fi
if [[ -z "$discord" ]]; then
    if prompt_yes_no "Use dedicated Discord vars" "y"; then discord="yes"; else discord="no"; fi
fi

data_dir="$(env_value SPARTAN_HERMES_DATA_PATH "$(env_value HERMES_DATA_PATH "$ROOT_DIR/runtime/hermes")")"
profile_dir="$data_dir/profiles/$profile_name"
upper_name="$(printf '%s' "$profile_name" | tr '[:lower:].-' '[:upper:]__')"

[[ ! -e "$profile_dir/.env" ]] || die "$profile_dir/.env already exists; this helper only creates new profile env files"

if [[ "$mode" == "gateway" ]]; then
    if [[ -z "$port" ]]; then
        used_ports="$(find "$data_dir/profiles" -mindepth 2 -maxdepth 2 -name .env -exec awk -F= '$1 == "API_SERVER_PORT" {print $2}' {} + 2>/dev/null | sort -n | tail -n 1)"
        next_port="$(( ${used_ports:-8642} + 1 ))"
        port="$(prompt_default "API_SERVER_PORT" "$next_port")"
    fi
    [[ "$port" =~ ^[0-9]+$ ]] || die "invalid port: $port"
    (( 10#$port >= 1 && 10#$port <= 65535 )) || die "invalid port: $port (expected 1..65535)"
    existing="$(find "$data_dir/profiles" -mindepth 2 -maxdepth 2 -name .env -exec awk -F= -v port="$port" '$1 == "API_SERVER_PORT" && $2 == port {print FILENAME}' {} + 2>/dev/null || true)"
    if [[ -n "$existing" ]] && ! grep -qx "$profile_dir/.env" <<<"$existing"; then
        die "API_SERVER_PORT $port is already used by $existing"
    fi
    if [[ -z "$autostart" ]]; then autostart="yes"; fi
else
    port=""
    if [[ -z "$autostart" ]]; then autostart="no"; fi
fi

if [[ "$autostart" == "yes" && "$mode" != "gateway" ]]; then
    die "manual profiles cannot be autostarted"
fi

ensure_env_key() {
    local key="$1"
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        printf '%s=\n' "$key" >> "$ENV_FILE"
        printf 'Added %s to %s\n' "$key" "$ENV_FILE"
    fi
}

ensure_compose_env() {
    local key="$1"
    local source_key="$2"
    if grep -q "^[[:space:]]*${key}:" "$COMPOSE_FILE"; then
        return 0
    fi
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v source_key="$source_key" '
        {
            print
            if ($0 ~ /^[[:space:]]*TELEGRAM_ALLOWED_USERS_WORK:/ && key ~ /^TELEGRAM_/) {
                print "      " key ": ${" source_key ":-}"
            }
            if ($0 ~ /^[[:space:]]*DISCORD_HOME_CHANNEL_WORK:/ && key ~ /^DISCORD_/) {
                print "      " key ": ${" source_key ":-}"
            }
        }
    ' "$COMPOSE_FILE" > "$tmp"
    mv "$tmp" "$COMPOSE_FILE"
    printf 'Exposed %s in %s\n' "$key" "$COMPOSE_FILE"
}

update_autostart() {
    local current
    current="$(env_value HERMES_AUTOSTART_PROFILES "")"
    IFS=',' read -ra parts <<< "$current"
    for item in "${parts[@]}"; do
        item="$(printf '%s' "$item" | xargs)"
        [[ "$item" != "$profile_name" ]] || return 0
    done
    local updated
    if [[ -n "$current" ]]; then updated="$current,$profile_name"; else updated="$profile_name"; fi
    local tmp
    tmp="$(mktemp)"
    if grep -q '^HERMES_AUTOSTART_PROFILES=' "$ENV_FILE"; then
        awk -v value="$updated" 'BEGIN{done=0} /^HERMES_AUTOSTART_PROFILES=/{print "HERMES_AUTOSTART_PROFILES=" value; done=1; next} {print} END{if(!done) print "HERMES_AUTOSTART_PROFILES=" value}' "$ENV_FILE" > "$tmp"
    else
        cp "$ENV_FILE" "$tmp"
        printf 'HERMES_AUTOSTART_PROFILES=%s\n' "$updated" >> "$tmp"
    fi
    mv "$tmp" "$ENV_FILE"
    printf 'Added %s to HERMES_AUTOSTART_PROFILES\n' "$profile_name"
}

write_profile_env() {
    mkdir -p "$profile_dir"
    local tmp
    tmp="$(mktemp)"
    {
        printf 'TERMINAL_MODAL_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20\n'
        printf 'TERMINAL_TIMEOUT=60\n'
        printf 'TERMINAL_LIFETIME_SECONDS=300\n'
        printf 'BROWSERBASE_PROXIES=true\n'
        printf 'BROWSERBASE_ADVANCED_STEALTH=false\n'
        printf 'BROWSER_SESSION_TIMEOUT=300\n'
        printf 'BROWSER_INACTIVITY_TIMEOUT=120\n'
        printf 'WEB_TOOLS_DEBUG=false\n'
        printf 'VISION_TOOLS_DEBUG=false\n'
        printf 'MOA_TOOLS_DEBUG=false\n'
        printf 'IMAGE_TOOLS_DEBUG=false\n'
        if [[ "$telegram" == "yes" ]]; then
            printf 'TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN_%s}\n' "$upper_name"
            printf 'TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS_%s}\n' "$upper_name"
        else
            printf 'TELEGRAM_BOT_TOKEN=\n'
            printf 'TELEGRAM_ALLOWED_USERS=\n'
        fi
        if [[ "$discord" == "yes" ]]; then
            printf 'DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN_%s}\n' "$upper_name"
            printf 'DISCORD_ALLOWED_USERS=${DISCORD_ALLOWED_USERS_%s}\n' "$upper_name"
            printf 'DISCORD_HOME_CHANNEL=${DISCORD_HOME_CHANNEL_%s}\n' "$upper_name"
        else
            printf 'DISCORD_BOT_TOKEN=\n'
            printf 'DISCORD_ALLOWED_USERS=\n'
            printf 'DISCORD_HOME_CHANNEL=\n'
        fi
        printf 'HERMES_GATEWAY_TOKEN=${HERMES_GATEWAY_TOKEN}\n'
        printf 'CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}\n'
        if [[ -n "$port" ]]; then printf 'API_SERVER_PORT=%s\n' "$port"; fi
        printf 'BROWSER_CDP_URL=${BROWSER_CDP_URL}\n'
        printf 'OPENAI_BASE_URL=http://clawroute:18790/v1\n'
        printf 'PYTHONPATH=/opt/data/hermes-extra-site:/opt/hermes/bootstrap\n'
        printf 'PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright\n'
    } > "$tmp"
    mv "$tmp" "$profile_dir/.env"
    chmod 600 "$profile_dir/.env" 2>/dev/null || true
    printf 'Wrote %s\n' "$profile_dir/.env"
}

printf 'Profile: %s\n' "$profile_name"
printf 'Mode: %s\n' "$mode"
printf 'Hermes data: %s\n' "$data_dir"
if [[ -n "$port" ]]; then printf 'API_SERVER_PORT: %s\n' "$port"; fi
printf 'Telegram vars: %s\n' "$telegram"
printf 'Discord vars: %s\n' "$discord"
printf 'Autostart: %s\n' "$autostart"

if [[ "$dry_run" == "true" ]]; then
    printf 'Dry run only; no files changed.\n'
    exit 0
fi

if [[ "$telegram" == "yes" ]]; then
    ensure_env_key "HERMES_TELEGRAM_BOT_TOKEN_$upper_name"
    ensure_env_key "HERMES_TELEGRAM_ALLOWED_USERS_$upper_name"
    ensure_compose_env "TELEGRAM_BOT_TOKEN_$upper_name" "HERMES_TELEGRAM_BOT_TOKEN_$upper_name"
    ensure_compose_env "TELEGRAM_ALLOWED_USERS_$upper_name" "HERMES_TELEGRAM_ALLOWED_USERS_$upper_name"
fi
if [[ "$discord" == "yes" ]]; then
    ensure_env_key "DISCORD_BOT_TOKEN_$upper_name"
    ensure_env_key "DISCORD_ALLOWED_USERS_$upper_name"
    ensure_env_key "DISCORD_HOME_CHANNEL_$upper_name"
    ensure_compose_env "DISCORD_BOT_TOKEN_$upper_name" "DISCORD_BOT_TOKEN_$upper_name"
    ensure_compose_env "DISCORD_ALLOWED_USERS_$upper_name" "DISCORD_ALLOWED_USERS_$upper_name"
    ensure_compose_env "DISCORD_HOME_CHANNEL_$upper_name" "DISCORD_HOME_CHANNEL_$upper_name"
fi

write_profile_env

if [[ "$autostart" == "yes" ]]; then
    update_autostart
fi

printf 'Done. Run sg-profile-ports and sg-config --quiet before recreating Hermes.\n'
