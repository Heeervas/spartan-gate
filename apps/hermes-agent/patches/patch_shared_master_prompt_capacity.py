#!/usr/bin/env python3
"""Raise finite native plugin prompt budgets for the measured common MASTER.

Ordinary registrations retain their 4000-character default. No runtime API,
transport or configuration is added; restoration retains native validation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile


EDITS = {
    "hermes_cli/plugins_dispatch.py": (
        ("MAX_SYSTEM_PROMPT_SECTION_CHARS = 4_000", "MAX_SYSTEM_PROMPT_SECTION_CHARS = 20_000"),
        ("MAX_SYSTEM_PROMPT_SECTIONS_TOTAL_CHARS = 8_000", "MAX_SYSTEM_PROMPT_SECTIONS_TOTAL_CHARS = 24_000"),
    ),
    "agent/system_prompt.py": (
        ("(?P<chars>[0-9]{1,4})", "(?P<chars>[0-9]{1,5})"),
        ("def _restore_plugin_prompt_sections(prompt: str) -> tuple:",
         "def _restore_plugin_prompt_sections(prompt: str, profile_line: str = \"\") -> tuple:"),
        ('    if not prompt[after_end:].startswith("\\n\\nConversation started:"):',
         '    suffix = prompt[after_end:]\n'
         '    timestamp_anchor = "\\n\\nConversation started:"\n'
         '    profile_anchor = "\\n\\n" + profile_line + timestamp_anchor\n'
         '    if not (suffix.startswith(timestamp_anchor) or (profile_line and suffix.startswith(profile_anchor))):'),
        ("rendered = _restore_plugin_prompt_sections(stored_prompt)",
         "rendered = _restore_plugin_prompt_sections(stored_prompt, _active_profile_line(agent))"),
        ("agent._plugin_system_prompt_sections_snapshot = _restore_plugin_prompt_sections(prompt)",
         "agent._plugin_system_prompt_sections_snapshot = _restore_plugin_prompt_sections(prompt, _active_profile_line(agent))"),
    ),
}


def patch_source(source: str, relative_path: str) -> str:
    """Accept one original or already-patched occurrence per supported anchor."""
    for original, replacement in EDITS[relative_path]:
        if source.count(original) == 1 and replacement not in source:
            source = source.replace(original, replacement)
        elif source.count(replacement) != 1 or original in source:
            raise ValueError(f"Unsupported Hermes prompt capacity anchor in {relative_path}")
    compile(source, relative_path, "exec")
    return source


def patch_tree(root: Path) -> None:
    """Preflight both targets before writing; replace each changed file atomically."""
    pending = []
    for relative in EDITS:
        target = root / relative
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"Expected regular Hermes source: {relative}")
        before = target.read_text(encoding="utf-8")
        after = patch_source(before, relative)
        if after != before:
            pending.append((target, after))
    for target, after in pending:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), stat.S_IMODE(target.stat().st_mode))
                handle.write(after.encode("utf-8"))
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("/opt/hermes"))
    args = parser.parse_args()
    try:
        patch_tree(args.root)
    except (OSError, ValueError, SyntaxError) as exc:
        parser.exit(1, f"Hermes prompt capacity patch refused: {exc}\n")


if __name__ == "__main__":
    main()
