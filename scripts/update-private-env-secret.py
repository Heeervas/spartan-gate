#!/usr/bin/env python3
"""Atomically update one private env secret received only through stdin."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid


KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAX_SECRET_BYTES = 4096


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def update_secret(env_file: Path, key: str, secret: str) -> Path:
    if not KEY_RE.fullmatch(key):
        raise ValueError("invalid env key")
    encoded = secret.encode("utf-8")
    if not encoded or len(encoded) > MAX_SECRET_BYTES or "\n" in secret or "\r" in secret:
        raise ValueError("invalid secret")

    env_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        value = env_file.lstat()
    except FileNotFoundError:
        had_original = False
        original = ""
    else:
        had_original = True
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
            raise ValueError("private env path is unsafe")
        original = env_file.read_text(encoding="utf-8")

    suffix = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    backup = env_file.with_name(f"{env_file.name}.bak.{suffix}")
    if had_original:
        shutil.copy2(env_file, backup)
    else:
        backup.write_bytes(b"")
    os.chmod(backup, 0o600)
    with backup.open("rb") as handle:
        os.fsync(handle.fileno())

    replacement = f"{key}={secret}"
    lines = original.splitlines()
    found = False
    updated: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}="):
            updated.append(replacement)
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(replacement)
    content = "\n".join(updated) + "\n"

    temporary = env_file.with_name(f".{env_file.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, env_file)
        os.chmod(env_file, 0o600)
        fsync_directory(env_file.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: update-private-env-secret.py ENV_FILE KEY", file=sys.stderr)
        return 2
    try:
        secret = sys.stdin.read(MAX_SECRET_BYTES + 2)
        update_secret(Path(sys.argv[1]), sys.argv[2], secret)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Private secret update failed safely: {error}", file=sys.stderr)
        return 1
    finally:
        secret = ""
    print("Updated private env secret; a private backup was retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
