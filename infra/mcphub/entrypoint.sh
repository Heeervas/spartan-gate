#!/bin/sh
# File mode only. Refuse an unprovisioned state instead of vendor example defaults.
set -eu
umask 077
# Never inherit a bootstrap secret into the provisioned hub or its stdio children.
unset ADMIN_PASSWORD
bootstrap=$(node --input-type=commonjs <<'NODE'
const fs = require('node:fs');
try {
    const settings = JSON.parse(fs.readFileSync('/state/mcp_settings.json', 'utf8'));
    if (!settings || !Array.isArray(settings.users)) throw new Error();
    if (settings.users.length === 0) {
        console.log('yes');
    } else if (settings.users.some(user => user && user.isAdmin === true)) {
        console.log('no');
    } else {
        throw new Error(); // Existing non-admin accounts are not a fresh install.
    }
} catch {
    console.error('MCPHub settings unreadable, invalid, or missing an admin');
    process.exit(1);
}
NODE
)
if [ "$bootstrap" = yes ]; then
    [ -r /state/bootstrap-password ] && [ -s /state/bootstrap-password ] || {
        echo 'MCPHub bootstrap password is missing or empty' >&2
        exit 1
    }
    ADMIN_PASSWORD=$(cat /state/bootstrap-password)
    [ -n "$ADMIN_PASSWORD" ] || {
        echo 'MCPHub bootstrap password is empty' >&2
        exit 1
    }
    export ADMIN_PASSWORD
    echo 'MCPHub first boot: hub must be restarted before any backend is granted' >&2
fi
mkdir -p "$HOME"
# Vendor entrypoint needs writable HOME for npm configuration. No root writes.
exec /usr/local/bin/entrypoint.sh node dist/index.js
