#!/usr/bin/env python3
"""Keep Spartan Gate's runtime overlay ahead of immutable Hermes sources."""

from __future__ import annotations

from pathlib import Path


TARGET = Path("/opt/hermes/hermes_cli/_startup_fast.py")
MARKER = "# Spartan Gate patch: preserve runtime overlay precedence"

OLD = '''def ensure_project_root_on_path() -> None:
    """Put the project root at sys.path[0], deduping realpath-equivalents."""
    project_root = project_root_str()
    normalized_root = os.path.normcase(os.path.realpath(project_root))
    sys.path[:] = [
        entry
        for entry in sys.path
        if not entry
        or os.path.normcase(os.path.realpath(entry)) != normalized_root
    ]
    sys.path.insert(0, project_root)
'''

NEW = f'''def ensure_project_root_on_path() -> None:
    """Keep a configured runtime overlay ahead of the immutable project root."""
    {MARKER}
    project_root = project_root_str()
    normalized_root = os.path.normcase(os.path.realpath(project_root))
    overlay_root = os.environ.get("PIP_TARGET", "").strip()
    normalized_overlay = (
        os.path.normcase(os.path.realpath(overlay_root))
        if overlay_root
        and os.path.normcase(os.path.realpath(overlay_root)) != normalized_root
        else None
    )
    rewritten = []
    overlay_index = None
    for entry in sys.path:
        normalized_entry = (
            os.path.normcase(os.path.realpath(entry)) if entry else None
        )
        if normalized_entry == normalized_root:
            continue
        if normalized_overlay and normalized_entry == normalized_overlay:
            if overlay_index is None:
                overlay_index = len(rewritten)
                rewritten.append(entry)
            continue
        rewritten.append(entry)
    insertion_index = 0 if overlay_index is None else overlay_index + 1
    rewritten.insert(insertion_index, project_root)
    sys.path[:] = rewritten
'''


def patch_source(source: str) -> str:
    if MARKER in source:
        if source.count(MARKER) != 1 or not any(
            block in source for block in (NEW, NEW.replace("os.path.realpath(", "_realpath_or_self("))
        ):
            raise ValueError("Hermes startup path patch marker is incomplete")
        return source
    current = '''def ensure_project_root_on_path() -> None:
    """Put the project root at sys.path[0], deduping realpath-equivalents."""
    project_root = project_root_str()
    normalized_root = os.path.normcase(_realpath_or_self(project_root))
    sys.path[:] = [entry for entry in sys.path
                   if not entry or os.path.normcase(_realpath_or_self(entry)) != normalized_root]
    sys.path.insert(0, project_root)
'''
    if source.count(current) == 1:
        # Preserve upstream's tolerance of a deleted working directory.
        return source.replace(current, NEW.replace("os.path.realpath(", "_realpath_or_self("), 1)
    if source.count(OLD) != 1:
        raise ValueError("Hermes startup path function anchor not found")
    return source.replace(OLD, NEW, 1)


def main() -> None:
    original = TARGET.read_text(encoding="utf-8")
    patched = patch_source(original)
    if patched == original:
        print(f"Already patched: {TARGET}")
        return
    TARGET.write_text(patched, encoding="utf-8")
    print(f"Patched: {TARGET} - runtime overlay retains import precedence")


if __name__ == "__main__":
    main()
