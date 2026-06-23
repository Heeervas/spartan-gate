#!/usr/bin/env bash
set -euo pipefail

DEFAULT_GOGCLI_SERVICES="gmail,calendar,chat,classroom,drive,docs,slides,contacts,tasks,sheets,people,forms,appscript,ads"
DEFAULT_GOGCLI_EXTRA_SCOPES="https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/tagmanager.readonly,https://www.googleapis.com/auth/tagmanager.edit.containers,https://www.googleapis.com/auth/tagmanager.edit.containerversions,https://www.googleapis.com/auth/script.projects,https://www.googleapis.com/auth/script.deployments,https://www.googleapis.com/auth/script.processes,https://www.googleapis.com/auth/script.metrics"
WORKSPACE_SETUP="/opt/data/skills/productivity/google-workspace/scripts/setup.py"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

default_env_file() {
    if [[ -f "$repo_dir/private/env/local.env" ]]; then
        printf '%s\n' "$repo_dir/private/env/local.env"
    elif [[ -f "$repo_dir/.env" ]]; then
        printf '%s\n' "$repo_dir/.env"
    else
        printf '%s\n' "$repo_dir/private/env/local.env"
    fi
}

env_value() {
    local key="$1"
    local default="${2:-}"
    local env_file
    env_file="$(default_env_file)"
    if [[ -f "$env_file" ]]; then
        local value
        value="$(awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' "$env_file")"
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

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_value() {
    local name="$1"
    local value="$2"
    [[ -n "$value" ]] || die "$name is not set in $(default_env_file)"
}

hermes_runtime_user() {
    "$docker_bin" exec -u root "$hermes_container" sh -lc 'printf "%s:%s\n" "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}" "${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"'
}

workspace_setup() {
    local runtime_user
    runtime_user="$(hermes_runtime_user)" || die "could not resolve Hermes runtime user in $hermes_container"
    "$docker_bin" exec \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        "$hermes_container" \
        python3 "$WORKSPACE_SETUP" "$@"
}

env_file="$(default_env_file)"
[[ -f "$env_file" ]] || die "private env file not found: $env_file"

gog_bin="${GOG_BIN:-gog}"
docker_bin="${DOCKER_BIN:-docker}"
hermes_container="${SPARTAN_HERMES_CONTAINER:-spartan_gate_hermes}"

require_command "$gog_bin"
require_command "$docker_bin"

gogcli_data_path="$(env_value SPARTAN_GOGCLI_DATA_PATH)"
gog_keyring_password="$(env_value GOG_KEYRING_PASSWORD)"
gogcli_account="$(env_value GOGCLI_ACCOUNT)"
gogcli_client_secret_path="$(env_value GOGCLI_CLIENT_SECRET_PATH)"
gogcli_extra_scopes="$(env_value GOGCLI_EXTRA_SCOPES "$DEFAULT_GOGCLI_EXTRA_SCOPES")"

require_value SPARTAN_GOGCLI_DATA_PATH "$gogcli_data_path"
require_value GOG_KEYRING_PASSWORD "$gog_keyring_password"
require_value GOGCLI_ACCOUNT "$gogcli_account"
require_value GOGCLI_CLIENT_SECRET_PATH "$gogcli_client_secret_path"
[[ -f "$gogcli_client_secret_path" ]] || die "GOGCLI_CLIENT_SECRET_PATH does not exist: $gogcli_client_secret_path"

mkdir -p "$gogcli_data_path"

export XDG_CONFIG_HOME
XDG_CONFIG_HOME="$(dirname "$gogcli_data_path")"
export GOG_KEYRING_BACKEND=file
export GOG_KEYRING_PASSWORD="$gog_keyring_password"
export GOOGLE_PROJECT_ID
GOOGLE_PROJECT_ID="$(env_value GOOGLE_PROJECT_ID)"
export GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_PROJECT="$(env_value GOOGLE_CLOUD_PROJECT)"

printf 'Configuring GogCLI shared store at %s\n' "$gogcli_data_path"
"$gog_bin" auth credentials set "$gogcli_client_secret_path"

printf 'Starting GogCLI login for %s\n' "$gogcli_account"
"$gog_bin" login "$gogcli_account" \
    --services "$DEFAULT_GOGCLI_SERVICES" \
    --extra-scopes "$gogcli_extra_scopes" \
    --force-consent \
    --manual

printf 'Checking GogCLI refresh token\n'
"$gog_bin" auth list --check
"$gog_bin" --account "$gogcli_account" gmail labels list

if [[ "$("$docker_bin" inspect -f '{{.State.Running}}' "$hermes_container" 2>/dev/null || true)" != "true" ]]; then
    die "$hermes_container is not running; start Hermes before running Google Workspace auth"
fi

printf 'Checking Google Workspace token in %s\n' "$hermes_container"
if workspace_setup --check; then
    printf 'Google Workspace token is valid\n'
    exit 0
fi

printf 'Google Workspace token check failed; starting reauth\n'
auth_url="$(workspace_setup --auth-url)"
printf '%s\n' "$auth_url"
printf 'Paste the Google Workspace code or final redirect URL: '
IFS= read -r workspace_auth_code
[[ -n "$workspace_auth_code" ]] || die "no Google Workspace auth code or URL provided"

workspace_setup --auth-code "$workspace_auth_code"
workspace_setup --check
printf 'GogCLI and Google Workspace auth are ready\n'
