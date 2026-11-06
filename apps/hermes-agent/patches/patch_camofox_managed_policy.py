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
    'browser_use_cli.py': ('browser_exec_available', '    if _spartan_camofox_policy():\n        return False\n'),
    'browser_session_cdp.py': ('get_cdp_override_raw', '    if _spartan_camofox_policy():\n        return ""\n'),
    'tool_backend_helpers.py': ('read_selection', '    if section == "browser" and _spartan_camofox_policy():\n        return "camofox"\n'),
    'browser_session_cloud.py': ('get_cloud_provider',
        '    if _spartan_camofox_policy():\n        return None\n'),
}
EXEC_GUARD = ('    if _spartan_camofox_policy():\n'
              '        return \'{"success":false,"error":"Managed Camofox is required; browser_exec is disabled."}\'\n')


def patch_source(filename: str, source: str) -> str:
    name, guard = TARGETS[filename]
    source = patch_function(filename, source, name, guard)
    if filename == 'browser_use_cli.py':
        source = patch_function(filename, source, 'browser_exec', EXEC_GUARD)
    return source


def patch_function(filename, source, name, guard):
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise ValueError(f'{filename}: expected exactly one {name}')
    fn = matches[0]
    first = fn.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    line = first.end_lineno if has_doc else first.lineno - 1
    block = (f'    # {MARKER}: {name}\n'
             '    from spartan_browser_policy import camofox_policy_enabled as _spartan_camofox_policy\n' + guard)
    lines = source.splitlines(keepends=True)
    if not has_doc and line and lines[line - 1].strip() == f'# {MARKER}: {name}':
        line -= 1
    offset = sum(len(s) for s in lines[:line])
    if f'{MARKER}: {name}\n' in source:
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
    print('Spartan Camofox managed policy: selectors and browser_exec validated')


if __name__ == '__main__':
    main()
