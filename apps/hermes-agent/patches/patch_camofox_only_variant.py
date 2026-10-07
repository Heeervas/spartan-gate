#!/usr/bin/env python3
"""Add marker-gated PM/browser guards before worker staging; no lock/fact changes."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

MARKER = 'SPARTAN_CAMOFOX_ONLY_VARIANT_V1'
# Function guards run after docstrings and before any original side effects.
GUARDS = {
    'pm/client.py': {
        'ensure': 'require_tools([name])',
        'stage_only': 'require_tools([name])',
        'prepare_tools': 'require_tools(names)',
        'stage_tools': 'require_tools(names)',
    },
    'pm/install.py': {
        'ensure': 'require_tools([name])',
        'stage_only': 'require_tools([name])',
        '_install': 'require_tools([package.name])',
        '_installed_location': 'if not supported([package.name]):\n    return None',
    },
    'pm/build_operations.py': {
        'prepare_tools': 'require_tools(names)',
        'stage_tools': 'require_tools(names)',
        'verified_tools': 'require_tools(names)',
    },
    'tools/browser_tool.py': {
        '_capture_vision_screenshot': '_spartan_native_hint = "a missing Chromium install (\'agent-browser install\'), "',
    },
    'tools/browser_tool_install.py': {
        '_find_agent_browser': "if camofox_only():\n    raise FileNotFoundError(ERROR)",
        '_browser_install_hint': 'if camofox_only():\n    return ERROR',
        '_chromium_installed': 'if camofox_only():\n    return False',
        '_maybe_autoinstall_chromium': 'if camofox_only():\n    return False',
    },
    'hermes_cli/browser_runtime.py': {
        'chromium_executable': 'if camofox_only():\n    return None',
    },
}
MEET_GUARDS = {
    'plugins/google_meet/meet_bot.py': {
        '_meet_camofox_mode_enabled': 'if require_managed_camofox():\n    return True',
    },
    'plugins/google_meet/cli.py': {
        '_meet_camofox_mode_enabled': 'if require_managed_camofox():\n    return True',
        '_cmd_install': "if camofox_only():\n    print(ERROR)\n    return 2",
        '_cmd_auth': "if camofox_only():\n    print(ERROR)\n    return 2",
    },
}
IMPORT = 'from pm.spartan_browser_variant import require_tools, supported, camofox_only, require_managed_camofox, ERROR'


def insert_guard(source: str, name: str, guard: str) -> str:
    matches = [node for node in ast.parse(source).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {name}')
    body = matches[0].body
    doc = isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)
    line = body[0].end_lineno if doc else body[0].lineno - 1
    lines = source.splitlines(keepends=True)
    if not doc and line and lines[line - 1].strip() == f'# {MARKER}: {name}':
        line -= 1
    offset = sum(map(len, lines[:line]))
    block = f'    # {MARKER}: {name}\n    {IMPORT}\n' + ''.join('    ' + text + '\n' for text in guard.splitlines())
    if f'{MARKER}: {name}' in source:
        if source[offset:offset + len(block)] != block:
            raise ValueError(f'Incompatible variant guard: {name}')
        return source
    return source[:offset] + block + source[offset:]


def patch_source(name: str, source: str, *, meet: bool = False) -> str:
    guards = (MEET_GUARDS if meet else GUARDS)[name]
    for function, guard in guards.items():
        source = insert_guard(source, function, guard)
    if name == 'tools/browser_tool.py':
        old = '''f"a missing Chromium install ('agent-browser install'), "'''
        new = '''f"{ERROR if camofox_only() else _spartan_native_hint}"'''
        if source.count(old) == 1:
            source = source.replace(old, new)
        elif source.count(new) != 1:
            raise ValueError('Missing browser screenshot diagnostic hint')
    if name == 'pm/client.py':
        old = '    for name in tool_roots(Lockfile(paths.lockfile_path()).names()):\n        ensure(name, explicit=True)'
        new = '    for name in tool_roots(Lockfile(paths.lockfile_path()).names()):\n        from pm.spartan_browser_variant import supported\n        if not supported([name]):\n            continue\n        ensure(name, explicit=True)'
        if old in source:
            source = source.replace(old, new, 1)
        elif new not in source:
            raise ValueError('Missing tool sync loop')
    if name == 'pm/install.py':
        old = '    for name in lockfile.names():\n'
        new = '    for name in lockfile.names():\n        from pm.spartan_browser_variant import supported\n        if not supported([name]):\n            continue\n'
        # Both drift and PATH activation must ignore absent native browser pins.
        if source.count(new) == 0:
            if source.count(old) != 2:
                raise ValueError('Missing drift/activation loops')
            source = source.replace(old, new)
        elif source.count(new) != 2:
            raise ValueError('Partial variant drift/activation guards')
    compile(source, name, 'exec')
    return source


def patch_tree(root: Path, *, helper: Path | None = None, meet_only: bool = False, pm_only: bool = False) -> None:
    helper = helper or Path(__file__).resolve().parents[1] / 'spartan_browser_variant.py'
    helper_bytes = helper.read_bytes()
    compile(helper_bytes, str(helper), 'exec')
    changes = {}
    targets = {**GUARDS, **MEET_GUARDS} if meet_only else {name: guards for name, guards in GUARDS.items() if not pm_only or name.startswith("pm/")}
    for name in targets:
        path = root / name
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f'{name}: target must be regular')
        changes[name] = patch_source(name, path.read_text(), meet=name in MEET_GUARDS).encode()
    changes['pm/spartan_browser_variant.py'] = helper_bytes
    if meet_only:
        for name in ('spartan_browser_policy.py', 'bootstrap/spartan_browser_policy.py',
                     'spartan_browser_variant.py', 'bootstrap/spartan_browser_variant.py'):
            path = root / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f'{name}: support must be a regular file')
            changes[name] = path.read_bytes()
    manifest_path = root / 'pm/spartan-browser-variant.json'
    previous = {}
    previous.update({name: hashlib.sha256(data).hexdigest() for name, data in changes.items()})
    changes['pm/spartan-browser-variant.json'] = (json.dumps({'schema': 1, 'files': previous}, sort_keys=True, indent=2) + '\n').encode()
    # All reads/transforms compile before the first write; preserve installed metadata.
    for name in changes:
        if (root / name).is_symlink():
            raise ValueError(f'{name}: symlinks are forbidden')
    staged = []
    try:
        for name, data in changes.items():
            path = root / name
            if path.exists() and path.read_bytes() == data:
                continue
            if path.is_symlink():
                raise ValueError(f'{name}: symlinks are forbidden')
            metadata = path.stat() if path.exists() else None
            fd, temporary = tempfile.mkstemp(prefix='.variant-', dir=path.parent)
            staged.append((Path(temporary), path))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.chmod(temporary, stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
            if metadata and os.geteuid() == 0:
                os.chown(temporary, metadata.st_uid, metadata.st_gid)
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path('/opt/hermes'))
    parser.add_argument('--helper', type=Path)
    parser.add_argument('--pm-only', action='store_true', help='early upstream layer contains only PM source')
    parser.add_argument('--meet-only', action='store_true', help='after existing Meet adaptation, finalize optional plugin guards')
    args = parser.parse_args()
    patch_tree(args.root, helper=args.helper, meet_only=args.meet_only, pm_only=args.pm_only)


if __name__ == '__main__':
    main()
