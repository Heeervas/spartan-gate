#!/usr/bin/env python3
"""Runtime migrator: move legacy route configs to native openai-api."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROUTE_PROVIDER = "openai-api"
ROUTE_AUTO_MODEL = "clawroute/auto"
LEGACY_PROVIDER_VALUES = {"custom", "custom-1", "custom:custom-1", "custom:spartan-route"}
LEGACY_AUTO_MODELS = {"clawroute/auto", "custom-1/clawroute/auto", "spartan-route/clawroute/auto"}
BASE_URL_MARKER = "${OPENAI_BASE_URL}"
KEY_ENV_MARKER = "CUSTOM_1_API_KEY"
ROUTE_PROVIDER_NAMES = {"custom-1", "Spartan Route"}
ROUTE_BASE_URLS = {"http://clawroute:18790/v1", "http://spartan-route:18790/v1"}


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


def _line_value(line: str) -> str:
    return line.split(":", 1)[1].strip().strip("'\"") if ":" in line else ""


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _set_line_value(line: str, value: str) -> str:
    key = line.split(":", 1)[0]
    return f"{key}: {value}"


def _has_route_marker(lines: list[str]) -> bool:
    return any(
        line.strip() == f"base_url: {BASE_URL_MARKER}"
        or any(base_url in line for base_url in ROUTE_BASE_URLS)
        for line in lines
    )


def _has_key_hint(lines: list[str]) -> bool:
    return any(
        line.strip() in {
            f"api_key_env: {KEY_ENV_MARKER}",
            f"key_env: {KEY_ENV_MARKER}",
        }
        for line in lines
    )


def _route_custom_provider_block(block: list[str]) -> bool:
    text = "\n".join(block)
    has_name = any(re.match(r"^\s*-?\s*name:\s*['\"]?(custom-1|Spartan Route)['\"]?\s*$", line) for line in block)
    if not has_name:
        return False
    has_base_url = BASE_URL_MARKER in text or any(base_url in text for base_url in ROUTE_BASE_URLS)
    has_key = KEY_ENV_MARKER in text
    has_route_model = any(alias in text for alias in LEGACY_AUTO_MODELS)
    return has_base_url or has_key or has_route_model


def _remove_route_custom_provider_blocks(lines: list[str]) -> tuple[list[str], bool]:
    output: list[str] = []
    changed = False
    index = 0
    while index < len(lines):
        if lines[index].strip() != "custom_providers:" or lines[index].startswith((" ", "\t")):
            output.append(lines[index])
            index += 1
            continue

        header = lines[index]
        index += 1
        child_lines: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and not line.startswith((" ", "\t", "#")):
                break
            child_lines.append(line)
            index += 1

        kept: list[str] = []
        cursor = 0
        while cursor < len(child_lines):
            if re.match(r"^\s*-\s*name:\s*", child_lines[cursor]):
                block = [child_lines[cursor]]
                cursor += 1
                while cursor < len(child_lines) and not re.match(r"^\s*-\s*name:\s*", child_lines[cursor]):
                    block.append(child_lines[cursor])
                    cursor += 1
                if _route_custom_provider_block(block):
                    changed = True
                else:
                    kept.extend(block)
            else:
                kept.append(child_lines[cursor])
                cursor += 1

        if any(line.strip() for line in kept):
            output.append(header)
            output.extend(kept)
        elif child_lines:
            changed = True
    return output, changed


def _migrate_model_value(value: str) -> str:
    if value in LEGACY_AUTO_MODELS:
        return ROUTE_AUTO_MODEL
    for prefix in ("custom-1/", "spartan-route/"):
        if value.startswith(prefix):
            return value.removeprefix(prefix)
    return value


def patch_lines(lines: list[str]) -> tuple[list[str], bool, bool]:
    updated, removed_blocks = _remove_route_custom_provider_blocks(lines)
    changed = removed_blocks

    start, end = model_block_range(updated)
    if start is None or end is None:
        return updated, changed, not changed

    block = updated[start:end]
    file_has_route_config = _has_route_marker(updated) and _has_key_hint(updated)
    block_has_route_config = _has_route_marker(block) and _has_key_hint(block)
    block_has_route_model = any(_line_value(line) in LEGACY_AUTO_MODELS for line in block if line.lstrip().startswith(("default:", "model:")))
    if not (file_has_route_config or block_has_route_config or block_has_route_model):
        return updated, changed, not changed

    default_index = next(
        (start + index for index, line in enumerate(block) if line.lstrip().startswith("default:")),
        None,
    )
    provider_index = next(
        (start + index for index, line in enumerate(block) if line.lstrip().startswith("provider:")),
        None,
    )
    base_url_index = next(
        (start + index for index, line in enumerate(block) if line.lstrip().startswith("base_url:")),
        None,
    )
    if default_index is None or provider_index is None:
        return updated, changed, not changed

    current_default = _line_value(updated[default_index])
    current_provider = _line_value(updated[provider_index])
    current_base_url = _line_value(updated[base_url_index]) if base_url_index is not None else ""

    migrated_default = _migrate_model_value(current_default)
    if migrated_default != current_default:
        updated[default_index] = f"{_indent(updated[default_index])}default: {migrated_default}"
        changed = True

    if current_provider in LEGACY_PROVIDER_VALUES and current_provider != ROUTE_PROVIDER:
        updated[provider_index] = f"{_indent(updated[provider_index])}provider: {ROUTE_PROVIDER}"
        changed = True

    if base_url_index is not None and current_base_url in ROUTE_BASE_URLS:
        updated[base_url_index] = f"{_indent(updated[base_url_index])}base_url: {BASE_URL_MARKER}"
        changed = True

    return updated, changed, not changed


def patch_file(path: Path) -> str:
    if not path.exists():
        return f"Skip: {path} missing"

    source = path.read_text(encoding="utf-8")
    trailing_newline = source.endswith("\n")
    patched_lines, changed, already_patched = patch_lines(source.splitlines())
    if already_patched:
        return f"Already patched: {path}"
    if not changed:
        return f"Skip: {path} has no legacy route provider config"

    updated = "\n".join(patched_lines)
    if trailing_newline:
        updated += "\n"
    path.write_text(updated, encoding="utf-8")
    return f"Patched: {path} - main model now uses native openai-api provider"


def main() -> None:
    for target in get_targets():
        print(patch_file(target))


if __name__ == "__main__":
    main()
