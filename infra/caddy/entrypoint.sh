#!/bin/sh
set -eu

validate_edge_token() {
    name="$1"
    eval "value=\${$name:-}"
    if ! printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$'; then
        printf '[caddy] %s must be exactly 64 lowercase hexadecimal characters\n' "$name" >&2
        exit 1
    fi
}

validate_edge_token CADDY_CAMOFOX_EDGE_TOKEN
validate_edge_token CADDY_WS_EDGE_TOKEN
validate_edge_token CADDY_SESSION_SERVICE_KEY

if [ "$CADDY_CAMOFOX_EDGE_TOKEN" = "$CADDY_WS_EDGE_TOKEN" ] || [ "$CADDY_SESSION_SERVICE_KEY" = "$CADDY_WS_EDGE_TOKEN" ] || [ "$CADDY_SESSION_SERVICE_KEY" = "$CADDY_CAMOFOX_EDGE_TOKEN" ]; then
    printf "[caddy] CADDY_CAMOFOX_EDGE_TOKEN and CADDY_WS_EDGE_TOKEN must differ\n" >&2
    exit 1
fi

exec "${CADDY_BIN:-caddy}" run --config /etc/caddy/Caddyfile --adapter caddyfile
