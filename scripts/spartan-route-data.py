#!/usr/bin/env python3
"""Prepare, check, or migrate Spartan Route's fixed SQLite state safely."""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote


MANAGED_FILE_MODES = {
    "spartan-route.db": 0o600,
    "spartan-route.db-wal": 0o600,
    "spartan-route.db-shm": 0o600,
}
DATA_DIRECTORY_MODE = 0o700
BACKUP_DIRECTORY_MODE = 0o700
BACKUP_RESERVE_BYTES = 64 * 1024 * 1024


class DataPolicyError(Exception):
    def __init__(self, label: str, message: str):
        super().__init__(message)
        self.label = label
        self.message = message


@dataclass(frozen=True)
class Metadata:
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class ManagedTarget:
    path: Path
    label: str
    expected_mode: int
    directory: bool
    metadata: Metadata


@dataclass(frozen=True)
class MigrationResult:
    changed: bool
    backup_created: bool


def _absolute(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path(os.path.abspath(path))


def _metadata(info: os.stat_result) -> Metadata:
    return Metadata(stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise DataPolicyError(label, "target is missing") from None
        raise DataPolicyError(label, "metadata is not readable") from None
    if stat.S_ISLNK(info.st_mode):
        raise DataPolicyError(label, "symlink targets are forbidden")
    return info


def _check_existing_components(path: Path, label: str) -> None:
    path = _absolute(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        _lstat(current, label)


def _validate_identity(uid: int, gid: int) -> None:
    maximum = 2**32 - 2
    if not 0 <= uid <= maximum:
        raise DataPolicyError("runtime identity", "UID is outside the supported range")
    if not 0 <= gid <= maximum:
        raise DataPolicyError("runtime identity", "GID is outside the supported range")


def _collect_targets(data_dir: Path) -> list[ManagedTarget]:
    data_dir = _absolute(data_dir)
    _check_existing_components(data_dir, "Spartan Route data directory")
    directory_info = _lstat(data_dir, "Spartan Route data directory")
    if not stat.S_ISDIR(directory_info.st_mode):
        raise DataPolicyError("Spartan Route data directory", "target is not a directory")

    targets = [
        ManagedTarget(
            data_dir,
            "Spartan Route data directory",
            DATA_DIRECTORY_MODE,
            True,
            _metadata(directory_info),
        )
    ]
    for name, expected_mode in MANAGED_FILE_MODES.items():
        path = data_dir / name
        if not os.path.lexists(path):
            continue
        info = _lstat(path, "Spartan Route SQLite state")
        if not stat.S_ISREG(info.st_mode):
            raise DataPolicyError("Spartan Route SQLite state", "target is not a regular file")
        targets.append(
            ManagedTarget(
                path,
                "Spartan Route SQLite state",
                expected_mode,
                False,
                _metadata(info),
            )
        )
    return targets


def _first_violation(targets: list[ManagedTarget], uid: int, gid: int) -> DataPolicyError | None:
    for target in targets:
        if target.metadata.uid != uid:
            return DataPolicyError(target.label, "owner does not match the configured runtime identity")
        if target.metadata.gid != gid:
            return DataPolicyError(target.label, "group does not match the configured runtime identity")
        if target.metadata.mode != target.expected_mode:
            return DataPolicyError(target.label, "mode does not match the private-state policy")
    return None


def check_data_dir(data_dir: Path, uid: int, gid: int) -> None:
    _validate_identity(uid, gid)
    violation = _first_violation(_collect_targets(data_dir), uid, gid)
    if violation is not None:
        raise violation


def prepare_data_dir(data_dir: Path, uid: int, gid: int) -> None:
    """Create one missing leaf directory, then enforce the normal check."""

    _validate_identity(uid, gid)
    data_dir = _absolute(data_dir)
    if os.path.lexists(data_dir):
        check_data_dir(data_dir, uid, gid)
        return

    parent = data_dir.parent
    _check_existing_components(parent, "Spartan Route data parent")
    parent_info = _lstat(parent, "Spartan Route data parent")
    if not stat.S_ISDIR(parent_info.st_mode):
        raise DataPolicyError("Spartan Route data parent", "target is not a directory")
    if os.geteuid() != uid:
        raise DataPolicyError(
            "Spartan Route data directory",
            "missing directory cannot be created for a different runtime owner",
        )
    allowed_groups = set(os.getgroups()) | {os.getgid()}
    if gid not in allowed_groups:
        raise DataPolicyError(
            "Spartan Route data directory",
            "missing directory cannot be created for an unavailable runtime group",
        )

    created = False
    try:
        os.mkdir(data_dir, DATA_DIRECTORY_MODE)
        created = True
        if os.getgid() != gid:
            os.chown(data_dir, -1, gid)
        os.chmod(data_dir, DATA_DIRECTORY_MODE)
    except OSError:
        if created:
            try:
                data_dir.rmdir()
            except OSError:
                pass
        raise DataPolicyError(
            "Spartan Route data directory",
            "creation failed without leaving a usable data directory",
        ) from None
    check_data_dir(data_dir, uid, gid)


def _open_read_only(path: Path) -> sqlite3.Connection:
    encoded = quote(str(path), safe="/")
    return sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)


def _backup_plan(
    data_dir: Path,
    db_target: ManagedTarget | None,
    uid: int,
    gid: int,
) -> tuple[Path, Path, bool] | None:
    if db_target is None:
        return None
    backup_dir = data_dir / "backups"
    create_directory = not os.path.lexists(backup_dir)
    if not create_directory:
        info = _lstat(backup_dir, "Spartan Route backup directory")
        if not stat.S_ISDIR(info.st_mode):
            raise DataPolicyError("Spartan Route backup directory", "target is not a directory")
        backup_metadata = _metadata(info)
        if (
            backup_metadata.uid != uid
            or backup_metadata.gid != gid
            or backup_metadata.mode != BACKUP_DIRECTORY_MODE
        ):
            raise DataPolicyError(
                "Spartan Route backup directory",
                "metadata does not match the private-state policy",
            )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"spartan-route-{stamp}.db"
    if os.path.lexists(destination):
        raise DataPolicyError("Spartan Route backup", "destination already exists")
    try:
        free_bytes = shutil.disk_usage(data_dir).free
        source_size = db_target.path.stat().st_size
    except OSError:
        raise DataPolicyError("Spartan Route backup", "free space could not be checked") from None
    if free_bytes < source_size + BACKUP_RESERVE_BYTES:
        raise DataPolicyError("Spartan Route backup", "insufficient free space")

    try:
        with _open_read_only(db_target.path) as source:
            quick_check = source.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error:
        raise DataPolicyError("Spartan Route backup", "source database is not safely readable") from None
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        raise DataPolicyError("Spartan Route backup", "source database did not pass its integrity check")
    return backup_dir, destination, create_directory


def _create_backup(
    plan: tuple[Path, Path, bool],
    source_path: Path,
    uid: int,
    gid: int,
) -> None:
    backup_dir, destination, create_directory = plan
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        if create_directory:
            os.mkdir(backup_dir, BACKUP_DIRECTORY_MODE)
            os.chown(backup_dir, uid, gid)
            os.chmod(backup_dir, BACKUP_DIRECTORY_MODE)
        with _open_read_only(source_path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
            quick_check = target.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or str(quick_check[0]).lower() != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
        os.chown(temporary, uid, gid)
        os.chmod(temporary, 0o600)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        directory_descriptor = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except (OSError, sqlite3.Error):
        try:
            temporary.unlink()
        except OSError:
            pass
        if create_directory:
            try:
                backup_dir.rmdir()
            except OSError:
                pass
        raise DataPolicyError(
            "Spartan Route backup",
            "backup failed before SQLite metadata was changed",
        ) from None


def migrate_data_dir(
    data_dir: Path,
    uid: int,
    gid: int,
    *,
    chmod_func: Callable[[Path, int], None] = os.chmod,
    chown_func: Callable[[Path, int, int], None] = os.chown,
) -> MigrationResult:
    _validate_identity(uid, gid)
    data_dir = _absolute(data_dir)
    targets = _collect_targets(data_dir)
    if _first_violation(targets, uid, gid) is None:
        return MigrationResult(changed=False, backup_created=False)

    db_target = next((target for target in targets if target.path.name == "spartan-route.db"), None)
    backup_plan = _backup_plan(data_dir, db_target, uid, gid)
    if backup_plan is not None and db_target is not None:
        _create_backup(backup_plan, db_target.path, uid, gid)

    # Opening a WAL database for backup can materialize missing -wal/-shm
    # files. Re-inventory the fixed family so those sidecars receive the same
    # exact-file metadata repair instead of inheriting the migration UID.
    targets = _collect_targets(data_dir)

    ordered = sorted(targets, key=lambda target: target.directory)
    changed: list[ManagedTarget] = []
    try:
        for target in ordered:
            if (
                target.metadata.uid == uid
                and target.metadata.gid == gid
                and target.metadata.mode == target.expected_mode
            ):
                continue
            changed.append(target)
            if target.metadata.uid != uid or target.metadata.gid != gid:
                chown_func(target.path, uid, gid)
            chmod_func(target.path, target.expected_mode)
    except OSError:
        rollback_failed = False
        for target in reversed(changed):
            try:
                chown_func(target.path, target.metadata.uid, target.metadata.gid)
                chmod_func(target.path, target.metadata.mode)
            except OSError:
                rollback_failed = True
        message = (
            "metadata repair failed and rollback was incomplete"
            if rollback_failed
            else "metadata repair failed; prior metadata restored"
        )
        raise DataPolicyError("Spartan Route SQLite state", message) from None

    return MigrationResult(changed=True, backup_created=backup_plan is not None)


def _identity(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise argparse.ArgumentTypeError("expected a numeric identity")
    value = int(raw)
    if value > 2**32 - 2:
        raise argparse.ArgumentTypeError("identity is outside the supported range")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare", "migrate"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--uid", type=_identity, required=True)
    parser.add_argument("--gid", type=_identity, required=True)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "check":
            check_data_dir(args.data_dir, args.uid, args.gid)
            message = "ok"
        elif args.action == "prepare":
            prepare_data_dir(args.data_dir, args.uid, args.gid)
            message = "prepared"
        else:
            result = migrate_data_dir(args.data_dir, args.uid, args.gid)
            if not result.changed:
                message = "already compatible"
            elif result.backup_created:
                message = "migrated after verified backup"
            else:
                message = "migrated"
    except DataPolicyError as exc:
        print(f"spartan-route-data: {exc.label}: {exc.message}", file=os.sys.stderr)
        return 1
    print(f"spartan-route-data: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
