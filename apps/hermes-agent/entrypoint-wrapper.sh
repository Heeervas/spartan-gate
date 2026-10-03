#!/command/with-contenv bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"
PYTHON_BIN="${INSTALL_DIR}/.venv/bin/python"
SPARTAN_HERMES_RUNTIME_UID="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}"
SPARTAN_HERMES_RUNTIME_GID="${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"
SPARTAN_HERMES_RUNTIME_OWNER="${SPARTAN_HERMES_RUNTIME_UID}:${SPARTAN_HERMES_RUNTIME_GID}"

run_as_hermes() {
    /command/s6-applyuidgid -u "$SPARTAN_HERMES_RUNTIME_UID" -g "$SPARTAN_HERMES_RUNTIME_GID" "$@"
}

run_user_patch() {
    local patch_path="$1"
    if [ -f "$patch_path" ]; then
        run_as_hermes "$PYTHON_BIN" "$patch_path" 2>&1 | sed 's/^/[entrypoint] /'
    fi
}

camofox_browser_mode_enabled() {
    [ -n "${CAMOFOX_URL:-}" ]
}

persist_cdp_env_for_with_contenv() {
    local env_dir="/run/s6/container_environment"
    local key
    [ -d "$env_dir" ] || return 0

    for key in \
        CAMOFOX_URL \
        CAMOFOX_API_KEY \
        CAMOFOX_ACCESS_KEY \
        CAMOFOX_USER_ID \
        CAMOFOX_SESSION_KEY \
        HERMES_MEET_CAMOFOX_SESSION_KEY \
        CAMOFOX_ADOPT_EXISTING_TAB; do
        rm -f "${env_dir}/${key}"
        if [ -n "${!key:-}" ]; then
            printf '%s' "${!key}" > "${env_dir}/${key}"
        fi
    done

    if camofox_browser_mode_enabled; then
        rm -f "${env_dir}/HERMES_MEET_CDP_PROFILE"
        rm -f "${env_dir}/HERMES_MEET_CDP_URL"
    fi

    rm -f "${env_dir}/BROWSER_CDP_LAUNCH_URL"
    if [ -n "${BROWSER_CDP_LAUNCH_URL:-}" ]; then
        printf '%s' "$BROWSER_CDP_LAUNCH_URL" > "${env_dir}/BROWSER_CDP_LAUNCH_URL"
    fi

    rm -f "${env_dir}/BROWSER_CDP_URL"
    if [ -n "${BROWSER_CDP_URL:-}" ]; then
        printf '%s' "$BROWSER_CDP_URL" > "${env_dir}/BROWSER_CDP_URL"
    fi

    rm -f "${env_dir}/BROWSER_CDP_MAIN_URL"
    if [ -n "${BROWSER_CDP_MAIN_URL:-}" ]; then
        printf '%s' "$BROWSER_CDP_MAIN_URL" > "${env_dir}/BROWSER_CDP_MAIN_URL"
    fi
}

sync_openai_base_url_for_with_contenv() {
    local desired="${OPENAI_BASE_URL:-}"
    local env_file="$HERMES_HOME/.env"
    local env_dir="/run/s6/container_environment"
    local tmp current

    [ -n "$desired" ] || return 0

    if [ -d "$env_dir" ]; then
        rm -f "${env_dir}/OPENAI_BASE_URL"
        printf '%s' "$desired" > "${env_dir}/OPENAI_BASE_URL"
    fi

    [ -f "$env_file" ] || return 0

    current="$(awk -F= '$1 == "OPENAI_BASE_URL" {print substr($0, index($0, "=") + 1); exit}' "$env_file" 2>/dev/null || true)"
    [ "$current" = "$desired" ] && return 0

    tmp="${env_file}.spartan-openai-base-url.tmp"
    awk -v desired="$desired" '
        BEGIN { written = 0 }
        /^OPENAI_BASE_URL=/ {
            if (!written) {
                print "OPENAI_BASE_URL=" desired
                written = 1
            }
            next
        }
        { print }
        END {
            if (!written) print "OPENAI_BASE_URL=" desired
        }
    ' "$env_file" > "$tmp"
    chmod --reference="$env_file" "$tmp" 2>/dev/null || chmod 600 "$tmp" 2>/dev/null || true
    chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$tmp" 2>/dev/null || true
    mv "$tmp" "$env_file"
    echo "[entrypoint] Synced OPENAI_BASE_URL in ${env_file}"
}

# Upstream /init runs docker/stage2-hook.sh before this wrapper. Keep this file
# limited to Spartan Gate boot additions, then delegate final command routing
# and privilege drop to /opt/hermes/docker/main-wrapper.sh.

# Keep Hermes' venv command available at the user-level path expected by
# `hermes doctor` and by shells that do not source the venv directly.
if [ -x "${INSTALL_DIR}/.venv/bin/hermes" ]; then
    run_as_hermes mkdir -p "${HERMES_HOME}/.local/bin"
    run_as_hermes ln -sfn "${INSTALL_DIR}/.venv/bin/hermes" "${HERMES_HOME}/.local/bin/hermes"
fi

# Installed-code patches are build-time-only. Runtime hooks below may update
# only data/config under HERMES_HOME.

# Keep the Meet workspace private and writable by the Hermes runtime user.
meet_workspace="$HERMES_HOME/workspace/meetings"
run_as_hermes mkdir -p "$meet_workspace"
chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME/workspace" "$meet_workspace" 2>/dev/null || true
chown -R "$SPARTAN_HERMES_RUNTIME_OWNER" "$meet_workspace" 2>/dev/null || true
chmod -R u+rwX,go-rwx "$meet_workspace" 2>/dev/null || true

# Preserve the venv path in login shells used by agent terminals.
_venv_marker="# hermes-runtime-user-path"
for _rc in "$HERMES_HOME/.bashrc" "$HERMES_HOME/.profile"; do
    if [ -f "$_rc" ] && ! grep -q "$_venv_marker" "$_rc" 2>/dev/null; then
        printf '\n%s\nexport PATH="/opt/data/.local/bin:/opt/hermes/.venv/bin:$PATH"\n' "$_venv_marker" >> "$_rc"
        printf 'export PYTHONUSERBASE="${PYTHONUSERBASE:-/opt/data/.local}"\n' >> "$_rc"
        printf 'export PIP_TARGET="${PIP_TARGET:-/opt/data/hermes-extra-site-py314}"\n' >> "$_rc"
        printf 'export PYTHONPATH="${PYTHONPATH:-/opt/hermes/bootstrap:/opt/data/hermes-extra-site-py314:/opt/hermes}"\n' >> "$_rc"
        printf 'export npm_config_prefix="${npm_config_prefix:-/opt/data/.local}"\n' >> "$_rc"
        chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$_rc" 2>/dev/null || true
    fi
done

if camofox_browser_mode_enabled; then
    unset BROWSER_CDP_URL
    unset BROWSER_CDP_MAIN_URL
    unset BROWSER_CDP_LAUNCH_URL
    unset HERMES_MEET_CDP_PROFILE
    unset HERMES_MEET_CDP_URL
    echo "[entrypoint] CAMOFOX_URL set; Browserless CDP launch and main broker skipped"
elif [ -z "${BROWSER_CDP_LAUNCH_URL:-}" ] && [ -f "$INSTALL_DIR/bootstrap/browserless-cdp-url.js" ]; then
    export BROWSER_CDP_LAUNCH_URL
    BROWSER_CDP_LAUNCH_URL="$(node "$INSTALL_DIR/bootstrap/browserless-cdp-url.js")"
    echo "[entrypoint] Browserless launch CDP: ephemeral launch"
fi

if ! camofox_browser_mode_enabled \
    && [ -z "${BROWSER_CDP_URL:-}" ] \
    && [ -n "${BROWSER_CDP_LAUNCH_URL:-}" ]; then
    export BROWSER_CDP_URL="$BROWSER_CDP_LAUNCH_URL"
fi

if ! camofox_browser_mode_enabled && [ -z "${BROWSER_CDP_MAIN_URL:-}" ]; then
    if [ -n "${BROWSER_CDP_LAUNCH_URL:-}" ]; then
        export BROWSER_CDP_URL="$BROWSER_CDP_LAUNCH_URL"
    fi
fi

run_user_patch "$INSTALL_DIR/patches/patch_chrome_devtools_ws_auth.py"

persist_cdp_env_for_with_contenv
sync_openai_base_url_for_with_contenv

exec /opt/hermes/docker/main-wrapper.sh "$@"
