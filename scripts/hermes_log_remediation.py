#!/usr/bin/env python3
"""Preflight and repair obsolete Hermes profile configuration; dry-run by default."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

import yaml

SEARCH_URL = "http://searxng:8080"
OBSOLETE_PLUGINS = {"adan-discord-launcher", "prompt-load-bridge", "web-search-plus"}
TIMED_RESETS = {"idle", "daily", "both"}


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key, value in loader.construct_pairs(node):
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _plain_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("expected a real directory")


@dataclass(frozen=True)
class Change:
    path: Path
    original: bytes
    replacement: bytes
    mode: int
    uid: int
    gid: int


def _read(path: Path) -> tuple[bytes, int, int, int]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular configuration file")
    metadata = path.stat()
    if metadata.st_uid != os.geteuid():
        raise ValueError("run remediation as the configuration owner")
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid


def plan(root: Path, *, repair_plugins: bool = False, talk_plugin: Path | None = None,
         discord_catalog_only: bool = False) -> list[Change]:
    _plain_directory(root)
    profiles = root / "profiles"
    homes = [root]
    if profiles.exists() or profiles.is_symlink():
        _plain_directory(profiles)
        for home in sorted(profiles.iterdir()):
            if home.is_symlink():
                raise ValueError("profile symlinks are refused")
            if home.is_dir():
                homes.append(home)
    changes = []
    for home in homes:
        path = home / "config.yaml"
        if not path.exists() and not path.is_symlink():
            continue
        original, mode, uid, gid = _read(path)
        config = yaml.load(original, Loader=UniqueLoader)
        if not isinstance(config, dict):
            raise ValueError("invalid profile configuration")
        before = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        plugins = config.get("plugins") or {}
        if not isinstance(plugins, dict):
            raise ValueError("invalid plugin configuration")
        enabled = plugins.get("enabled", [])
        disabled = plugins.get("disabled", [])
        for names in [enabled, disabled]:
            if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
                raise ValueError("invalid plugin names")
        if (not discord_catalog_only and "hermes-session-reset-policy" in enabled
                and "hermes-session-reset-policy" not in disabled):
            raise ValueError("automatic reset plugin requires explicit disablement first")
        if "alberto-tools" in enabled and "alberto-tools" not in disabled:
            retained = []
            for name in enabled:
                absent = not any((base / "plugins" / name).exists() for base in [root, home])
                if name not in OBSOLETE_PLUGINS or not absent:
                    retained.append(name)
            plugins["enabled"] = retained
        if discord_catalog_only:
            replacement = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
            if replacement != before:
                changes.append(Change(path, original, replacement.encode(), mode, uid, gid))
            continue
        # Native Hermes now resets only on /new or /reset. Remove only dormant timers.
        for section in [config, config.get("gateway")]:
            if isinstance(section, dict):
                reset = section.get("session_reset")
                if isinstance(reset, dict) and str(reset.get("mode", "")).lower() in TIMED_RESETS:
                    section.pop("session_reset")
        replacement = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        if replacement != before:
            changes.append(Change(path, original, replacement.encode(), mode, uid, gid))
        env_path = home / ".env"
        if not env_path.exists() and not env_path.is_symlink():
            raise ValueError("profile .env is absent; create it explicitly before remediation")
        if env_path.exists() or env_path.is_symlink():
            env_original, env_mode, env_uid, env_gid = _read(env_path)
            text = env_original.decode("utf-8")
            declarations = re.findall(r"(?m)^\s*(?:export\s+)?SEARXNG_URL\s*=.*$", text)
            if len(declarations) > 1:
                raise ValueError("duplicate SearXNG setting")
            if not declarations:
                updated = text + ("\n" if text and not text.endswith("\n") else "") + f"SEARXNG_URL={SEARCH_URL}\n"
                changes.append(Change(env_path, env_original, updated.encode(), env_mode, env_uid, env_gid))
    if repair_plugins:
        try:
            from . import hermes_plugin_scope_repairs as repairs
        except ImportError:
            import hermes_plugin_scope_repairs as repairs
        targets = [
            (root / "plugins/node-search/node_index.py", repairs.patch_node_search),
            (root / "plugins/toolaria/__init__.py", repairs.patch_toolaria),
            (root / "plugins/toolaria/passref.py", repairs.patch_toolaria_passref),
        ]
        if talk_plugin is not None:
            _plain_directory(talk_plugin)
            targets.append((talk_plugin / "talk_transcript.py", repairs.patch_talk_transcript))
        for path, patcher in targets:
            _plain_directory(path.parent)
            data, mode, uid, gid = _read(path)
            candidate = patcher(data.decode("utf-8"))
            compile(candidate, path.name, "exec")
            if candidate.encode() != data:
                changes.append(Change(path, data, candidate.encode(), mode, uid, gid))
    return changes


def _atomic(path: Path, data: bytes, mode: int) -> None:
    metadata = path.stat(follow_symlinks=False)
    fd, temporary = tempfile.mkstemp(prefix=".hermes-repair-", dir=path.parent)
    try:
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(changes: list[Change], backup: Path, *, home: Path | None = None) -> None:
    # Serializes operator migrations; callers must also quiesce profile writers.
    root = home or (Path(os.path.commonpath([c.path.parent for c in changes])) if changes else backup.parent)
    descriptor = os.open(root / '.hermes-remediation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('unsafe remediation lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _apply_locked(changes, backup)
    finally:
        os.close(descriptor)


def _apply_locked(changes: list[Change], backup: Path) -> None:
    _plain_directory(backup)
    if backup.stat().st_uid != os.geteuid() or stat.S_IMODE(backup.stat().st_mode) & 0o077:
        raise ValueError("backup directory must be owner-only")
    if any(backup.iterdir()):
        raise ValueError("backup directory must be empty")
    for change in changes:
        if _read(change.path) != (change.original, change.mode, change.uid, change.gid):
            raise ValueError("configuration changed after preflight")
    # Retain every original before the first configuration write.
    for index, change in enumerate(changes):
        target = backup / f"{index:03d}.original"
        with target.open("xb") as stream:
            os.chmod(target, 0o600)
            stream.write(change.original)
    import json
    manifest = [
        {"path": str(change.path), "backup": f"{index:03d}.original", "mode": change.mode,
         "uid": change.uid, "gid": change.gid,
         "original_sha256": hashlib.sha256(change.original).hexdigest(),
         "replacement_sha256": hashlib.sha256(change.replacement).hexdigest()}
        for index, change in enumerate(changes)
    ]
    with (backup / "manifest.json").open("x") as stream:
        os.chmod(backup / "manifest.json", 0o600)
        json.dump(manifest, stream, indent=2)
    completed = []
    try:
        for change in changes:
            if _read(change.path) != (change.original, change.mode, change.uid, change.gid):
                raise ValueError("configuration changed during apply")
            _atomic(change.path, change.replacement, change.mode)
            completed.append(change)
    except BaseException:
        conflict = False
        for change in reversed(completed):
            try:
                current = _read(change.path)
            except (OSError, ValueError):
                conflict = True
                continue
            if current != (change.replacement, change.mode, change.uid, change.gid):
                conflict = True
                continue
            try:
                _atomic(change.path, change.original, change.mode)
            except OSError:
                conflict = True
        if conflict:
            raise RuntimeError('Rollback preserved a concurrent edit; inspect the private backup manifest') from None
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--discord-catalog-only", action="store_true", help="only remove absent superseded Discord command plugins")
    parser.add_argument("--repair-plugins", action="store_true", help="also repair installed node-search and Toolaria sources")
    parser.add_argument("--talk-plugin", type=Path, help="optional real Talk plugin source directory; requires --repair-plugins")
    args = parser.parse_args()
    if args.talk_plugin is not None and not args.repair_plugins:
        parser.error("--talk-plugin requires --repair-plugins")
    if args.discord_catalog_only and (args.repair_plugins or args.talk_plugin is not None):
        parser.error("--discord-catalog-only cannot modify other plugins")
    changes = plan(args.home, repair_plugins=args.repair_plugins, talk_plugin=args.talk_plugin,
                   discord_catalog_only=args.discord_catalog_only)
    if args.apply:
        if args.backup_dir is None:
            parser.error("--apply requires an empty --backup-dir")
        apply(changes, args.backup_dir, home=args.home)
    print(f"{'Applied' if args.apply else 'Planned'} {len(changes)} file changes")


if __name__ == "__main__":
    main()
