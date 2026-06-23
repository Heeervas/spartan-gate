#!/usr/bin/env python3
"""Build-time patcher: make supervised gateway services import CDP env."""

from __future__ import annotations

import sys
from pathlib import Path


TARGET = Path("/opt/hermes/hermes_cli/service_manager.py")
MARKER = "# Spartan Gate patch: import Browser/CDP env for gateway services"
RUNTIME_IDENTITY_MARKER = "# Spartan Gate patch: gateway runtime identity"
RUNTIME_OWNER_MARKER = "# Spartan Gate patch: service manager runtime owner"
ANCHOR = '            ". /opt/hermes/.venv/bin/activate",'

IMPORT_LINES = '''            "# Spartan Gate patch: import Browser/CDP env for gateway services",
            "_hermes_browser_env_keys=\\"BROWSER_CDP_URL BROWSER_CDP_MAIN_URL BROWSER_CDP_LAUNCH_URL BROWSERLESS_CDP_BROKER_ENABLED BROWSERLESS_CDP_BROKER_PORT CAMOFOX_URL CAMOFOX_ACCESS_KEY CAMOFOX_USER_ID CAMOFOX_SESSION_KEY HERMES_MEET_CAMOFOX_SESSION_KEY CAMOFOX_ADOPT_EXISTING_TAB\\"",
            "for _hermes_browser_key in $_hermes_browser_env_keys; do",
            "    _hermes_browser_file=/run/s6/container_environment/$_hermes_browser_key",
            "    if [ -f \\"$_hermes_browser_file\\" ]; then",
            "        export \\"$_hermes_browser_key=$(cat \\"$_hermes_browser_file\\")\\"",
            "    fi",
            "done",
            "if [ \\"${BROWSERLESS_CDP_BROKER_ENABLED:-true}\\" = \\"true\\" ] && [ -n \\"${BROWSERLESS_CDP_BROKER_PORT:-}\\" ] && [ -n \\"${BROWSER_CDP_URL:-}\\" ]; then",
            "    case \\"$BROWSER_CDP_URL\\" in",
            "        ws://127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}|ws://127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}/*)",
            "            if command -v nc >/dev/null 2>&1; then",
            "                _hermes_broker_ready=false",
            "                _hermes_broker_attempt=0",
            "                while [ $_hermes_broker_attempt -lt 50 ]; do",
            "                    if nc -z 127.0.0.1 \\"$BROWSERLESS_CDP_BROKER_PORT\\" >/dev/null 2>&1; then",
            "                        _hermes_broker_ready=true",
            "                        break",
            "                    fi",
            "                    _hermes_broker_attempt=$((_hermes_broker_attempt + 1))",
            "                    sleep 0.2",
            "                done",
            "                if [ \\"$_hermes_broker_ready\\" != \\"true\\" ]; then",
            "                    if [ -n \\"${BROWSER_CDP_LAUNCH_URL:-}\\" ]; then",
            "                        export BROWSER_CDP_URL=\\"$BROWSER_CDP_LAUNCH_URL\\"",
            "                        echo \\"[gateway-service] Browserless CDP broker not ready on 127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}; using launch CDP URL\\" >&2",
            "                    else",
            "                        echo \\"[gateway-service] Browserless CDP broker not ready on 127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}; continuing\\" >&2",
            "                    fi",
            "                fi",
            "                unset _hermes_broker_ready _hermes_broker_attempt",
            "            fi",
            "            ;;",
            "    esac",
            "fi",
            "unset _hermes_browser_env_keys _hermes_browser_key _hermes_browser_file",
'''


def patch_source(source: str) -> str:
    patched = source

    if MARKER not in patched:
        index = patched.find(ANCHOR)
        if index == -1:
            raise ValueError("service manager run-script anchor not found")

        insert_at = patched.find("\n", index)
        if insert_at == -1:
            raise ValueError("service manager run-script anchor line is incomplete")

        patched = patched[: insert_at + 1] + IMPORT_LINES + patched[insert_at + 1 :]

    if RUNTIME_OWNER_MARKER not in patched:
        runtime_owner_anchor = "_HERMES_UID = 10000\n_HERMES_GID = 10000\n"
        runtime_owner_replacement = f'''{RUNTIME_OWNER_MARKER}
import os as _spartan_os
_HERMES_UID = int(_spartan_os.environ.get("SPARTAN_HERMES_RUN_UID") or "10000")
_HERMES_GID = int(_spartan_os.environ.get("SPARTAN_HERMES_RUN_GID") or "10000")
'''
        if runtime_owner_anchor not in patched:
            raise ValueError("service manager runtime owner anchor not found")
        patched = patched.replace(runtime_owner_anchor, runtime_owner_replacement, 1)

    if RUNTIME_IDENTITY_MARKER not in patched:
        gateway_anchor = '        lines.append(f"exec s6-setuidgid hermes {gateway_cmd}")'
        gateway_replacement = '''        lines.append("# Spartan Gate patch: gateway runtime identity")
        lines.append('if [ -n "${SPARTAN_HERMES_RUN_UID:-}${SPARTAN_HERMES_RUN_GID:-}" ]; then')
        lines.append('    _spartan_run_uid="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}"')
        lines.append('    _spartan_run_gid="${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"')
        lines.append(f'    exec /command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" {gateway_cmd}')
        lines.append("fi")
        lines.append(f"exec /command/s6-setuidgid hermes {gateway_cmd}")'''
        if gateway_anchor not in patched:
            raise ValueError("service manager gateway privilege-drop anchor not found")
        patched = patched.replace(gateway_anchor, gateway_replacement, 1)

        log_chown_anchor = "            f'chown -R hermes:hermes \"$log_dir\" 2>/dev/null || true\\n'"
        log_chown_replacement = (
            "            f'chown -R \"${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}:${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}\" \"$log_dir\" 2>/dev/null || true\\n'"
        )
        if log_chown_anchor not in patched:
            raise ValueError("service manager log ownership anchor not found")
        patched = patched.replace(log_chown_anchor, log_chown_replacement, 1)

        log_parent_chown_anchor = "            f'chown hermes:hermes \"$HERMES_HOME/logs/gateways\" 2>/dev/null || true\\n'"
        log_parent_chown_replacement = (
            "            f'chown \"${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}:${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}\" \"$HERMES_HOME/logs/gateways\" 2>/dev/null || true\\n'"
        )
        if log_parent_chown_anchor not in patched:
            raise ValueError("service manager log parent ownership anchor not found")
        patched = patched.replace(log_parent_chown_anchor, log_parent_chown_replacement, 1)

        log_drop_anchor = "            f'exec s6-setuidgid hermes s6-log 1 n10 s1000000 T \"$log_dir\"\\n'"
        log_drop_replacement = (
            "            f'if [ -n \"${{SPARTAN_HERMES_RUN_UID:-}}${{SPARTAN_HERMES_RUN_GID:-}}\" ]; then\\n'\n"
            "            f'    exec /command/s6-applyuidgid -u \"${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}\" -g \"${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}\" s6-log 1 n10 s1000000 T \"$log_dir\"\\n'\n"
            "            f'fi\\n'\n"
            "            f'exec /command/s6-setuidgid hermes s6-log 1 n10 s1000000 T \"$log_dir\"\\n'"
        )
        if log_drop_anchor not in patched:
            raise ValueError("service manager log privilege-drop anchor not found")
        patched = patched.replace(log_drop_anchor, log_drop_replacement, 1)

    return patched


def main() -> None:
    if not TARGET.exists():
        print(f"FATAL: {TARGET} not found", file=sys.stderr)
        sys.exit(1)

    source = TARGET.read_text(encoding="utf-8")
    try:
        patched = patch_source(source)
    except ValueError as exc:
        print(f"FATAL: {exc} in {TARGET}", file=sys.stderr)
        sys.exit(1)

    if patched == source:
        print(f"SKIP: {TARGET} already imports Browser/CDP env")
        return

    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - gateway services import Browser/CDP env")


if __name__ == "__main__":
    main()
