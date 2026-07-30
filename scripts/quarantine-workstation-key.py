#!/usr/bin/env python3
"""Quarantine an exact container-visible workstation key after hash proof.

This helper is intentionally not part of `sg-workstation up`: removing a
credential is a separate, explicit operator action. It refuses symlinks,
different owners, broad permissions, hash mismatches, and cross-filesystem
moves. It never prints key contents.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import time
from pathlib import Path


class QuarantineError(RuntimeError):
    pass


def inspect_private_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise QuarantineError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise QuarantineError(f"{label} must be a regular non-symlink file")
    if info.st_uid != os.getuid():
        raise QuarantineError(f"{label} must be owned by uid {os.getuid()}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise QuarantineError(f"{label} must not grant group/other access")
    return info


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def quarantine(source: Path, exposed: Path, destination_dir: Path) -> Path:
    source = source.resolve(strict=True)
    source_info = inspect_private_file(source, "source execution key")
    exposed_info = inspect_private_file(exposed, "container-visible execution key")
    if source == exposed.resolve(strict=True):
        raise QuarantineError("source and exposed key must be different files")
    source_hash = digest(source)
    exposed_hash = digest(exposed)
    if source_hash != exposed_hash:
        raise QuarantineError("key hashes differ; exposed key was not changed")

    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_info = destination_dir.lstat()
    if (
        stat.S_ISLNK(directory_info.st_mode)
        or not stat.S_ISDIR(directory_info.st_mode)
        or directory_info.st_uid != os.getuid()
        or stat.S_IMODE(directory_info.st_mode) & 0o077
    ):
        raise QuarantineError("quarantine directory must be private and owner-only")
    if exposed_info.st_dev != directory_info.st_dev:
        raise QuarantineError(
            "quarantine must be on the same filesystem for an atomic move"
        )
    target = destination_dir / (
        f"workstation-execution-key.{int(time.time())}.{source_hash[:12]}.quarantined"
    )
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    os.close(descriptor)
    try:
        os.replace(exposed, target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    # Re-prove the moved inode and content. The host source remains untouched.
    inspect_private_file(target, "quarantined execution key")
    if digest(target) != source_hash:
        raise QuarantineError("quarantined key hash changed unexpectedly")
    return target


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--exposed", type=Path, required=True)
    result.add_argument("--quarantine-dir", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        target = quarantine(args.source, args.exposed, args.quarantine_dir)
    except (OSError, QuarantineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Quarantined matching execution key: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
