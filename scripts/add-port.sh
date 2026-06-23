#!/usr/bin/env bash
# Expose a Hermes app port through the private Caddy override.
#
# Usage:
#   scripts/add-port.sh 8787
#   scripts/add-port.sh --tailscale 8787 28787
#   scripts/add-port.sh --localhost 8787

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

default_env_file() {
    if [[ -n "${SPARTAN_ENV_FILE:-}" ]]; then
        printf '%s\n' "$SPARTAN_ENV_FILE"
    elif [[ -f "$ROOT/private/env/local.env" ]]; then
        printf '%s\n' "$ROOT/private/env/local.env"
    elif [[ -f "$ROOT/.env" ]]; then
        printf '%s\n' "$ROOT/.env"
    else
        printf '%s\n' "$ROOT/private/env/local.env"
    fi
}

PRIVATE_COMPOSE="${SPARTAN_PRIVATE_COMPOSE:-$ROOT/private/compose.local.yml}"
CADDY_LOCAL_DIR="${SPARTAN_CADDY_LOCAL_DIR:-$ROOT/private/caddy.local.d}"
CADDY_PORTS_FILE="$CADDY_LOCAL_DIR/ports.caddy"
ENV_FILE="$(default_env_file)"

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

usage() {
    cat >&2 <<'EOF'
Usage: scripts/add-port.sh [--localhost|--tailscale] <container-port> [host-port]

Default bind is localhost. Use --tailscale to bind to ${TAILSCALE_IP}.
If SPARTAN_EDGE_SCHEME=https, generated Caddy listeners include the manual TLS cert paths.
The script writes only ignored private files and restarts Caddy only if it is already running.
EOF
}

bind_mode=localhost
declare -a args=()
for arg in "$@"; do
    case "$arg" in
        --localhost)
            bind_mode=localhost
            ;;
        --tailscale)
            bind_mode=tailscale
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            args+=("$arg")
            ;;
    esac
done

if [[ "${#args[@]}" -lt 1 || "${#args[@]}" -gt 2 ]]; then
    usage
    exit 1
fi

container_port="${args[0]}"
host_port="${args[1]:-${args[0]}}"

for port in "$container_port" "$host_port"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
        printf '[add-port] Invalid port: %s\n' "$port" >&2
        exit 1
    fi
done

if [[ ! -f "$PRIVATE_COMPOSE" ]]; then
    mkdir -p "$(dirname "$PRIVATE_COMPOSE")"
    cp "$ROOT/private.example/compose.local.yml" "$PRIVATE_COMPOSE"
    printf '[add-port] Created private compose override: %s\n' "$PRIVATE_COMPOSE"
fi

mkdir -p "$CADDY_LOCAL_DIR"
touch "$CADDY_PORTS_FILE"

marker="# spartan-gate:add-port:${container_port}"
edge_scheme="$(env_value SPARTAN_EDGE_SCHEME http)"
tls_line=""
if [[ "$edge_scheme" == "https" ]]; then
    tls_line=$'\ttls {$SPARTAN_CADDY_TLS_CERT_FILE:/certs/cert.pem} {$SPARTAN_CADDY_TLS_KEY_FILE:/certs/key.pem}\n\n'
fi

if grep -Fq "$marker" "$CADDY_PORTS_FILE"; then
    printf '[add-port] Caddy listener already exists for container port %s\n' "$container_port"
else
    cat >> "$CADDY_PORTS_FILE" <<EOF

$marker
:${container_port} {
${tls_line}\tbasic_auth {
\t\t{\$CADDY_AUTH_USER:admin} {\$CADDY_AUTH_HASH}
\t}

\treverse_proxy http://hermes:${container_port}
}
EOF
    printf '[add-port] Added Caddy listener for container port %s\n' "$container_port"
fi

case "$bind_mode" in
    localhost)
        bind_expr='${SPARTAN_BIND_LOCALHOST:-127.0.0.1}'
        ;;
    tailscale)
        bind_expr='${TAILSCALE_IP}'
        ;;
esac

port_env="SPARTAN_APP_${container_port}_PORT"
mapping_marker="# spartan-gate:add-port:${container_port}"
mapping_line="      - \"${bind_expr}:\${${port_env}:-${host_port}}:${container_port}\""

if grep -Fq "$mapping_marker" "$PRIVATE_COMPOSE"; then
    printf '[add-port] Compose port mapping already exists for container port %s\n' "$container_port"
else
    tmp_file="$(mktemp)"
    awk -v marker="$mapping_marker" -v port_line="$mapping_line" '
        BEGIN { inserted = 0 }
        {
            print
            if ($0 ~ /scripts\/add-port\.sh inserts below this line\./ && inserted == 0) {
                print "      " marker
                print port_line
                inserted = 1
            }
        }
        END { if (inserted == 0) exit 1 }
    ' "$PRIVATE_COMPOSE" > "$tmp_file" || {
        rm -f "$tmp_file"
        printf '[add-port] Could not find the add-port insertion marker in %s\n' "$PRIVATE_COMPOSE" >&2
        exit 1
    }
    mv "$tmp_file" "$PRIVATE_COMPOSE"
    printf '[add-port] Added private Compose mapping: host %s -> hermes:%s\n' "$host_port" "$container_port"
fi

compose_cmd() {
    local compose_args=(-f "$ROOT/infra/compose/compose.yml" -f "$PRIVATE_COMPOSE")
    local env_file
    env_file="$(default_env_file)"
    if [[ -f "$env_file" ]]; then
        compose_args+=(--env-file "$env_file")
    elif [[ -f "$ROOT/.env" ]]; then
        compose_args+=(--env-file "$ROOT/.env")
    fi
    docker compose "${compose_args[@]}" "$@"
}

if command -v docker >/dev/null 2>&1; then
    if compose_cmd ps --services --status running 2>/dev/null | grep -Fxq caddy; then
        printf '[add-port] Restarting running Caddy service.\n'
        compose_cmd restart caddy
    else
        printf '[add-port] Caddy is not running; no service was started.\n'
    fi
fi

url_host="localhost"
if [[ "$bind_mode" == "tailscale" ]]; then
    if [[ "$edge_scheme" == "https" ]]; then
        url_host="$(env_value TAILSCALE_HOST "$(env_value TAILSCALE_IP localhost)")"
    else
        url_host="$(env_value TAILSCALE_IP localhost)"
    fi
fi
printf '[add-port] Done. Use %s://%s:%s or your private bind host.\n' "$edge_scheme" "$url_host" "$host_port"
