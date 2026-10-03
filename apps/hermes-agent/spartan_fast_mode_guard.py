"""Verify the temporary image-pinned fast-mode overlay before Hermes starts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

FILES = ("hermes_cli/models.py", "bootstrap/spartan_fast_mode_guard.py")
ANCHORS = ("hermes_constants.py", "agent/fast_mode.py", "hermes_cli/models_catalog_static.py")
INIT_PATH = "/etc/cont-init.d/0196-spartan-fast-mode"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounded_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Fast-mode overlay requires bounded regular files")
    return path.read_bytes()


def validate(root: Path, manifest_path: Path, image: str, manifest_sha: str, init_path: Path = Path(INIT_PATH)) -> None:
    raw = bounded_file(manifest_path)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or digest(raw) != manifest_sha:
        raise ValueError("Fast-mode overlay manifest fingerprint changed")
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int
            or manifest["schema"] != 1 or manifest.get("image") != image):
        raise ValueError("Fast-mode overlay image or schema changed")
    for field, names in (("files", FILES), ("anchors", ANCHORS), ("originals", ("hermes_cli/models.py",)), ("support", (INIT_PATH,))):
        values = manifest.get(field)
        if (not isinstance(values, dict) or set(values) != set(names)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in values.values())):
            raise ValueError("Fast-mode overlay manifest lacks required fingerprints")
    for field in ("files", "anchors"):
        for name, checksum in manifest[field].items():
            if digest(bounded_file(root / name)) != checksum:
                raise ValueError("Fast-mode overlay source fingerprint changed")
    if digest(bounded_file(init_path)) != manifest["support"][INIT_PATH]:
        raise ValueError("Fast-mode overlay init fingerprint changed")


if __name__ == "__main__":
    try:
        validate(Path("/opt/hermes"), Path("/run/spartan-fast-code/manifest.json"),
                 os.environ.get("SPARTAN_FAST_CODE_IMAGE", ""),
                 os.environ.get("SPARTAN_FAST_CODE_MANIFEST_SHA256", ""))
    except Exception:
        raise SystemExit("[fast-mode] FATAL: code overlay validation failed") from None
