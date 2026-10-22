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
The script validates candidates before publication. Apply the new mapping explicitly with sg up -d --no-deps --force-recreate caddy.
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

# Serialize cooperating config writers using one stable lock. Lock creation is
# not publication; no listener or selection changes before validation.
umask 077
mkdir -p "$(dirname "$ENV_FILE")"
[[ ! -L "${ENV_FILE}.config.lock" ]] || { printf '[add-port] Unsafe lock.\n' >&2; exit 1; }
exec 9>"${ENV_FILE}.config.lock"
flock -x -w 30 9 || { printf '[add-port] Configuration busy.\n' >&2; exit 1; }
# Build both outputs privately. An absent insertion marker or invalid config
# must not publish half a listener.
umask 077
original_compose="$PRIVATE_COMPOSE"
original_ports="$CADDY_PORTS_FILE"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
PRIVATE_COMPOSE="$stage/compose.local.yml"
CADDY_PORTS_FILE="$stage/ports.caddy"
if [[ -f "$original_compose" ]]; then
    cp -p "$original_compose" "$PRIVATE_COMPOSE"
else
    cp "$ROOT/private.example/compose.local.yml" "$PRIVATE_COMPOSE"
fi
if [[ -f "$original_ports" ]]; then cp -p "$original_ports" "$CADDY_PORTS_FILE"; else : > "$CADDY_PORTS_FILE"; fi

marker="# spartan-gate:add-port:${container_port}"
edge_scheme="$(env_value SPARTAN_EDGE_SCHEME http)"
tls_line=""
cookie_secure=""
edge_secure=no
if [[ "$edge_scheme" == "https" ]]; then
    tls_line=$'\ttls {$SPARTAN_CADDY_TLS_CERT_FILE:/certs/cert.pem} {$SPARTAN_CADDY_TLS_KEY_FILE:/certs/key.pem}\n\n'
    cookie_secure="; Secure"
    edge_secure=yes
fi

if grep -Fq "$marker" "$CADDY_PORTS_FILE"; then
    if ! awk -v marker="$marker" '
        $0 == marker { found = 1; in_block = 1; next }
        in_block && /^# spartan-gate:add-port:/ { exit }
        in_block && /uri \/verify\/apps/ { verify = 1 }
        in_block && /uri \/mint\/apps/ { mint = 1 }
        in_block && /header_up X-Edge-Service-Key/ { secret = 1 }
        END { exit !(found && verify && mint && secret) }
    ' "$CADDY_PORTS_FILE"; then
        printf '[add-port] Existing listener for container port %s uses legacy or incomplete Safari session authentication (signed server TTL required).\n' "$container_port" >&2
        printf '[add-port] Back up %s, remove only the %s block, then rerun this command to regenerate it safely.\n' \
            "$CADDY_PORTS_FILE" "$marker" >&2
        exit 1
    fi
    printf '[add-port] Caddy listener already exists for container port %s\n' "$container_port"
else
    cat >> "$CADDY_PORTS_FILE" <<EOF

$marker
:${container_port} {
${tls_line}	route {
	request_header -X-Edge-Valid
	request_header -X-Edge-Cookie
	forward_auth edge-sessions:9080 {
		uri /verify/apps
		header_up X-Edge-Service-Key {\$CADDY_SESSION_SERVICE_KEY}
		copy_headers X-Edge-Valid
	}
	@spartan_app_websocket {
		header Connection *Upgrade*
		header Upgrade websocket
	}

	@spartan_app_session_authorized {
		header X-Edge-Valid yes
	}

	@spartan_app_only_edge_cookies {
		header_regexp Cookie "^[ \t]*((spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*[; \t]*)+\$"
	}

	handle @spartan_app_session_authorized {
		route {
			header +Set-Cookie "spartan_ws_edge=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict${cookie_secure}"
			request_header @spartan_app_only_edge_cookies -Cookie
			request_header Cookie "^([ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*(;[ \t]*)?)+" ""
			request_header Cookie ";[ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*" ""
			request_header -Authorization
			reverse_proxy http://hermes:${container_port} {
				header_up -X-Edge-Valid
				header_up -X-Edge-Cookie
				header_up -X-Edge-Service-Key
			}
		}
	}

	handle @spartan_app_websocket {
		respond "Unauthorized" 401
	}

	handle {
		route {
			basic_auth {
				{\$CADDY_AUTH_USER:admin} {\$CADDY_AUTH_HASH}
			}
			forward_auth edge-sessions:9080 {
				uri /mint/apps
				header_up X-Edge-Service-Key {\$CADDY_SESSION_SERVICE_KEY}
				header_up X-Edge-Secure ${edge_secure}
				copy_headers X-Edge-Cookie
			}
			header {
				+Set-Cookie "{http.request.header.X-Edge-Cookie}"
				+Set-Cookie "spartan_ws_edge=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict${cookie_secure}"
			}
			request_header @spartan_app_only_edge_cookies -Cookie
			request_header Cookie "^([ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*(;[ \t]*)?)+" ""
			request_header Cookie ";[ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*" ""
			request_header -Authorization
			reverse_proxy http://hermes:${container_port} {
				header_up -X-Edge-Valid
				header_up -X-Edge-Cookie
				header_up -X-Edge-Service-Key
			}
		}
	}
	}
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

command -v docker >/dev/null 2>&1 || { printf '[add-port] Docker Compose is required for candidate validation.\n' >&2; exit 1; }
compose_cmd config --quiet
# Native adaptation catches listener syntax; never pull/start a container merely
# to validate. Supply an already available Caddy binary explicitly when needed.
caddy_bin="${CADDY_BIN:-caddy}"
command -v "$caddy_bin" >/dev/null 2>&1 || { printf '[add-port] Native Caddy required; set CADDY_BIN to an existing binary.\n' >&2; exit 1; }
CADDY_AUTH_USER="$(env_value CADDY_AUTH_USER admin)" CADDY_AUTH_HASH="$(env_value CADDY_AUTH_HASH)" \
CADDY_SESSION_SERVICE_KEY="$(env_value CADDY_SESSION_SERVICE_KEY)" \
SPARTAN_CADDY_TLS_CERT_FILE="$(env_value SPARTAN_CADDY_TLS_CERT_FILE /certs/cert.pem)" \
SPARTAN_CADDY_TLS_KEY_FILE="$(env_value SPARTAN_CADDY_TLS_KEY_FILE /certs/key.pem)" \
    "$caddy_bin" adapt --config "$CADDY_PORTS_FILE" --adapter caddyfile > "$stage/adapted.json"
"$caddy_bin" validate --config "$stage/adapted.json"
for target in "$original_compose" "$original_ports"; do
    [[ ! -L "$target" && ( ! -e "$target" || -f "$target" ) ]] || { printf '[add-port] Unsafe configuration target.\n' >&2; exit 1; }
done
mkdir -p "$(dirname "$original_compose")" "$(dirname "$original_ports")"
[[ ! -f "$original_compose" ]] || cp -p "$original_compose" "$stage/backup-compose"
[[ ! -f "$original_ports" ]] || cp -p "$original_ports" "$stage/backup-ports"
rollback() {
    if [[ -f "$stage/backup-compose" ]]; then cp -p "$stage/backup-compose" "$original_compose"; else rm -f "$original_compose"; fi
    if [[ -f "$stage/backup-ports" ]]; then cp -p "$stage/backup-ports" "$original_ports"; else rm -f "$original_ports"; fi
    rm -rf "$stage"
}
trap rollback EXIT
cp -p "$PRIVATE_COMPOSE" "$original_compose"
cp -p "$CADDY_PORTS_FILE" "$original_ports"
trap 'rm -rf "$stage"' EXIT
printf '[add-port] Configuration saved; no service changed. Apply with: sg up -d --no-deps --force-recreate caddy\n'

url_host="localhost"
if [[ "$bind_mode" == "tailscale" ]]; then
    if [[ "$edge_scheme" == "https" ]]; then
        url_host="$(env_value TAILSCALE_HOST "$(env_value TAILSCALE_IP localhost)")"
    else
        url_host="$(env_value TAILSCALE_IP localhost)"
    fi
fi
printf '[add-port] Done. Use %s://%s:%s or your private bind host.\n' "$edge_scheme" "$url_host" "$host_port"
