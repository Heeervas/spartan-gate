#!/usr/bin/env bash
# Load Spartan Gate helpers:
#   cd /path/to/spartan-gate
#   source scripts/aliases.sh
#   echo 'cd /path/to/spartan-gate && source scripts/aliases.sh' >> ~/.bashrc   # or ~/.zshrc
#
# Compose interpolation uses variables from the file passed with --env-file
# (private/env/local.env here, falling back to .env). Service-level env_file:
# entries only inject variables into containers; they do not replace --env-file
# for compose.yml interpolation.

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    export SPARTAN_GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
else
    export SPARTAN_GATE_DIR="$PWD"
fi

# Clear aliases from older versions before defining functions with the same
# command names. This makes repeated `source scripts/aliases.sh` predictable.
for alias_name in \
    sg sg-help sg-tier sg-hermes sg-browser sg-meet sg-disk \
    sg-priv sg-up sg-build-up sg-down sg-status sg-ps sg-health sg-logs \
    sg-exec sg-config sg-doctor sg-up-core sg-up-hermes \
    sg-up-private-hermes sg-up-full sg-restart-hermes sg-restart-caddy \
    sg-restart-proxy sg-rebuild-hermes sg-rebuild-clawroute sg-logs-hermes \
    sg-logs-clawroute sg-hermes-shell sg-hermes-doctor sg-hermes-profile \
    sg-hermes-profiles sg-hermes-profile-create sg-hermes-profile-new sg-hermes-gateway-profile \
    sg-clawroute-shell sg-whitelist-domain sg-add-port sg-reader-test \
    sg-browserless-profile-live sg-hermes-chrome-profile sg-hermes-meet sg-hermes-meet-join \
    sg-browser-mode-apply sg-camofox-profile-live sg-camofox-profile-use \
    sg-camofox-profile-save sg-camofox-profile-reset \
    sg-camofox-health sg-camofox-smoke sg-camofox-url sg-logs-camofox \
    sg-browserless-profile-chrome sg-browserless-snapshot sg-meet-google-check \
    sg-meet-chrome-start sg-meet-chrome-relay-start sg-meet-chrome-ws-url sg-meet-chrome-check \
    sg-urls sg-caddy-validate sg-ports sg-docker-df sg-cache-top \
    sg-cache-live sg-cache-clean sg-profile-ports sg-gog-check sg-gog-login; do
    unalias "$alias_name" 2>/dev/null || true
    unset -f "$alias_name" 2>/dev/null || true
done
unset alias_name

# -----------------------------------------------------------------------------
# Environment and Compose plumbing
#
# These helpers centralize Spartan Gate's Compose file/env-file selection. Most
# user-facing commands below call sg_compose instead of spelling out Compose
# flags, so private overrides and env interpolation remain consistent.

sg_default_env_file() {
    if [[ -f "$SPARTAN_GATE_DIR/private/env/local.env" ]]; then
        printf '%s\n' "$SPARTAN_GATE_DIR/private/env/local.env"
    elif [[ -f "$SPARTAN_GATE_DIR/.env" ]]; then
        printf '%s\n' "$SPARTAN_GATE_DIR/.env"
    else
        printf '%s\n' "$SPARTAN_GATE_DIR/private/env/local.env"
    fi
}

sg_env_value() {
    local key="$1"
    local default="${2:-}"
    local env_file
    env_file="$(sg_default_env_file)"
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

sg_compose() {
    local private_compose="$SPARTAN_GATE_DIR/private/compose.local.yml"
    local env_file
    env_file="$(sg_default_env_file)"
    local args=(-f "$SPARTAN_GATE_DIR/infra/compose/compose.yml")

    if [[ -f "$private_compose" ]]; then
        args+=(-f "$private_compose")
    fi

    if [[ -f "$env_file" ]]; then
        args+=(--env-file "$env_file")
    elif [[ -f "$SPARTAN_GATE_DIR/.env" ]]; then
        args+=(--env-file "$SPARTAN_GATE_DIR/.env")
    fi

    docker compose "${args[@]}" "$@"
}

sg_normalize_tier() {
    case "${1:-}" in
        L0|l0|0) printf 'L0\n' ;;
        L1|l1|1) printf 'L1\n' ;;
        L2|l2|2) printf 'L2\n' ;;
        L3|l3|3) printf 'L3\n' ;;
        L4|l4|4) printf 'L4\n' ;;
        *) return 1 ;;
    esac
}

sg_current_tier() {
    local tier
    tier="${SPARTAN_TIER:-$(sg_env_value SPARTAN_TIER L4)}"
    sg_normalize_tier "$tier"
}

sg_default_hermes_mode() {
    case "$1" in
        L0|L1) printf 'free\n' ;;
        L2|L3) printf 'gated\n' ;;
        L4) printf 'full\n' ;;
        *) return 1 ;;
    esac
}

sg_normalize_hermes_mode() {
    case "${1:-}" in
        free|gated|full) printf '%s\n' "$1" ;;
        *) return 1 ;;
    esac
}

sg_current_hermes_mode() {
    local tier mode
    tier="${1:-$(sg_current_tier)}" || return 1
    mode="${SPARTAN_HERMES_MODE:-$(sg_env_value SPARTAN_HERMES_MODE "")}"
    if [[ -z "$mode" ]]; then
        sg_default_hermes_mode "$tier"
    else
        sg_normalize_hermes_mode "$mode"
    fi
}

sg_default_addons() {
    case "$1" in
        L3|L4) printf 'clawroute\n' ;;
        L0|L1|L2) printf '\n' ;;
        *) return 1 ;;
    esac
}

sg_normalize_addons() {
    local raw="${1:-}"
    local addon
    local normalized=()
    raw="${raw//,/ }"
    for addon in $raw; do
        case "$addon" in
            ""|none|None|NONE) ;;
            clawroute)
                if [[ " ${normalized[*]} " != *" clawroute "* ]]; then
                    normalized+=("clawroute")
                fi
                ;;
            *)
                printf 'Invalid Spartan Gate addon: %s\n' "$addon" >&2
                return 1
                ;;
        esac
    done
    local IFS=,
    printf '%s\n' "${normalized[*]}"
}

sg_current_addons() {
    local tier addons
    tier="${1:-$(sg_current_tier)}" || return 1
    addons="${SPARTAN_ADDONS:-$(sg_env_value SPARTAN_ADDONS "")}"
    if [[ -z "$addons" ]]; then
        sg_default_addons "$tier"
    else
        sg_normalize_addons "$addons"
    fi
}

sg_addons_has() {
    local addons="$1"
    local wanted="$2"
    local addon
    IFS=',' read -ra _spartan_addons <<< "$addons"
    for addon in "${_spartan_addons[@]}"; do
        [[ "$addon" == "$wanted" ]] && return 0
    done
    return 1
}

sg_tier_compose_args() {
    local tier="$1"
    local addons="${2:-$(sg_current_addons "$tier")}"
    local private_compose="$SPARTAN_GATE_DIR/private/compose.local.yml"
    case "$tier" in
        L0)
            printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l0.yml"
            sg_addons_has "$addons" clawroute && printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.clawroute.yml"
            ;;
        L1)
            printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l1.yml"
            sg_addons_has "$addons" clawroute && printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.clawroute.yml"
            ;;
        L2)
            printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l2.yml"
            sg_addons_has "$addons" clawroute && printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l3.yml"
            ;;
        L3) printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l2.yml" -f "$SPARTAN_GATE_DIR/infra/compose/tiers/compose.l3.yml" ;;
        L4)
            printf '%s\n' -f "$SPARTAN_GATE_DIR/infra/compose/compose.yml"
            [[ -f "$private_compose" ]] && printf '%s\n' -f "$private_compose"
            ;;
        *) return 1 ;;
    esac
}

sg_tier_compose() {
    local tier="${1:-}"
    shift || true
    local env_file
    local args=()
    local addons
    tier="$(sg_normalize_tier "$tier")" || {
        printf 'Invalid Spartan Gate tier: %s\n' "${tier:-}" >&2
        return 2
    }
    addons="$(sg_current_addons "$tier")" || return 2
    while IFS= read -r item; do
        args+=("$item")
    done < <(sg_tier_compose_args "$tier" "$addons")

    env_file="$(sg_default_env_file)"
    if [[ -f "$env_file" ]]; then
        args+=(--env-file "$env_file")
    elif [[ -f "$SPARTAN_GATE_DIR/.env" ]]; then
        args+=(--env-file "$SPARTAN_GATE_DIR/.env")
    fi

    docker compose "${args[@]}" "$@"
}

# -----------------------------------------------------------------------------
# Discoverable help
#
# Keep the old flat command names, but provide grouped help so a sourced shell
# can answer "what was that command called?" without opening this file.

sg_help() {
    cat <<'EOF'
Spartan Gate helpers

Usage:
  sg                         cd to the Spartan Gate checkout
  sg <compose-args...>        run docker compose with public + private config
  sg-tier <command>           select and operate L0-L4 install tiers
  sg -h | sg-help             show this help

Groups:
  sg-hermes -h                Hermes shell, profiles, gateway, and doctor
  sg-tier list                show tier descriptions
  sg-browser -h               Browserless/Camofox mode and profile helpers
  sg-meet -h                  Google Meet and host Chrome CDP helpers
  sg-disk -h                  PC, Docker, Apport, and Spartan cache usage

Common commands:
  sg-up                       start the selected tier
  sg-status | sg-health       container status and basic Hermes health
  sg-logs-hermes              follow Hermes logs
  sg-logs-clawroute           follow ClawRoute logs
  sg-urls                     print local/remote service URLs
  sg-doctor                   run repository preflight checks
  sg-whitelist-domain <host>  allow an outbound domain temporarily
  sg-reader-test [url]        test the bounded reader service
  sg-gog-login                refresh GogCLI and Google Workspace auth

Examples:
  sg up -d hermes
  sg logs -f proxy dns
  sg-disk top /var 2 30
EOF
}

sg_tier_help() {
    cat <<'EOF'
Tier helpers

Usage:
  sg-tier list                 show L0-L4 descriptions
  sg-tier set L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]
                               persist the selected tier in private/env/local.env
  sg-tier show                 show selected tier, addons, and Hermes mode
  sg-tier up|apply             start the selected tier
  sg-tier down                 stop the selected tier
  sg-tier setup                run `hermes setup` for the selected tier
  sg-tier status               show selected tier container status
  sg-tier doctor               validate repo and selected tier Compose config

Tiers:
  L0  Hermes only, free user-level package installs
  L1  Hermes + Camofox/noVNC, free user-level package installs
  L2  L1 + proxy, DNS, and Caddy edge
  L3  L2 + ClawRoute internal LLM routing compatibility alias
  L4  full Spartan Gate topology

Addons:
  clawroute can be enabled on L0, L1, or L2 without moving provider auth into Hermes.
EOF
}

sg_hermes_help() {
    cat <<'EOF'
Hermes helpers

Usage:
  sg-hermes -h                         show this help
  sg-hermes shell [args...]            open a shell in the Hermes container
  sg-hermes doctor [args...]           run `hermes doctor` in the container
  sg-hermes profile [args...]          run `hermes profile` in the container
  sg-hermes profiles                   list Hermes profiles
  sg-hermes profile-create <name>      create a Hermes profile
  sg-hermes profile-new [args...]      run the profile scaffolding helper
  sg-hermes gateway-profile <name>     start gateway for one profile

Flat aliases remain available:
  sg-hermes-shell, sg-hermes-doctor, sg-hermes-profile,
  sg-hermes-profiles, sg-hermes-profile-create,
  sg-hermes-profile-new, sg-hermes-gateway-profile
EOF
}

sg_browser_help() {
    cat <<'EOF'
Browser helpers

Usage:
  sg-browser -h                              show this help
  sg-browser mode                            print selected browser mode
  sg-browser apply                           apply selected mode and recreate Hermes
  sg-browser browserless-live <profile> [url]
  sg-browser browserless-snapshot
  sg-browser camofox-live <profile> [url]
  sg-browser camofox-use <profile> [session-key]
  sg-browser camofox-save <profile>
  sg-browser camofox-reset <profile>
  sg-browser camofox-health
  sg-browser camofox-smoke [url]
  sg-browser camofox-url

Selection:
  CAMOFOX_URL empty                  Browserless mode
  CAMOFOX_URL=http://camofox:9377    Camofox mode
EOF
}

sg_meet_help() {
    cat <<'EOF'
Meet helpers

Usage:
  sg-meet -h                                      show this help
  sg-meet chrome-profile <profile> [url] [port]   open host Chrome with CDP
  sg-meet join <meet-id-or-url> [duration] [profile] [port]
  sg-meet plugin <setup|install|auth|join|status|transcript|say|stop|leave|node> [args...]

Flat aliases remain available:
  sg-hermes-chrome-profile, sg-hermes-meet-join, sg-hermes-meet
EOF
}

sg_disk_help() {
    cat <<'EOF'
Disk helpers

Usage:
  sg-disk -h                         show this help
  sg-disk overview                   df summary for /, /home, /var, and Docker root
  sg-disk top [path] [depth] [limit] top disk users; defaults to $HOME 1 25
  sg-disk apport                     sizes for common Apport/crash-log paths
  sg-disk docker                     Docker system df plus host filesystem summary
  sg-disk spartan                    Spartan Gate repo/runtime/cache usage
  sg-disk clean-cache                remove known Spartan Hermes cache directories

Compatibility aliases:
  sg-docker-df, sg-cache-top, sg-cache-live, sg-cache-clean

Local extension:
  Define private/aliases.local.sh for machine-specific helpers such as
  sg-disk-private. That file is ignored by git.
EOF
}

sg_cd() { cd "$SPARTAN_GATE_DIR"; }
sg() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
        sg_help
    elif [[ "$#" -eq 0 ]]; then
        sg_cd
    else
        sg_compose "$@"
    fi
}

# Compose lifecycle and repository preflight. These are the day-to-day commands
# for starting, inspecting, rebuilding, and stopping the selected stack.
sg_priv() { sg_compose "$@"; }
sg_up() {
    if [[ "$#" -eq 0 ]]; then
        sg_tier_up
    else
        sg_compose up -d "$@"
    fi
}
sg_build_up() { sg_compose up -d --build "$@"; }
sg_config() { sg_compose config "$@"; }
sg_doctor() { (cd "$SPARTAN_GATE_DIR" && sh scripts/doctor.sh); }
sg_ps() { sg_compose ps "$@"; }
sg_status() { sg_ps "$@"; }
sg_logs() { sg_compose logs -f "$@"; }
sg_down() { sg_compose down "$@"; }
sg_exec() { sg_compose exec "$@"; }

sg_tier_list() {
    sg_tier_help | sed -n '/^Tiers:/,$p'
}

sg_tier_set() {
    if [[ "$#" -lt 1 ]]; then
        printf 'Usage: sg-tier set L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]\n' >&2
        return 2
    fi
    local tier profiles addons hermes_mode arg
    tier="$(sg_normalize_tier "$1")" || {
        printf 'Invalid Spartan Gate tier: %s\n' "$1" >&2
        return 2
    }
    shift
    addons="$(sg_default_addons "$tier")" || return 2
    hermes_mode="$(sg_default_hermes_mode "$tier")" || return 2
    while [[ "$#" -gt 0 ]]; do
        arg="$1"
        shift
        case "$arg" in
            --with)
                if [[ "$#" -lt 1 ]]; then
                    printf 'Usage: --with clawroute\n' >&2
                    return 2
                fi
                addons="$(sg_normalize_addons "$addons,$1")" || return 2
                shift
                ;;
            --addons)
                if [[ "$#" -lt 1 ]]; then
                    printf 'Usage: --addons clawroute|none\n' >&2
                    return 2
                fi
                addons="$(sg_normalize_addons "$1")" || return 2
                shift
                ;;
            --hermes)
                if [[ "$#" -lt 1 ]]; then
                    printf 'Usage: --hermes free|gated|full\n' >&2
                    return 2
                fi
                hermes_mode="$(sg_normalize_hermes_mode "$1")" || {
                    printf 'Invalid Hermes mode: %s\n' "$1" >&2
                    return 2
                }
                shift
                ;;
            *)
                printf 'Unknown sg-tier set option: %s\n' "$arg" >&2
                return 2
                ;;
        esac
    done
    if [[ "$tier" == "L3" || "$tier" == "L4" ]]; then
        addons="$(sg_default_addons "$tier")" || return 2
    fi
    profiles=""
    [[ "$tier" == "L4" ]] && profiles="camofox"
    sg_update_private_env \
        "SPARTAN_TIER=$tier" \
        "SPARTAN_ADDONS=$addons" \
        "SPARTAN_HERMES_MODE=$hermes_mode" \
        "COMPOSE_PROJECT_NAME=spartan-gate" \
        "COMPOSE_PROFILES=$profiles" || return 1
    printf 'Selected Spartan Gate tier: %s\n' "$tier"
    printf 'Addons: %s\n' "${addons:-none}"
    printf 'Hermes mode: %s\n' "$hermes_mode"
}

sg_tier_show() {
    local tier addons hermes_mode profiles project env_file
    tier="$(sg_current_tier)" || return 2
    addons="$(sg_current_addons "$tier")" || return 2
    hermes_mode="$(sg_current_hermes_mode "$tier")" || return 2
    profiles="$(sg_env_value COMPOSE_PROFILES "")"
    project="$(sg_env_value COMPOSE_PROJECT_NAME spartan-gate)"
    env_file="$(sg_default_env_file)"
    printf 'Tier: %s\n' "$tier"
    printf 'Addons: %s\n' "${addons:-none}"
    printf 'Hermes mode: %s\n' "$hermes_mode"
    printf 'Compose project: %s\n' "$project"
    printf 'Compose profiles: %s\n' "${profiles:-none}"
    printf 'Env file: %s\n' "$env_file"
}

sg_tier_up() {
    local tier
    tier="$(sg_current_tier)" || {
        printf 'Invalid SPARTAN_TIER value. Run: sg-tier set L0|L1|L2|L3|L4\n' >&2
        return 2
    }
    sg_tier_compose "$tier" config --quiet || return 1
    sg_tier_compose "$tier" up -d --remove-orphans || return 1
    sg_tier_compose "$tier" up -d --force-recreate hermes
}

sg_tier_down() {
    local tier
    tier="$(sg_current_tier)" || return 2
    sg_tier_compose "$tier" down "$@"
}

sg_tier_setup() {
    local tier
    tier="$(sg_current_tier)" || return 2
    sg_tier_compose "$tier" run --rm --no-deps hermes setup
}

sg_tier_status() {
    local tier
    tier="$(sg_current_tier)" || return 2
    sg_tier_compose "$tier" ps "$@"
}

sg_tier_doctor() {
    local tier
    tier="$(sg_current_tier)" || return 2
    (cd "$SPARTAN_GATE_DIR" && sh scripts/doctor.sh) || return 1
    sg_tier_compose "$tier" config --quiet
}

sg_tier() {
    local command="${1:-}"
    case "$command" in
        ""|-h|--help|help) sg_tier_help ;;
        list) shift; sg_tier_list "$@" ;;
        set) shift; sg_tier_set "$@" ;;
        show) shift; sg_tier_show "$@" ;;
        up) shift; sg_tier_up "$@" ;;
        apply) shift; sg_tier_up "$@" ;;
        down) shift; sg_tier_down "$@" ;;
        setup) shift; sg_tier_setup "$@" ;;
        status) shift; sg_tier_status "$@" ;;
        doctor) shift; sg_tier_doctor "$@" ;;
        *)
            printf 'Unknown sg-tier command: %s\n\n' "$command" >&2
            sg_tier_help >&2
            return 2
            ;;
    esac
}

sg_hermes() {
    local command="${1:-}"
    case "$command" in
        ""|-h|--help|help) sg_hermes_help ;;
        shell) shift; sg_hermes_shell "$@" ;;
        doctor) shift; sg_hermes_doctor "$@" ;;
        profile) shift; sg_hermes_profile "$@" ;;
        profiles) shift; sg_hermes_profiles "$@" ;;
        profile-create) shift; sg_hermes_profile_create "$@" ;;
        profile-new) shift; sg_hermes_profile_new "$@" ;;
        gateway-profile) shift; sg_hermes_gateway_profile "$@" ;;
        *)
            printf 'Unknown sg-hermes command: %s\n\n' "$command" >&2
            sg_hermes_help >&2
            return 2
            ;;
    esac
}

sg_deprecated() {
    printf 'Deprecated: %s. Use %s.\n' "$1" "$2" >&2
}

sg_up_core() {
    sg_deprecated 'sg_up_core / sg-up-core' 'sg-up or sg-browser-mode-apply for the selected browser mode'
    sg_browser_mode_apply "$@"
}

sg_up_hermes() {
    sg_deprecated 'sg_up_hermes / sg-up-hermes' 'sg up -d hermes for explicit service targeting'
    sg_compose up -d hermes "$@"
}

sg_up_private_hermes() {
    sg_deprecated 'sg_up_private_hermes / sg-up-private-hermes' 'sg up -d hermes for explicit service targeting'
    sg_compose up -d hermes "$@"
}

sg_up_full() {
    sg_deprecated 'sg_up_full / sg-up-full' 'sg-build-up or sg up -d --build'
    if [[ "$#" -eq 0 ]]; then
        sg_browser_mode_apply
    else
        sg_build_up "$@"
    fi
}

sg_restart_hermes() { sg_compose restart hermes; }
sg_restart_caddy() { sg_compose restart caddy; }
sg_restart_proxy() { sg_compose restart proxy dns; }
sg_rebuild_hermes() { sg_compose build hermes && sg_compose up -d hermes; }
sg_rebuild_clawroute() { sg_compose build clawroute && sg_compose up -d clawroute; }

sg_logs_hermes() { sg_compose logs -f hermes "$@"; }
sg_logs_clawroute() { sg_compose logs -f clawroute "$@"; }

# Hermes container helpers. Use these for shell access, profile management, and
# gateway startup without remembering the docker exec incantations.
sg_hermes_runtime_user() {
    docker exec -u root spartan_gate_hermes sh -lc 'printf "%s:%s\n" "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}" "${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"'
}
sg_hermes_exec() {
    local runtime_user
    runtime_user="$(sg_hermes_runtime_user)" || return
    docker exec \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        spartan_gate_hermes "$@"
}
sg_hermes_exec_i() {
    local runtime_user
    runtime_user="$(sg_hermes_runtime_user)" || return
    docker exec -i \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        spartan_gate_hermes "$@"
}
sg_hermes_exec_tty() {
    local runtime_user
    runtime_user="$(sg_hermes_runtime_user)" || return
    docker exec -it \
        -u "$runtime_user" \
        -e HOME=/opt/data \
        -e HERMES_HOME=/opt/data \
        -e USER=hermes \
        -e LOGNAME=hermes \
        spartan_gate_hermes "$@"
}
sg_hermes_shell() {
    if [[ "$#" -gt 0 ]]; then
        sg_hermes_exec_tty bash "$@"
        return
    fi
    sg_hermes_exec_tty sh -lc '
tmp_rc="$(mktemp /tmp/sg-hermes-bashrc.XXXXXX)"
cat > "$tmp_rc" <<'"'"'EOF'"'"'
test -f /etc/bash.bashrc && . /etc/bash.bashrc
test -f "$HOME/.bashrc" && . "$HOME/.bashrc"
PS1="hermes@\h:\w\$ "
EOF
exec bash --rcfile "$tmp_rc" -i
'
}
sg_hermes_doctor() { sg_hermes_exec hermes doctor "$@"; }
sg_hermes_profile() { sg_hermes_exec_tty hermes profile "$@"; }
sg_hermes_profiles() { sg_hermes_profile list; }
sg_hermes_profile_create() {
    if [[ "$#" -ne 1 ]]; then
        printf 'Usage: sg-hermes-profile-create <name>\n' >&2
        return 1
    fi
    sg_hermes_profile create "$1"
}
sg_hermes_profile_new() { "$SPARTAN_GATE_DIR/scripts/hermes-profile-new.sh" "$@"; }
sg_hermes_gateway_profile() {
    if [[ "$#" -ne 1 ]]; then
        printf 'Usage: sg-hermes-gateway-profile <name>\n' >&2
        return 1
    fi
    sg_hermes_exec_tty hermes -p "$1" gateway run
}

# Google Meet plugin wrapper. It normalizes "leave" to "stop", preserves the
# selected browser backend, and prepares the writable meeting workspace.
sg_hermes_meet() {
    if [[ "$#" -lt 1 ]]; then
        printf 'Usage: sg-hermes-meet <setup|install|auth|join|status|transcript|say|stop|leave|node> [args...]\n' >&2
        return 1
    fi

    local command="$1"
    shift
    if [[ "$command" == "leave" ]]; then
        command="stop"
    fi

    local camofox_mode=0
    if [[ -n "$(sg_env_value CAMOFOX_URL "")" ]]; then
        camofox_mode=1
    fi
    local runtime_user
    runtime_user="$(sg_hermes_runtime_user)" || return

    local docker_opts=(
        -u "$runtime_user"
        -e HOME=/opt/data
        -e HERMES_HOME=/opt/data
        -e USER=hermes
        -e LOGNAME=hermes
    )

    local meet_profile
    local meet_cdp_url
    meet_profile="$(sg_env_value HERMES_MEET_CDP_PROFILE "")"
    meet_cdp_url="$(sg_env_value HERMES_MEET_CDP_URL "")"
    if [[ "$camofox_mode" -eq 0 && -n "$meet_profile" ]]; then
        docker_opts+=(-e "HERMES_MEET_CDP_PROFILE=$meet_profile")
    fi
    if [[ "$camofox_mode" -eq 0 && -n "$meet_cdp_url" ]]; then
        docker_opts+=(-e "HERMES_MEET_CDP_URL=$meet_cdp_url")
    fi

    docker exec -u root -e HERMES_HOME=/opt/data spartan_gate_hermes sh -lc '
runtime_user="$1"
meet_workspace="${HERMES_HOME:-/opt/data}/workspace/meetings"
mkdir -p "$meet_workspace"
chown "$runtime_user" "${HERMES_HOME:-/opt/data}/workspace" "$meet_workspace" 2>/dev/null || true
chown -R "$runtime_user" "$meet_workspace" 2>/dev/null || true
chmod -R u+rwX,go-rwx "$meet_workspace" 2>/dev/null || true
' sh "$runtime_user" >/dev/null 2>&1 || true

    docker exec "${docker_opts[@]}" spartan_gate_hermes sh -lc '
cd /opt/hermes || exit 1
exec python -W ignore::RuntimeWarning -m plugins.google_meet.cli "$@"
' sh "$command" "$@"
}

sg_clawroute_shell() { docker exec -it spartan_gate_clawroute sh "$@"; }
sg_whitelist_domain() { "$SPARTAN_GATE_DIR/scripts/whitelist-domain.sh" "$@"; }
sg_add_port() { "$SPARTAN_GATE_DIR/scripts/add-port.sh" "$@"; }
sg_reader_test() {
    local url="${1:-https://example.com}"
    sg_compose exec -T reader python3 - "$url" <<'PY'
import sys
import urllib.parse
import urllib.request

url = sys.argv[1]
health = urllib.request.urlopen("http://127.0.0.1:3000/health", timeout=5).read().decode()
print("health:", health.strip())

endpoint = "http://127.0.0.1:3000/fetch?url=" + urllib.parse.quote(url, safe="")
with urllib.request.urlopen(endpoint, timeout=20) as response:
    body = response.read(1000).decode("utf-8", "replace")
print(body)
PY
}

# Browser mode selection. CAMOFOX_URL is the mode selector; helpers keep the
# inactive browser service stopped so operators do not mistake it for available.
sg_browser_mode() {
    if [[ -n "$(sg_env_value CAMOFOX_URL "")" ]]; then
        printf 'camofox\n'
    else
        printf 'browserless\n'
    fi
}

sg_validate_compose_profiles_for_mode() {
    local mode="$1"
    local configured
    local item
    local has_selected=0
    local has_other=0
    configured="$(sg_env_value COMPOSE_PROFILES "")"
    [[ -n "$configured" ]] || return 0

    IFS=',' read -ra _profiles <<< "$configured"
    for item in "${_profiles[@]}"; do
        item="$(echo "$item" | xargs)"
        [[ -n "$item" ]] || continue
        if [[ "$item" == "$mode" ]]; then
            has_selected=1
        elif [[ "$item" == "browserless" || "$item" == "camofox" ]]; then
            has_other=1
        fi
    done

    if [[ "$has_selected" -ne 1 || "$has_other" -ne 0 ]]; then
        printf 'Error: CAMOFOX_URL selects %s mode, but COMPOSE_PROFILES=%s.\n' "$mode" "$configured" >&2
        printf 'Fix private/env/local.env or run sg-camofox-profile-use <profile> for Camofox mode.\n' >&2
        return 1
    fi
}

sg_browser_mode_apply() {
    local mode active inactive
    mode="$(sg_browser_mode)"
    sg_validate_compose_profiles_for_mode "$mode" || return 1

    if [[ "$mode" == "camofox" ]]; then
        active="camofox"
        inactive="browserless"
    else
        active="browserless"
        inactive="camofox"
    fi

    printf 'Applying Spartan Gate browser mode: %s\n' "$mode"
    COMPOSE_PROFILES="$inactive" sg_compose stop "$inactive" >/dev/null 2>&1 || true
    COMPOSE_PROFILES="$mode" sg_compose up -d proxy dns searxng reader clawroute caddy "$active" || return 1
    COMPOSE_PROFILES="$mode" sg_compose up -d --force-recreate hermes
}

sg_private_env_file() {
    printf '%s\n' "$SPARTAN_GATE_DIR/private/env/local.env"
}

sg_update_private_env() {
    local env_file backup tmp
    env_file="$(sg_private_env_file)"
    mkdir -p "$(dirname "$env_file")"
    [[ -f "$env_file" ]] || : > "$env_file"
    backup="${env_file}.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$env_file" "$backup"
    tmp="$(mktemp)"
    awk '
BEGIN {
    last = ARGC - 1
    for (i = 1; i < last; i++) {
        key = ARGV[i]
        value = substr(key, index(key, "=") + 1)
        key = substr(key, 1, index(key, "=") - 1)
        keys[i] = key
        values[key] = value
        ARGV[i] = ""
    }
    key_count = last - 1
}
{
    stripped = $0
    sub(/^[ \t]*/, "", stripped)
    replaced = 0
    for (i = 1; i <= key_count; i++) {
        key = keys[i]
        if (stripped ~ "^" key "=") {
            print key "=" values[key]
            seen[key] = 1
            replaced = 1
            break
        }
    }
    if (!replaced) print $0
}
END {
    for (i = 1; i <= key_count; i++) {
        key = keys[i]
        if (!(key in seen)) print key "=" values[key]
    }
}
' "$@" "$env_file" > "$tmp" || {
        rm -f "$tmp"
        return 1
    }
    mv "$tmp" "$env_file"
    printf 'Updated %s (backup: %s)\n' "$env_file" "$backup"
}

sg_camofox_profile_user_id() {
    local profile="$1"
    if [[ ! "$profile" =~ ^[A-Za-z0-9._-]+$ || "$profile" == "." || "$profile" == ".." ]]; then
        printf 'Error: invalid Camofox profile name: %s\n' "$profile" >&2
        return 1
    fi
    printf 'spartan-camofox-%s\n' "$profile"
}

sg_urlencode() {
    python3 - "$1" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
}

sg_camofox_novnc_url() {
    local enabled scheme host ip port resolved password encoded_password fragment
    enabled="$(sg_env_value CAMOFOX_ENABLE_VNC 0)"
    [[ "$enabled" == "1" || "$enabled" == "true" ]] || return 1
    scheme="$(sg_edge_scheme)"
    ip="$(sg_env_value TAILSCALE_IP localhost)"
    if [[ "$scheme" == "https" ]]; then
        host="$(sg_env_value TAILSCALE_HOST "")"
        if [[ -z "$host" ]]; then
            printf 'Error: HTTPS noVNC requires TAILSCALE_HOST covered by the configured certificate.\n' >&2
            return 1
        fi
        resolved="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u)"
        if ! grep -qxF "$ip" <<< "$resolved"; then
            printf 'Error: HTTPS noVNC hostname %s does not resolve to TAILSCALE_IP %s.\n' "$host" "$ip" >&2
            return 1
        fi
    else
        host="$ip"
    fi
    port="$(sg_env_value CAMOFOX_NOVNC_PORT 26080)"
    password="$(sg_env_value CAMOFOX_VNC_PASSWORD "")"
    fragment="autoconnect=1"
    if [[ -n "$password" ]]; then
        encoded_password="$(sg_urlencode "$password")" || return 1
        fragment="${fragment}&password=${encoded_password}"
    fi
    printf '%s://%s:%s/vnc_lite.html#%s\n' "$scheme" "$host" "$port" "$fragment"
}

sg_camofox_novnc_reachable() {
    local url status
    url="$(sg_camofox_novnc_url)" || return 1
    url="${url%%#*}"
    status="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 5 "$url" 2>/dev/null || true)"
    case "$status" in
        200|401) return 0 ;;
        *)
            printf 'Error: Camofox noVNC is not reachable through Caddy at %s (HTTP %s).\n' "$url" "${status:-unreachable}" >&2
            return 1
            ;;
    esac
}

sg_camofox_require_novnc() {
    local password config
    if ! sg_camofox_novnc_url >/dev/null; then
        printf 'Error: CAMOFOX_ENABLE_VNC must be 1 to use Camofox live profiles.\n' >&2
        return 1
    fi
    password="$(sg_env_value CAMOFOX_VNC_PASSWORD "")"
    if [[ -z "$password" ]]; then
        printf 'Error: CAMOFOX_VNC_PASSWORD must be set before exposing noVNC.\n' >&2
        return 1
    fi
    config="$(COMPOSE_PROFILES=camofox sg_compose config 2>/dev/null || true)"
    if ! grep -q 'target: 26080' <<< "$config"; then
        printf 'Error: Camofox noVNC Caddy port is not published in Compose.\n' >&2
        printf 'Enable the Tailscale-only caddy CAMOFOX_NOVNC_PORT mapping in private/compose.local.yml.\n' >&2
        return 1
    fi
}

sg_camofox_request_node() {
    local script="$1"
    shift
    local key
    key="$(sg_env_value CAMOFOX_ACCESS_KEY change-me-camofox-access-key)"
    COMPOSE_PROFILES=camofox sg_compose exec -T \
        -e CAMOFOX_HELPER_KEY="$key" \
        "$@" \
        camofox node -e "$script"
}

sg_camofox_wait_ready() {
    local i
    for i in {1..60}; do
        if COMPOSE_PROFILES=camofox sg_compose exec -T camofox curl -fsS http://127.0.0.1:9377/health >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    printf 'Error: Camofox did not become ready at /health.\n' >&2
    return 1
}

sg_camofox_service_running() {
    COMPOSE_PROFILES=camofox sg_compose ps --status running --services camofox 2>/dev/null | grep -qx camofox
}

sg_camofox_novnc_ready() {
    sg_camofox_service_running || return 1
    COMPOSE_PROFILES=camofox sg_compose exec -T camofox \
        curl -fsS http://127.0.0.1:6080/vnc_lite.html >/dev/null 2>&1
}

sg_camofox_ensure_live_service() {
    COMPOSE_PROFILES=camofox sg_compose up -d proxy dns caddy || return 1

    if sg_camofox_service_running; then
        if sg_camofox_novnc_ready; then
            sg_camofox_novnc_reachable
            return $?
        fi
        printf 'Error: camofox is running, but noVNC is not reachable at http://127.0.0.1:6080/vnc_lite.html inside the container.\n' >&2
        printf 'Not recreating camofox automatically because that could interrupt active browser sessions.\n' >&2
        printf 'If you changed VNC env or need to recover it, run sg-browser-mode-apply after active browser work is done.\n' >&2
        return 1
    fi

    COMPOSE_PROFILES=camofox sg_compose up -d camofox || return 1
    sg_camofox_wait_ready || return 1
    if ! sg_camofox_novnc_ready; then
        printf 'Error: camofox started, but noVNC did not become reachable at http://127.0.0.1:6080/vnc_lite.html inside the container.\n' >&2
        printf 'Check CAMOFOX_ENABLE_VNC, CAMOFOX_VNC_PASSWORD, and sg-logs-camofox.\n' >&2
        return 1
    fi
    sg_camofox_novnc_reachable
}

sg_camofox_profile_live() {
    if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
        printf 'Usage: sg-camofox-profile-live <profile> [url]\n' >&2
        return 1
    fi

    local profile="$1"
    local url="${2:-https://accounts.google.com}"
    local user_id
    local session_key="manual-login"
    local novnc_url
    user_id="$(sg_camofox_profile_user_id "$profile")" || return 1
    sg_camofox_require_novnc || return 1

    sg_camofox_ensure_live_service || return 1
    sg_camofox_request_node '
const key = process.env.CAMOFOX_HELPER_KEY || "";
const userId = process.env.CAMOFOX_PROFILE_USER_ID;
const sessionKey = process.env.CAMOFOX_PROFILE_SESSION_KEY;
const url = process.env.CAMOFOX_PROFILE_URL;
const base = "http://127.0.0.1:9377";
async function request(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, { Authorization: `Bearer ${key}` });
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(`${base}${path}`, Object.assign({}, options, { headers }));
  const text = await response.text();
  if (!response.ok) throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${text}`);
  try { return text ? JSON.parse(text) : null; } catch { return text; }
}
(async () => {
  const created = await request("/tabs", {
    method: "POST",
    body: JSON.stringify({ userId, sessionKey, url }),
  });
  console.log(JSON.stringify({ ok: true, userId, sessionKey, url, created }, null, 2));
})().catch((error) => {
  console.error(error.message || String(error));
  process.exit(1);
});
' -e CAMOFOX_PROFILE_USER_ID="$user_id" -e CAMOFOX_PROFILE_SESSION_KEY="$session_key" -e CAMOFOX_PROFILE_URL="$url" || return 1

    novnc_url="$(sg_camofox_novnc_url)"
    printf '\nCamofox profile is ready for manual login.\n'
    printf 'Profile:             %s\n' "$profile"
    printf 'CAMOFOX_USER_ID:     %s\n' "$user_id"
    printf 'CAMOFOX_SESSION_KEY: %s\n' "$session_key"
    printf 'Camofox noVNC:       %s\n' "$novnc_url"
    printf '\nAfter logging in, run:\n'
    printf '  sg-camofox-profile-save %s\n' "$profile"
    printf '  sg-camofox-profile-use %s\n' "$profile"
    printf '  sg-browser-mode-apply\n'
}

sg_camofox_profile_use() {
    if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
        printf 'Usage: sg-camofox-profile-use <profile> [session-key]\n' >&2
        return 1
    fi
    local profile="$1"
    local session_key="${2:-manual-login}"
    local user_id
    user_id="$(sg_camofox_profile_user_id "$profile")" || return 1
    sg_update_private_env \
        "COMPOSE_PROFILES=camofox" \
        "CAMOFOX_URL=http://camofox:9377" \
        "CAMOFOX_USER_ID=$user_id" \
        "CAMOFOX_SESSION_KEY=$session_key" \
        "CAMOFOX_ADOPT_EXISTING_TAB=true" || return 1
    printf 'Camofox profile %s selected for Hermes. Run: sg-browser-mode-apply\n' "$profile"
}

sg_camofox_profile_save() {
    if [[ "$#" -ne 1 ]]; then
        printf 'Usage: sg-camofox-profile-save <profile>\n' >&2
        return 1
    fi
    local profile="$1"
    local user_id root
    user_id="$(sg_camofox_profile_user_id "$profile")" || return 1
    root="$(sg_env_value SPARTAN_CAMOFOX_DATA_PATH "$SPARTAN_GATE_DIR/runtime/camofox")"
    sg_camofox_request_node '
const key = process.env.CAMOFOX_HELPER_KEY || "";
const userId = process.env.CAMOFOX_PROFILE_USER_ID;
const base = "http://127.0.0.1:9377";
fetch(`${base}/tabs?userId=${encodeURIComponent(userId)}`, {
  headers: { Authorization: `Bearer ${key}` },
}).then(async (response) => {
  const text = await response.text();
  if (!response.ok) throw new Error(`GET /tabs -> ${response.status}: ${text}`);
  console.log(text || "{}");
}).catch((error) => {
  console.error(error.message || String(error));
  process.exit(1);
});
' -e CAMOFOX_PROFILE_USER_ID="$user_id" || return 1
    printf 'Checked active Camofox profile %s.\n' "$user_id"
    printf 'State root: %s\n' "$root"
    find "$root" -maxdepth 4 \( -name "$user_id" -o -name "$user_id.json" \) -print 2>/dev/null || true
    printf 'No session deletion was performed.\n'
}

sg_camofox_profile_reset() {
    if [[ "$#" -ne 1 ]]; then
        printf 'Usage: sg-camofox-profile-reset <profile>\n' >&2
        return 1
    fi
    local profile="$1"
    local user_id root confirmation
    user_id="$(sg_camofox_profile_user_id "$profile")" || return 1
    root="$(sg_env_value SPARTAN_CAMOFOX_DATA_PATH "$SPARTAN_GATE_DIR/runtime/camofox")"
    printf 'This will delete Camofox persisted state for %s under %s.\n' "$user_id" "$root" >&2
    printf 'Type exactly "reset %s" to continue: ' "$user_id" >&2
    IFS= read -r confirmation
    if [[ "$confirmation" != "reset $user_id" ]]; then
        printf 'Reset cancelled.\n' >&2
        return 1
    fi
    COMPOSE_PROFILES=camofox sg_compose stop hermes camofox || true
    rm -rf \
        "$root/profiles/$user_id" \
        "$root/profiles/$user_id.json" \
        "$root/cookies/$user_id" \
        "$root/cookies/$user_id.json" \
        "$root/traces/$user_id"
    printf 'Deleted known Camofox state paths for %s.\n' "$user_id"
}

sg_browserless_snapshot() {
    (cd "$SPARTAN_GATE_DIR" && bash scripts/browserless-snapshot.sh)
}

sg_browserless_profile_live() {
    if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
        printf 'Usage: sg-browserless-profile-live <profile> [url]\n' >&2
        return 1
    fi

    local profile="$1"
    local url="${2:-}"
    local active_profile
    local origins
    active_profile="$(sg_env_value BROWSERLESS_PROFILE main)"

    if [[ "$profile" == "$active_profile" ]] && sg_compose ps --status running --services hermes 2>/dev/null | grep -qx hermes; then
        printf 'Error: hermes is running with BROWSERLESS_PROFILE=%s. Stop hermes before opening that Browserless profile in the debugger.\n' "$profile" >&2
        return 1
    fi

    origins="$(sg_browserless_debug_origins)"
    sg_compose up -d browserless caddy
    sg_compose run --rm --no-deps --entrypoint node \
        -e BROWSERLESS_DEBUG_ORIGIN= -e BROWSERLESS_DEBUG_ORIGINS="$origins" hermes \
        /opt/hermes/bootstrap/browserless-profile-live.js "$profile" "$url"
}

sg_spartan_internal_gateway() {
    local project gateway
    project="$(sg_env_value COMPOSE_PROJECT_NAME spartan-gate)"
    gateway="$(docker network inspect "${project}_spartan_internal" --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
    if [[ -z "$gateway" ]]; then
        gateway="$(sg_env_value SPARTAN_INTERNAL_SUBNET 172.28.0.0/24)"
        gateway="${gateway%.*}.1"
    fi
    printf '%s\n' "$gateway"
}

sg_hermes_chrome_bin() {
    local candidate
    for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    printf 'Error: google-chrome, google-chrome-stable, chromium, or chromium-browser was not found in PATH.\n' >&2
    return 1
}

sg_hermes_chrome_relay_start() {
    local port="${1:-9222}"
    local gateway
    local pid_file
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        printf 'Usage: sg-hermes-chrome-profile <profile> [url] [port]\n' >&2
        return 1
    fi
    if ! command -v socat >/dev/null 2>&1; then
        printf 'Error: socat is required for the Docker-to-host Chrome CDP relay.\n' >&2
        return 1
    fi
    gateway="$(sg_spartan_internal_gateway)"
    pid_file="${TMPDIR:-/tmp}/spartan-gate-hermes-chrome-relay-${port}.pid"

    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        return 0
    fi
    if ss -ltn "( sport = :$port )" 2>/dev/null | awk '{print $4}' | grep -qx "${gateway}:${port}"; then
        return 0
    fi

    socat "TCP-LISTEN:${port},bind=${gateway},fork,reuseaddr" "TCP:127.0.0.1:${port}" \
        >"/tmp/spartan-gate-hermes-chrome-relay-${port}.log" 2>&1 &
    printf '%s\n' "$!" > "$pid_file"
    sleep 0.2
    if ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        printf 'Error: Chrome CDP relay failed to start; see /tmp/spartan-gate-hermes-chrome-relay-%s.log\n' "$port" >&2
        return 1
    fi
}

sg_hermes_chrome_ws_url() {
    local port="${1:-9222}"
    local gateway
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        printf 'Usage: sg-hermes-chrome-profile <profile> [url] [port]\n' >&2
        return 1
    fi
    gateway="$(sg_spartan_internal_gateway)"
    python3 -c 'import json, sys, urllib.request
from urllib.parse import urlsplit, urlunsplit

port = sys.argv[1]
gateway = sys.argv[2]
url = f"http://127.0.0.1:{port}/json/version"
with urllib.request.urlopen(url, timeout=2) as response:
    data = json.loads(response.read().decode("utf-8"))
ws_url = data.get("webSocketDebuggerUrl", "")
if not ws_url:
    raise SystemExit(f"Chrome CDP did not publish webSocketDebuggerUrl at {url}")
parts = urlsplit(ws_url)
print(urlunsplit((parts.scheme, f"{gateway}:{port}", parts.path, parts.query, parts.fragment)))' "$port" "$gateway"
}

sg_hermes_chrome_profile() {
    if [[ "$#" -lt 1 || "$#" -gt 3 ]]; then
        printf 'Usage: sg-hermes-chrome-profile <profile> [url] [port]\n' >&2
        return 1
    fi

    local profile="$1"
    local url="${2:-https://accounts.google.com}"
    local port="${3:-9222}"
    local profile_root
    local profile_dir
    local chrome_bin
    local cdp_url=""
    local i

    if [[ ! "$profile" =~ ^[A-Za-z0-9._-]+$ || "$profile" == "." || "$profile" == ".." ]]; then
        printf 'Error: invalid Chrome profile name: %s\n' "$profile" >&2
        return 1
    fi
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        printf 'Usage: sg-hermes-chrome-profile <profile> [url] [port]\n' >&2
        return 1
    fi

    chrome_bin="$(sg_hermes_chrome_bin)" || return 1
    profile_root="$(sg_env_value HERMES_CHROME_PROFILES_ROOT "$HOME/.config/hermes-chrome-profiles")"
    profile_dir="$profile_root/$profile"
    mkdir -p "$profile_dir"

    if sg_hermes_chrome_ws_url "$port" >/dev/null 2>&1; then
        printf 'Error: Chrome CDP is already listening on 127.0.0.1:%s. Close it or choose another port.\n' "$port" >&2
        return 1
    fi

    sg_hermes_chrome_relay_start "$port" || return 1

    printf 'Opening Chrome profile %s at %s\n' "$profile" "$profile_dir"
    "$chrome_bin" \
        --remote-debugging-port="$port" \
        --user-data-dir="$profile_dir" \
        --profile-directory=Default \
        --no-first-run \
        --no-default-browser-check \
        "$url" >/tmp/spartan-gate-hermes-chrome-${port}.log 2>&1 &

    for i in {1..50}; do
        if cdp_url="$(sg_hermes_chrome_ws_url "$port" 2>/dev/null)"; then
            break
        fi
        sleep 0.2
    done
    if [[ -z "$cdp_url" ]]; then
        printf 'Error: Chrome opened but CDP did not become ready on 127.0.0.1:%s. See /tmp/spartan-gate-hermes-chrome-%s.log\n' "$port" "$port" >&2
        return 1
    fi

    printf '\nHermes Chrome profile is ready. Keep this Chrome window open.\n'
    printf 'HERMES_MEET_CDP_PROFILE=%s\n' "$profile"
    printf 'HERMES_MEET_CDP_URL=%s\n' "$cdp_url"
    printf '\nThis command will wait while Chrome CDP is reachable. Press Ctrl-C to stop waiting; Chrome is left open.\n'

    trap 'printf "\nStopped waiting; Chrome was left open.\n"; trap - INT; return 130' INT
    while sg_hermes_chrome_ws_url "$port" >/dev/null 2>&1; do
        sleep 2
    done
    trap - INT
    printf 'Chrome CDP stopped.\n'
}

sg_hermes_meet_url() {
    if [[ "$#" -ne 1 ]]; then
        printf 'Usage: sg-hermes-meet-join <meet-id-or-url> [duration] [profile] [port]\n' >&2
        return 1
    fi
    python3 -c 'import re, sys
from urllib.parse import urlparse

raw = sys.argv[1].strip()
if not raw:
    raise SystemExit("Error: Meet URL or ID is required")
if re.fullmatch(r"[a-z]{3}-[a-z]{4}-[a-z]{3}", raw, re.I):
    print("https://meet.google.com/" + raw.lower())
    raise SystemExit(0)
candidate = raw if "://" in raw else "https://" + raw
parsed = urlparse(candidate)
host = parsed.netloc.lower()
if host not in {"meet.google.com", "www.meet.google.com"}:
    raise SystemExit(f"Error: not a Google Meet URL or meeting ID: {raw}")
match = re.search(r"[a-z]{3}-[a-z]{4}-[a-z]{3}", parsed.path, re.I)
if not match:
    raise SystemExit(f"Error: no Meet ID found in: {raw}")
print("https://meet.google.com/" + match.group(0).lower())' "$1"
}

sg_hermes_set_meet_env() {
    if [[ "$#" -ne 2 ]]; then
        printf 'Usage: sg_hermes_set_meet_env <profile> <cdp-url>\n' >&2
        return 1
    fi
    local profile="$1"
    local cdp_url="$2"
    local env_file="$SPARTAN_GATE_DIR/private/env/local.env"
    mkdir -p "$(dirname "$env_file")"
    python3 -c 'from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "HERMES_MEET_CDP_PROFILE": sys.argv[2],
    "HERMES_MEET_CDP_URL": sys.argv[3],
}
lines = path.read_text().splitlines() if path.exists() else []
seen = set()
out = []
for line in lines:
    stripped = line.lstrip()
    replaced = False
    for key, value in updates.items():
        if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
            out.append(f"{key}={value}")
            seen.add(key)
            replaced = True
            break
    if not replaced:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")' "$env_file" "$profile" "$cdp_url"
}

sg_hermes_meet_join() {
    if [[ "$#" -lt 1 || "$#" -gt 4 ]]; then
        printf 'Usage: sg-hermes-meet-join <meet-id-or-url> [duration] [profile] [port]\n' >&2
        return 1
    fi

    local meet_input="$1"
    local duration="${2:-20m}"
    local profile="${3:-}"
    local port="${4:-9222}"
    local meet_url
    local cdp_url

    if [[ -z "$profile" ]]; then
        profile="$(sg_env_value HERMES_MEET_CDP_PROFILE google2)"
    fi
    if [[ -z "$profile" ]]; then
        profile="google2"
    fi
    if [[ ! "$profile" =~ ^[A-Za-z0-9._-]+$ || "$profile" == "." || "$profile" == ".." ]]; then
        printf 'Error: invalid Chrome profile name: %s\n' "$profile" >&2
        return 1
    fi
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        printf 'Usage: sg-hermes-meet-join <meet-id-or-url> [duration] [profile] [port]\n' >&2
        return 1
    fi
    if [[ -z "$duration" ]]; then
        printf 'Error: duration must not be empty\n' >&2
        return 1
    fi

    meet_url="$(sg_hermes_meet_url "$meet_input")" || return 1
    if [[ -n "$(sg_env_value CAMOFOX_URL "")" ]]; then
        printf 'Meet URL: %s\n' "$meet_url"
        printf 'Using Camofox Meet backend with CAMOFOX_USER_ID=%s\n' "$(sg_env_value CAMOFOX_USER_ID spartan-camofox-main)"
        sg_hermes_meet setup || return 1
        sg_hermes_meet join "$meet_url" --duration "$duration" --mode transcribe
        return $?
    fi
    if ! cdp_url="$(sg_hermes_chrome_ws_url "$port" 2>/dev/null)"; then
        printf 'Error: Chrome CDP is not reachable on 127.0.0.1:%s. Start it first with:\n' "$port" >&2
        printf '  sg-hermes-chrome-profile %s https://accounts.google.com %s\n' "$profile" "$port" >&2
        return 1
    fi
    sg_hermes_chrome_relay_start "$port" || return 1
    sg_hermes_set_meet_env "$profile" "$cdp_url" || return 1

    printf 'Updated private/env/local.env for Meet profile %s.\n' "$profile"
    printf 'Meet URL: %s\n' "$meet_url"
    sg_compose up -d hermes --force-recreate || return 1

    sg_hermes_meet setup || return 1
    sg_hermes_meet join "$meet_url" --duration "$duration"
}

sg_edge_scheme() {
    local configured
    configured="$(sg_env_value SPARTAN_EDGE_SCHEME "")"
    if [[ -n "$configured" ]]; then
        printf '%s\n' "$configured"
    elif [[ -n "$(sg_env_value SPARTAN_CADDY_TLS_CERT_FILE "")" || -n "$(sg_env_value SPARTAN_CADDY_CERTS_PATH "")" ]]; then
        printf 'https\n'
    else
        printf 'http\n'
    fi
}
sg_remote_host() {
    local scheme host
    scheme="$(sg_edge_scheme)"
    if [[ "$scheme" == "https" ]]; then
        host="$(sg_env_value TAILSCALE_HOST "")"
        [[ -n "$host" ]] || host="$(sg_env_value TAILSCALE_IP localhost)"
    else
        host="$(sg_env_value TAILSCALE_IP localhost)"
    fi
    printf '%s\n' "$host"
}

sg_browserless_debug_origins() {
    local override scheme tailscale_host ip port label origin existing index
    local -a labels origins
    local add_origin
    add_origin() {
        label="$1"
        origin="$2"
        [[ -n "$origin" ]] || return 0
        for existing in "${origins[@]}"; do
            [[ "$existing" == "$origin" ]] && return 0
        done
        labels+=("$label")
        origins+=("$origin")
    }

    override="$(sg_env_value BROWSERLESS_DEBUG_ORIGIN "")"
    if [[ -n "$override" ]]; then
        add_origin configured "$override"
    fi

    scheme="$(sg_edge_scheme)"
    tailscale_host="$(sg_env_value TAILSCALE_HOST "")"
    ip="$(sg_env_value TAILSCALE_IP "")"
    port="$(sg_env_value BROWSERLESS_DEBUG_PORT 3005)"

    add_origin local "${scheme}://localhost:${port}"
    if [[ -n "$tailscale_host" && "$tailscale_host" != "localhost" ]]; then
        add_origin tailscale-host "${scheme}://${tailscale_host}:${port}"
    fi
    if [[ -n "$ip" && "$ip" != "localhost" ]]; then
        add_origin tailscale-ip "${scheme}://${ip}:${port}"
    fi

    for index in "${!origins[@]}"; do
        printf '%s|%s\n' "${labels[$index]}" "${origins[$index]}"
    done
}

sg_urls() {
    local scheme host gate claw browserless dashboard camofox_vnc camofox_enabled
    scheme="$(sg_edge_scheme)"
    host="$(sg_remote_host)"
    gate="$(sg_env_value SPARTAN_GATE_PORT 18789)"
    claw="$(sg_env_value CLAWROUTE_EDGE_PORT 18790)"
    browserless="$(sg_env_value BROWSERLESS_DEBUG_PORT 3005)"
    dashboard="$(sg_env_value HERMES_DASHBOARD_PORT 9119)"
    camofox_vnc="$(sg_env_value CAMOFOX_NOVNC_PORT 26080)"
    camofox_enabled="$(sg_env_value CAMOFOX_ENABLE_VNC 0)"
    if [[ "$scheme" == "http" ]]; then
        printf 'Spartan Gate local:      http://localhost:%s\n' "$gate"
        printf 'ClawRoute local:         http://localhost:%s\n' "$claw"
        printf 'Browserless local:       http://localhost:%s\n' "$browserless"
        printf 'Hermes dashboard local:  http://localhost:%s\n' "$dashboard"
    fi
    if [[ "$host" != "localhost" || "$scheme" != "http" ]]; then
        printf 'Spartan Gate remote:     %s://%s:%s\n' "$scheme" "$host" "$gate"
        printf 'ClawRoute remote:        %s://%s:%s\n' "$scheme" "$host" "$claw"
        printf 'Browserless remote:      %s://%s:%s\n' "$scheme" "$host" "$browserless"
        printf 'Hermes dashboard remote: %s://%s:%s\n' "$scheme" "$host" "$dashboard"
    fi
    if [[ "$camofox_enabled" == "1" || "$camofox_enabled" == "true" ]]; then
        printf 'Camofox noVNC private:   %s\n' "$(sg_camofox_novnc_url)"
    fi
}

sg_health() {
    sg_status
    if docker exec spartan_gate_hermes true >/dev/null 2>&1; then
        sg_hermes_exec sh -lc 'for p in 8642 8643 8644; do printf "%s " "$p"; wget -qO- "http://127.0.0.1:$p/health" 2>/dev/null || printf FAIL; printf "\n"; done'
    fi
}

# -----------------------------------------------------------------------------
# Diagnostics, ports, disk, and cache
#
# These helpers are read-only except sg_cache_clean/sg_disk clean-cache, which
# intentionally removes known Hermes cache directories and npm transient files.

sg_caddy_validate() { sg_compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile; }
sg_ports() { ss -H -ltn '( sport = :18789 or sport = :18790 or sport = :3005 or sport = :9119 )' 2>/dev/null || sg_compose ps; }
sg_docker_root() {
    docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
}
sg_disk_overview() {
    local docker_root path
    local -a paths=("/")
    [[ -d /home ]] && paths+=("/home")
    [[ -d /var ]] && paths+=("/var")
    docker_root="$(sg_docker_root)"
    [[ -n "$docker_root" && -d "$docker_root" ]] && paths+=("$docker_root")

    printf 'Filesystem overview:\n'
    for path in "${paths[@]}"; do
        df -h "$path" 2>/dev/null | awk 'NR == 1 || NR == 2'
    done | awk '!seen[$0]++'
}
sg_disk_top() {
    local path="${1:-$HOME}"
    local depth="${2:-1}"
    local limit="${3:-25}"
    if [[ ! "$depth" =~ ^[0-9]+$ || ! "$limit" =~ ^[0-9]+$ ]]; then
        printf 'Usage: sg-disk top [path] [depth] [limit]\n' >&2
        return 1
    fi
    if [[ ! -e "$path" ]]; then
        printf 'Error: path does not exist: %s\n' "$path" >&2
        return 1
    fi
    du -ah --max-depth="$depth" "$path" 2>/dev/null | sort -hr | head -n "$limit"
}
sg_disk_apport() {
    local pattern path
    local -a matches=()
    for pattern in \
        /var/crash \
        /var/log/apport.log \
        /var/log/apport.log.1 \
        /var/log/apport.log.* \
        /var/lib/apport \
        "$HOME/.cache/apport"; do
        while IFS= read -r path; do
            [[ -n "$path" ]] && matches+=("$path")
        done < <(compgen -G "$pattern" 2>/dev/null || true)
    done
    if [[ "${#matches[@]}" -eq 0 ]]; then
        printf 'No common Apport/crash-log paths were found.\n'
        return 0
    fi
    du -sh "${matches[@]}" 2>/dev/null | sort -hr
}
sg_disk_docker() {
    local docker_root
    docker system df
    printf '%s\n' '---'
    docker_root="$(sg_docker_root)"
    if [[ -n "$docker_root" && -d "$docker_root" ]]; then
        df -h "$docker_root"
    else
        df -h /home 2>/dev/null || df -h /
    fi
}
sg_docker_df() { sg_disk_docker "$@"; }
sg_cache_top() {
    local data_dir
    data_dir="$(sg_env_value SPARTAN_HERMES_DATA_PATH "$(sg_env_value HERMES_DATA_PATH "$SPARTAN_GATE_DIR/runtime/hermes")")"
    du -ah --max-depth=2 "$data_dir/.cache" "$data_dir/.npm" 2>/dev/null | sort -hr | head -n 20
}
sg_cache_live() {
    sg_hermes_exec sh -lc 'du -ah --max-depth=2 /opt/data/.cache /opt/data/.npm 2>/dev/null | sort -hr | head -n 20'
}
sg_cache_clean() {
    local data_dir
    data_dir="$(sg_env_value SPARTAN_HERMES_DATA_PATH "$(sg_env_value HERMES_DATA_PATH "$SPARTAN_GATE_DIR/runtime/hermes")")"
    rm -rf "$data_dir/.cache/huggingface" "$data_dir/.npm/_cacache" "$data_dir/.npm/_npx"
    find "$data_dir/.npm/_logs" -type f -delete 2>/dev/null || true
    du -ah --max-depth=2 "$data_dir/.cache" "$data_dir/.npm" 2>/dev/null | sort -hr | head -n 20
}
sg_disk_spartan() {
    local data_dir
    local path
    data_dir="$(sg_env_value SPARTAN_HERMES_DATA_PATH "$(sg_env_value HERMES_DATA_PATH "$SPARTAN_GATE_DIR/runtime/hermes")")"
    printf 'Spartan Gate paths:\n'
    for path in \
        "$SPARTAN_GATE_DIR" \
        "$SPARTAN_GATE_DIR/runtime" \
        "$data_dir" \
        "$data_dir/.cache" \
        "$data_dir/.npm" \
        "$data_dir/profiles"; do
        [[ -e "$path" ]] && du -sh "$path" 2>/dev/null
    done
    printf '%s\n' '---'
    printf 'Largest Spartan cache entries:\n'
    sg_cache_top
}
sg_disk() {
    local command="${1:-overview}"
    case "$command" in
        -h|--help|help) sg_disk_help ;;
        overview) shift; sg_disk_overview "$@" ;;
        top) shift; sg_disk_top "$@" ;;
        apport) shift; sg_disk_apport "$@" ;;
        docker) shift; sg_disk_docker "$@" ;;
        spartan) shift; sg_disk_spartan "$@" ;;
        clean-cache) shift; sg_cache_clean "$@" ;;
        *)
            printf 'Unknown sg-disk command: %s\n\n' "$command" >&2
            sg_disk_help >&2
            return 2
            ;;
    esac
}
sg_profile_ports() {
    local data_dir
    data_dir="$(sg_env_value SPARTAN_HERMES_DATA_PATH "$(sg_env_value HERMES_DATA_PATH "$SPARTAN_GATE_DIR/runtime/hermes")")"
    find "$data_dir/profiles" -mindepth 2 -maxdepth 2 -name .env \
        -exec awk -F= '$1 == "API_SERVER_PORT" {print FILENAME ":" FNR ":" $0}' {} + 2>/dev/null || true
}
sg_gog_check() { sg_hermes_exec gog auth list --check "$@"; }
sg_gog_login() { "$SPARTAN_GATE_DIR/scripts/gog-workspace-auth.sh" "$@"; }
sg_logs_camofox() { sg_compose logs -f camofox "$@"; }
sg_camofox_health() { sg_compose exec -T camofox curl -fsS http://127.0.0.1:9377/health; }
sg_camofox_smoke() {
    local key url
    key="$(sg_env_value CAMOFOX_ACCESS_KEY change-me-camofox-access-key)"
    url="${1:-https://example.com}"
    sg_compose exec -T \
        -e CAMOFOX_SMOKE_KEY="$key" \
        -e CAMOFOX_SMOKE_URL="$url" \
        camofox node -e '
const key = process.env.CAMOFOX_SMOKE_KEY || "";
const url = process.env.CAMOFOX_SMOKE_URL || "https://example.com";
const base = "http://127.0.0.1:9377";
const userId = "spartan-smoke";
const sessionKey = "default";
async function request(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, {
    "Authorization": `Bearer ${key}`,
  });
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(`${base}${path}`, Object.assign({}, options, { headers }));
  const text = await response.text();
  let body = text;
  try { body = text ? JSON.parse(text) : null; } catch {}
  if (!response.ok) {
    throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${text}`);
  }
  return body;
}
(async () => {
  const created = await request("/tabs", {
    method: "POST",
    body: JSON.stringify({ userId, sessionKey, url }),
  });
  const tabs = await request(`/tabs?userId=${encodeURIComponent(userId)}`);
  await request(`/sessions/${encodeURIComponent(userId)}`, { method: "DELETE" }).catch((error) => {
    console.error(`cleanup warning: ${error.message}`);
  });
  console.log(JSON.stringify({ ok: true, created, tabCount: tabs.tabs?.length ?? null }, null, 2));
})().catch((error) => {
  console.error(error.message || String(error));
  process.exit(1);
});
'
}
sg_camofox_url() {
    local enabled
    printf 'Camofox internal: http://camofox:9377\n'
    enabled="$(sg_env_value CAMOFOX_ENABLE_VNC 0)"
    if [[ "$enabled" == "1" || "$enabled" == "true" ]]; then
        printf 'Camofox noVNC:    %s\n' "$(sg_camofox_novnc_url)"
    else
        printf 'Camofox noVNC:    disabled (set CAMOFOX_ENABLE_VNC=1)\n'
    fi
}

# -----------------------------------------------------------------------------
# Group dispatchers and backwards-compatible hyphenated wrappers

sg_browser() {
    local command="${1:-}"
    case "$command" in
        ""|-h|--help|help) sg_browser_help ;;
        mode) shift; sg_browser_mode "$@" ;;
        apply) shift; sg_browser_mode_apply "$@" ;;
        browserless-live) shift; sg_browserless_profile_live "$@" ;;
        browserless-snapshot) shift; sg_browserless_snapshot "$@" ;;
        camofox-live) shift; sg_camofox_profile_live "$@" ;;
        camofox-use) shift; sg_camofox_profile_use "$@" ;;
        camofox-save) shift; sg_camofox_profile_save "$@" ;;
        camofox-reset) shift; sg_camofox_profile_reset "$@" ;;
        camofox-health) shift; sg_camofox_health "$@" ;;
        camofox-smoke) shift; sg_camofox_smoke "$@" ;;
        camofox-url) shift; sg_camofox_url "$@" ;;
        *)
            printf 'Unknown sg-browser command: %s\n\n' "$command" >&2
            sg_browser_help >&2
            return 2
            ;;
    esac
}

sg_meet() {
    local command="${1:-}"
    case "$command" in
        ""|-h|--help|help) sg_meet_help ;;
        chrome-profile) shift; sg_hermes_chrome_profile "$@" ;;
        join) shift; sg_hermes_meet_join "$@" ;;
        plugin) shift; sg_hermes_meet "$@" ;;
        *)
            printf 'Unknown sg-meet command: %s\n\n' "$command" >&2
            sg_meet_help >&2
            return 2
            ;;
    esac
}

sg-help() { sg_help "$@"; }
sg-tier() { sg_tier "$@"; }
sg-hermes() { sg_hermes "$@"; }
sg-browser() { sg_browser "$@"; }
sg-meet() { sg_meet "$@"; }
sg-disk() { sg_disk "$@"; }
sg-priv() { sg_priv "$@"; }
sg-up() { sg_up "$@"; }
sg-build-up() { sg_build_up "$@"; }
sg-down() { sg_down "$@"; }
sg-status() { sg_status "$@"; }
sg-ps() { sg_ps "$@"; }
sg-health() { sg_health "$@"; }
sg-logs() { sg_logs "$@"; }
sg-exec() { sg_exec "$@"; }
sg-config() { sg_config "$@"; }
sg-doctor() { sg_doctor "$@"; }
sg-up-core() { sg_up_core "$@"; }
sg-up-hermes() { sg_up_hermes "$@"; }
sg-up-private-hermes() { sg_up_private_hermes "$@"; }
sg-up-full() { sg_up_full "$@"; }
sg-restart-hermes() { sg_restart_hermes "$@"; }
sg-restart-caddy() { sg_restart_caddy "$@"; }
sg-restart-proxy() { sg_restart_proxy "$@"; }
sg-rebuild-hermes() { sg_rebuild_hermes "$@"; }
sg-rebuild-clawroute() { sg_rebuild_clawroute "$@"; }
sg-logs-hermes() { sg_logs_hermes "$@"; }
sg-logs-clawroute() { sg_logs_clawroute "$@"; }
sg-logs-camofox() { sg_logs_camofox "$@"; }
sg-hermes-shell() { sg_hermes_shell "$@"; }
sg-hermes-doctor() { sg_hermes_doctor "$@"; }
sg-hermes-profile() { sg_hermes_profile "$@"; }
sg-hermes-profiles() { sg_hermes_profiles "$@"; }
sg-hermes-profile-create() { sg_hermes_profile_create "$@"; }
sg-hermes-profile-new() { sg_hermes_profile_new "$@"; }
sg-hermes-gateway-profile() { sg_hermes_gateway_profile "$@"; }
sg-hermes-meet() { sg_hermes_meet "$@"; }
sg-clawroute-shell() { sg_clawroute_shell "$@"; }
sg-whitelist-domain() { sg_whitelist_domain "$@"; }
sg-add-port() { sg_add_port "$@"; }
sg-reader-test() { sg_reader_test "$@"; }
sg-browserless-snapshot() { sg_browserless_snapshot "$@"; }
sg-browserless-profile-live() { sg_browserless_profile_live "$@"; }
sg-browser-mode-apply() { sg_browser_mode_apply "$@"; }
sg-camofox-profile-live() { sg_camofox_profile_live "$@"; }
sg-camofox-profile-use() { sg_camofox_profile_use "$@"; }
sg-camofox-profile-save() { sg_camofox_profile_save "$@"; }
sg-camofox-profile-reset() { sg_camofox_profile_reset "$@"; }
sg-hermes-chrome-profile() { sg_hermes_chrome_profile "$@"; }
sg-hermes-meet-join() { sg_hermes_meet_join "$@"; }
sg-urls() { sg_urls "$@"; }
sg-caddy-validate() { sg_caddy_validate "$@"; }
sg-ports() { sg_ports "$@"; }
sg-docker-df() { sg_docker_df "$@"; }
sg-cache-top() { sg_cache_top "$@"; }
sg-cache-live() { sg_cache_live "$@"; }
sg-cache-clean() { sg_cache_clean "$@"; }
sg-profile-ports() { sg_profile_ports "$@"; }
sg-gog-check() { sg_gog_check "$@"; }
sg-gog-login() { sg_gog_login "$@"; }
sg-camofox-health() { sg_camofox_health "$@"; }
sg-camofox-smoke() { sg_camofox_smoke "$@"; }
sg-camofox-url() { sg_camofox_url "$@"; }

# Optional private shell customizations. Keep host-specific aliases, paths, and
# cleanup shortcuts in this ignored file instead of committing them here.
if [[ -f "$SPARTAN_GATE_DIR/private/aliases.local.sh" ]]; then
    # shellcheck source=/dev/null
    source "$SPARTAN_GATE_DIR/private/aliases.local.sh"
fi
