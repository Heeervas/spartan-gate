#!/usr/bin/env python3
"""Adapt the catalog-pinned official Camofox plugin, not deleted browser core.

Upstream owns strict tab adoption, navigation recovery, origin/OTP checks and
redaction. Gate retains durable routing, non-replayed stale actions, tab-only
closure, the viewport endpoint and pinned protected vault transport.
"""
import argparse
import ast
import os
from pathlib import Path
import stat
import tempfile

MARKER = 'SPARTAN_OFFICIAL_CAMOFOX_V1'


def replace_once(source, old, new):
    if source.count(new) == 1:
        return source
    if source.count(old) != 1:
        raise ValueError('official Camofox anchor changed or partially patched')
    return source.replace(old, new, 1)


def replace_function(source, name, replacement):
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(functions) != 1:
        raise ValueError(f'expected exactly one {name}')
    node = functions[0]
    lines = source.splitlines(keepends=True)
    current = ''.join(lines[node.lineno - 1:node.end_lineno])
    if MARKER in current and current != replacement:
        raise ValueError(f'incompatible {name} adaptation')
    lines[node.lineno - 1:node.end_lineno] = [replacement]
    return ''.join(lines)


STALE_ACTION = '''def _run_on_tab(session, body, *, read_only=False):
    # SPARTAN_OFFICIAL_CAMOFOX_V1: only explicit navigation replaces a dead tab.
    tracked = session["tab_id"]
    try:
        return body(session)
    except requests.HTTPError as exc:
        if not _tab_gone(exc):
            raise
        with client._sessions_lock:
            if session.get("tab_id") == tracked:
                session["tab_id"] = None
                session["adopt_existing_tab"] = False
        return _dumps({"success": False, "code": "stale_browser_generation",
            "error": _TAB_GONE_ERROR, "requires_navigation": True,
            "http_status": exc.response.status_code})
'''
CLOSE = '''def close_session(task_id=None):
    # SPARTAN_OFFICIAL_CAMOFOX_V1: never delete a shared managed profile.
    session = drop_session(task_id)
    if not session:
        return None
    try:
        if session.get("managed"):
            if session.get("tab_id"):
                delete(f"/tabs/{session['tab_id']}", user_params(session))
        else:
            delete(f"/sessions/{session['user_id']}")
    except Exception as exc:
        return str(exc)
    return None
'''
BACKEND = '''def make_page_backend():
    # SPARTAN_OFFICIAL_CAMOFOX_V1: native page API; Gate's existing secret transport.
    from spartan_camofox_vault import active, make_page_backend as managed_backend
    return managed_backend(client) if active() else _upstream_make_page_backend()
'''
VIEWPORT = '''

# SPARTAN_OFFICIAL_CAMOFOX_V1: the existing tab's real viewport, without relaunch.
SPARTAN_VIEWPORT_SCHEMA = {"name": "browser_viewport",
    "description": "Resize the current Camofox tab's real CSS viewport. Navigate first. Never restarts the browser; inspect success and effective dimensions before capturing.",
    "parameters": {"type": "object", "properties": {
        "width": {"type": "integer", "minimum": 100, "maximum": 4096},
        "height": {"type": "integer", "minimum": 100, "maximum": 4096}},
        "required": ["width", "height"], "additionalProperties": False}}


def browser_viewport(width, height, task_id=None):
    if any(type(n) is not int or not 100 <= n <= 4096 for n in (width, height)):
        return _fail("Width and height must be integers from 100 to 4096.")
    if not task_id:
        return _fail("An active browser session is required.")
    def resize(session):
        result = client.post(client.tab_path(session, "spartan/viewport"), {
            "userId": session["user_id"], "width": width, "height": height, "relaunch": False})
        fields = ("innerWidth", "innerHeight", "visualViewportWidth", "visualViewportHeight",
                  "devicePixelRatio", "maxWidth767", "maxWidth430")
        effective = {key: result.get("effective", {}).get(key) for key in fields}
        matches = all(type(effective[k]) in (int, float) and abs(effective[k] - n) <= 2
                      for k, n in (("innerWidth", width), ("innerHeight", height)))
        return _dumps({"success": result.get("ok") is True and matches,
            "requested": {"width": width, "height": height},
            "effective": effective, "strategy": "pageViewport"})
    try:
        # Never resync/adopt for this operation: an existing tracked tab only.
        with client._sessions_lock:
            session = client._sessions.get(client._session_key(task_id))
        if not session or not session.get("tab_id"):
            return _fail(client.NO_SESSION_ERROR)
        return _run_on_tab(session, resize)
    except Exception:
        return _fail("Camofox could not apply the viewport; no browser relaunch was attempted.")


def handle_viewport(args, **kw):
    return browser_viewport(args.get("width"), args.get("height"), task_id=_kw_task(kw))
'''


def patch_tools(source):
    source = replace_function(source, '_run_on_tab', STALE_ACTION)
    source = replace_once(source, '    return kw.get("task_id")\n',
                          '    return kw.get("session_id") or kw.get("task_id")\n')
    if VIEWPORT not in source:
        if 'def browser_viewport(' in source:
            raise ValueError('incompatible viewport adaptation')
        source += VIEWPORT
    return source


def patch_manifest(source):
    # This image dependency restores a formerly built-in backend. Native explicit
    # disables still win before bundled-backend loading; user plugins stay opt-in.
    source = replace_once(source, 'kind: standalone\n', 'kind: backend\n')
    return replace_once(source, '  - browser_console\n',
                         '  - browser_console\n  - browser_viewport\n')


def patch_entry(source):
    source = replace_once(source, '        (s.BROWSER_CONSOLE, t.handle_console, "🖥️"),\n',
                          '        (s.BROWSER_CONSOLE, t.handle_console, "🖥️"),\n'
                          '        (t.SPARTAN_VIEWPORT_SCHEMA, t.handle_viewport, "📐"),\n')
    return replace_once(source, '            t.cleanup_task(task_id or session_id)\n',
                         '            t.cleanup_task(session_id or task_id)\n')


def patch_page_backend(source):
    if BACKEND in source:
        return source
    if MARKER in source:
        raise ValueError('incompatible Camofox page backend')
    source = replace_once(source, 'def make_page_backend():', 'def _upstream_make_page_backend():')
    return source + '\n\n' + BACKEND


def patch_native_vault(source):
    """Keep native origin/OTP/redaction; pin complete operations around prompts."""
    source = replace_once(source, 'import json\n',
                          'import json\nimport spartan_camofox_vault as _spartan_vault\n')
    for name in ('browser_vault_fill', 'browser_vault_save_login', 'browser_vault_enter_code'):
        definition = f'def {name}('
        source = replace_once(source, definition, '@_spartan_vault.page_operation\n' + definition)
    source = replace_once(source, '    tid = kwargs.get("task_id")\n',
        '    tid = (kwargs.get("session_id") or kwargs.get("task_id")) if _spartan_vault.active() else kwargs.get("task_id")\n')
    source = replace_once(source, '    identifier = str(answer["identifier"]).strip()\n',
        '    _spartan_vault.validate_pin(effective_task_id)\n'
        '    identifier = str(answer["identifier"]).strip()\n')
    # This managed REST browser is not a local Bot Desktop/CDP page. Do not
    # manufacture a different core session to fence it; its operation pin owns it.
    anchor = '    from tools.browser_session import run_fenced, session_for_task\n'
    return replace_once(source, anchor,
        '    if _spartan_vault.active():\n        return fn()\n' + anchor)


def patch_migration(source):
    anchor = 'def plugin_present(plugin: str, home: Path) -> bool:\n'
    start = source.index(anchor)
    first = ast.parse(source).body
    node = next(n for n in first if isinstance(n, ast.FunctionDef) and n.name == 'plugin_present')
    body = node.body[0]
    line = body.end_lineno if isinstance(body, ast.Expr) and isinstance(body.value, ast.Constant) else body.lineno - 1
    block = ('    # SPARTAN_OFFICIAL_CAMOFOX_V1: only this managed bundled dependency.\n'
             '    if plugin == "camofox" and (Path(__file__).resolve().parents[1] / "plugins/camofox/plugin.yaml").is_file():\n'
             '        return True\n')
    if block in source[start:]:
        return source
    lines = source.splitlines(keepends=True)
    lines.insert(line, block)
    return ''.join(lines)


def patch_tree(root):
    transforms = {
        'plugins/camofox/plugin.yaml': patch_manifest,
        'plugins/camofox/__init__.py': patch_entry,
        'plugins/camofox/camofox_tools.py': patch_tools,
        'plugins/camofox/camofox_client.py': lambda s: replace_function(s, 'close_session', CLOSE),
        'plugins/camofox/camofox_vault.py': patch_page_backend,
        'tools/browser_vault_tool.py': patch_native_vault,
        'hermes_cli/left_core_migration.py': patch_migration,
    }
    prepared = []
    for name, transform in transforms.items():
        path = root / name
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f'{name}: target must be regular')
        original = path.read_text()
        result = transform(original)
        if path.suffix == '.py':
            compile(result, name, 'exec')
        else:
            import yaml
            if yaml.safe_load(result).get('kind') != 'backend':
                raise ValueError('invalid bundled Camofox manifest')
        prepared.append((path, original, result, metadata))
    staged = []
    try:
        for path, original, result, metadata in prepared:
            if result == original:
                continue
            fd, temporary = tempfile.mkstemp(prefix='.camofox-', dir=path.parent)
            staged.append((Path(temporary), path))
            with os.fdopen(fd, 'w') as stream:
                stream.write(result)
                os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
                if os.geteuid() == 0:
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    patch_tree(parser.parse_args().root)


if __name__ == '__main__':
    main()
