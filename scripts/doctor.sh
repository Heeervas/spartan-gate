#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
TMPDIR=${TMPDIR:-/tmp}

fail() {
  printf 'doctor: %s\n' "$1" >&2
  exit 1
}

warn() {
  printf 'doctor: warning: %s\n' "$1" >&2
}

scan_with_grep() {
  pattern=$1
  out=$2
  shift 2
  : >"$out"
  found=1
  files="$TMPDIR/spartan-gate-scan-files.$$"
  tmp="$TMPDIR/spartan-gate-scan-grep.$$"
  err="$TMPDIR/spartan-gate-scan-error.$$"
  : >"$files"

  for path in "$@"; do
    if test -d "$path"; then
      find "$path" \
        \( -path '*/.git/*' -o -path '*/private/*' -o -path '*/runtime/*' -o -path '*/node_modules/*' -o -path '*/dist/*' \) -prune \
        -o -type f -print >>"$files" || fail "file scan failed under $path"
    elif test -f "$path"; then
      printf '%s\n' "$path" >>"$files"
    fi
  done

  while IFS= read -r file; do
    case "$file" in
      ./.env|.env|./scripts/doctor.sh|scripts/doctor.sh)
        continue
        ;;
      ./AGENTS.md|AGENTS.md|./.agents/*|.agents/*|./.github/agents/*|.github/agents/*|./.github/hooks/*|.github/hooks/*|./.github/prompts/*|.github/prompts/*|./.github/skills/*|.github/skills/*)
        continue
        ;;
    esac
    if grep -nE "$pattern" "$file" >"$tmp" 2>"$err"; then
      sed "s|^|$file:|" "$tmp" >>"$out"
      found=0
    else
      status=$?
      if test "$status" -ne 1; then
        cat "$err" >&2
        fail "grep scan failed for $file"
      fi
    fi
  done <"$files"

  rm -f "$files" "$tmp" "$err"
  return "$found"
}

scan_repo() {
  pattern=$1
  out=$2
  if command -v rg >/dev/null 2>&1; then
    if rg -n "$pattern" \
      --glob '!.env' \
      --glob '!.git/**' \
      --glob '!private/**' \
      --glob '!runtime/**' \
      --glob '!AGENTS.md' \
      --glob '!.agents/**' \
      --glob '!.github/agents/**' \
      --glob '!.github/hooks/**' \
      --glob '!.github/prompts/**' \
      --glob '!.github/skills/**' \
      --glob '!**/node_modules/**' \
      --glob '!**/dist/**' \
      --glob '!scripts/doctor.sh' . >"$out"; then
      return 0
    else
      status=$?
    fi
    test "$status" -eq 1 && return 1
    fail "rg scan failed"
  else
    scan_with_grep "$pattern" "$out" .
  fi
}

scan_paths() {
  pattern=$1
  out=$2
  shift 2
  if command -v rg >/dev/null 2>&1; then
    if rg -n "$pattern" "$@" >"$out"; then
      return 0
    else
      status=$?
    fi
    test "$status" -eq 1 && return 1
    fail "rg scan failed"
  else
    scan_with_grep "$pattern" "$out" "$@"
  fi
}

cidr_range() {
  cidr=$1
  awk -v cidr="$cidr" '
    function ip2int(ip, parts, i, n) {
      n = split(ip, parts, ".")
      if (n != 4) return -1
      for (i = 1; i <= 4; i++) {
        if (parts[i] !~ /^[0-9]+$/ || parts[i] < 0 || parts[i] > 255) return -1
      }
      return (((parts[1] * 256) + parts[2]) * 256 + parts[3]) * 256 + parts[4]
    }
    function pow2(exponent, i, n) {
      n = 1
      for (i = 0; i < exponent; i++) n *= 2
      return n
    }
    BEGIN {
      split(cidr, parts, "/")
      bits = parts[2] + 0
      if (parts[1] == "" || parts[2] == "" || bits < 0 || bits > 32) exit 2
      ip = ip2int(parts[1])
      if (ip < 0) exit 2
      size = pow2(32 - bits)
      start = int(ip / size) * size
      end = start + size - 1
      printf "%.0f %.0f\n", start, end
    }'
}

cidr_overlaps() {
  left=$1
  right=$2
  left_range=$(cidr_range "$left") || return 2
  right_range=$(cidr_range "$right") || return 2
  set -- $left_range
  left_start=$1
  left_end=$2
  set -- $right_range
  right_start=$1
  right_end=$2
  awk -v ls="$left_start" -v le="$left_end" -v rs="$right_start" -v re="$right_end" 'BEGIN { exit !((ls <= re) && (rs <= le)) }'
}

env_value() {
  env_file=$1
  key=$2
  sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | sed "s/^'//; s/'$//; s/^\"//; s/\"$//"
}

validate_private_env() {
  env_file=$1
  test -f "$env_file" || return 0
  allow_existing_stack_paths=$(env_value "$env_file" SPARTAN_ALLOW_EXISTING_STACK_PATHS)
  camofox_enabled=$(env_value "$env_file" CAMOFOX_URL)

  if awk -v camofox_enabled="$camofox_enabled" '
    /^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=/ {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      split(line, pair, "=")
      key = pair[1]
      value = substr(line, index(line, "=") + 1)
      if (camofox_enabled == "" && (key ~ /^CAMOFOX_/ || key ~ /^SPARTAN_CAMOFOX_/)) next
      if (value ~ /^(100\.x\.y\.z|\/absolute\/path|change-me($|-)|<password>|your-|TODO|REPLACE_ME)/) {
        print FNR ":" $0
        found = 1
      }
    }
    END { exit !found }
  ' "$env_file" >"$TMPDIR/spartan-gate-env-placeholders.txt"; then
    cat "$TMPDIR/spartan-gate-env-placeholders.txt" >&2
    fail "private env still contains placeholder values: $env_file"
  fi

  for key in SPARTAN_HERMES_DATA_PATH SPARTAN_CLAWROUTE_DATA_PATH SPARTAN_GOGCLI_DATA_PATH SPARTAN_CAMOFOX_DATA_PATH SPARTAN_CAMOFOX_ADDONS_PATH; do
    case "$key" in
      SPARTAN_CAMOFOX_*) test -n "$camofox_enabled" || continue ;;
    esac
    value=$(env_value "$env_file" "$key")
    test -n "$value" || continue
    case "$value" in
      *my-lobster*|*lobster-cage*|*openclaw*|*wger*|*/.custom_claw/hermes|*/.custom_claw/hermes/*)
        if test "$allow_existing_stack_paths" = "true"; then
          warn "allowing old stack path because SPARTAN_ALLOW_EXISTING_STACK_PATHS=true: $key=$value"
        else
          fail "private data path looks like an old stack path: $key=$value; set SPARTAN_ALLOW_EXISTING_STACK_PATHS=true only for an intentional cutover with the old stack stopped"
        fi
        ;;
    esac
    test -d "$value" || fail "private data path does not exist; create it before compose up: $key=$value"
  done
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker CLI not found; install Docker before running Compose preflight"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin not available; install docker compose"
  docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable; start Docker and rerun doctor"
}

need_file() {
  test -f "$1" || fail "missing required file: $1"
}

need_dir() {
  test -d "$1" || fail "missing required directory: $1"
}

need_dir apps/hermes-agent
need_dir packages/clawroute
need_dir infra/compose
need_dir infra/caddy
need_dir infra/dns
need_dir infra/outbound-proxy
need_dir infra/reader
need_dir infra/browserless
need_dir config/clawroute
need_dir config/searxng
need_dir private.example

need_file README.md
need_file .env.example
need_file infra/compose/compose.yml
need_file infra/outbound-proxy/Dockerfile
need_file infra/outbound-proxy/entrypoint.sh
need_file packages/clawroute/package.json
need_file packages/clawroute/Dockerfile

test ! -e .gitmodules || fail ".gitmodules must not exist"

if test -d .git; then
  if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    fail ".env must stay local and untracked"
  fi

  if git ls-files | grep -E '^(AGENTS\.md|\.agents/|\.github/(agents|hooks|prompts|skills)/)' >"$TMPDIR/spartan-gate-tracked-agent-packs.txt"; then
    cat "$TMPDIR/spartan-gate-tracked-agent-packs.txt" >&2
    fail "local agent instructions and packs must stay untracked"
  fi

  if git ls-files | grep -E '(^|/)node_modules/|(^|/)dist/' >/tmp/spartan-gate-tracked-builds.txt; then
    cat /tmp/spartan-gate-tracked-builds.txt >&2
    fail "dependency or build output is tracked"
  fi

  if git ls-files | grep -E '^(private|runtime)/' >"$TMPDIR/spartan-gate-tracked-local.txt"; then
    cat "$TMPDIR/spartan-gate-tracked-local.txt" >&2
    fail "private or runtime state is tracked"
  fi

  if git ls-files | grep -E '(^|/)\.env($|\.)' | grep -Ev '(^|/)\.env\.example$' >"$TMPDIR/spartan-gate-tracked-env.txt"; then
    cat "$TMPDIR/spartan-gate-tracked-env.txt" >&2
    fail "local env file is tracked"
  fi

  git check-ignore -q private/compose.local.yml || fail "private/ must stay ignored"
  git check-ignore -q runtime/hermes/.keep || fail "runtime/ must stay ignored"
  git check-ignore -q AGENTS.md || fail "AGENTS.md must stay ignored"
  git check-ignore -q .agents/example || fail ".agents/ must stay ignored"
  git check-ignore -q .github/prompts/example.md || fail ".github/prompts/ must stay ignored"
fi

if find . -name .git -not -path './.git' | grep . >/dev/null; then
  fail "nested .git directory found"
fi

if scan_repo '/home/|/Users/|\\\\Users\\\\|~/.codex|~/.custom_claw|\\.custom_claw' /tmp/spartan-gate-path-scan.txt; then
  cat /tmp/spartan-gate-path-scan.txt >&2
  fail "personal host path found in public runtime files"
fi

if scan_repo 'private/env/[a][l]berto\.env|[a][l]berto\.env' /tmp/spartan-gate-old-env-name-scan.txt; then
  cat /tmp/spartan-gate-old-env-name-scan.txt >&2
  fail "old private env filename found"
fi

if scan_paths 'wger|docker-compose\.openclaw|OPENCLAW_|openclaw_' /tmp/spartan-gate-legacy-scan.txt apps infra packages config .env.example package.json; then
  cat /tmp/spartan-gate-legacy-scan.txt >&2
  fail "legacy runtime reference found"
fi

if scan_repo 'ghp_[A-Za-z0-9_]{10,}|sk-[A-Za-z0-9_-]{20,}|sess-[A-Za-z0-9_-]{10,}|BEGIN (RSA |OPENSSH |PRIVATE )?KEY' /tmp/spartan-gate-secret-scan.txt; then
  cat /tmp/spartan-gate-secret-scan.txt >&2
  fail "secret-like material found"
fi

if scan_paths 'tls internal|skip_install_trust|https://localhost' /tmp/spartan-gate-http-scan.txt infra/caddy infra/compose docs README.md .env.example; then
  cat /tmp/spartan-gate-http-scan.txt >&2
  fail "public edge is HTTP-only; stale HTTPS/TLS config found"
fi

extract_published_ports() {
  config_file=$1
  out=$2
  awk '
    function flush_item() {
      if (item && published) {
        gsub(/"/, "", host)
        gsub(/"/, "", published)
        print host, published
      }
    }
    /^    ports:/ {
      in_ports = 1
      item = 0
      host = ""
      published = ""
      next
    }
    in_ports && /^    [A-Za-z0-9_-]+:/ {
      flush_item()
      in_ports = 0
      item = 0
      next
    }
    in_ports && /^      - / {
      flush_item()
      item = 1
      host = ""
      published = ""
      next
    }
    in_ports && /host_ip:/ {
      host = $2
      next
    }
    in_ports && /published:/ {
      published = $2
      next
    }
    END { flush_item() }
  ' "$config_file" >"$out"
}

check_safe_port_binds() {
  label=$1
  config_file=$2
  if awk '
    function check_item() {
      if (item && published && (host == "" || host == "0.0.0.0" || host == "::")) {
        print item_line ": published port without explicit safe host_ip"
        bad = 1
      }
    }
    /^    ports:/ {
      in_ports = 1
      item = 0
      published = 0
      host = ""
      item_line = NR
      next
    }
    in_ports && /^    [A-Za-z0-9_-]+:/ {
      check_item()
      in_ports = 0
      item = 0
      next
    }
    in_ports && /^      - / {
      check_item()
      item = 1
      published = 0
      host = ""
      item_line = NR
      next
    }
    in_ports && /host_ip:/ {
      host = $2
      gsub(/"/, "", host)
      next
    }
    in_ports && /published:/ {
      published = 1
      next
    }
    END {
      check_item()
      exit bad
    }
  ' "$config_file" >"$TMPDIR/spartan-gate-compose-$label-ports.txt"; then
    :
  else
    cat "$TMPDIR/spartan-gate-compose-$label-ports.txt" >&2
    fail "unsafe public port bind in Compose config: $label"
  fi
}

project_name_from_config() {
  awk '/^name:/ {print $2; exit}' "$1"
}

network_is_own_project() {
  network=$1
  project=$2
  labels=$(docker network inspect "$network" --format '{{index .Labels "com.docker.compose.project"}} {{index .Labels "com.docker.compose.network"}}' 2>/dev/null || true)
  set -- $labels
  if test "${1:-}" = "$project" && test "${2:-}" = "spartan_internal"; then
    return 0
  fi
  test "$network" = "${project}_spartan_internal"
}

check_subnet_conflicts() {
  label=$1
  config_file=$2
  project=$3
  subnet=$(awk '/subnet:/ {print $NF; exit}' "$config_file")
  test -n "${subnet:-}" || return 0

  docker network ls --format '{{.Name}}' >"$TMPDIR/spartan-gate-networks-$label.txt" || fail "could not list Docker networks"
  while IFS= read -r network; do
    test -n "$network" || continue
    if network_is_own_project "$network" "$project"; then
      continue
    fi
    docker network inspect "$network" --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' >"$TMPDIR/spartan-gate-network-$label-subnets.txt" || fail "could not inspect Docker network: $network"
    while IFS= read -r other_subnet; do
      test -n "$other_subnet" || continue
      case "$subnet" in *:*) continue ;; esac
      case "$other_subnet" in *:*) continue ;; esac
      if cidr_overlaps "$subnet" "$other_subnet"; then
        fail "docker subnet overlaps existing network: $label ($subnet overlaps $other_subnet on $network)"
      else
        status=$?
        test "$status" -eq 1 || fail "invalid Docker subnet while checking overlap: $label ($subnet / $other_subnet)"
      fi
    done <"$TMPDIR/spartan-gate-network-$label-subnets.txt"
  done <"$TMPDIR/spartan-gate-networks-$label.txt"
}

port_owned_by_project() {
  project=$1
  port=$2
  docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Ports}}' >"$TMPDIR/spartan-gate-project-ports.txt" 2>/dev/null || return 1
  tr ',' '\n' <"$TMPDIR/spartan-gate-project-ports.txt" \
    | sed -n 's/.*:\([0-9][0-9]*\)\(-\([0-9][0-9]*\)\)\{0,1\}->.*/\1 \3/p' \
    >"$TMPDIR/spartan-gate-project-port-ranges.txt"
  while read -r start end; do
    test -n "${start:-}" || continue
    test -n "${end:-}" || end=$start
    if test "$port" -ge "$start" && test "$port" -le "$end"; then
      return 0
    fi
  done <"$TMPDIR/spartan-gate-project-port-ranges.txt"
  return 1
}

port_listening() {
  port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn >"$TMPDIR/spartan-gate-listeners.txt" || fail "could not inspect listening TCP ports with ss"
    awk -v port=":$port" '{ if ($4 ~ port "$") found = 1 } END { exit !found }' "$TMPDIR/spartan-gate-listeners.txt"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    return $?
  fi
  warn "could not check host port availability; install ss, lsof, or nc"
  return 1
}

check_port_conflicts() {
  label=$1
  config_file=$2
  project=$3
  ports_file="$TMPDIR/spartan-gate-compose-$label-published-ports.txt"
  extract_published_ports "$config_file" "$ports_file"
  while read -r host port; do
    test -n "${port:-}" || continue
    if port_owned_by_project "$project" "$port"; then
      continue
    fi
    if port_listening "$port"; then
      fail "host port already in use before compose up: $label ($host:$port)"
    fi
  done <"$ports_file"
}

check_compose_config() {
  label=$1
  shift
  out="$TMPDIR/spartan-gate-compose-$label.yml"
  docker compose "$@" config >"$out" || fail "docker compose config failed: $label"
  project=$(project_name_from_config "$out")
  test -n "$project" || fail "could not determine Compose project name: $label"
  check_safe_port_binds "$label" "$out"
}

check_effective_runtime_config() {
  label=$1
  shift
  out="$TMPDIR/spartan-gate-compose-$label.yml"
  docker compose "$@" config >"$out" || fail "docker compose config failed: $label"
  project=$(project_name_from_config "$out")
  test -n "$project" || fail "could not determine Compose project name: $label"
  check_safe_port_binds "$label" "$out"
  check_subnet_conflicts "$label" "$out" "$project"
  check_port_conflicts "$label" "$out" "$project"
}

require_docker

check_compose_config base -f infra/compose/compose.yml
check_compose_config dev -f infra/compose/compose.yml -f infra/compose/compose.dev.yml
check_compose_config prod -f infra/compose/compose.yml -f infra/compose/compose.prod.example.yml

if test -f private/compose.local.yml && test -f private/env/local.env; then
  need_file private/outbound-proxy/whitelist.private.txt
  validate_private_env private/env/local.env
  check_effective_runtime_config private-local -f infra/compose/compose.yml -f private/compose.local.yml --env-file private/env/local.env
elif test -f .env; then
  check_effective_runtime_config base-local -f infra/compose/compose.yml --env-file .env
else
  check_effective_runtime_config base-default -f infra/compose/compose.yml
fi

printf 'doctor: ok\n'
