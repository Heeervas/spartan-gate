#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

default_env_file() {
    if [[ -f "$ROOT/private/env/local.env" ]]; then
        printf '%s\n' "$ROOT/private/env/local.env"
    elif [[ -f "$ROOT/.env" ]]; then
        printf '%s\n' "$ROOT/.env"
    else
        printf '%s\n' "$ROOT/private/env/local.env"
    fi
}

redact() {
    sed -E \
        -e 's#(token=)[^&[:space:]]+#\1REDACTED#gI' \
        -e 's#(TOKEN|KEY|SECRET|HASH|PASSWORD|CDP_URL)=.*#\1=<redacted>#g' \
        -e 's#(ws://[^[:space:]]*/devtools/browser/)[^[:space:]]+#\1<redacted>#g'
}

compose_args=(-f "$ROOT/infra/compose/compose.yml")
private_compose="$ROOT/private/compose.local.yml"
env_file="$(default_env_file)"

if [[ -f "$private_compose" ]]; then
    compose_args+=(-f "$private_compose")
fi

if [[ -f "$env_file" ]]; then
    compose_args+=(--env-file "$env_file")
elif [[ -f "$ROOT/.env" ]]; then
    compose_args+=(--env-file "$ROOT/.env")
fi

stamp="$(date '+%Y%m%d-%H%M%S')"
out_dir="$ROOT/runtime/diagnostics"
tmp_config="$(mktemp)"
if ! mkdir -p "$out_dir" 2>/dev/null; then
    out_dir="${TMPDIR:-/tmp}/spartan-gate-diagnostics"
    mkdir -p "$out_dir"
fi
out="$out_dir/browserless-snapshot-$stamp.txt"
trap 'rm -f "$tmp_config"' EXIT

docker compose "${compose_args[@]}" config --format json >"$tmp_config"

{
    printf 'Browserless snapshot: %s\n' "$stamp"
    printf 'repo: %s\n' "$ROOT"
    printf 'env_file: %s\n' "$env_file"
    printf 'private_compose: %s\n' "$([[ -f "$private_compose" ]] && printf '%s' "$private_compose" || printf '<none>')"
    printf '\n[rendered compose]\n'
    python3 - "$tmp_config" "$env_file" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
env_path = Path(sys.argv[2])
config = json.loads(config_path.read_text())

def parse_env(path):
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = raw.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values

def env_map(service):
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return env
    parsed = {}
    for item in env:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            parsed[key] = value
    return parsed

def volume_source(service, target):
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict) and volume.get("target") == target:
            return volume.get("source") or ""
    return ""

services = config.get("services") or {}
browserless = services.get("browserless") or {}
hermes = services.get("hermes") or {}
private_env = parse_env(env_path)

browserless_profile_source = volume_source(browserless, "/profiles")
expected_profile_source = private_env.get("SPARTAN_BROWSERLESS_PROFILES_PATH", "")
if expected_profile_source and browserless_profile_source != expected_profile_source:
    raise SystemExit(
        "rendered /profiles source does not match SPARTAN_BROWSERLESS_PROFILES_PATH: "
        f"{browserless_profile_source!r} != {expected_profile_source!r}"
    )

print(f"browserless.image={browserless.get('image', '')}")
print(f"browserless.profiles_source={browserless_profile_source}")
print(f"hermes.profiles_source={volume_source(hermes, '/profiles')}")

network = (config.get("networks") or {}).get("spartan_internal") or {}
ipam = network.get("ipam") or {}
subnet = ""
if ipam.get("config"):
    subnet = (ipam["config"][0] or {}).get("subnet", "")
print(f"spartan_internal.subnet={subnet}")

keys = [
    "BROWSERLESS_PROFILE",
    "BROWSERLESS_PROFILE_ROOT",
    "BROWSERLESS_WS_BASE",
    "BROWSERLESS_HEADLESS",
    "BROWSERLESS_STEALTH",
    "BROWSERLESS_STEALTH_ENDPOINT",
    "BROWSERLESS_ROUTE",
    "BROWSERLESS_STEALTH_ROUTE",
    "BROWSERLESS_LANG",
    "BROWSERLESS_TZ",
    "BROWSERLESS_CDP_BROKER_ENABLED",
    "BROWSERLESS_CDP_BROKER_PORT",
    "HERMES_MEET_CDP_PROFILE",
]
print("\n[hermes selected env]")
env = env_map(hermes)
for key in keys:
    if key in env:
        print(f"{key}={env[key]}")
print("HERMES_MEET_CDP_URL=<redacted>" if env.get("HERMES_MEET_CDP_URL") else "HERMES_MEET_CDP_URL=")
PY

    printf '\n[generated cdp urls]\n'
    node - "$env_file" <<'NODE' | redact
const path = require('node:path');
const { buildBrowserlessCdpUrl } = require('./apps/hermes-agent/browserless-cdp-url');

function parseEnvFile(file) {
  const fs = require('node:fs');
  const values = {};
  if (!file || !fs.existsSync(file)) return values;
  for (const raw of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !raw.includes('=')) continue;
    const index = raw.indexOf('=');
    const key = raw.slice(0, index).trim();
    let value = raw.slice(index + 1).trim();
    if (value.length >= 2 && value[0] === value[value.length - 1] && ['"', "'"].includes(value[0])) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

const env = { ...process.env, ...parseEnvFile(process.argv[2]) };
console.log(`guest=${buildBrowserlessCdpUrl(env)}`);
console.log(`profile=${buildBrowserlessCdpUrl(env, { profile: env.BROWSERLESS_PROFILE || 'main' })}`);
NODE

    printf '\n[running container]\n'
    if docker inspect spartan_gate_browserless >/dev/null 2>&1; then
        printf 'container.image_id='
        docker inspect spartan_gate_browserless --format '{{.Image}}'
        printf 'container.status='
        docker inspect spartan_gate_browserless --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
        printf 'local.image='
        docker ps --filter name=spartan_gate_browserless --format '{{.Image}}'
        printf 'image.inspect='
        docker image inspect "$(docker inspect spartan_gate_browserless --format '{{.Image}}')" \
            --format '{{.Id}} {{json .RepoTags}} {{json .RepoDigests}} {{.Created}}' 2>/dev/null || true
        printf 'package='
        docker exec spartan_gate_browserless node -p "JSON.stringify({version: require('/usr/src/app/package.json').version, puppeteer: require('/usr/src/app/package.json').dependencies['puppeteer-core'], playwright: require('/usr/src/app/package.json').dependencies['playwright-core'], node: process.version})" 2>/dev/null || true
        printf 'cdp_version='
        docker exec spartan_gate_browserless sh -lc 'wget -qO- "http://127.0.0.1:3000/json/version?token=${TOKEN}"' 2>/dev/null | tr '\n' ' ' || true
        printf '\n'
    else
        printf 'container=<not found>\n'
    fi
} | redact >"$out"

printf '%s\n' "$out"
