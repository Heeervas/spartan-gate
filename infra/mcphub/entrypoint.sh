#!/bin/sh
# File mode only. Refuse an unprovisioned state instead of vendor example defaults.
set -eu
umask 077
[ -s /state/mcp_settings.json ] && [ -s /state/bootstrap-password ] || {
    echo 'MCPHub state is not provisioned' >&2
    exit 1
}
ADMIN_PASSWORD=$(cat /state/bootstrap-password)
[ -n "$ADMIN_PASSWORD" ] || exit 1
export ADMIN_PASSWORD
mkdir -p "$HOME"
# Vendor entrypoint needs writable HOME for npm configuration. No root writes.
exec /usr/local/bin/entrypoint.sh node dist/index.js
