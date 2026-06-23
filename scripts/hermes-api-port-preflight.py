#!/usr/bin/env python3
"""Read-only preflight for Hermes profile API port conflicts.

Spartan Gate no longer patches Hermes to auto-increment occupied API ports.
Before recreating the Hermes container, check the persisted profile configs so
named profiles that relied on auto-shifted ports fail before container startup.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def expand_env(value: str, env: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, "")

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", repl, value)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    return data if isinstance(data, dict) else {}


def nested_get(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def platform_api_config(config: dict[str, Any]) -> dict[str, Any]:
    platforms = config.get("platforms")
    if isinstance(platforms, dict):
        api = platforms.get("api_server") or platforms.get("api")
        return api if isinstance(api, dict) else {}
    if isinstance(platforms, list):
        for item in platforms:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("type") or item.get("platform") or "")
            if name in {"api_server", "api"}:
                return item
    return {}


def bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return None


def int_port(value: Any, env: dict[str, str]) -> int | None:
    if value is None:
        return None
    text = expand_env(str(value).strip(), env)
    try:
        port = int(text)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def profile_dirs(home: Path) -> list[tuple[str, Path]]:
    profiles = [("default", home)]
    root = home / "profiles"
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.name == "default":
                continue
            if entry.is_dir() and (entry / "SOUL.md").is_file():
                profiles.append((entry.name, entry))
    return profiles


def profile_api_port(
    *,
    name: str,
    path: Path,
    base_env: dict[str, str],
    default_port: int,
) -> tuple[bool, int | None, str]:
    env = dict(base_env)
    env.update(parse_dotenv(path / ".env"))
    config = load_yaml(path / "config.yaml")
    api_config = platform_api_config(config)

    enabled = bool_value(env.get("API_SERVER_ENABLED"))
    source = "API_SERVER_PORT/default"
    if enabled is None:
        enabled = bool_value(api_config.get("enabled"))
    if enabled is None:
        enabled = True if name == "default" else bool_value(base_env.get("API_SERVER_ENABLED")) is not False

    if not enabled:
        return False, None, "disabled"

    port = int_port(env.get("API_SERVER_PORT"), env)
    if port is not None:
        return True, port, f"{path / '.env'}:API_SERVER_PORT"

    port = int_port(api_config.get("port"), env)
    if port is not None:
        return True, port, f"{path / 'config.yaml'}:platforms.api_server.port"

    port = int_port(nested_get(config, ("gateway", "api_server", "port")), env)
    if port is not None:
        return True, port, f"{path / 'config.yaml'}:gateway.api_server.port"

    return True, default_port, source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "hermes_home",
        nargs="?",
        default=os.environ.get("SPARTAN_HERMES_DATA_PATH") or "runtime/hermes",
        help="host path for Hermes data, default: runtime/hermes",
    )
    args = parser.parse_args()

    home = Path(args.hermes_home).resolve()
    if not home.is_dir():
        print(f"ERROR: Hermes data directory not found: {home}", file=sys.stderr)
        return 2

    root_env = dict(os.environ)
    root_env.update(parse_dotenv(home / ".env"))
    default_port = int_port(root_env.get("HERMES_API_PORT"), root_env) or int_port(
        root_env.get("API_SERVER_PORT"), root_env
    ) or 8642
    root_env.setdefault("API_SERVER_ENABLED", "true")

    by_port: dict[int, list[tuple[str, str]]] = {}
    disabled: list[str] = []
    for name, path in profile_dirs(home):
        enabled, port, source = profile_api_port(
            name=name,
            path=path,
            base_env=root_env,
            default_port=default_port,
        )
        if not enabled:
            disabled.append(name)
            continue
        if port is None:
            print(f"ERROR: profile {name!r} has an enabled API server but no valid port", file=sys.stderr)
            return 1
        by_port.setdefault(port, []).append((name, source))

    duplicates = {port: entries for port, entries in by_port.items() if len(entries) > 1}
    if duplicates:
        print("ERROR: duplicate Hermes API ports detected; fix profile config before recreating Hermes.", file=sys.stderr)
        for port, entries in sorted(duplicates.items()):
            owners = ", ".join(f"{name} ({source})" for name, source in entries)
            print(f"  port {port}: {owners}", file=sys.stderr)
        return 1

    for port, entries in sorted(by_port.items()):
        name, source = entries[0]
        print(f"OK: profile {name} API port {port} from {source}")
    for name in disabled:
        print(f"OK: profile {name} API server disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
