#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIER=""
ADDONS=""
HERMES_MODE=""

usage() {
    cat <<'EOF'
Usage: scripts/install.sh --tier L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]

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
                [[ " $out " == *" clawroute "* ]] || out="${out:+$out }clawroute"
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
        L3|L4) printf 'clawroute\n' ;;
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
if [[ "$TIER" == "L3" || "$TIER" == "L4" ]]; then
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
        L0)
            printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.l0.yml"
            has_addon clawroute && printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.clawroute.yml"
            ;;
        L1)
            printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.l1.yml"
            has_addon clawroute && printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.clawroute.yml"
            ;;
        L2)
            printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.l2.yml"
            has_addon clawroute && printf '%s\n' -f "$ROOT/infra/compose/tiers/compose.l3.yml"
            ;;
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

caddy_hash_password() {
    local existing password
    existing="$(env_value CADDY_AUTH_HASH || true)"
    if [[ -n "$existing" && "$existing" != *UMuSzA3WdVqcAwE3yvFeCO* ]]; then
        printf '%s\n' "$existing"
        return
    fi
    password="$1"
    docker run --rm caddy:2-alpine@sha256:86deaf5e3d3408a6ccec08fbb79989783dd26e206ae10bcf78a801dc8c9ab794 caddy hash-password --plaintext "$password"
}

quote_env() {
    local value="$1"
    printf "'%s'" "$value"
}

write_env_file() {
    local tmp managed_keys key caddy_password
    mkdir -p "$(dirname "$env_file")"
    mkdir -p "$data_root/hermes" "$data_root/camofox" "$data_root/clawroute" "$data_root/browserless-profiles" "$data_root/gogcli"
    [[ -f "$env_file" ]] || : > "$env_file"

    managed_keys=(
        SPARTAN_TIER SPARTAN_ADDONS SPARTAN_HERMES_MODE
        COMPOSE_PROJECT_NAME COMPOSE_PROFILES
        SPARTAN_BIND_LOCALHOST SPARTAN_GATE_PORT CLAWROUTE_EDGE_PORT
        BROWSERLESS_DEBUG_PORT HERMES_DASHBOARD_PORT HERMES_API_PORT
        CAMOFOX_API_PORT CAMOFOX_NOVNC_PORT SPARTAN_INTERNAL_SUBNET
        SPARTAN_DNS_IP SPARTAN_ALLOW_EXISTING_STACK_PATHS
        SPARTAN_HERMES_DATA_PATH SPARTAN_CLAWROUTE_DATA_PATH
        SPARTAN_GOGCLI_DATA_PATH SPARTAN_BROWSERLESS_PROFILES_PATH
        SPARTAN_CAMOFOX_DATA_PATH SPARTAN_CODEX_HOME_PATH
        HERMES_UID HERMES_GID
        SPARTAN_HERMES_RUN_UID SPARTAN_HERMES_RUN_GID
        HERMES_API_KEY API_SERVER_KEY HERMES_GATEWAY_TOKEN
        HERMES_DASHBOARD_BASIC_AUTH_USERNAME
        HERMES_DASHBOARD_BASIC_AUTH_PASSWORD
        HERMES_DASHBOARD_BASIC_AUTH_SECRET
        CLAWROUTE_TOKEN BROWSERLESS_TOKEN CADDY_AUTH_USER CADDY_AUTH_HASH
        SPARTAN_CADDY_PASSWORD
        CAMOFOX_URL CAMOFOX_API_KEY CAMOFOX_ACCESS_KEY CAMOFOX_USER_ID
        CAMOFOX_SESSION_KEY CAMOFOX_ADOPT_EXISTING_TAB CAMOFOX_ENABLE_VNC
        CAMOFOX_VNC_PASSWORD CAMOFOX_IMAGE CAMOFOX_BASE_IMAGE
        HERMES_IMAGE
    )

    tmp="$(mktemp)"
    awk -F= -v keys="$(printf '%s\n' "${managed_keys[@]}")" '
        BEGIN {
            split(keys, key_list, "\n")
            for (i in key_list) managed[key_list[i]] = 1
        }
        /^[A-Za-z_][A-Za-z0-9_]*=/ {
            if ($1 in managed) next
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
    if [[ "$TIER" == "L4" ]]; then
        append_env COMPOSE_PROFILES "camofox"
    else
        append_env COMPOSE_PROFILES ""
    fi
    append_env SPARTAN_BIND_LOCALHOST "127.0.0.1"
    append_env SPARTAN_GATE_PORT "${SPARTAN_GATE_PORT:-18789}"
    append_env CLAWROUTE_EDGE_PORT "${CLAWROUTE_EDGE_PORT:-18790}"
    append_env BROWSERLESS_DEBUG_PORT "${BROWSERLESS_DEBUG_PORT:-3005}"
    append_env HERMES_DASHBOARD_PORT "${HERMES_DASHBOARD_PORT:-9119}"
    append_env HERMES_API_PORT "${HERMES_API_PORT:-8642}"
    append_env CAMOFOX_API_PORT "${CAMOFOX_API_PORT:-9377}"
    append_env CAMOFOX_NOVNC_PORT "${CAMOFOX_NOVNC_PORT:-26080}"
    append_env SPARTAN_INTERNAL_SUBNET "${SPARTAN_INTERNAL_SUBNET:-172.28.0.0/24}"
    append_env SPARTAN_DNS_IP "${SPARTAN_DNS_IP:-172.28.0.253}"
    append_env SPARTAN_ALLOW_EXISTING_STACK_PATHS "false"
    append_env SPARTAN_HERMES_DATA_PATH "$data_root/hermes"
    append_env SPARTAN_CLAWROUTE_DATA_PATH "$data_root/clawroute"
    append_env SPARTAN_GOGCLI_DATA_PATH "$data_root/gogcli"
    append_env SPARTAN_BROWSERLESS_PROFILES_PATH "$data_root/browserless-profiles"
    append_env SPARTAN_CAMOFOX_DATA_PATH "$data_root/camofox"
    append_env SPARTAN_CODEX_HOME_PATH "$private_codex_home"
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
    append_env BROWSERLESS_TOKEN "$(get_or_generate BROWSERLESS_TOKEN 32)"
    append_env CADDY_AUTH_USER "admin"
    caddy_password="$(get_or_generate SPARTAN_CADDY_PASSWORD 24)"
    append_env SPARTAN_CADDY_PASSWORD "$caddy_password"
    if [[ "$TIER" == "L0" || "$TIER" == "L1" ]]; then
        append_env CADDY_AUTH_HASH "$(env_value CADDY_AUTH_HASH || printf '%s' '$2a$14$UMuSzA3WdVqcAwE3yvFeCO.CesMjqqGDVrxpL1s87FsWM/NRrADi2')"
    else
        append_env CADDY_AUTH_HASH "$(caddy_hash_password "$caddy_password")"
    fi
    if [[ "$TIER" == "L0" ]]; then
        append_env CAMOFOX_URL ""
    else
        append_env CAMOFOX_URL "http://camofox:9377"
    fi
    append_env CAMOFOX_API_KEY "$(get_or_generate CAMOFOX_API_KEY 32)"
    append_env CAMOFOX_ACCESS_KEY "$(get_or_generate CAMOFOX_ACCESS_KEY 32)"
    append_env CAMOFOX_USER_ID "spartan-camofox-main"
    append_env CAMOFOX_SESSION_KEY "manual-login"
    append_env CAMOFOX_ADOPT_EXISTING_TAB "true"
    append_env CAMOFOX_ENABLE_VNC "1"
    append_env CAMOFOX_VNC_PASSWORD "$(get_or_generate CAMOFOX_VNC_PASSWORD 24)"
    append_env CAMOFOX_IMAGE "spartan-gate-camofox:1.11.2"
    append_env CAMOFOX_BASE_IMAGE "ghcr.io/jo-inc/camofox-browser:1.11.2"
    append_env HERMES_IMAGE "spartan-gate-hermes:latest"

    mv "$tmp" "$env_file"
    chmod 600 "$env_file" 2>/dev/null || true
}

write_private_compose() {
    [[ "$TIER" == "L4" ]] || return 0
    mkdir -p "$(dirname "$private_whitelist_file")" "$private_dir/caddy.local.d" "$private_codex_home"
    [[ -f "$private_whitelist_file" ]] || : > "$private_whitelist_file"
    [[ -f "$private_compose_file" ]] && return 0

    cat > "$private_compose_file" <<'EOF'
# Generated by scripts/install.sh. Keep local/private values here.

services:
  proxy:
    volumes:
      - ../../private/outbound-proxy/whitelist.private.txt:/etc/tinyproxy/whitelist.private.txt:ro

  dns:
    volumes:
      - ../../private/outbound-proxy/whitelist.private.txt:/etc/dns-whitelist.private.txt:ro

  browserless:
    volumes:
      - ${SPARTAN_BROWSERLESS_PROFILES_PATH:-../../runtime/browserless/profiles}:/profiles:Z

  camofox:
    volumes:
      - ${SPARTAN_CAMOFOX_DATA_PATH:-../../runtime/camofox}:/root/.camofox:Z

  caddy:
    ports:
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${SPARTAN_GATE_PORT:-18789}:18789"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${CLAWROUTE_EDGE_PORT:-18790}:18790"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${BROWSERLESS_DEBUG_PORT:-3005}:3005"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${HERMES_DASHBOARD_PORT:-9119}:9119"
      - "${SPARTAN_BIND_LOCALHOST:-127.0.0.1}:${CAMOFOX_NOVNC_PORT:-26080}:26080"
    volumes:
      - ../../private/caddy.local.d:/etc/caddy/local.d:ro

  hermes:
    volumes:
      - ${SPARTAN_HERMES_DATA_PATH:-../../runtime/hermes}:/opt/data:Z
      - ${SPARTAN_GOGCLI_DATA_PATH:-../../runtime/gogcli}:/opt/data/.config/gogcli:Z
      - ${SPARTAN_BROWSERLESS_PROFILES_PATH:-../../runtime/browserless/profiles}:/profiles:Z
      - ../../private/outbound-proxy/whitelist.private.txt:/etc/proxy-whitelist.private.txt:ro

  clawroute:
    volumes:
      - ${SPARTAN_CLAWROUTE_DATA_PATH:-../../runtime/clawroute}:/app/data:Z
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
if has_addon clawroute; then
    mkdir -p "$private_codex_home"
fi
write_env_file
write_private_compose
write_state

printf 'Spartan Gate tier %s configured for %s.\n' "$TIER" "$(detect_platform)"
printf 'Env file: %s\n' "$env_file"
printf 'Running Hermes setup, then starting the selected tier.\n'

compose config --quiet
compose run --rm --no-deps hermes setup
compose up -d --remove-orphans
compose up -d --force-recreate hermes

printf 'Spartan Gate %s is installed. Run `source scripts/aliases.sh` for helpers.\n' "$TIER"
