#!/usr/bin/env python3
"""Fail closed when the immutable Hermes install tree is writable or misowned."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


WRITABLE_RUNTIME_ROOTS = (
    Path("/opt/data"),
    Path("/profiles"),
    Path("/run"),
    Path("/tmp"),
    Path("/var/tmp"),
)
POSIX_ACL_XATTRS = {"system.posix_acl_access", "system.posix_acl_default"}
UNSAFE_XATTRS = POSIX_ACL_XATTRS | {"security.capability"}
ALLOWED_EXTERNAL_ROOTS = (Path("/usr"), Path("/lib"), Path("/lib64"))


class InstallTreeError(RuntimeError):
    pass


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _check_external_target_chain(path: Path) -> None:
    """Require every resolved external target component to be root-controlled."""
    if not any(_under(path, root) for root in ALLOWED_EXTERNAL_ROOTS):
        raise InstallTreeError(f"external symlink target is outside system roots: {path}")
    current = path
    while True:
        try:
            metadata = current.lstat()
            xattrs = set(os.listxattr(current, follow_symlinks=False))
        except OSError as exc:
            raise InstallTreeError(
                f"cannot inspect external symlink target {current}: {exc}"
            ) from exc
        if metadata.st_uid != 0 or metadata.st_gid != 0:
            raise InstallTreeError(
                f"external symlink target is not root-owned: {current}"
            )
        if metadata.st_mode & 0o7022:
            raise InstallTreeError(
                f"external symlink target has unsafe mode: {current}"
            )
        if xattrs & UNSAFE_XATTRS:
            raise InstallTreeError(
                f"external symlink target has unsafe xattrs: {current}"
            )
        if current.parent == current:
            return
        current = current.parent


def _check_path(
    path: Path,
    *,
    install_root: Path,
    expected_uid: int,
    expected_gid: int,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InstallTreeError(f"cannot lstat {path}: {exc}") from exc

    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise InstallTreeError(
            f"unexpected owner {metadata.st_uid}:{metadata.st_gid}: {path}"
        )

    is_link = stat.S_ISLNK(metadata.st_mode)
    if not is_link and metadata.st_mode & 0o7022:
        raise InstallTreeError(
            f"unsafe mode {stat.S_IMODE(metadata.st_mode):04o}: {path}"
        )

    try:
        xattrs = set(os.listxattr(path, follow_symlinks=False))
    except OSError as exc:
        raise InstallTreeError(f"cannot inspect xattrs for {path}: {exc}") from exc
    unsafe_xattrs = sorted(xattrs & UNSAFE_XATTRS)
    if unsafe_xattrs:
        raise InstallTreeError(f"unsafe xattr present ({', '.join(unsafe_xattrs)}): {path}")

    if not is_link:
        return
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InstallTreeError(f"broken or cyclic symlink {path}: {exc}") from exc
    if _under(target, install_root):
        return
    for writable_root in WRITABLE_RUNTIME_ROOTS:
        if _under(target, writable_root):
            raise InstallTreeError(
                f"symlink escapes into writable runtime root {writable_root}: {path} -> {target}"
            )
    _check_external_target_chain(target)


def audit_tree(root: Path, *, expected_uid: int = 0, expected_gid: int = 0) -> int:
    root = root.absolute()
    if not root.is_dir() or root.is_symlink():
        raise InstallTreeError(f"install root must be a real directory: {root}")
    root_device = root.lstat().st_dev
    checked = 0
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [".", *directories, *files]:
            path = current_path if name == "." else current_path / name
            if name != "." and path.lstat().st_dev != root_device:
                raise InstallTreeError(f"unexpected mount boundary under install tree: {path}")
            _check_path(
                path,
                install_root=root,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            checked += 1
        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("/opt/hermes"))
    args = parser.parse_args()
    try:
        checked = audit_tree(args.root)
    except InstallTreeError as exc:
        print(f"FATAL: immutable Hermes install audit failed: {exc}", file=sys.stderr)
        return 1
    print(f"Immutable Hermes install audit passed: {checked} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
