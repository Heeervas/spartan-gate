#!/usr/bin/env python3
"""Adapt native vault plumbing to the pinned Camofox session, preserving native flows."""
import argparse
import ast
from pathlib import Path
import os
import stat
import tempfile

MARKER = 'SPARTAN_CAMOFOX_VAULT_V1'


def _insert_function(source, name, block, decorator=False):
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise ValueError(f'expected exactly one {name}')
    fn = matches[0]
    if decorator:
        line = fn.lineno - 1
    else:
        first = fn.body[0]
        doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
        line = first.end_lineno if doc else first.lineno - 1
    lines = source.splitlines(keepends=True)
    lines.insert(line, block)
    return ''.join(lines)


def patch_vault(source):
    if MARKER in source:
        compile(source, '<vault>', 'exec')
        return source
    source = source.replace('import json\n', f'import json\n# {MARKER}\nimport spartan_camofox_vault as _spartan_vault\n', 1)
    for name in ('browser_vault_fill', 'browser_vault_save_login', 'browser_vault_enter_code'):
        source = _insert_function(source, name, '@_spartan_vault.page_operation\n', decorator=True)
    guards = {
        '_check_vault_available': '    if _spartan_vault.active():\n        return True\n',
        '_eval_js': '    if _spartan_vault.active():\n        return _spartan_vault.evaluate(task_id, expression)\n',
        '_eval_js_secret': '    if _spartan_vault.active():\n        return _spartan_vault.evaluate(task_id, expression, secret=True)\n',
        '_focus_bound_origin': '    if _spartan_vault.active():\n        return _spartan_vault.focus_current(task_id, origin)\n',
        '_fenced_page_op': '    if _spartan_vault.active():\n        return fn()\n',
    }
    for name, block in guards.items():
        source = _insert_function(source, name, block)
    old = '    tid = kwargs.get("task_id")\n'
    if source.count(old) != 3:
        raise ValueError('vault handlers changed')
    source = source.replace(old, '    tid = (kwargs.get("session_id") or kwargs.get("task_id")) if _spartan_vault.active() else kwargs.get("task_id")\n')
    old = '    identifier = str(answer["identifier"]).strip()\n'
    if source.count(old) != 1:
        raise ValueError('save-login prompt boundary changed')
    source = source.replace(old, '    _spartan_vault.validate_pin(effective_task_id)\n' + old)
    old = '    if backend is not None:\n        try:\n            code = backend.resolve_otp(handle)'
    new = ('    if backend is not None:\n'
           '        if _spartan_vault.active() and not _spartan_vault.otp_origin_allowed(backend, handle, origin):\n'
           '            return json.dumps({"success": False, "error_type": "origin_mismatch", "error": "OTP origin does not match the saved login."})\n'
           '        try:\n            code = backend.resolve_otp(handle)')
    if source.count(old) != 1:
        raise ValueError('OTP backend boundary changed')
    source = source.replace(old, new)
    old = 'return json.dumps({"success": False, "error": str(result.get("error") or "fill failed")[:200]})'
    if source.count(old) != 1:
        raise ValueError('OTP error boundary changed')
    source = source.replace(old, 'return json.dumps({"success": False, "error_type": result.get("error_type", "eval_failed"), "error": str(result.get("error") or "fill failed")[:200]})')
    compile(source, '<vault>', 'exec')
    return source


def patch_camofox(source):
    if MARKER in source:
        compile(source, '<camofox>', 'exec')
        return source
    old = '    data = _snapshot_data(session)\n'
    if source.count(old) != 1:
        raise ValueError('Camofox snapshot boundary changed')
    source = source.replace(old, f'    # {MARKER}\n    from tools.browser_tool_snapshot import _redact_browser_output\n    data = _redact_browser_output(_snapshot_data(session))\n')
    old = '        return body(session)\n'
    if source.count(old) != 1:
        raise ValueError('Camofox text result boundary changed')
    source = source.replace(old, '        from tools.browser_tool_snapshot import _redact_browser_output\n        return _redact_browser_output(body(session))\n')
    compile(source, '<camofox>', 'exec')
    return source


def patch_tree(root):
    prepared = []
    for name, transform in [('browser_vault_tool.py', patch_vault), ('browser_camofox.py', patch_camofox)]:
        path = root / 'tools' / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('patch target must be regular')
        source = path.read_text()
        prepared.append((path, source, transform(source), info))
    staged = []
    try:
        for path, source, result, info in prepared:
            if source == result:
                continue
            fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
            staged.append((Path(temp), path))
            with os.fdopen(fd, 'w') as stream:
                stream.write(result)
            os.chmod(temp, stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.chown(temp, info.st_uid, info.st_gid)
        for temp, path in staged:
            os.replace(temp, path)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    patch_tree(parser.parse_args().root)


if __name__ == '__main__':
    main()
