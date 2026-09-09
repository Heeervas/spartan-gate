#!/usr/bin/env python3
"""Opt-in boot provisioning of one reviewed Talk plugin across Hermes profiles."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import yaml

NAME = "hermes-talk"


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    pairs = loader.construct_pairs(node)
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate profile config key")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".talk-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def prepare(root: Path, plugin: Path, enabled: bool, check: bool = False) -> int:
    """Preflight every profile before writing; undo completed writes on error."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Invalid Hermes home")
    if enabled and not (plugin / "plugin.yaml").is_file():
        raise ValueError("Reviewed Talk plugin is missing")
    profiles = root / "profiles"
    if profiles.is_symlink():
        raise ValueError("Profile directory must not be a symlink")
    homes = [root]
    if profiles.is_dir():
        homes += sorted(p for p in profiles.iterdir() if p.is_dir())
    plans = []
    for home in homes:
        if home.is_symlink():
            raise ValueError("Profile home must not be a symlink")
        config = home / "config.yaml"
        if config.is_symlink():
            raise ValueError("Profile config must not be a symlink")
        if not config.exists():
            continue
        if not config.is_file():
            raise ValueError("Profile config must be a regular file")
        original = config.read_bytes()
        data = yaml.load(original, Loader=UniqueLoader) or {}
        if not isinstance(data, dict):
            raise ValueError("Invalid profile config")
        cfg = data.get("plugins", {})
        if not isinstance(cfg, dict):
            raise ValueError("Invalid plugin config")
        active, disabled = cfg.get("enabled", []), cfg.get("disabled", [])
        if not isinstance(active, list) or not isinstance(disabled, list):
            raise ValueError("Invalid plugin lists")
        if any(not isinstance(x, str) for x in active + disabled):
            raise ValueError("Plugin names must be strings")
        if enabled and NAME in disabled:
            raise ValueError("Talk explicitly disabled in a profile")
        directory = home / "plugins"
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("Plugin directory must be a real directory")
        link = directory / NAME
        # Root plugin is a read-only bind mount; named profiles use narrow links.
        managed_link = home != root
        if managed_link and (link.exists() or link.is_symlink()):
            if not link.is_symlink() or os.readlink(link) != str(plugin):
                raise ValueError("Conflicting Talk plugin installation")
        updated = dict(cfg)
        updated["enabled"] = ([*active, NAME] if NAME not in active else active) if enabled else [x for x in active if x != NAME]
        if updated["enabled"] != active:
            data["plugins"] = updated
            content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()
        else:
            content = original
        plans.append((config, original, content, stat.S_IMODE(config.stat().st_mode), link, managed_link, directory.exists(), link.is_symlink()))
    if not plans:
        raise ValueError("No Hermes profile configs found")
    if check:
        return len(plans)
    completed = []
    try:
        for plan in plans:
            config, original, content, mode, link, managed, had_dir, had_link = plan
            completed.append(plan)
            if managed and enabled and not had_link:
                link.parent.mkdir(exist_ok=True)
                link.symlink_to(plugin)
            elif managed and not enabled and had_link:
                link.unlink()
            if content != original:
                atomic_write(config, content, mode)
    except Exception:
        for config, original, content, mode, link, managed, had_dir, had_link in reversed(completed):
            if content != original:
                atomic_write(config, original, mode)
            if managed:
                if had_link and not link.is_symlink():
                    link.symlink_to(plugin)
                elif not had_link and link.is_symlink():
                    link.unlink()
                if not had_dir and link.parent.exists():
                    link.parent.rmdir()
        raise
    return len(plans)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("/opt/data"))
    parser.add_argument("--plugin", type=Path, default=Path("/opt/data/plugins/hermes-talk"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        flag = os.environ.get("SPARTAN_HERMES_TALK_ENABLED", "false")
        if flag not in {"true", "false"}:
            raise ValueError("Invalid Talk activation flag")
        enabled = flag == "true"
        if enabled:
            expected = {
                "TALK_OPENAI_TRANSPORT": "spartan",
                "TALK_SPARTAN_BASE_URL": "http://spartan-route:18790",
                "TALK_SPARTAN_TRUSTED_HTTP": "true",
                "TALK_PROVIDER": "openai",
                "TALK_MODEL": "gpt-realtime-2.1",
                "TALK_VOICE": "cedar",
                "TALK_VOICE_MODE": "native",
            }
            if any(os.environ.get(k) != v for k, v in expected.items()):
                raise ValueError("Talk Route configuration missing or mismatched")
            token = os.environ.get("TALK_SPARTAN_TOKEN", "")
            if not re.fullmatch(r"[\x21-\x7e]{32,512}", token):
                raise ValueError("Dedicated Talk credential missing or malformed")
            if token == os.environ.get("OPENAI_API_KEY"):
                raise ValueError("Talk credential must differ from text credential")
        count = prepare(args.home, args.plugin, enabled, args.check)
        print(json.dumps({"profiles": count, "enabled": enabled, "check_only": args.check}))
        return 0
    except Exception:
        # YAML/parser/filesystem exceptions may contain private config values.
        print("Talk profile preparation failed; no credentials or config values logged", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
