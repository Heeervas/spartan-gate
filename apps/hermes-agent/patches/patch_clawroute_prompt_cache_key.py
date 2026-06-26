#!/usr/bin/env python3
"""Add a stable Hermes session cache key to ClawRoute chat requests."""

from __future__ import annotations

import os
from pathlib import Path

MARKER = "# spartan-gate: clawroute prompt cache key v3"
LEGACY_MARKERS = (
    "# spartan-gate: clawroute prompt cache key v2",
    "# spartan-gate: clawroute prompt cache key",
)


def candidate_paths() -> list[Path]:
    configured = os.environ.get("HERMES_CHAT_COMPLETIONS_TRANSPORT")
    candidates = [
        Path(configured) if configured else None,
        Path("/opt/hermes/hermes_cli/agent/transports/chat_completions.py"),
        Path("/opt/hermes/agent/transports/chat_completions.py"),
    ]
    return [path for path in candidates if path is not None]


def patch_source(source: str) -> tuple[str, bool]:
    if MARKER in source:
        return source, False

    lines = source.splitlines()
    for legacy_marker in LEGACY_MARKERS:
        while any(legacy_marker in line for line in lines):
            marker_index = next(index for index, line in enumerate(lines) if legacy_marker in line)
            block_end = marker_index + 1
            if lines[marker_index].startswith("        "):
                while block_end < len(lines):
                    line = lines[block_end]
                    if line.startswith("        return api_kwargs"):
                        break
                    block_end += 1
            else:
                while block_end < len(lines):
                    line = lines[block_end]
                    if line.startswith("class ChatCompletionsTransport"):
                        break
                    block_end += 1
            lines = lines[:marker_index] + lines[block_end:]

    function_start = next(
        (index for index, line in enumerate(lines) if line.startswith("    def build_kwargs(")),
        None,
    )
    if function_start is None:
        return source, False

    function_end = next(
        (index for index in range(function_start + 1, len(lines)) if lines[index].startswith("    def ")),
        len(lines),
    )
    legacy_return_index = next(
        (index for index in range(function_start + 1, function_end) if lines[index] == "        return api_kwargs"),
        None,
    )
    profile_return_index = next(
        (
            index
            for index in range(function_start + 1, function_end)
            if lines[index].strip() == "return self._build_kwargs_from_profile("
        ),
        None,
    )
    class_index = next(
        (index for index, line in enumerate(lines) if line.startswith("class ChatCompletionsTransport")),
        None,
    )
    if legacy_return_index is None or profile_return_index is None or class_index is None:
        return source, False

    helper = [
        MARKER,
        "def _spartan_add_clawroute_prompt_cache_key(api_kwargs, params, allow_missing_base_url=False):",
        '    request_base_url = str(api_kwargs.get("base_url") or params.get("base_url") or "")',
        '    clawroute_base_url = __import__("os").environ.get("OPENAI_BASE_URL", "")',
        "    is_clawroute_request = bool(clawroute_base_url) and (",
        "        request_base_url.rstrip('/') == clawroute_base_url.rstrip('/')",
        "        or (allow_missing_base_url and not request_base_url)",
        "    )",
        "    if not is_clawroute_request:",
        "        return api_kwargs",
        '    session_id = params.get("session_id")',
        "    if session_id:",
        '        extra_body = dict(api_kwargs.get("extra_body") or {})',
        '        extra_body.setdefault("prompt_cache_key", f"hermes:{session_id}")',
        '        api_kwargs["extra_body"] = extra_body',
        '    reasoning_config = params.get("reasoning_config")',
        '    if "reasoning_effort" not in api_kwargs and isinstance(reasoning_config, dict):',
        '        if reasoning_config.get("enabled") is not False:',
        '            effort = str(reasoning_config.get("effort") or "medium").strip().lower()',
        "            if effort:",
        '                api_kwargs["reasoning_effort"] = "low" if effort == "minimal" else effort',
        "    return api_kwargs",
        "",
        "",
    ]

    profile_call_end = profile_return_index
    while profile_call_end < function_end and lines[profile_call_end].strip() != ")":
        profile_call_end += 1
    if profile_call_end >= function_end:
        return source, False

    profile_call = lines[profile_return_index:profile_call_end + 1]
    profile_call[0] = profile_call[0].replace(
        "return self._build_kwargs_from_profile(",
        "profile_kwargs = self._build_kwargs_from_profile(",
        1,
    )
    profile_call.append(
        "            return _spartan_add_clawroute_prompt_cache_key(profile_kwargs, params, allow_missing_base_url=True)"
    )
    lines = lines[:profile_return_index] + profile_call + lines[profile_call_end + 1:]

    legacy_return_index += 1
    lines[legacy_return_index] = (
        "        return _spartan_add_clawroute_prompt_cache_key(api_kwargs, params)"
    )
    lines = lines[:class_index] + helper + lines[class_index:]

    trailing_newline = "\n" if source.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, True


def patch_file(path: Path) -> str:
    if not path.exists():
        return f"Skip: {path} missing"
    source = path.read_text(encoding="utf-8")
    patched, changed = patch_source(source)
    if not changed:
        status = "already patched" if MARKER in source else "build_kwargs shape not recognized"
        return f"Skip: {path} {status}"
    path.write_text(patched, encoding="utf-8")
    return f"Patched: {path} sends session_id as prompt_cache_key and reasoning_effort to ClawRoute"


def main() -> None:
    for path in candidate_paths():
        if path.exists():
            print(patch_file(path))
            return
    print("Skip: Hermes chat completions transport not found")


if __name__ == "__main__":
    main()
