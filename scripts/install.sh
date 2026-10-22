#!/usr/bin/env bash
set -euo pipefail

umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIER=""
ADDONS=""
HERMES_MODE=""

usage() {
    cat <<'EOF'
Usage: scripts/install.sh --tier L0|L1|L2|L3|L4 [--hermes free|gated|full]

Creates private/env/local.env, generates local secrets, runs `hermes setup`,
and starts the selected Spartan Gate tier.
EOF
}

die() {
    printf 'install: %s\n' "$1" >&2
    exit 1
}

normalize_tier() {
    case "${1:-}" in
        L0|l0|0) printf 'L0\n' ;;
        L1|l1|1) printf 'L1\n' ;;
        L2|l2|2) printf 'L2\n' ;;
        L3|l3|3) printf 'L3\n' ;;
        L4|l4|4) printf 'L4\n' ;;
        *) return 1 ;;
    esac
}

normalize_hermes_mode() {
    case "${1:-}" in
        free|gated|full) printf '%s\n' "$1" ;;
        *) return 1 ;;
    esac
}

default_hermes_mode() {
    case "$TIER" in
        L0|L1) printf 'free\n' ;;
        L2|L3) printf 'gated\n' ;;
        L4) printf 'full\n' ;;
    esac
}

normalize_addons() {
    local raw="${1:-}" addon out=""
    raw="${raw//,/ }"
    for addon in $raw; do
        case "$addon" in
            clawroute)
                printf 'ClawRoute addon retired; select L3 or L4 for Spartan Route, and clear SPARTAN_ADDONS after reviewing your configuration.\n' >&2
                return 1
                ;;
            none|"")
                ;;
            *)
                return 1
                ;;
        esac
    done
    printf '%s\n' "$out"
}

default_addons() {
    case "$TIER" in
        L3) printf '\n' ;;
        *) printf '\n' ;;
    esac
}

has_addon() {
    local wanted="$1"
    [[ " $ADDONS " == *" $wanted "* ]]
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --tier)
            [[ "$#" -ge 2 ]] || die "--tier requires a value"
            TIER="$(normalize_tier "$2")" || die "invalid tier: $2"
            shift 2
            ;;
        --with)
            [[ "$#" -ge 2 ]] || die "--with requires a value"
            ADDONS="$(normalize_addons "${ADDONS:+$ADDONS }$2")" || die "invalid addon: $2"
            shift 2
            ;;
        --addons)
            [[ "$#" -ge 2 ]] || die "--addons requires a value"
            ADDONS="$(normalize_addons "$2")" || die "invalid addons: $2"
            shift 2
            ;;
        --hermes)
            [[ "$#" -ge 2 ]] || die "--hermes requires a value"
            HERMES_MODE="$(normalize_hermes_mode "$2")" || die "invalid Hermes mode: $2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "$TIER" ]] || die "missing --tier L0|L1|L2|L3|L4"
[[ -n "$ADDONS" ]] || ADDONS="$(default_addons)"
if [[ "$TIER" == "L3" ]]; then
    ADDONS="$(default_addons)"
fi
[[ -n "$HERMES_MODE" ]] || HERMES_MODE="$(default_hermes_mode)"

tier_lower="$(printf '%s' "$TIER" | tr '[:upper:]' '[:lower:]')"
private_dir="${SPARTAN_INSTALL_PRIVATE_DIR:-$ROOT/private}"
env_file="${SPARTAN_ENV_FILE:-$private_dir/env/local.env}"
data_root="${SPARTAN_INSTALL_DATA_ROOT:-$private_dir/data/current}"
state_file="$private_dir/install-state.json"
private_compose_file="$private_dir/compose.local.yml"
private_whitelist_file="$private_dir/outbound-proxy/whitelist.private.txt"
private_codex_home="$private_dir/codex-home"

compose_args_for_tier() {
    case "$TIER" in
        L0|L1|L2) printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.${tier_lower}.yml" ;;
        L3) printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.l2.yml" -f "$ROOT/infra/compose/tiers/compose.l3.yml" ;;
        L4) printf '%s\n' -f "$ROOT/infra/compose/compose.yml" -f "$private_compose_file" ;;
    esac
}

detect_platform() {
    local kernel os
    if [[ -n "${SPARTAN_INSTALL_PLATFORM:-}" ]]; then
        printf '%s\n' "$SPARTAN_INSTALL_PLATFORM"
        return
    fi
    os="$(uname -s 2>/dev/null || printf unknown)"
    if [[ "$os" == "Linux" ]] && [[ -r /proc/version ]]; then
        kernel="$(tr '[:upper:]' '[:lower:]' < /proc/version)"
        if [[ "$kernel" == *microsoft* || "$kernel" == *wsl* ]]; then
            printf 'wsl\n'
            return
        fi
    fi
    case "$os" in
        Darwin) printf 'macos\n' ;;
        Linux) printf 'linux\n' ;;
        *) printf 'unknown\n' ;;
    esac
}

require_docker() {
    command -v docker >/dev/null 2>&1 || die "Docker CLI not found"
    docker compose version >/dev/null 2>&1 || die "Docker Compose plugin not available"
    docker info >/dev/null 2>&1 || die "Docker daemon is not reachable"
}

env_value() {
    local key="$1"
    [[ -f "$env_file" ]] || return 1
    awk -F= -v key="$key" '
        $1 == key {
            value = substr($0, index($0, "=") + 1)
        }
        END {
            gsub(/^'\''|'\''$/, "", value)
            gsub(/^"|"$/, "", value)
            if (value != "") print value
        }
    ' "$env_file"
}

random_hex() {
    local bytes="${1:-32}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        LC_ALL=C tr -dc 'A-Fa-f0-9' < /dev/urandom | head -c "$((bytes * 2))"
        printf '\n'
    fi
}

get_or_generate() {
    local key="$1"
    local bytes="${2:-32}"
    local current
    current="$(env_value "$key" || true)"
    if [[ -n "$current" && "$current" != change-me* && "$current" != "/absolute/path"* && "$current" != "100.x.y.z" ]]; then
        printf '%s\n' "$current"
    else
        random_hex "$bytes"
    fi
}

get_or_generate_edge_token() {
    local key="$1"
    local current
    current="$(env_value "$key" || true)"
    if [[ "$current" =~ ^[0-9a-f]{64}$ ]]; then
        printf "%s\n" "$current"
    else
        random_hex 32
    fi
}

caddy_hash_password() {
    local existing password
    existing="$(env_value CADDY_AUTH_HASH || true)"
    if [[ -n "$existing" && "$existing" != *UMuSzA3WdVqcAwE3yvFeCO* ]]; then
        printf '%s\n' "$existing"
        return
    fi
    password="$1"
    docker run --rm caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 caddy hash-password --plaintext "$password"
}

quote_env() {
    local value="$1"
    printf "'%s'" "$value"
}

write_env_file() {
    local tmp managed_keys key caddy_password caddy_hash camofox_edge_token websocket_edge_token camofox_disable_default_addons
    mkdir -p "$(dirname "$env_file")"
    [[ -f "$env_file" ]] || : > "$env_file"

    managed_keys=(
        SPARTAN_TIER SPARTAN_ADDONS SPARTAN_HERMES_MODE SPARTAN_PRIVATE_WHITELIST_PATH SPARTAN_CADDY_LOCAL_DIR
        COMPOSE_PROJECT_NAME COMPOSE_PROFILES
        SPARTAN_BIND_LOCALHOST SPARTAN_GATE_PORT CLAWROUTE_EDGE_PORT
        BROWSERLESS_DEBUG_PORT HERMES_DASHBOARD_PORT HERMES_API_PORT
        CAMOFOX_API_PORT CAMOFOX_NOVNC_PORT SPARTAN_INTERNAL_SUBNET
        SPARTAN_DNS_IP SPARTAN_ALLOW_EXISTING_STACK_PATHS
        SPARTAN_HERMES_DATA_PATH SPARTAN_CLAWROUTE_DATA_PATH SPARTAN_ROUTE_DATA_PATH
        SPARTAN_GOGCLI_DATA_PATH SPARTAN_BROWSERLESS_PROFILES_PATH
        SPARTAN_CAMOFOX_DATA_PATH SPARTAN_CODEX_HOME_PATH
        HERMES_UID HERMES_GID BROWSERLESS_PROFILES_GID
        SPARTAN_CADDY_UID SPARTAN_CADDY_GID
        SPARTAN_ROUTE_UID SPARTAN_ROUTE_GID
        SPARTAN_CLAWROUTE_UID SPARTAN_CLAWROUTE_GID
        SPARTAN_HERMES_RUN_UID SPARTAN_HERMES_RUN_GID
        HERMES_API_KEY API_SERVER_KEY HERMES_GATEWAY_TOKEN
        HERMES_DASHBOARD_BASIC_AUTH_USERNAME
        HERMES_DASHBOARD_BASIC_AUTH_PASSWORD
        HERMES_DASHBOARD_BASIC_AUTH_SECRET
        CLAWROUTE_TOKEN SPARTAN_REASONING_BINDING_SECRET BROWSERLESS_TOKEN BROWSERLESS_EDGE_TOKEN CADDY_CAMOFOX_EDGE_TOKEN CADDY_WS_EDGE_TOKEN CADDY_AUTH_USER CADDY_AUTH_HASH
        SPARTAN_CADDY_PASSWORD CADDY_SESSION_SERVICE_KEY
        CLOUDFLARE_API_TOKEN
        CAMOFOX_URL CAMOFOX_API_KEY CAMOFOX_ACCESS_KEY CAMOFOX_USER_ID
        CAMOFOX_SESSION_KEY CAMOFOX_ADOPT_EXISTING_TAB CAMOFOX_DISABLE_DEFAULT_ADDONS CAMOFOX_ENABLE_VNC
        CAMOFOX_VNC_PASSWORD CAMOFOX_IMAGE CAMOFOX_BASE_IMAGE
        BROWSERLESS_IMAGE BROWSERLESS_BASE_IMAGE HERMES_IMAGE
    )

    tmp="$(mktemp)"
    awk -F= -v keys="$(printf '%s\n' "${managed_keys[@]}")" '
        BEGIN {
            split(keys, key_list, "\n")
            for (i in key_list) managed[key_list[i]] = 1
        }
        /^[A-Za-z_][A-Za-z0-9_]*=/ {
            if ($1 in managed || $1 ~ /^BROWSERLESS_/ || $1 ~ /^BROWSER_CDP_/ || $1 ~ /^HERMES_MEET_CDP_/) next
        }
        { print }
    ' "$env_file" > "$tmp"

    append_env() {
        local key="$1"
        local value="$2"
        printf '%s=%s\n' "$key" "$(quote_env "$value")" >> "$tmp"
    }

    append_env SPARTAN_TIER "$TIER"
    append_env SPARTAN_ADDONS "$ADDONS"
    append_env SPARTAN_HERMES_MODE "$HERMES_MODE"
    append_env COMPOSE_PROJECT_NAME "spartan-gate"
    if [[ "$TIER" != "L0" ]]; then
        append_env COMPOSE_PROFILES "camofox"
    else
        append_env COMPOSE_PROFILES ""
    fi
    append_env SPARTAN_BIND_LOCALHOST "127.0.0.1"
    append_env SPARTAN_GATE_PORT "${SPARTAN_GATE_PORT:-18789}"
    append_env CLAWROUTE_EDGE_PORT "${CLAWROUTE_EDGE_PORT:-18790}"
    append_env HERMES_DASHBOARD_PORT "${HERMES_DASHBOARD_PORT:-9119}"
    append_env HERMES_API_PORT "${HERMES_API_PORT:-8642}"
    append_env CAMOFOX_API_PORT "${CAMOFOX_API_PORT:-9377}"
    append_env CAMOFOX_NOVNC_PORT "${CAMOFOX_NOVNC_PORT:-26080}"
    append_env SPARTAN_INTERNAL_SUBNET "${SPARTAN_INTERNAL_SUBNET:-172.28.0.0/24}"
    append_env SPARTAN_DNS_IP "${SPARTAN_DNS_IP:-172.28.0.253}"
    append_env SPARTAN_ALLOW_EXISTING_STACK_PATHS "false"
    for key in SPARTAN_HERMES_DATA_PATH SPARTAN_ROUTE_DATA_PATH SPARTAN_GOGCLI_DATA_PATH SPARTAN_CAMOFOX_DATA_PATH SPARTAN_CODEX_HOME_PATH; do
        current_path="$(env_value "$key" || true)"
        if [[ -z "$current_path" ]]; then
            case "$key" in
                SPARTAN_HERMES_DATA_PATH) current_path="$data_root/hermes" ;;
                SPARTAN_ROUTE_DATA_PATH) current_path="$data_root/spartan-route" ;;
                SPARTAN_GOGCLI_DATA_PATH) current_path="$data_root/gogcli" ;;
                SPARTAN_CAMOFOX_DATA_PATH) current_path="$data_root/camofox" ;;
                SPARTAN_CODEX_HOME_PATH) current_path="$private_codex_home" ;;
            esac
        fi
        append_env "$key" "$current_path"
    done
    append_env SPARTAN_PRIVATE_WHITELIST_PATH "$private_whitelist_file"
    append_env SPARTAN_CADDY_LOCAL_DIR "$private_dir/caddy.local.d"
    append_env SPARTAN_CADDY_UID "$(id -u)"
    append_env SPARTAN_CADDY_GID "$(id -g)"
    append_env SPARTAN_ROUTE_UID "$(id -u)"
    append_env SPARTAN_ROUTE_GID "$(id -g)"
    append_env HERMES_UID "$(id -u)"
    append_env HERMES_GID "$(id -g)"
    append_env SPARTAN_HERMES_RUN_UID "$(id -u)"
    append_env SPARTAN_HERMES_RUN_GID "$(id -g)"
    append_env HERMES_API_KEY "$(get_or_generate HERMES_API_KEY 32)"
    append_env API_SERVER_KEY "$(get_or_generate API_SERVER_KEY 32)"
    append_env HERMES_GATEWAY_TOKEN "$(get_or_generate HERMES_GATEWAY_TOKEN 32)"
    append_env HERMES_DASHBOARD_BASIC_AUTH_USERNAME "admin"
    append_env HERMES_DASHBOARD_BASIC_AUTH_PASSWORD "$(get_or_generate HERMES_DASHBOARD_BASIC_AUTH_PASSWORD 24)"
    append_env HERMES_DASHBOARD_BASIC_AUTH_SECRET "$(get_or_generate HERMES_DASHBOARD_BASIC_AUTH_SECRET 32)"
    append_env CLAWROUTE_TOKEN "$(get_or_generate CLAWROUTE_TOKEN 32)"
    append_env SPARTAN_REASONING_BINDING_SECRET "$(get_or_generate SPARTAN_REASONING_BINDING_SECRET 32)"
    camofox_edge_token="$(get_or_generate_edge_token CADDY_CAMOFOX_EDGE_TOKEN)"
    websocket_edge_token="$(get_or_generate_edge_token CADDY_WS_EDGE_TOKEN)"
    if [[ "$camofox_edge_token" == "$websocket_edge_token" ]]; then
        camofox_edge_token="$(random_hex 32)"
    fi
    append_env CADDY_CAMOFOX_EDGE_TOKEN "$camofox_edge_token"
    append_env CADDY_WS_EDGE_TOKEN "$websocket_edge_token"
    service_key="$(get_or_generate_edge_token CADDY_SESSION_SERVICE_KEY)"
    while [[ "$service_key" == "$websocket_edge_token" || "$service_key" == "$camofox_edge_token" ]]; do service_key="$(random_hex 32)"; done
    append_env CADDY_SESSION_SERVICE_KEY "$service_key"
    append_env CADDY_AUTH_USER "admin"
    caddy_password="$(env_value SPARTAN_CADDY_PASSWORD || true)"
    caddy_hash="$(env_value CADDY_AUTH_HASH || true)"
    if [[ -n "$caddy_password" && "$caddy_password" != change-me* ]]; then
        append_env SPARTAN_CADDY_PASSWORD "$caddy_password"
        append_env CADDY_AUTH_HASH "$(caddy_hash_password "$caddy_password")"
    elif [[ -n "$caddy_hash" && "$caddy_hash" != *UMuSzA3WdVqcAwE3yvFeCO* ]]; then
        append_env CADDY_AUTH_HASH "$caddy_hash"
    else
        caddy_password="$(random_hex 24)"
        append_env SPARTAN_CADDY_PASSWORD "$caddy_password"
        append_env CADDY_AUTH_HASH "$(caddy_hash_password "$caddy_password")"
    fi
    if [[ "$TIER" == "L0" ]]; then
        append_env CAMOFOX_URL ""
    else
        append_env CAMOFOX_URL "http://camofox:9377"
    fi
    append_env CLOUDFLARE_API_TOKEN "$(env_value CLOUDFLARE_API_TOKEN || true)"
    append_env CAMOFOX_API_KEY "$(get_or_generate CAMOFOX_API_KEY 32)"
    append_env CAMOFOX_ACCESS_KEY "$(get_or_generate CAMOFOX_ACCESS_KEY 32)"
    append_env CAMOFOX_USER_ID ""
    append_env CAMOFOX_SESSION_KEY ""
    append_env CAMOFOX_ADOPT_EXISTING_TAB "false"
    camofox_disable_default_addons="$(env_value CAMOFOX_DISABLE_DEFAULT_ADDONS || true)"
    case "$camofox_disable_default_addons" in
        1|true) ;;
        *) camofox_disable_default_addons="false" ;;
    esac
    append_env CAMOFOX_DISABLE_DEFAULT_ADDONS "$camofox_disable_default_addons"
    append_env CAMOFOX_ENABLE_VNC "1"
    append_env CAMOFOX_VNC_PASSWORD "$(get_or_generate CAMOFOX_VNC_PASSWORD 24)"
    append_env CAMOFOX_IMAGE "spartan-gate-camofox:1.15.0"
    append_env CAMOFOX_BASE_IMAGE "ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae"
    append_env HERMES_IMAGE "spartan-gate-hermes:latest"

    mv "$tmp" "$env_file"
    chmod 600 "$env_file"
}

write_private_compose() {
    [[ "$TIER" == "L4" ]] || return 0
    [[ -f "$private_compose_file" ]] && return 0

    cat > "$private_compose_file" <<'EOF'
# Generated by scripts/install.sh. Keep local/private values here.

services:
  proxy:
    volumes:
      - ${SPARTAN_PRIVATE_WHITELIST_PATH}:/etc/tinyproxy/whitelist.private.txt:ro

  dns:
    volumes:
      - ${SPARTAN_PRIVATE_WHITELIST_PATH}:/etc/dns-whitelist.private.txt:ro

  camofox:
    volumes:
      - ${SPARTAN_CAMOFOX_DATA_PATH:-../../runtime/camofox}:/root/.camofox:Z

  caddy:
    ports:
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${SPARTAN_GATE_PORT:-18789}:18789"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${CLAWROUTE_EDGE_PORT:-18790}:18790"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${HERMES_DASHBOARD_PORT:-9119}:9119"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${CAMOFOX_NOVNC_PORT:-26080}:26080"
      # scripts/add-port.sh inserts below this line.
    volumes:
      - ${SPARTAN_CADDY_LOCAL_DIR}:/etc/caddy/local.d:ro

  hermes:
    volumes:
      - ${SPARTAN_HERMES_DATA_PATH:-../../runtime/hermes}:/opt/data:Z
      - ${SPARTAN_GOGCLI_DATA_PATH:-../../runtime/gogcli}:/opt/data/.config/gogcli:Z
      - ${SPARTAN_PRIVATE_WHITELIST_PATH}:/etc/proxy-whitelist.private.txt:ro

  spartan-route:
    volumes:
      - ${SPARTAN_ROUTE_DATA_PATH:-../../runtime/spartan-route}:/app/data:Z
      - ${SPARTAN_CODEX_HOME_PATH:-../../private/codex-home}:/codex-home:ro
EOF
}

compose() {
    local args=()
    while IFS= read -r item; do
        args+=("$item")
    done < <(compose_args_for_tier)
    docker compose "${args[@]}" --env-file "$env_file" "$@"
}

write_state() {
    mkdir -p "$private_dir"
    cat > "$state_file" <<EOF
{
  "tier": "$TIER",
  "addons": "$ADDONS",
  "hermesMode": "$HERMES_MODE",
  "platform": "$(detect_platform)",
  "envFile": "$env_file"
}
EOF
}

require_docker
if [[ -f "$env_file" ]]; then
    normalize_addons "$(env_value SPARTAN_ADDONS || true)" >/dev/null || die "persisted addon retired; clear SPARTAN_ADDONS and select L3/L4"
fi
mkdir -p "$(dirname "$env_file")"
[[ ! -L "${env_file}.config.lock" ]] || die "unsafe configuration lock"
exec 9>"${env_file}.config.lock"
flock -x -w 30 9 || die "configuration busy"
# Render an unpublished env candidate before changing the selected configuration.
final_env_file="$env_file"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
if [[ -f "$env_file" ]]; then
    normalize_addons "$(env_value SPARTAN_ADDONS || true)" >/dev/null || die "persisted addon retired; clear SPARTAN_ADDONS and select L3/L4"
    cp -p "$env_file" "$stage/local.env"
fi
env_file="$stage/local.env"
original_compose_file="$private_compose_file"
if [[ ! -f "$private_compose_file" && "$TIER" == L4 ]]; then
    private_compose_file="$stage/compose.local.yml"
    write_private_compose
fi
write_env_file
compose config --quiet
private_compose_file="$original_compose_file"
# Preserve previous configuration until publication and permission checks finish.
final_state_file="$state_file"
final_compose_file="$private_compose_file"
for name in env state compose; do
    case "$name" in env) target="$final_env_file" ;; state) target="$state_file" ;; compose) target="$private_compose_file" ;; esac
    [[ ! -L "$target" && ( ! -e "$target" || -f "$target" ) ]] || die "unsafe configuration target"
    [[ ! -f "$target" ]] || cp -p "$target" "$stage/backup-$name"
done
rollback() {
    for name in env state compose; do
        case "$name" in env) target="$final_env_file" ;; state) target="$final_state_file" ;; compose) target="$final_compose_file" ;; esac
        if [[ -f "$stage/backup-$name" ]]; then cp -p "$stage/backup-$name" "$target"; else rm -f "$target"; fi
    done
    rm -rf "$stage"
}
trap rollback EXIT
mkdir -p "$(dirname "$final_env_file")" "$private_codex_home" "$data_root/hermes" "$data_root/camofox" "$data_root/spartan-route" "$data_root/gogcli" "$private_dir/caddy.local.d" "$(dirname "$private_whitelist_file")"
[[ -f "$private_whitelist_file" ]] || : > "$private_whitelist_file"
cp "$env_file" "$final_env_file"
env_file="$final_env_file"
write_private_compose
write_state
python3 "$ROOT/scripts/private-permissions.py" fix --private-dir "$private_dir" --env-file "$env_file"
if [[ "$TIER" == "L3" || "$TIER" == "L4" ]]; then
    route_data_path="$(env_value SPARTAN_ROUTE_DATA_PATH)"
    route_uid="$(env_value SPARTAN_ROUTE_UID)"
    route_gid="$(env_value SPARTAN_ROUTE_GID)"
    python3 "$ROOT/scripts/spartan-route-data.py" prepare \
        --data-dir "$route_data_path" --uid "$route_uid" --gid "$route_gid" \
        || die "legacy Spartan Route SQLite state requires sg-spartan-route-data-migrate before L4 can start"
fi

trap 'rm -rf "$stage"' EXIT
printf 'Spartan Gate tier %s configured for %s.\n' "$TIER" "$(detect_platform)"
printf 'Env file: %s\n' "$env_file"
printf 'Running Hermes setup, then starting the selected tier.\n'

compose config --quiet
compose run --rm --no-deps hermes setup
compose up -d --remove-orphans
compose up -d --force-recreate hermes

printf 'Spartan Gate %s is installed. Run `source scripts/aliases.sh` for helpers.\n' "$TIER"
