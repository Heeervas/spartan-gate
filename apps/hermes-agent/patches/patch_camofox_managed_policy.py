#!/usr/bin/env python3
"""Enforce the administrator browser policy before upstream fallback paths."""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import stat
import tempfile


MARKER = 'SPARTAN_CAMOFOX_MANAGED_POLICY_V1'
TARGETS = {
    'browser_camofox.py': ('is_camofox_mode', '    if _spartan_camofox_policy():\n        return True\n'),
    'browser_use_cli.py': ('is_browser_use_cli_mode', '    if _spartan_camofox_policy():\n        return False\n'),
    'browser_tool_cdp.py': ('_get_cdp_override_raw', '    if _spartan_camofox_policy():\n        return ""\n'),
    'tool_backend_helpers.py': ('read_selection', '    if section == "browser" and _spartan_camofox_policy():\n        return "camofox"\n'),
    'browser_tool_cloud.py': ('_resolve_cloud_provider_uncached',
        '    if _spartan_camofox_policy():\n'
        '        _bt = _origin()\n'
        '        _bt._cached_cloud_provider = None\n'
        '        _bt._cloud_provider_resolved = True\n'
        '        return None\n'),
}


def patch_source(filename: str, source: str) -> str:
    name, guard = TARGETS[filename]
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise ValueError(f'{filename}: expected exactly one {name}')
    fn = matches[0]
    first = fn.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    line = first.end_lineno if has_doc else first.lineno - 1
    block = (f'    # {MARKER}\n'
             '    from spartan_browser_policy import camofox_policy_enabled as _spartan_camofox_policy\n' + guard)
    lines = source.splitlines(keepends=True)
    offset = sum(len(s) for s in lines[:line])
    if MARKER in source:
        if source[offset:offset + len(block)] != block:
            raise ValueError(f'{filename}: policy marker has incompatible content')
        return source
    result = source[:offset] + block + source[offset:]
    compile(result, filename, 'exec')
    return result


def patch_tree(root: Path) -> None:
    prepared = []
    # Compile and validate EVERY transformation before the first write.
    for filename in TARGETS:
        path = root / 'tools' / filename
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f'{filename}: target must be a regular file')
        source = path.read_text()
        prepared.append((path, source, patch_source(filename, source), st))
    staged = []
    try:
        for path, source, result, st in prepared:
            if result == source:
                continue
            fd, raw = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
            tmp = Path(raw)
            staged.append((tmp, path))
            with os.fdopen(fd, 'w') as stream:
                stream.write(result)
            os.chmod(tmp, stat.S_IMODE(st.st_mode))
            if os.geteuid() == 0:
                os.chown(tmp, st.st_uid, st.st_gid)
        for tmp, path in staged:
            os.replace(tmp, path)
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    args = parser.parse_args()
    patch_tree(args.root)
    print('Spartan Camofox managed policy: five selectors validated')


if __name__ == '__main__':
    main()
