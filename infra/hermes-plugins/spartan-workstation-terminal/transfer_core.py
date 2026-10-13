"""Pure transfer primitives shared by the Hermes plugin and laptop broker.

The model never sees or supplies archive bytes.  This module walks declared
workspace roots without following links, produces deterministic manifests,
streams normalized tar records, and publishes verified staging paths.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_DEPTH = 64
MAX_COMPONENT_BYTES = 255
MAX_RELATIVE_BYTES = 4096
MIN_FREE_BYTES = 1024 * 1024 * 1024
SENSITIVE_SAMPLE_LIMIT = 10
PRIVATE_KEY_SCAN_BYTES = 64 * 1024
TRANSFER_ID_RE = re.compile(r"^wtx_[a-f0-9]{24}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
WINDOWS_RESERVED_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
    b"PuTTY-User-Key-File-",
)
HARD_COMPONENTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
}
HARD_BASENAMES = {
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
}
SOFT_BASENAMES = {
    ".npmrc",
    ".pypirc",
    ".netrc",
}
SOFT_WORD_RE = re.compile(r"(?:credential|secret|token)", re.IGNORECASE)


class TransferError(RuntimeError):
    """A bounded failure safe to return through the tool boundary."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def validate_relative(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\0" in value:
        raise TransferError("path must be a non-empty relative UTF-8 string")
    if value.startswith("/") or value.endswith("/"):
        raise TransferError("path must be relative and must not end with slash")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise TransferError("path must be valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise TransferError("path must use NFC Unicode normalization")
    path = PurePosixPath(value)
    parts = path.parts
    if not parts or len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} for part in parts):
        raise TransferError("path traversal or excessive depth is not allowed")
    if len(value.encode("utf-8")) > MAX_RELATIVE_BYTES:
        raise TransferError("relative path exceeds the size limit")
    for part in parts:
        encoded = part.encode("utf-8")
        if len(encoded) > MAX_COMPONENT_BYTES:
            raise TransferError("path component exceeds the size limit")
        if any(unicodedata.category(char) in {"Cc", "Cf"} for char in part):
            raise TransferError("path contains control or format characters")
    return parts


def validate_windows_paths(paths: list[str]) -> None:
    seen: set[str] = set()
    for value in paths:
        parts = validate_relative(value)
        for part in parts:
            if any(char in '<>:"/\\|?*' for char in part):
                raise TransferError("path is incompatible with Windows")
            if part.endswith((" ", ".")) or WINDOWS_RESERVED_RE.fullmatch(part):
                raise TransferError("path is incompatible with Windows")
        key = unicodedata.normalize("NFC", value).casefold()
        if key in seen:
            raise TransferError("case-insensitive destination collision")
        seen.add(key)


def _open_root(root: Path) -> tuple[int, os.stat_result]:
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise TransferError("workspace root is unavailable or unsafe") from exc
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise TransferError("workspace root is not a directory")
    return fd, info


def _open_relative(root_fd: int, parts: tuple[str, ...]) -> tuple[int, os.stat_result]:
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        return current, os.fstat(current)
    except OSError as exc:
        try:
            os.close(current)
        except OSError:
            pass
        raise TransferError("path is unavailable or crosses a symlink") from exc


def _hash_fd(fd: int) -> tuple[str, bytes]:
    digest = hashlib.sha256()
    prefix = bytearray()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        if len(prefix) < PRIVATE_KEY_SCAN_BYTES:
            prefix.extend(chunk[: PRIVATE_KEY_SCAN_BYTES - len(prefix)])
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest(), bytes(prefix)


def _path_sensitivity(relative: str, prefix: bytes) -> tuple[str | None, str | None]:
    components = [item.casefold() for item in PurePosixPath(relative or "payload").parts]
    basename = components[-1]
    if any(item in HARD_COMPONENTS for item in components):
        return "hard", "credential-store path"
    if basename in HARD_BASENAMES or basename.endswith((".p12", ".pfx", ".ppk")):
        return "hard", "private-key path"
    if any(marker in prefix for marker in PRIVATE_KEY_MARKERS):
        return "hard", "private-key content"
    if basename == "config" and ".git" in components:
        return "soft", "Git credential-bearing config"
    if basename == ".env" or basename.startswith(".env.") or basename in SOFT_BASENAMES:
        return "soft", "sensitive configuration path"
    if SOFT_WORD_RE.search(basename):
        return "soft", "sensitive filename"
    return None, None


def _entry_dict(relative: str, info: os.stat_result, kind: str, digest: str = "") -> dict[str, Any]:
    return {
        "path": relative,
        "type": kind,
        "size": info.st_size if kind == "file" else 0,
        "executable": bool(info.st_mode & 0o111) if kind == "file" else False,
        "sha256": digest,
    }


def _walk_directory(
    directory_fd: int,
    root_dev: int,
    prefix: tuple[str, ...],
    entries: list[dict[str, Any]],
    sensitive: list[dict[str, str]],
) -> int:
    total = 0
    with os.scandir(directory_fd) as iterator:
        names = sorted((entry.name for entry in iterator), key=lambda item: item.encode("utf-8"))
    for name in names:
        relative_parts = prefix + (name,)
        relative = "/".join(relative_parts)
        validate_relative(relative)
        try:
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise TransferError("tree contains a symlink or unreadable entry") from exc
        try:
            info = os.fstat(child_fd)
            if info.st_dev != root_dev:
                raise TransferError("tree crosses a filesystem boundary")
            if stat.S_ISDIR(info.st_mode):
                entries.append(_entry_dict(relative, info, "directory"))
                if len(entries) > MAX_ENTRIES:
                    raise TransferError("tree exceeds the entry limit")
                total += _walk_directory(
                    child_fd, root_dev, relative_parts, entries, sensitive
                )
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise TransferError("hard-linked files are not transferable")
                digest, sample = _hash_fd(child_fd)
                entries.append(_entry_dict(relative, info, "file", digest))
                level, reason = _path_sensitivity(relative, sample)
                if level:
                    sensitive.append({"path": relative, "level": level, "reason": reason or ""})
                total += info.st_size
                if total > MAX_TOTAL_BYTES:
                    raise TransferError("transfer exceeds the byte limit")
                if len(entries) > MAX_ENTRIES:
                    raise TransferError("tree exceeds the entry limit")
            else:
                raise TransferError("special files are not transferable")
        finally:
            os.close(child_fd)
    return total


def _manifest_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(canonical_json(entry))
        digest.update(b"\n")
    return digest.hexdigest()


def preflight_source(
    root: Path | str,
    relative: str,
    *,
    allow_sensitive: bool = False,
    windows_destination: bool = False,
) -> dict[str, Any]:
    root_path = Path(root)
    parts = validate_relative(relative)
    root_fd, root_info = _open_root(root_path)
    try:
        source_fd, source_info = _open_relative(root_fd, parts)
        try:
            if source_info.st_dev != root_info.st_dev:
                raise TransferError("source crosses a filesystem boundary")
            entries: list[dict[str, Any]] = []
            sensitive: list[dict[str, str]] = []
            if stat.S_ISREG(source_info.st_mode):
                if source_info.st_nlink != 1:
                    raise TransferError("hard-linked files are not transferable")
                digest, sample = _hash_fd(source_fd)
                entries.append(_entry_dict("", source_info, "file", digest))
                level, reason = _path_sensitivity(relative, sample)
                if level:
                    sensitive.append({"path": relative, "level": level, "reason": reason or ""})
                kind = "file"
                total = source_info.st_size
            elif stat.S_ISDIR(source_info.st_mode):
                entries.append(_entry_dict("", source_info, "directory"))
                total = _walk_directory(
                    source_fd, root_info.st_dev, (), entries, sensitive
                )
                kind = "directory"
            else:
                raise TransferError("source must be a regular file or directory")
        finally:
            os.close(source_fd)
    finally:
        os.close(root_fd)

    if total > MAX_TOTAL_BYTES:
        raise TransferError("transfer exceeds the byte limit")
    if windows_destination and kind == "directory":
        validate_windows_paths(
            [entry["path"] for entry in entries if entry["path"]]
        )
    elif windows_destination:
        validate_windows_paths([PurePosixPath(relative).name])
    hard = [item for item in sensitive if item["level"] == "hard"]
    soft = [item for item in sensitive if item["level"] == "soft"]
    if hard:
        raise TransferError("source contains hard-denied private-key or credential material")
    if soft and not allow_sensitive:
        raise TransferError("source contains sensitive paths; allow_sensitive=true is required")
    if len(sensitive) > SENSITIVE_SAMPLE_LIMIT:
        raise TransferError("sensitive path list exceeds the approval display limit")
    return {
        "kind": kind,
        "entry_count": len(entries),
        "total_bytes": total,
        "manifest_sha256": _manifest_digest(entries),
        "entries": entries,
        "soft_sensitive_count": len(soft),
        "sensitive_samples": sensitive,
    }


def public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary[key]
        for key in (
            "kind",
            "entry_count",
            "total_bytes",
            "manifest_sha256",
            "soft_sensitive_count",
            "sensitive_samples",
        )
        if key in summary
    }


class _VerifyingReader:
    def __init__(self, fd: int, expected: str):
        self.fd = fd
        self.expected = expected
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = os.read(self.fd, 1024 * 1024 if size is None or size < 0 else size)
        self.digest.update(chunk)
        if not chunk and self.digest.hexdigest() != self.expected:
            raise TransferError("source changed after approval")
        return chunk


def _open_entry(root: Path, source_relative: str, entry_relative: str) -> tuple[int, os.stat_result]:
    root_fd, root_info = _open_root(root)
    try:
        parts = validate_relative(source_relative)
        if entry_relative:
            parts += validate_relative(entry_relative)
        fd, info = _open_relative(root_fd, parts)
        if info.st_dev != root_info.st_dev:
            os.close(fd)
            raise TransferError("source crosses a filesystem boundary")
        return fd, info
    finally:
        os.close(root_fd)


def stream_tar(
    root: Path | str,
    relative: str,
    approved: dict[str, Any],
    destination: BinaryIO,
) -> None:
    root_path = Path(root)
    current = preflight_source(
        root_path,
        relative,
        allow_sensitive=bool(approved.get("soft_sensitive_count")),
    )
    if current["manifest_sha256"] != approved.get("manifest_sha256"):
        raise TransferError("source changed after approval")
    with tarfile.open(fileobj=destination, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for entry in current["entries"]:
            name = "payload" + (f"/{entry['path']}" if entry["path"] else "")
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            if entry["type"] == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
                continue
            info.type = tarfile.REGTYPE
            info.mode = 0o700 if entry["executable"] else 0o600
            info.size = entry["size"]
            fd, live = _open_entry(root_path, relative, entry["path"])
            try:
                if not stat.S_ISREG(live.st_mode) or live.st_nlink != 1 or live.st_size != entry["size"]:
                    raise TransferError("source changed after approval")
                archive.addfile(info, _VerifyingReader(fd, entry["sha256"]))
            finally:
                os.close(fd)


def _safe_remove_staging(path: Path, transfer_id: str) -> None:
    if path.name != f".spartan-transfer-{transfer_id}.part":
        raise TransferError("refusing to remove an unexpected staging path")
    if path.exists():
        shutil.rmtree(path)


def _fsync_tree(path: Path) -> None:
    if path.is_file():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return
    for base, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            fd = os.open(Path(base) / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise TransferError("atomic no-replace publication is unsupported")
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise TransferError("destination already exists")
        raise TransferError(
            f"atomic no-replace publication failed: {os.strerror(error)}"
        )


def probe_destination_capabilities(root: Path | str) -> dict[str, bool]:
    root_path = Path(root)
    root_fd, _root_info = _open_root(root_path)
    os.close(root_fd)
    name = f".spartan-transfer-probe-{os.getpid()}-{secrets.token_hex(8)}"
    probe = root_path / name
    if not name.startswith(".spartan-transfer-probe-"):
        raise TransferError("invalid capability probe path")
    created = False
    try:
        probe.mkdir(mode=0o700)
        created = True
        source = probe / "source"
        first = probe / "published"
        replacement = probe / "replacement"
        descriptor = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(descriptor, b"first")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _rename_noreplace(source, first)
        descriptor = os.open(
            replacement,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(descriptor, b"second")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(replacement, first)
        descriptor = os.open(first, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if os.read(descriptor, 16) != b"second":
                raise TransferError("destination replacement probe was not durable")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_fd = os.open(
            probe, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            "atomic_noreplace": True,
            "regular_file_replace": True,
            "file_fsync": True,
            "directory_fsync": True,
        }
    except OSError as exc:
        raise TransferError(
            f"destination lacks required atomic publication capability: {exc}"
        ) from exc
    finally:
        if created:
            shutil.rmtree(probe)

def destination_metadata(root: Path | str, relative: str) -> dict[str, Any]:

    parts = validate_relative(relative)
    root_fd, root_info = _open_root(Path(root))
    try:
        try:
            fd, info = _open_relative(root_fd, parts)
        except TransferError as exc:
            cause = exc.__cause__
            if isinstance(cause, OSError) and cause.errno == errno.ENOENT:
                return {"exists": False}
            raise
        try:
            if info.st_dev != root_info.st_dev:
                raise TransferError("destination crosses a filesystem boundary")
            if stat.S_ISDIR(info.st_mode):
                return {"exists": True, "kind": "directory"}
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise TransferError("destination is not a replaceable regular file")
            digest, _sample = _hash_fd(fd)
            return {
                "exists": True,
                "kind": "file",
                "size": info.st_size,
                "sha256": digest,
                "device": info.st_dev,
                "inode": info.st_ino,
                "ctime_ns": info.st_ctime_ns,
            }
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


def _destination_metadata_at(
    parent_fd: int,
    name: str,
    root_dev: int,
) -> dict[str, Any]:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return {"exists": False}
        raise TransferError("destination is unavailable or unsafe") from exc
    try:
        info = os.fstat(fd)
        if info.st_dev != root_dev:
            raise TransferError("destination crosses a filesystem boundary")
        if stat.S_ISDIR(info.st_mode):
            return {"exists": True, "kind": "directory"}
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TransferError("destination is not a replaceable regular file")
        digest, _sample = _hash_fd(fd)
        return {
            "exists": True,
            "kind": "file",
            "size": info.st_size,
            "sha256": digest,
            "device": info.st_dev,
            "inode": info.st_ino,
            "ctime_ns": info.st_ctime_ns,
        }
    finally:
        os.close(fd)


def receive_tar(
    source: BinaryIO,
    root: Path | str,
    relative: str,
    approved: dict[str, Any],
    *,
    transfer_id: str,
    replace: bool,
    expected_destination: dict[str, Any] | None = None,
    windows_destination: bool = False,
    minimum_free_bytes: int = MIN_FREE_BYTES,
) -> dict[str, Any]:
    if not TRANSFER_ID_RE.fullmatch(transfer_id):
        raise TransferError("invalid transfer id")
    requested = approved.get("total_bytes")
    if type(requested) is not int or not 0 <= requested <= MAX_TOTAL_BYTES:
        raise TransferError("invalid approved transfer byte budget")
    if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
        raise TransferError("invalid transfer free-space reserve")
    root_path = Path(root)
    parts = validate_relative(relative)
    root_fd, root_info = _open_root(root_path)
    try:
        if len(parts) == 1:
            parent_fd = os.dup(root_fd)
            parent_info = os.fstat(parent_fd)
        else:
            parent_fd, parent_info = _open_relative(root_fd, parts[:-1])
    finally:
        os.close(root_fd)
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_dev != root_info.st_dev
    ):
        os.close(parent_fd)
        raise TransferError("destination parent must be a safe existing directory")

    parent = Path(f"/proc/self/fd/{parent_fd}")
    destination = parent / parts[-1]
    display_destination = root_path.joinpath(*parts)
    staging_name = f".spartan-transfer-{transfer_id}.part"
    staging = parent / staging_name
    try:
        current_destination = _destination_metadata_at(
            parent_fd, parts[-1], root_info.st_dev
        )
        if approved.get("kind") == "directory" and current_destination.get("exists"):
            raise TransferError("an existing directory destination is never replaced")
        if current_destination.get("exists") and not replace:
            raise TransferError("destination already exists")
        if replace:
            if approved.get("kind") != "file" or current_destination.get("kind") != "file":
                raise TransferError("replace is supported only for regular files")
            if expected_destination and current_destination != expected_destination:
                raise TransferError("destination changed after approval")
        try:
            filesystem = os.fstatvfs(parent_fd)
        except OSError as exc:
            raise TransferError("cannot determine destination free space") from exc
        if filesystem.f_bavail < 0 or filesystem.f_frsize <= 0:
            raise TransferError("cannot determine destination free space")
        available = filesystem.f_bavail * filesystem.f_frsize
        if available - requested < minimum_free_bytes:
            raise TransferError(
                "insufficient free space for transfer staging: "
                f"available={available} bytes, requested={requested} bytes, "
                f"reserve={minimum_free_bytes} bytes"
            )
        try:
            os.mkdir(staging_name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise TransferError("transfer staging path already exists") from exc
        payload = staging / "payload"
        received_entries: list[dict[str, Any]] = []
        received_total = 0
        try:
            with tarfile.open(fileobj=source, mode="r|*") as archive:
                for member in archive:
                    name = member.name
                    if name == "payload":
                        child_relative = ""
                    elif name.startswith("payload/"):
                        child_relative = name[len("payload/") :]
                        validate_relative(child_relative)
                    else:
                        raise TransferError("archive member escapes payload root")
                    target = (
                        payload
                        if not child_relative
                        else payload.joinpath(*PurePosixPath(child_relative).parts)
                    )
                    if member.isdir():
                        target.mkdir(mode=0o700, parents=False, exist_ok=False)
                        received_entries.append(
                            {
                                "path": child_relative,
                                "type": "directory",
                                "size": 0,
                                "executable": False,
                                "sha256": "",
                            }
                        )
                    elif member.isfile():
                        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise TransferError("archive file payload is missing")
                        digest = hashlib.sha256()
                        written = 0
                        fd = os.open(
                            target,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o700 if member.mode & 0o111 else 0o600,
                        )
                        try:
                            while True:
                                chunk = extracted.read(1024 * 1024)
                                if not chunk:
                                    break
                                written += len(chunk)
                                received_total += len(chunk)
                                if received_total > requested:
                                    raise TransferError("archive exceeds the approved byte limit")
                                digest.update(chunk)
                                os.write(fd, chunk)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        received_entries.append(
                            {
                                "path": child_relative,
                                "type": "file",
                                "size": written,
                                "executable": bool(member.mode & 0o111),
                                "sha256": digest.hexdigest(),
                            }
                        )
                    else:
                        raise TransferError("archive contains a link or special member")
                    if len(received_entries) > MAX_ENTRIES:
                        raise TransferError("archive exceeds the entry limit")
            if windows_destination:
                validate_windows_paths(
                    [
                        entry["path"]
                        for entry in received_entries
                        if entry["path"]
                    ]
                )
            received_entries.sort(key=lambda item: item["path"].encode("utf-8"))
            received_digest = _manifest_digest(received_entries)
            if (
                received_digest != approved.get("manifest_sha256")
                or len(received_entries) != approved.get("entry_count")
                or sum(item["size"] for item in received_entries)
                != approved.get("total_bytes")
            ):
                raise TransferError("received archive does not match approved manifest")
            _fsync_tree(payload)
            if replace:
                latest = _destination_metadata_at(
                    parent_fd, parts[-1], root_info.st_dev
                )
                if expected_destination and latest != expected_destination:
                    raise TransferError("destination changed after approval")
                os.replace(payload, destination)
            else:
                _rename_noreplace(payload, destination)
            os.fsync(parent_fd)
            staging.rmdir()
            return {
                **public_summary(approved),
                "destination": str(display_destination),
                "replaced": bool(replace),
            }
        except Exception:
            _safe_remove_staging(staging, transfer_id)
            raise
    finally:
        os.close(parent_fd)


def _cli() -> int:
    if len(sys.argv) != 3:
        print("invalid transfer helper invocation", file=sys.stderr)
        return 2
    mode = sys.argv[1]
    try:
        request = json.loads(sys.argv[2])
        root = Path(request["root"])
        relative = request["path"]
        if mode == "probe":
            print(
                json.dumps(
                    {"ok": True, "result": probe_destination_capabilities(root)},
                    sort_keys=True,
                )
            )
        elif mode == "preflight":
            result = public_summary(
                preflight_source(
                    root,
                    relative,
                    allow_sensitive=bool(request.get("allow_sensitive")),
                    windows_destination=bool(request.get("windows_destination")),
                )
            )
            print(json.dumps({"ok": True, "result": result}, sort_keys=True))
        elif mode == "destination":
            print(
                json.dumps(
                    {"ok": True, "result": destination_metadata(root, relative)},
                    sort_keys=True,
                )
            )
        elif mode == "send":
            summary = request["summary"]
            current = preflight_source(
                root,
                relative,
                allow_sensitive=bool(summary.get("soft_sensitive_count")),
                windows_destination=bool(request.get("windows_destination")),
            )
            if current["manifest_sha256"] != summary.get("manifest_sha256"):
                raise TransferError("source changed after approval")
            stream_tar(root, relative, current, sys.stdout.buffer)
        elif mode == "receive":
            result = receive_tar(
                sys.stdin.buffer,
                root,
                relative,
                request["summary"],
                transfer_id=request["transfer_id"],
                replace=bool(request.get("replace")),
                expected_destination=request.get("expected_destination"),
                minimum_free_bytes=request.get("minimum_free_bytes", MIN_FREE_BYTES),
                windows_destination=bool(request.get("windows_destination")),
            )
            print(json.dumps({"ok": True, "result": result}, sort_keys=True))
        else:
            raise TransferError("unknown transfer helper mode")
        return 0
    except (KeyError, OSError, TransferError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
