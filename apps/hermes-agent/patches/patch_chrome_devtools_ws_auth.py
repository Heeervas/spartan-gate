#!/usr/bin/env python3
"""Runtime migrator: keep browser tooling pointed at the configured CDP URL."""

from __future__ import annotations

import os
from pathlib import Path

WS_ENDPOINT_ARG = (
    "- --wsEndpoint=${BROWSER_CDP_URL}"
)
WS_HEADERS_ARG = '- \'--wsHeaders={"Authorization":"Bearer ${BROWSERLESS_TOKEN}"}\''
WS_HEADERS_MARKER = '--wsHeaders={"Authorization":"Bearer ${BROWSERLESS_TOKEN}"}'
WS_ENDPOINT_MARKER = "--wsEndpoint=${BROWSER_CDP_URL}"
LEGACY_BROWSER_URL_ARGS = {
    "- --browserUrl=http://browserless:3000",
    "- --browserUrl=http://browserless:3000?token=${BROWSERLESS_TOKEN}",
}
LEGACY_WS_ENDPOINT_PREFIX = "- --wsEndpoint=ws://browserless:3000/chromium"
LEGACY_CDP_URL_PREFIX = "ws://browserless:3000/chromium"
CDP_URL_MARKER = "${BROWSER_CDP_URL}"
CDP_MAIN_URL_MARKER = "${BROWSER_CDP_MAIN_URL}"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
MAIN_PROFILE_BLOCK = [
    "    main:",
    "      color: '#AA6600'",
    f"      cdp_url: {CDP_MAIN_URL_MARKER}",
    "      headless: false",
]
STEALTH_PROFILES_BLOCK = [
    "  profiles:",
    "    stealth:",
    "      color: '#00AA00'",
    f"      cdp_url: {CDP_URL_MARKER}",
    "      headless: false",
    *MAIN_PROFILE_BLOCK,
]


def main_profile_enabled() -> bool:
    if os.environ.get("BROWSER_CDP_MAIN_URL", "").strip():
        return True

    value = os.environ.get("BROWSERLESS_CDP_BROKER_ENABLED", "true").strip().lower()
    if value in FALSE_VALUES:
        return False
    if value in TRUE_VALUES:
        return True
    return bool(value)


def camofox_browser_mode_enabled() -> bool:
    return bool(os.environ.get("CAMOFOX_URL", "").strip())


def get_targets() -> list[Path]:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    targets = [home / "config.yaml"]
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        targets.extend(sorted(profiles_dir.glob("*/config.yaml")))
    return targets


def get_env_targets() -> list[Path]:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    targets = [home / ".env"]
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        targets.extend(sorted(profiles_dir.glob("*/.env")))
    return targets


def is_managed_cdp_value(value: str) -> bool:
    value = value.strip()
    return (
        not value
        or value in {"''", '\"\"'}
        or value in {CDP_URL_MARKER, CDP_MAIN_URL_MARKER}
        or LEGACY_CDP_URL_PREFIX in value
    )


def find_browser_block(lines: list[str]) -> tuple[int, int] | None:
    browser_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "browser:" and not line.startswith((" ", "\t"))
        ),
        None,
    )
    if browser_start is None:
        return None

    browser_end = len(lines)
    for index in range(browser_start + 1, len(lines)):
        if lines[index].strip() and not lines[index].startswith((" ", "\t")):
            browser_end = index
            break
    return browser_start, browser_end


def patch_lines(lines: list[str]) -> tuple[list[str], bool]:
    patched: list[str] = []
    changed = False
    has_headers = any(WS_HEADERS_MARKER in line for line in lines)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("cdp_url: ") and LEGACY_CDP_URL_PREFIX in stripped:
            indent = line[: len(line) - len(line.lstrip())]
            patched.append(f"{indent}cdp_url: {CDP_URL_MARKER}")
            changed = True
            continue

        is_legacy_browser_url = stripped in LEGACY_BROWSER_URL_ARGS
        is_legacy_ws_endpoint = stripped.startswith(LEGACY_WS_ENDPOINT_PREFIX)
        if not is_legacy_browser_url and not is_legacy_ws_endpoint:
            patched.append(line)
            continue

        indent = line[: len(line) - len(line.lstrip())]
        patched.append(f"{indent}{WS_ENDPOINT_ARG}")
        if not has_headers:
            patched.append(f"{indent}{WS_HEADERS_ARG}")
            has_headers = True
        changed = True

    if not has_headers:
        for index, line in enumerate(patched):
            if WS_ENDPOINT_MARKER in line:
                indent = line[: len(line) - len(line.lstrip())]
                patched.insert(index + 1, f"{indent}{WS_HEADERS_ARG}")
                changed = True
                break

    return patched, changed


def line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def remove_managed_chrome_devtools(lines: list[str]) -> tuple[list[str], bool]:
    updated: list[str] = []
    changed = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() != "chrome_devtools:":
            updated.append(line)
            index += 1
            continue

        indent = line_indent(line)
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and line_indent(candidate) <= indent:
                break
            end += 1

        block = lines[index:end]
        if any(WS_ENDPOINT_MARKER in item or WS_HEADERS_MARKER in item for item in block):
            changed = True
            index = end
            continue

        updated.extend(block)
        index = end

    return updated, changed


def remove_managed_browser_cdp_urls(lines: list[str]) -> tuple[list[str], bool]:
    updated: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("cdp_url:") and is_managed_cdp_value(stripped.split(":", 1)[1].strip()):
            changed = True
            continue
        updated.append(line)
    return updated, changed


def sanitize_camofox_mode(lines: list[str]) -> tuple[list[str], bool]:
    updated, devtools_changed = remove_managed_chrome_devtools(lines)
    updated, browser_changed = remove_managed_browser_cdp_urls(updated)
    return updated, devtools_changed or browser_changed


def set_top_browser_key(
    lines: list[str],
    browser_start: int,
    browser_end: int,
    key: str,
    value: str,
) -> tuple[list[str], int, bool]:
    updated = list(lines)
    for index in range(browser_start + 1, browser_end):
        stripped = updated[index].strip()
        if updated[index].startswith("  ") and not updated[index].startswith("    ") and stripped.startswith(f"{key}:"):
            if key == "cdp_url" and not is_managed_cdp_value(stripped.split(":", 1)[1].strip()):
                return updated, browser_end, False
            replacement = f"  {key}: {value}"
            if updated[index] == replacement:
                return updated, browser_end, False
            updated[index] = replacement
            return updated, browser_end, True

    insert_at = browser_start + 1
    updated.insert(insert_at, f"  {key}: {value}")
    return updated, browser_end + 1, True


def find_profiles_block(lines: list[str], browser_start: int, browser_end: int) -> tuple[int, int] | None:
    profiles_start = next(
        (
            index
            for index in range(browser_start + 1, browser_end)
            if lines[index] == "  profiles:"
        ),
        None,
    )
    if profiles_start is None:
        return None

    profiles_end = browser_end
    for index in range(profiles_start + 1, browser_end):
        if lines[index].startswith("  ") and not lines[index].startswith("    ") and lines[index].strip():
            profiles_end = index
            break
    return profiles_start, profiles_end


def find_profile_block(lines: list[str], profiles_start: int, profiles_end: int, profile: str) -> tuple[int, int] | None:
    profile_start = next(
        (
            index
            for index in range(profiles_start + 1, profiles_end)
            if lines[index] == f"    {profile}:"
        ),
        None,
    )
    if profile_start is None:
        return None

    profile_end = profiles_end
    for index in range(profile_start + 1, profiles_end):
        if lines[index].startswith("    ") and not lines[index].startswith("      ") and lines[index].strip():
            profile_end = index
            break
    return profile_start, profile_end


def set_profile_cdp_url(
    lines: list[str],
    profile_start: int,
    profile_end: int,
    value: str,
) -> tuple[list[str], int, bool]:
    updated = list(lines)
    for index in range(profile_start + 1, profile_end):
        stripped = updated[index].strip()
        if updated[index].startswith("      ") and stripped.startswith("cdp_url:"):
            if not is_managed_cdp_value(stripped.split(":", 1)[1].strip()):
                return updated, profile_end, False
            replacement = f"      cdp_url: {value}"
            if updated[index] == replacement:
                return updated, profile_end, False
            updated[index] = replacement
            return updated, profile_end, True

    insert_at = profile_start + 1
    updated.insert(insert_at, f"      cdp_url: {value}")
    return updated, profile_end + 1, True


def ensure_profile(
    lines: list[str],
    browser_start: int,
    browser_end: int,
    profile: str,
    value: str,
) -> tuple[list[str], int, bool]:
    updated = list(lines)
    profiles = find_profiles_block(updated, browser_start, browser_end)
    changed = False
    if profiles is None:
        updated[browser_end:browser_end] = ["  profiles:"]
        browser_end += 1
        changed = True
        profiles = (browser_end - 1, browser_end)

    profiles_start, profiles_end = profiles
    profile_block = find_profile_block(updated, profiles_start, profiles_end, profile)
    if profile_block is None:
        block = [
            f"    {profile}:",
            "      color: '#00AA00'" if profile == "stealth" else "      color: '#AA6600'",
            f"      cdp_url: {value}",
            "      headless: false",
        ]
        updated[profiles_end:profiles_end] = block
        return updated, browser_end + len(block), True

    profile_start, profile_end = profile_block
    updated, new_profile_end, profile_changed = set_profile_cdp_url(
        updated,
        profile_start,
        profile_end,
        value,
    )
    return updated, browser_end + (new_profile_end - profile_end), changed or profile_changed


def ensure_browser_profiles(lines: list[str]) -> tuple[list[str], bool]:
    browser_block = find_browser_block(lines)
    if browser_block is None:
        return lines, False

    updated = list(lines)
    changed = False
    browser_start, browser_end = browser_block

    updated, browser_end, key_changed = set_top_browser_key(
        updated,
        browser_start,
        browser_end,
        "default_profile",
        "stealth",
    )
    changed = changed or key_changed

    updated, browser_end, key_changed = set_top_browser_key(
        updated,
        browser_start,
        browser_end,
        "cdp_url",
        CDP_URL_MARKER,
    )
    changed = changed or key_changed

    updated, browser_end, profile_changed = ensure_profile(
        updated,
        browser_start,
        browser_end,
        "stealth",
        CDP_URL_MARKER,
    )
    changed = changed or profile_changed

    if main_profile_enabled():
        updated, browser_end, profile_changed = ensure_profile(
            updated,
            browser_start,
            browser_end,
            "main",
            CDP_MAIN_URL_MARKER,
        )
        changed = changed or profile_changed

    return updated, changed


def patch_env_file(path: Path) -> str:
    if not path.exists():
        return f"Skip: {path} missing"

    source = path.read_text(encoding="utf-8")
    trailing_newline = source.endswith("\n")
    changed = False
    patched: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped in {
            "BROWSER_CDP_LAUNCH_URL=${BROWSER_CDP_URL}",
            "BROWSER_CDP_LAUNCH_URL=",
        }:
            changed = True
            continue
        patched.append(line)

    if not changed:
        return f"Skip: {path} has no CDP env change"

    updated = "\n".join(patched)
    if trailing_newline:
        updated += "\n"
    path.write_text(updated, encoding="utf-8")
    return f"Patched: {path} - removed stale CDP launch env override"

def patch_file(path: Path) -> str:
    if not path.exists():
        return f"Skip: {path} missing"

    source = path.read_text(encoding="utf-8")
    has_chrome_devtools = "chrome_devtools:" in source
    has_browser_config = "browser:" in source
    has_managed_browser_config = "browser:" in source and (
        CDP_URL_MARKER in source
        or CDP_MAIN_URL_MARKER in source
        or "cdp_url: ''" in source
        or 'cdp_url: ""' in source
    )
    has_legacy_cdp_url = f"cdp_url: {LEGACY_CDP_URL_PREFIX}" in source
    if not has_chrome_devtools and not has_legacy_cdp_url and not has_managed_browser_config:
        return f"No-op: {path} has no Browserless/CDP config to migrate"

    trailing_newline = source.endswith("\n")
    patched_lines, endpoint_changed = patch_lines(source.splitlines())
    if camofox_browser_mode_enabled():
        patched_lines, camofox_changed = sanitize_camofox_mode(patched_lines)
        changed = endpoint_changed or camofox_changed
        if not changed:
            return f"Already patched: {path}"
        updated = "\n".join(patched_lines)
        if trailing_newline:
            updated += "\n"
        path.write_text(updated, encoding="utf-8")
        return f"Patched: {path} - disabled managed Browserless CDP tooling in Camofox mode"

    profiles_changed = False
    if has_browser_config:
        patched_lines, profiles_changed = ensure_browser_profiles(patched_lines)
    changed = endpoint_changed or profiles_changed
    if not changed:
        if has_chrome_devtools or has_managed_browser_config or has_legacy_cdp_url:
            return f"Already patched: {path}"
        return f"Skip: {path} has no browser tooling change"

    updated = "\n".join(patched_lines)
    if trailing_newline:
        updated += "\n"
    path.write_text(updated, encoding="utf-8")
    return f"Patched: {path} — browser tooling now uses default CDP plus optional main profile"


def main() -> None:
    for target in get_targets():
        print(patch_file(target))
    for target in get_env_targets():
        print(patch_env_file(target))


if __name__ == "__main__":
    main()
