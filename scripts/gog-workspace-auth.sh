#!/usr/bin/env bash
set -euo pipefail

DEFAULT_GOGCLI_SERVICES="gmail,calendar,chat,classroom,drive,docs,slides,contacts,tasks,sheets,people,forms,appscript,ads"
DEFAULT_GOGCLI_EXTRA_SCOPES="https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/tagmanager.readonly,https://www.googleapis.com/auth/tagmanager.edit.containers,https://www.googleapis.com/auth/tagmanager.edit.containerversions,https://www.googleapis.com/auth/script.projects,https://www.googleapis.com/auth/script.deployments,https://www.googleapis.com/auth/script.processes,https://www.googleapis.com/auth/script.metrics"
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

hermes_exec() {
    "$docker_bin" exec \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        "$hermes_container" \
        "$@"
}

hermes_exec_i() {
    local interactive_args=(-i)
    if [[ -t 0 && -t 1 ]]; then
        interactive_args+=(-t)
    fi
    "$docker_bin" exec "${interactive_args[@]}" \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        "$hermes_container" \
        "$@"
}

workspace_setup() {
    hermes_exec python3 "$WORKSPACE_SETUP" "$@"
}

set_gog_credentials() {
    hermes_exec_i sh -eu -c '
credential_file="$(mktemp /tmp/gogcli-credentials.XXXXXX.json)"
trap '\''rm -f "$credential_file"'\'' EXIT HUP INT TERM
chmod 600 "$credential_file"
cat > "$credential_file"
gog auth credentials set "$credential_file"
' < "$gogcli_client_secret_path"
}

verify_requested_scopes() {
    local auth_json="$1"
    python3 -c '
import json
import sys

account, requested_csv = sys.argv[1:]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError) as exc:
    print(f"Error: GogCLI returned invalid auth JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

records = [
    record
    for record in payload.get("accounts", [])
    if isinstance(record, dict) and record.get("email") == account
]
if not records:
    print(f"Error: GogCLI auth result does not contain the configured account: {account}", file=sys.stderr)
    raise SystemExit(1)

record = next((item for item in records if item.get("client") == "default"), records[0])
stored = set(record.get("scopes") or [])
requested = [scope.strip() for scope in requested_csv.split(",") if scope.strip()]
missing = [scope for scope in requested if scope not in stored]
if missing:
    print("Error: missing requested OAuth scopes: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
' "$gogcli_account" "$gogcli_extra_scopes" <<< "$auth_json"
}

env_file="$(default_env_file)"
[[ -f "$env_file" ]] || die "private env file not found: $env_file"

docker_bin="${DOCKER_BIN:-docker}"
hermes_container="${SPARTAN_HERMES_CONTAINER:-spartan_gate_hermes}"

require_command "$docker_bin"
require_command python3

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

if [[ "$("$docker_bin" inspect -f '{{.State.Running}}' "$hermes_container" 2>/dev/null || true)" != "true" ]]; then
    die "$hermes_container is not running; start Hermes before running Google Workspace auth"
fi
runtime_user="$(hermes_runtime_user)" || die "could not resolve Hermes runtime user in $hermes_container"

printf 'Configuring GogCLI shared store at %s\n' "$gogcli_data_path"
set_gog_credentials

printf 'Starting GogCLI login for %s with the Hermes runtime CLI\n' "$gogcli_account"
hermes_exec_i gog login "$gogcli_account" \
    --services "$DEFAULT_GOGCLI_SERVICES" \
    --extra-scopes "$gogcli_extra_scopes" \
    --force-consent \
    --manual

printf 'Checking GogCLI refresh token\n'
auth_json="$(hermes_exec gog auth list --check --json)"
verify_requested_scopes "$auth_json"
hermes_exec gog --account "$gogcli_account" gmail labels list
printf 'GogCLI refresh token and requested OAuth scopes are valid\n'

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
