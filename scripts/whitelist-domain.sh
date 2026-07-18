#!/usr/bin/env bash
# Add one or more hostnames to the private outbound whitelist extension.
#
# Usage:
#   scripts/whitelist-domain.sh example.com,api.example.com https://docs.example.com/path
#   scripts/whitelist-domain.sh 15d example.com
#   scripts/whitelist-domain.sh --no-reload example.com
#
# By default this hot-reloads running proxy/dns services so generated whitelist
# files are rebuilt from the public + private fragments without container kills.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_TTL_SECONDS=$((365 * 24 * 60 * 60))

_default_env_file() {
    if [[ -n "${SPARTAN_ENV_FILE:-}" ]]; then
        printf '%s\n' "$SPARTAN_ENV_FILE"
    elif [[ -f "$ROOT/private/env/local.env" ]]; then
        printf '%s\n' "$ROOT/private/env/local.env"
    else
        printf '%s\n' "$ROOT/private/env/local.env"
    fi
}
WHITELIST="${SPARTAN_WHITELIST_FILE:-$ROOT/private/outbound-proxy/whitelist.private.txt}"

usage() {
    printf 'Usage: %s [--no-reload|--no-restart] [--recreate-on-reload-failure] [15m|6h|15d] domain.com[,api.example.com] [more-hosts]\n' "$0" >&2
}

trim_spaces() {
    printf '%s' "$1" | tr -d '[:space:]'
}

normalize_host() {
    local value
    value="$(trim_spaces "$1")"
    value="${value,,}"
    value="${value#http://}"
    value="${value#https://}"
    value="${value#//}"
    value="${value%%/*}"
    value="${value%%\?*}"
    value="${value%%\#*}"
    if [[ "$value" == \[* || "$value" == *:*:* ]]; then
        return 1
    fi
    if [[ "$value" == *:* ]]; then
        [[ "${value##*:}" =~ ^[0-9]+$ ]] || return 1
        value="${value%%:*}"
    fi
    value="${value%.}"
    printf '%s\n' "$value"
}

valid_ipv4() {
    local host="$1"
    local -a octets
    local octet

    IFS='.' read -r -a octets <<< "$host"
    [[ "${#octets[@]}" -eq 4 ]] || return 1
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
        ((10#$octet <= 255)) || return 1
    done
}

valid_host() {
    local host="$1"
    [[ -n "$host" ]] || return 1
    [[ "$host" != *' '* ]] || return 1
    [[ "$host" != *'/'* ]] || return 1
    if [[ "$host" =~ ^[0-9.]+$ ]]; then
        valid_ipv4 "$host"
        return
    fi
    [[ ${#host} -le 253 ]] || return 1
    [[ "$host" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

now_epoch() {
    if [[ -n "${SPARTAN_WHITELIST_NOW:-}" ]]; then
        printf '%s\n' "$SPARTAN_WHITELIST_NOW"
    else
        date +%s
    fi
}

parse_ttl_seconds() {
    local ttl="$1"
    local amount unit multiplier seconds

    [[ "$ttl" =~ ^([1-9][0-9]*)([mhd])$ ]] || return 1
    amount="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    [[ ${#amount} -le 8 ]] || return 1

    case "$unit" in
        m) multiplier=60 ;;
        h) multiplier=$((60 * 60)) ;;
        d) multiplier=$((24 * 60 * 60)) ;;
        *) return 1 ;;
    esac
    seconds=$((10#$amount * multiplier))
    ((seconds <= MAX_TTL_SECONDS)) || return 1
    printf '%s\n' "$seconds"
}

duration_like() {
    [[ "$1" =~ ^-?[0-9]+(\.[0-9]+)?[[:alpha:]]+$ ]] || [[ "$1" =~ ^[[:alpha:]]+[0-9]+$ ]]
}

line_domain() {
    normalize_host "${1%%#*}" || true
}

line_expiry() {
    local line="$1"
    if [[ "$line" =~ (^|[[:space:]])sg-expires-at=([0-9]+)($|[[:space:]]) ]]; then
        printf '%s\n' "${BASH_REMATCH[2]}"
    fi
}

format_expiry() {
    local epoch="$1"
    if date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' >/dev/null 2>&1; then
        date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ'
    else
        printf '%s\n' "$epoch"
    fi
}

compose_cmd() {
    local args=(-f "$ROOT/infra/compose/compose.yml")
    local private_compose="${SPARTAN_PRIVATE_COMPOSE:-$ROOT/private/compose.local.yml}"
    local env_file
    env_file="$(_default_env_file)"

    if [[ -f "$private_compose" ]]; then
        args+=(-f "$private_compose")
    fi

    if [[ -f "$env_file" ]]; then
        args+=(--env-file "$env_file")
    elif [[ -f "$ROOT/.env" ]]; then
        args+=(--env-file "$ROOT/.env")
    fi

    docker compose "${args[@]}" "$@"
}

reload_consumers() {
    command -v docker >/dev/null 2>&1 || {
        printf '[whitelist] Docker is not available; reload proxy/dns manually if the stack is running.\n'
        return 1
    }

    local running service
    running="$(compose_cmd ps --services --status running 2>/dev/null || true)"

    local -a reload_services=()
    for service in proxy dns; do
        if printf '%s\n' "$running" | grep -Fxq "$service"; then
            reload_services+=("$service")
        fi
    done

    if [[ "${#reload_services[@]}" -eq 0 ]]; then
        printf '[whitelist] No running proxy/dns services found; nothing reloaded.\n'
        return 0
    fi

    printf '[whitelist] Hot-reloading running services: %s\n' "${reload_services[*]}"
    local -a failed_services=()
    local -a fallback_services=()
    for service in "${reload_services[@]}"; do
        if compose_cmd exec -T "$service" sh -c 'kill -USR1 1'; then
            printf '[whitelist] Reload signal delivered: %s\n' "$service"
        else
            failed_services+=("$service")
            if [[ "$recreate_on_reload_failure" == true ]]; then
                printf '[whitelist] Reload failed for %s; will recreate it as requested.\n' "$service" >&2
                fallback_services+=("$service")
            else
                printf '[whitelist] Reload failed for %s; existing runtime policy remains active.\n' "$service" >&2
            fi
        fi
    done

    if [[ "${#fallback_services[@]}" -gt 0 ]]; then
        printf '[whitelist] Recreating fallback services: %s\n' "${fallback_services[*]}"
        compose_cmd up -d --no-build --force-recreate --no-deps "${fallback_services[@]}"
    fi

    if [[ "${#failed_services[@]}" -gt 0 && "$recreate_on_reload_failure" != true ]]; then
        return 1
    fi
}

reload=true
recreate_on_reload_failure=false
case "${SPARTAN_WHITELIST_RELOAD_FALLBACK:-}" in
    "")
        ;;
    recreate)
        recreate_on_reload_failure=true
        ;;
    none|fail)
        ;;
    *)
        printf '[whitelist] Invalid SPARTAN_WHITELIST_RELOAD_FALLBACK: %s (expected recreate, none, or fail)\n' "$SPARTAN_WHITELIST_RELOAD_FALLBACK" >&2
        exit 1
        ;;
esac
declare -a inputs=()
for arg in "$@"; do
    case "$arg" in
        --no-reload|--no-restart)
            reload=false
            ;;
        --recreate-on-reload-failure)
            recreate_on_reload_failure=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            inputs+=("$arg")
            ;;
    esac
done

if [[ "${#inputs[@]}" -eq 0 ]]; then
    usage
    exit 1
fi

ttl=""
if parse_ttl_seconds "${inputs[0]}" >/dev/null 2>&1; then
    ttl="${inputs[0]}"
    inputs=("${inputs[@]:1}")
elif duration_like "${inputs[0]}"; then
    printf '[whitelist] Invalid duration: %s (expected 15m, 6h or 15d)\n' "${inputs[0]}" >&2
    exit 1
fi

if [[ "${#inputs[@]}" -eq 0 ]]; then
    usage
    exit 1
fi

expires_at=""
if [[ -n "$ttl" ]]; then
    ttl_seconds="$(parse_ttl_seconds "$ttl")"
    now="$(now_epoch)"
    [[ "$now" =~ ^[0-9]{1,10}$ ]] || {
        printf '[whitelist] Invalid current epoch: %s\n' "$now" >&2
        exit 1
    }
    expires_at="$((10#$now + ttl_seconds))"
fi

mkdir -p "$(dirname "$WHITELIST")"
exec 9>"${WHITELIST}.lock"
flock -x 9

if [[ ! -f "$WHITELIST" ]]; then
    {
        printf '# Private outbound whitelist extension.\n'
        printf '# This file is merged with infra/outbound-proxy/whitelist.txt at container startup.\n'
    } > "$WHITELIST"
    printf '[whitelist] Created private whitelist file: %s\n' "$WHITELIST"
fi

declare -A seen=()
declare -a domains=()

for arg in "${inputs[@]}"; do
    IFS=',' read -r -a raw_values <<< "$arg"
    for raw in "${raw_values[@]}"; do
        if [[ "$raw" == *:*:* || "$raw" == *'['* || "$raw" == *']'* ]]; then
            printf '[whitelist] IPv6 addresses are not supported: %s\n' "$raw" >&2
            continue
        fi
        domain="$(normalize_host "$raw" || true)"
        if ! valid_host "$domain"; then
            printf '[whitelist] Skipping invalid hostname: %s\n' "$raw" >&2
            continue
        fi
        [[ -n "${seen[$domain]:-}" ]] && continue
        seen[$domain]=1
        domains+=("$domain")
    done
done

if [[ "${#domains[@]}" -eq 0 ]]; then
    printf '[whitelist] No valid hostnames found.\n' >&2
    exit 1
fi

declare -A requested=()
declare -A states=()
declare -a whitelist_lines=()
for domain in "${domains[@]}"; do
    requested[$domain]=1
    states[$domain]="missing"
done

while IFS= read -r line || [[ -n "$line" ]]; do
    whitelist_lines+=("$line")
    existing="$(line_domain "$line")"
    [[ -n "$existing" && -n "${requested[$existing]:-}" ]] || continue

    expiry="$(line_expiry "$line")"
    if [[ "$line" == *sg-expires-at=* && -z "$expiry" ]]; then
        if [[ "${states[$existing]}" != "permanent" ]]; then
            states[$existing]="temporary"
        fi
        # Malformed expiry markers are inactive at runtime; treat them as replaceable.
    elif [[ -z "$expiry" ]]; then
        states[$existing]="permanent"
    elif [[ "${states[$existing]}" != "permanent" ]]; then
        states[$existing]="temporary"
    fi
done < "$WHITELIST"

declare -A replacements=()
declare -A status_messages=()
for domain in "${domains[@]}"; do
    state="${states[$domain]}"
    if [[ -z "$ttl" ]]; then
        case "$state" in
            permanent)
                status_messages[$domain]="Already permanent: $domain"
                ;;
            temporary)
                replacements[$domain]="$domain"
                status_messages[$domain]="Promoted to permanent: $domain"
                ;;
            *)
                replacements[$domain]="$domain"
                status_messages[$domain]="Added permanent: $domain"
                ;;
        esac
    else
        case "$state" in
            permanent)
                status_messages[$domain]="Already permanent: $domain"
                ;;
            temporary)
                replacements[$domain]="$domain # sg-expires-at=$expires_at sg-ttl=$ttl"
                status_messages[$domain]="Updated temporary: $domain expires at $(format_expiry "$expires_at")"
                ;;
            *)
                replacements[$domain]="$domain # sg-expires-at=$expires_at sg-ttl=$ttl"
                status_messages[$domain]="Added temporary: $domain expires at $(format_expiry "$expires_at")"
                ;;
        esac
    fi
done

if [[ "${#replacements[@]}" -gt 0 ]]; then
    tmp="$(mktemp "${WHITELIST}.tmp.XXXXXX")"
    declare -A wrote=()

    for line in "${whitelist_lines[@]}"; do
        existing="$(line_domain "$line")"
        if [[ -n "$existing" && -n "${replacements[$existing]:-}" ]]; then
            if [[ -z "${wrote[$existing]:-}" ]]; then
                printf '%s\n' "${replacements[$existing]}" >> "$tmp"
                wrote[$existing]=1
            fi
            continue
        fi
        printf '%s\n' "$line" >> "$tmp"
    done

    for domain in "${domains[@]}"; do
        if [[ -n "${replacements[$domain]:-}" && -z "${wrote[$domain]:-}" ]]; then
            printf '%s\n' "${replacements[$domain]}" >> "$tmp"
        fi
    done

    mv "$tmp" "$WHITELIST"
fi

for domain in "${domains[@]}"; do
    printf '[whitelist] %s\n' "${status_messages[$domain]}"
done

if [[ "$reload" == true ]]; then
    reload_consumers
fi
