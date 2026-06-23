#!/usr/bin/env python3
"""Runtime migrator: move legacy ClawRoute configs to the named custom-1 provider."""

from __future__ import annotations

import os
from pathlib import Path

LEGACY_DEFAULT_MODEL = "clawroute/auto"
MIGRATED_DEFAULT_MODEL = "custom-1/clawroute/auto"
LEGACY_PROVIDER = "custom"
MIGRATED_PROVIDER = "custom-1"
BASE_URL_MARKER = "${OPENAI_BASE_URL}"
KEY_ENV_MARKER = "CUSTOM_1_API_KEY"


def get_targets() -> list[Path]:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    targets = [home / "config.yaml"]
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        targets.extend(sorted(profiles_dir.glob("*/config.yaml")))
    return targets


def model_block_range(lines: list[str]) -> tuple[int | None, int | None]:
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "model:" and not line.startswith((" ", "\t"))
        ),
        None,
    )
    if start is None:
        return None, None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and not lines[index].startswith((" ", "\t")):
            end = index
            break
    return start, end


def patch_lines(lines: list[str]) -> tuple[list[str], bool, bool]:
    start, end = model_block_range(lines)
    if start is None or end is None:
        return lines, False, False

    block = lines[start:end]
    has_base_url = any(line.strip() == f"base_url: {BASE_URL_MARKER}" for line in block)
    has_key_hint = any(
        line.strip() in {
            f"api_key_env: {KEY_ENV_MARKER}",
            f"key_env: {KEY_ENV_MARKER}",
        }
        for line in block
    )

    default_index = next(
        (start + index for index, line in enumerate(block) if line.lstrip().startswith("default:")),
        None,
    )
    provider_index = next(
        (start + index for index, line in enumerate(block) if line.lstrip().startswith("provider:")),
        None,
    )
    if default_index is None or provider_index is None:
        return lines, False, False

    current_default = lines[default_index].split(":", 1)[1].strip()
    current_provider = lines[provider_index].split(":", 1)[1].strip()
    already_patched = (
        current_default == MIGRATED_DEFAULT_MODEL
        and current_provider == MIGRATED_PROVIDER
    )
    if already_patched:
        return lines, False, True

    should_patch = (
        has_base_url
        and has_key_hint
        and current_default in {LEGACY_DEFAULT_MODEL, MIGRATED_DEFAULT_MODEL}
        and current_provider in {LEGACY_PROVIDER, MIGRATED_PROVIDER}
    )
    if not should_patch:
        return lines, False, False

    updated = list(lines)
    default_indent = lines[default_index][: len(lines[default_index]) - len(lines[default_index].lstrip())]
    provider_indent = lines[provider_index][: len(lines[provider_index]) - len(lines[provider_index].lstrip())]
    updated[default_index] = f"{default_indent}default: {MIGRATED_DEFAULT_MODEL}"
    updated[provider_index] = f"{provider_indent}provider: {MIGRATED_PROVIDER}"
    changed = updated != lines
    return updated, changed, False


def patch_file(path: Path) -> str:
    if not path.exists():
        return f"Skip: {path} missing"

    source = path.read_text(encoding="utf-8")
    trailing_newline = source.endswith("\n")
    patched_lines, changed, already_patched = patch_lines(source.splitlines())
    if already_patched:
        return f"Already patched: {path}"
    if not changed:
        return f"Skip: {path} has no legacy ClawRoute provider config"

    updated = "\n".join(patched_lines)
    if trailing_newline:
        updated += "\n"
    path.write_text(updated, encoding="utf-8")
    return f"Patched: {path} — main model now uses named custom-1 provider"


def main() -> None:
    for target in get_targets():
        print(patch_file(target))


if __name__ == "__main__":
    main()