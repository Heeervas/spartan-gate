#!/usr/bin/env python3
"""Record the image's actual optional dependencies for native Hermes PM."""

from __future__ import annotations

from pathlib import Path


REQUIRED_FEATURES = frozenset({"cron", "discord", "mcp", "telegram", "web"})


def freeze_features(root: Path, *, inventory=None, writer=None) -> list[str]:
    if inventory is None or writer is None:
        from pm.features import installed_extras, write_features

        inventory = inventory or installed_extras
        writer = writer or write_features
    root = root.resolve()
    venv = root / ".venv"
    extras = inventory(root, venv, python_exe=venv / "bin/python")
    if (
        not isinstance(extras, list)
        or not extras
        or any(not isinstance(extra, str) or not extra.strip() for extra in extras)
    ):
        raise ValueError("invalid image feature inventory")
    missing = REQUIRED_FEATURES.difference(extras)
    if missing:
        raise ValueError("image lacks required features: " + ", ".join(sorted(missing)))
    extras = sorted(set(extras))
    writer(extras, root)
    return extras


if __name__ == "__main__":
    freeze_features(Path("/opt/hermes"))
