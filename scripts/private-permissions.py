#!/usr/bin/env python3
"""Check or repair least-privilege metadata for private Spartan Gate state."""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


CONFIG_TREES = (
    ("env", True),
    ("outbound-proxy", True),
    ("caddy", True),
    ("caddy.local.d", True),
    ("backups/tailscale-https", False),
)
CONFIG_FILES = ("compose.local.yml", "install-state.json", "aliases.local.sh")
DATA_ROOT_MODES = {
    "SPARTAN_HERMES_DATA_PATH": 0o700,
    "SPARTAN_ROUTE_DATA_PATH": 0o700,
    "SPARTAN_GOGCLI_DATA_PATH": 0o700,
    "SPARTAN_CAMOFOX_DATA_PATH": 0o700,
    "SPARTAN_CAMOFOX_ADDONS_PATH": 0o700,
    "SPARTAN_CODEX_HOME_PATH": 0o700,
    "SPARTAN_CADDY_CERTS_PATH": 0o700,
}
MAX_ENV_BYTES = 1024 * 1024


class PermissionPolicyError(Exception):
    def __init__(self, label: str, message: str):
        super().__init__(message)
        self.label = label
        self.message = message


@dataclass(frozen=True)
class Target:
    path: Path
    label: str
    expected_mode: int
    expected_uid: int
    expected_gid: int
    directory: bool


@dataclass(frozen=True)
class Metadata:
    mode: int
    uid: int
    gid: int


def _absolute(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path(os.path.abspath(path))


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise PermissionPolicyError(label, "target is missing") from None
        raise PermissionPolicyError(label, "metadata is not readable") from None
    if stat.S_ISLNK(result.st_mode):
        raise PermissionPolicyError(label, "symlink targets are forbidden")
    return result


def _check_path_components(path: Path, label: str) -> None:
    path = _absolute(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        _lstat(current, label)


def _parse_env(path: Path) -> dict[str, str]:
    _check_path_components(path, "private env")
    info = _lstat(path, "private env")
    if not stat.S_ISREG(info.st_mode):
        raise PermissionPolicyError("private env", "target is not a regular file")
    if info.st_size > MAX_ENV_BYTES:
        raise PermissionPolicyError("private env", "file exceeds the safe size limit")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise PermissionPolicyError("private env", "file is not safely readable") from None

    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.replace("_", "a").isalnum() or key[:1].isdigit():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _numeric_setting(values: dict[str, str], key: str, default: int) -> int:
    raw = values.get(key, "")
    if raw == "":
        return default
    if not raw.isascii() or not raw.isdigit():
        raise PermissionPolicyError(key, "expected a numeric identity")
    value = int(raw)
    if value < 0 or value > 2**32 - 2:
        raise PermissionPolicyError(key, "identity is outside the supported range")
    return value


def _target(
    path: Path,
    label: str,
    expected_mode: int,
    expected_uid: int,
    expected_gid: int,
    directory: bool,
) -> Target:
    path = _absolute(path)
    _check_path_components(path, label)
    info = _lstat(path, label)
    if directory and not stat.S_ISDIR(info.st_mode):
        raise PermissionPolicyError(label, "target is not a directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise PermissionPolicyError(label, "target is not a regular file")
    return Target(path, label, expected_mode, expected_uid, expected_gid, directory)


def _walk_config_tree(
    root: Path,
    uid: int,
    gid: int,
    include_root: bool = True,
) -> list[Target]:
    targets = []
    _check_path_components(root, "private config directory")
    root_info = _lstat(root, "private config directory")
    if not stat.S_ISDIR(root_info.st_mode):
        raise PermissionPolicyError("private config directory", "target is not a directory")

    if include_root:
        targets.append(_target(root, "private config directory", 0o700, uid, gid, True))
    try:
        entries = sorted(os.scandir(root), key=lambda item: item.name)
    except OSError:
        raise PermissionPolicyError("private config directory", "directory is not readable") from None

    for entry in entries:
        path = Path(entry.path)
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            raise PermissionPolicyError("private config entry", "metadata is not readable") from None
        if stat.S_ISLNK(info.st_mode):
            raise PermissionPolicyError("private config entry", "symlink targets are forbidden")
        if stat.S_ISDIR(info.st_mode):
            targets.extend(_walk_config_tree(path, uid, gid))
        elif stat.S_ISREG(info.st_mode):
            targets.append(_target(path, "private config file", 0o600, uid, gid, False))
        else:
            raise PermissionPolicyError("private config entry", "unsupported file type")
    return targets


def collect_targets(private_dir: Path, env_file: Path) -> list[Target]:
    private_dir = _absolute(private_dir)
    env_file = _absolute(env_file)
    values = _parse_env(env_file)
    expected_uid = _numeric_setting(values, "HERMES_UID", os.getuid())
    expected_gid = _numeric_setting(values, "HERMES_GID", os.getgid())

    service_identities = {
        "SPARTAN_ROUTE_DATA_PATH": (
            _numeric_setting(values, "SPARTAN_ROUTE_UID", expected_uid),
            _numeric_setting(values, "SPARTAN_ROUTE_GID", expected_gid),
        ),
        "SPARTAN_CADDY_CERTS_PATH": (
            _numeric_setting(values, "SPARTAN_CADDY_UID", expected_uid),
            _numeric_setting(values, "SPARTAN_CADDY_GID", expected_gid),
        ),
    }

    targets = [
        _target(private_dir, "private root", 0o700, expected_uid, expected_gid, True),
        _target(env_file, "private env", 0o600, expected_uid, expected_gid, False),
    ]

    for name, include_root in CONFIG_TREES:
        path = private_dir / name
        if os.path.lexists(path):
            targets.extend(_walk_config_tree(path, expected_uid, expected_gid, include_root))
    for name in CONFIG_FILES:
        path = private_dir / name
        if os.path.lexists(path):
            targets.append(
                _target(path, "private config file", 0o600, expected_uid, expected_gid, False)
            )

    for key, expected_mode in DATA_ROOT_MODES.items():
        raw = values.get(key, "")
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            raise PermissionPolicyError(key, "configured root must be absolute")
        if not os.path.lexists(path):
            raise PermissionPolicyError(key, "target is missing")
        uid, gid = service_identities.get(key, (expected_uid, expected_gid))
        targets.append(_target(path, key, expected_mode, uid, gid, True))

    deduplicated: dict[Path, Target] = {}
    for item in targets:
        previous = deduplicated.get(item.path)
        if previous is not None and (
            previous.expected_mode,
            previous.expected_uid,
            previous.expected_gid,
            previous.directory,
        ) != (
            item.expected_mode,
            item.expected_uid,
            item.expected_gid,
            item.directory,
        ):
            raise PermissionPolicyError(item.label, "conflicting policy for one target")
        deduplicated[item.path] = item
    return list(deduplicated.values())


def _metadata(target: Target) -> Metadata:
    info = _lstat(target.path, target.label)
    return Metadata(stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)


def _repairable_group(old_gid: int, new_gid: int) -> bool:
    if old_gid == new_gid or os.geteuid() == 0:
        return True
    groups = set(os.getgroups()) | {os.getgid()}
    return old_gid in groups and new_gid in groups


def preflight(targets: Iterable[Target]) -> dict[Path, Metadata]:
    states: dict[Path, Metadata] = {}
    for target in targets:
        _check_path_components(target.path, target.label)
        state = _metadata(target)
        if state.uid != target.expected_uid:
            raise PermissionPolicyError(target.label, "owner does not match the configured runtime identity")
        if not _repairable_group(state.gid, target.expected_gid):
            raise PermissionPolicyError(target.label, "group cannot be repaired safely")
        if target.directory and not target.path.is_dir():
            raise PermissionPolicyError(target.label, "target is not a directory")
        if not target.directory and not target.path.is_file():
            raise PermissionPolicyError(target.label, "target is not a regular file")
        states[target.path] = state
    return states


def unsafe_metadata(targets: Iterable[Target], states: dict[Path, Metadata]) -> list[str]:
    failures = []
    for target in targets:
        state = states[target.path]
        if state.mode != target.expected_mode:
            failures.append(f"{target.label}: unsafe mode")
        if state.gid != target.expected_gid:
            failures.append(f"{target.label}: unsafe group")
    return failures


def write_snapshot(path: Path, targets: Iterable[Target], states: dict[Path, Metadata]) -> None:
    path = _absolute(path)
    parent = path.parent
    _check_path_components(parent, "snapshot")
    if os.path.lexists(path):
        raise PermissionPolicyError("snapshot", "destination already exists")
    payload = {
        "version": 1,
        "targets": [
            {
                "path": str(target.path),
                "mode": states[target.path].mode,
                "uid": states[target.path].uid,
                "gid": states[target.path].gid,
            }
            for target in targets
        ],
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise PermissionPolicyError("snapshot", "could not create local metadata snapshot") from None


def apply_fixes(
    targets: list[Target],
    states: dict[Path, Metadata],
    chmod_func: Callable[[Path, int], None] = os.chmod,
    chown_func: Callable[[Path, int, int], None] = os.chown,
) -> None:
    changed: list[Target] = []
    try:
        for target in targets:
            before = states[target.path]
            if before.mode == target.expected_mode and before.gid == target.expected_gid:
                continue
            changed.append(target)
            if before.gid != target.expected_gid:
                chown_func(target.path, -1, target.expected_gid)
            chmod_func(target.path, target.expected_mode)
    except OSError:
        rollback_failed = False
        for target in reversed(changed):
            before = states[target.path]
            try:
                chown_func(target.path, -1, before.gid)
                chmod_func(target.path, before.mode)
            except OSError:
                rollback_failed = True
        message = (
            "repair failed and rollback was incomplete"
            if rollback_failed
            else "repair failed; prior metadata restored"
        )
        raise PermissionPolicyError("repair", message) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "fix"))
    parser.add_argument("--private-dir", type=Path, default=Path("private"))
    parser.add_argument("--env-file", type=Path, default=Path("private/env/local.env"))
    parser.add_argument("--snapshot", type=Path)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "check" and args.snapshot is not None:
        print("private-permissions: snapshot: only fix can create a snapshot", file=sys.stderr)
        return 2
    try:
        targets = collect_targets(args.private_dir, args.env_file)
        states = preflight(targets)
        failures = unsafe_metadata(targets, states)
        if args.action == "check":
            if failures:
                for failure in failures:
                    print(f"private-permissions: {failure}", file=sys.stderr)
                return 1
            print("private-permissions: ok")
            return 0
        if args.snapshot is not None:
            write_snapshot(args.snapshot, targets, states)
        apply_fixes(targets, states)
        print(f"private-permissions: repaired {len(failures)} metadata entries")
        return 0
    except PermissionPolicyError as exc:
        print(f"private-permissions: {exc.label}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
