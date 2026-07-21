#!/usr/bin/env python3
"""Transactionally copy the legacy Camofox login into existing Hermes profiles.

Credential files are treated as opaque bytes. The migration hashes and copies
them but never parses or prints their contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time
import uuid

import yaml


PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LEGACY_USER_ID = "spartan-camofox-main"
MANAGED_CAMOFOX = {
    "managed_persistence": True,
    "adopt_existing_tab": False,
    "user_id": "",
    "session_key": "",
}


class MigrationError(RuntimeError):
    pass


def hashed_user_dir(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:32]


def hermes_user_id(profile: str) -> str:
    home = "/opt/data" if profile == "default" else f"/opt/data/profiles/{profile}"
    scope = f"{home}/browser_auth/camofox"
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"camofox-user:{scope}").hex[:10]
    return f"hermes_{digest}"


def require_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        value = path.lstat()
    except FileNotFoundError as error:
        raise MigrationError(f"missing {label}") from error
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise MigrationError(f"unsafe {label}")
    return value


def require_regular(path: Path, *, label: str) -> os.stat_result:
    try:
        value = path.lstat()
    except FileNotFoundError as error:
        raise MigrationError(f"missing {label}") from error
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise MigrationError(f"unsafe {label}")
    return value


def safe_owners(*roots: Path) -> set[int]:
    return {0, os.geteuid(), *(root.lstat().st_uid for root in roots)}


def tree_manifest(root: Path, owners: set[int]) -> list[dict[str, object]]:
    require_directory(root, label="credential directory")
    result: list[dict[str, object]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            path = current_path / name
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode):
                raise MigrationError("credential tree contains a symlink")
            if value.st_uid not in owners:
                raise MigrationError("credential tree has an unsafe owner")
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(value.st_mode):
                result.append({
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(value.st_mode),
                    "uid": value.st_uid,
                    "gid": value.st_gid,
                })
            elif stat.S_ISREG(value.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                result.append({
                    "path": relative,
                    "type": "file",
                    "mode": stat.S_IMODE(value.st_mode),
                    "uid": value.st_uid,
                    "gid": value.st_gid,
                    "size": value.st_size,
                    "sha256": digest.hexdigest(),
                })
            else:
                raise MigrationError("credential tree contains a special file")
    return result


def manifest_content(manifest: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in item.items() if key not in {"uid", "gid"}}
        for item in manifest
    ]


def manifest_size(manifest: list[dict[str, object]]) -> int:
    return sum(int(item.get("size", 0)) for item in manifest)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: object) -> None:
    parent_stat = require_directory(path.parent, label="transaction control parent")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.geteuid() == 0:
            os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def discover_profiles(hermes_root: Path) -> list[tuple[str, Path, Path]]:
    root_stat = require_directory(hermes_root, label="Hermes data root")
    allowed_owners = {0, os.geteuid(), root_stat.st_uid}
    profiles: list[tuple[str, Path, Path]] = []

    default_config = hermes_root / "config.yaml"
    value = require_regular(default_config, label="default Hermes config")
    if value.st_uid not in allowed_owners:
        raise MigrationError("default Hermes config has an unsafe owner")
    profiles.append(("default", hermes_root, default_config))

    profiles_root = hermes_root / "profiles"
    if profiles_root.exists():
        profiles_stat = require_directory(profiles_root, label="Hermes profiles root")
        allowed_owners.add(profiles_stat.st_uid)
        seen = {hermes_root.resolve()}
        for entry in sorted(profiles_root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith("."):
                continue
            if not PROFILE_RE.fullmatch(entry.name) or entry.name in {".", ".."}:
                raise MigrationError("invalid Hermes profile name")
            value = require_directory(entry, label="Hermes profile directory")
            if value.st_uid not in allowed_owners:
                raise MigrationError("Hermes profile has an unsafe owner")
            resolved = entry.resolve()
            if resolved in seen:
                raise MigrationError("duplicate Hermes profile path")
            seen.add(resolved)
            config = entry / "config.yaml"
            config_stat = require_regular(config, label="Hermes profile config")
            if config_stat.st_uid not in allowed_owners:
                raise MigrationError("Hermes profile config has an unsafe owner")
            profiles.append((entry.name, entry, config))
    return profiles


def migrated_config(config_path: Path) -> bytes:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise MigrationError("Hermes profile config is invalid") from error
    if not isinstance(loaded, dict):
        raise MigrationError("Hermes profile config root is invalid")
    browser = loaded.setdefault("browser", {})
    if not isinstance(browser, dict):
        raise MigrationError("Hermes browser config is invalid")
    camofox = browser.setdefault("camofox", {})
    if not isinstance(camofox, dict):
        raise MigrationError("Hermes Camofox config is invalid")
    camofox.update(MANAGED_CAMOFOX)
    return yaml.safe_dump(loaded, sort_keys=False).encode()


def stage_config(config_path: Path, content: bytes, transaction: str) -> Path:
    value = require_regular(config_path, label="Hermes profile config")
    temporary = config_path.with_name(f".{config_path.name}.spartan-{transaction}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(value.st_mode))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(value.st_mode))
        if os.geteuid() == 0:
            os.chown(temporary, value.st_uid, value.st_gid)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def copy_verified(source: Path, target: Path, expected: list[dict[str, object]], owners: set[int]) -> None:
    if target.exists():
        raise MigrationError("staging path already exists")
    shutil.copytree(source, target, copy_function=shutil.copy2)
    actual = tree_manifest(target, owners | {target.lstat().st_uid})
    if manifest_content(actual) != manifest_content(expected):
        raise MigrationError("staged credential copy verification failed")


def write_journal(path: Path, journal: dict[str, object]) -> None:
    atomic_json(path, journal)


def rollback_published(
    journal_path: Path,
    journal: dict[str, object],
    backup: Path,
) -> None:
    failed = backup / "failed-published"
    failed.mkdir(mode=0o700, exist_ok=True)
    for item in reversed(journal.get("configs_published", [])):
        config = Path(item["path"])
        saved = Path(item["backup"])
        os.replace(saved, config)
        fsync_directory(config.parent)
    for item in reversed(journal.get("targets_published", [])):
        target = Path(item["target"])
        rollback = Path(item["rollback"]) if item.get("rollback") else None
        if target.exists():
            os.replace(target, failed / item["name"])
        if rollback and rollback.exists():
            os.replace(rollback, target)
        fsync_directory(target.parent)
    journal["status"] = "rolled_back_after_failure"
    write_journal(journal_path, journal)


def apply_migration(
    hermes_root: Path,
    camofox_root: Path,
    fail_after: int | None,
    fail_before_journal: str | None,
) -> int:
    profiles = discover_profiles(hermes_root)
    profiles_dir = camofox_root / "profiles"
    require_directory(camofox_root, label="Camofox data root")
    require_directory(profiles_dir, label="Camofox profiles root")
    backup_parent = camofox_root / ".spartan-backups"
    backup_root = backup_parent / "camofox-login-migration"
    if backup_parent.exists():
        require_directory(backup_parent, label="Camofox backup root")
    if backup_root.exists():
        require_directory(backup_root, label="Camofox login backup root")
    latest_path = backup_root / "latest.json"

    if latest_path.exists():
        require_regular(latest_path, label="migration latest marker")
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        journal_value = latest.get("journal") if isinstance(latest, dict) else None
        if not isinstance(journal_value, str):
            raise MigrationError("migration latest marker is invalid")
        journal_path = Path(journal_value).resolve(strict=True)
        if backup_root.resolve() not in journal_path.parents:
            raise MigrationError("migration journal escapes the backup root")
        require_regular(journal_path, label="migration journal")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal.get("status") == "applied":
            if os.geteuid() == 0:
                owner = camofox_root.lstat()
                for control_path in (journal_path.parent, journal_path, latest_path):
                    os.chown(control_path, owner.st_uid, owner.st_gid)
            print(f"Camofox login migration already applied for {journal['profile_count']} profiles.")
            return 0

    transaction = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:12]}"
    stage_root = profiles_dir / f".spartan-stage-{transaction}"
    root_owners = safe_owners(camofox_root, profiles_dir)
    legacy_source = profiles_dir / hashed_user_dir(LEGACY_USER_ID)
    source_manifest = None
    if legacy_source.exists():
        source_manifest = tree_manifest(legacy_source, root_owners | {legacy_source.lstat().st_uid})

    targets: list[dict[str, object]] = []
    config_plan: list[dict[str, object]] = []
    existing_size = 0
    for profile, _home, config in profiles:
        user_id = hermes_user_id(profile)
        name = hashed_user_dir(user_id)
        target = profiles_dir / name
        existing_manifest = None
        if target.exists():
            existing_manifest = tree_manifest(target, root_owners | {target.lstat().st_uid})
            existing_size += manifest_size(existing_manifest)
        staged = stage_root / name
        targets.append({
            "profile": profile,
            "name": name,
            "target": str(target),
            "staged": str(staged),
            "had_original": existing_manifest is not None,
            "original_manifest": existing_manifest,
        })
        config_plan.append({
            "profile": profile,
            "path": str(config),
            "content": migrated_config(config),
            "original_size": config.stat().st_size,
        })

    source_size = manifest_size(source_manifest or [])
    required = source_size * (len(targets) + 1) + existing_size + sum(
        int(item["original_size"]) + len(item["content"]) for item in config_plan
    )
    if shutil.disk_usage(camofox_root).free < required + 16 * 1024 * 1024:
        raise MigrationError("insufficient free space for verified migration rollback")

    # All validation and capacity checks above are read-only. Only now create
    # transaction-private staging and rollback paths.
    backup_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup = backup_root / transaction
    if os.geteuid() == 0:
        owner = camofox_root.lstat()
        os.chown(backup_parent, owner.st_uid, owner.st_gid)
        os.chown(backup_root, owner.st_uid, owner.st_gid)
    backup.mkdir(mode=0o700)
    rollback_targets = backup / "rollback-targets"
    if os.geteuid() == 0:
        os.chown(backup, owner.st_uid, owner.st_gid)
    rollback_targets.mkdir(mode=0o700)
    source_archive = backup / "legacy-source"
    configs_backup = backup / "configs"
    configs_backup.mkdir(mode=0o700)
    stage_root.mkdir(mode=0o700)
    staged_configs: list[dict[str, object]] = []
    try:
        if source_manifest is not None:
            copy_verified(legacy_source, source_archive, source_manifest, root_owners)
            for item in targets:
                copy_verified(legacy_source, Path(item["staged"]), source_manifest, root_owners)
        for item in config_plan:
            config = Path(item["path"])
            backup_config = configs_backup / f"{item['profile']}.yaml"
            shutil.copy2(config, backup_config)
            if hashlib.sha256(backup_config.read_bytes()).digest() != hashlib.sha256(config.read_bytes()).digest():
                raise MigrationError("config backup verification failed")
            staged_configs.append({
                "profile": item["profile"],
                "path": item["path"],
                "backup": str(backup_config),
                "staged": str(stage_config(config, item["content"], transaction)),
            })
    except BaseException:
        for item in config_plan:
            config = Path(item["path"])
            config.with_name(f".{config.name}.spartan-{transaction}").unlink(missing_ok=True)
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        raise


    manifest = {
        "version": 1,
        "transaction": transaction,
        "created_at": int(time.time()),
        "legacy_source_present": source_manifest is not None,
        "legacy_source_manifest": source_manifest,
        "profiles": targets,
        "configs": staged_configs,
    }
    manifest_path = backup / "manifest.json"
    atomic_json(manifest_path, manifest)
    journal: dict[str, object] = {
        "version": 1,
        "transaction": transaction,
        "status": "publishing",
        "profile_count": len(profiles),
        "manifest": str(manifest_path),
        "targets_published": [],
        "configs_published": [],
    }
    journal_path = backup / "journal.json"
    write_journal(journal_path, journal)
    atomic_json(latest_path, {"journal": str(journal_path)})

    interrupted = False

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True
        raise MigrationError("migration interrupted")

    previous_handlers = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    publish_count = 0
    try:
        if source_manifest is not None:
            for item in targets:
                target = Path(item["target"])
                staged = Path(item["staged"])
                rollback = rollback_targets / item["name"]
                published = {
                    "name": item["name"],
                    "target": str(target),
                    "rollback": str(rollback) if item["had_original"] else None,
                }
                original_moved = False
                staged_moved = False
                try:
                    if target.exists():
                        os.replace(target, rollback)
                        original_moved = True
                        if manifest_content(tree_manifest(rollback, root_owners | {rollback.lstat().st_uid})) != manifest_content(item["original_manifest"]):
                            raise MigrationError("rollback target verification failed")
                    os.replace(staged, target)
                    staged_moved = True
                    fsync_directory(profiles_dir)
                    if fail_before_journal == "target":
                        fail_before_journal = None
                        raise MigrationError("injected target pre-journal failure")
                    journal["targets_published"].append(published)
                    write_journal(journal_path, journal)
                except BaseException:
                    if published not in journal["targets_published"]:
                        if staged_moved and target.exists():
                            os.replace(target, staged)
                        if original_moved and rollback.exists():
                            os.replace(rollback, target)
                        fsync_directory(profiles_dir)
                    raise
                publish_count += 1
                if fail_after is not None and publish_count >= fail_after:
                    raise MigrationError("injected publish failure")

        for item in staged_configs:
            config = Path(item["path"])
            staged = Path(item["staged"])
            config_moved = False
            try:
                os.replace(staged, config)
                config_moved = True
                fsync_directory(config.parent)
                if fail_before_journal == "config":
                    fail_before_journal = None
                    raise MigrationError("injected config pre-journal failure")
                journal["configs_published"].append(item)
                write_journal(journal_path, journal)
            except BaseException:
                if item not in journal["configs_published"] and config_moved:
                    os.replace(Path(item["backup"]), config)
                    fsync_directory(config.parent)
                raise
            publish_count += 1
            if fail_after is not None and publish_count >= fail_after:
                raise MigrationError("injected publish failure")

        journal["status"] = "applied"
        journal["interrupted"] = interrupted
        write_journal(journal_path, journal)
    except BaseException:
        rollback_published(journal_path, journal, backup)
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        for item in staged_configs:
            Path(item["staged"]).unlink(missing_ok=True)
        try:
            stage_root.rmdir()
        except OSError:
            pass

    print(f"Camofox login migration applied transactionally to {len(profiles)} profiles.")
    print("Rollback archive retained for at least seven days; no credential contents were displayed.")
    return 0


def rollback_migration(camofox_root: Path) -> int:
    backup_root = camofox_root / ".spartan-backups" / "camofox-login-migration"
    latest_path = backup_root / "latest.json"
    require_regular(latest_path, label="migration latest marker")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    journal_path = Path(latest["journal"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("status") in {"rolled_back", "rolled_back_after_failure"}:
        print("Camofox login migration is already rolled back.")
        return 0
    if journal.get("status") != "applied":
        raise MigrationError("migration journal is not safely rollbackable")
    backup = journal_path.parent
    rolled = backup / "rolled-back-published"
    rolled.mkdir(mode=0o700, exist_ok=True)
    for item in reversed(journal.get("configs_published", [])):
        config = Path(item["path"])
        saved = Path(item["backup"])
        current = rolled / f"config-{item['profile']}.yaml"
        os.replace(config, current)
        shutil.copy2(saved, config)
        fsync_directory(config.parent)
    for item in reversed(journal.get("targets_published", [])):
        target = Path(item["target"])
        if target.exists():
            os.replace(target, rolled / item["name"])
        rollback = Path(item["rollback"]) if item.get("rollback") else None
        if rollback and rollback.exists():
            os.replace(rollback, target)
        fsync_directory(target.parent)
    journal["status"] = "rolled_back"
    write_journal(journal_path, journal)
    print(f"Camofox login migration rolled back for {journal['profile_count']} profiles.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "rollback"))
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--camofox-root", type=Path, required=True)
    parser.add_argument("--test-fail-after-publish", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--test-fail-before-journal",
        choices=("target", "config"),
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.test_fail_after_publish is not None and args.test_fail_after_publish < 1:
            raise MigrationError("invalid injected failure point")
        if args.action == "apply":
            return apply_migration(
                args.hermes_root.resolve(),
                args.camofox_root.resolve(),
                args.test_fail_after_publish,
                args.test_fail_before_journal,
            )
        return rollback_migration(args.camofox_root.resolve())
    except (MigrationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Camofox login migration failed safely: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
