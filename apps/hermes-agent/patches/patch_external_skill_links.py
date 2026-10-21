"""Preserve canonical external skill links during native bundled sync."""
from pathlib import Path
import sys

TARGET = Path("/opt/hermes/tools/skills_sync.py")
REPLACEMENTS = (
    (
        '''def _build_external_skill_index() -> Set[str]:
    """Names (directory and frontmatter) of every skill provided by external_dirs,
    so sync_skills never shadows an externally-delegated skill."""
    from agent.skill_utils import get_external_skills_dirs, _external_dirs_cache_clear
    _external_dirs_cache_clear()  # so a config edit (or a test patch) is seen
    external_names: Set[str] = set()
    for ext_dir in get_external_skills_dirs():
        for skill_md in _iter_skill_mds(ext_dir):
            external_names.update({skill_md.parent.name, _read_skill_name(skill_md, "")})
    external_names.discard("")
    return external_names
''',
        '''def _build_external_skill_index() -> Dict[str, Set[Path]]:
    """Package paths by name (directory and frontmatter) from external_dirs,
    so sync_skills never shadows an externally-delegated skill."""
    from agent.skill_utils import get_external_skills_dirs, _external_dirs_cache_clear
    _external_dirs_cache_clear()  # so a config edit (or a test patch) is seen
    external_names: Dict[str, Set[Path]] = {}
    for ext_dir in get_external_skills_dirs():
        for skill_md in _iter_skill_mds(ext_dir):
            for name in {skill_md.parent.name, _read_skill_name(skill_md, "")} - {""}:
                external_names.setdefault(name, set()).add(skill_md.parent.resolve())
    return external_names
''',
    ),
    (
        '''def _defer_to_external(st: _SyncState, skill_name: str, dest: Path, bundled_hash: str) -> None:''',
        '''def _defer_to_external(st: _SyncState, skill_name: str, dest: Path, bundled_hash: str,
                       external_paths: Set[Path]) -> None:''',
    ),
    (
        '''    if dest.exists() and _dir_hash(dest) == bundled_hash:
        _rmtree_writable(dest)''',
        '''    # Spartan Gate: preserve only the discovered external package for this name.
    if dest.is_symlink() and dest.resolve() in external_paths:
        return
    if dest.exists() and _dir_hash(dest) == bundled_hash:
        _rmtree_writable(dest)''',
    ),
    (
        '''            _defer_to_external(st, skill_name, dest, bundled_hash)''',
        '''            _defer_to_external(st, skill_name, dest, bundled_hash, external_index[skill_name])''',
    ),
)

def patch_text(source: str) -> str:
    """Accept precisely the supported upstream or complete repaired shape."""
    repaired = REPLACEMENTS[1][1] in source
    for original, replacement in REPLACEMENTS:
        expected = replacement if repaired else original
        if source.count(expected) != 1:
            raise ValueError("external skill sync anchors changed or repair is incomplete")
    result = source
    if not repaired:
        for original, replacement in REPLACEMENTS:
            result = result.replace(original, replacement, 1)
    compile(result, str(TARGET), "exec")
    return result


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else TARGET
    source = target.read_text(encoding="utf-8")
    result = patch_text(source)
    if result != source:
        target.write_text(result, encoding="utf-8")
    print("OK: native sync preserves matching external package links")


if __name__ == "__main__":
    main()
