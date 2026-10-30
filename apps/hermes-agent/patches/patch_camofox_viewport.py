#!/usr/bin/env python3
"""Add a scope-preserving Camofox-only viewport tool after tab isolation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile

MARKER = '# SPARTAN_CAMOFOX_NATIVE_VIEWPORT_V1'
ADDITION = '''

# SPARTAN_CAMOFOX_NATIVE_VIEWPORT_V1
_SPARTAN_VIEWPORT_FIELDS = (
    "innerWidth", "innerHeight", "visualViewportWidth", "visualViewportHeight",
    "devicePixelRatio", "maxWidth767", "maxWidth430",
)


def browser_viewport(width, height, task_id=None):
    """Resize only the current Camofox tab; never create a tab or relaunch."""
    from tools.browser_camofox import _require_tab, _post, _spartan_stale_tab
    if not _is_camofox_mode():
        return json.dumps({"success": False, "error": "Viewport requires managed Camofox."})
    if any(type(n) is not int or not 100 <= n <= 4096 for n in (width, height)):
        return json.dumps({"success": False, "error": "Width and height must be integers from 100 to 4096."})
    if not task_id:
        return json.dumps({"success": False, "error": "An active browser session is required."})
    session = None
    try:
        session, blocked = _require_tab(task_id, "resize the viewport")
        if blocked:
            return blocked
        result = _post(f"/tabs/{session['tab_id']}/spartan/viewport", {
            "userId": session["user_id"], "width": width, "height": height,
            "relaunch": False,
        })
        effective = {k: result.get("effective", {}).get(k) for k in _SPARTAN_VIEWPORT_FIELDS}
        matches = all(type(effective[k]) in (int, float) and abs(effective[k] - n) <= 2
                      for k, n in (("innerWidth", width), ("innerHeight", height)))
        return json.dumps({"success": result.get("ok") is True and matches,
            "requested": {"width": width, "height": height}, "effective": effective,
            "strategy": "pageViewport"})
    except Exception as exc:
        stale = _spartan_stale_tab(exc, session, "browser_viewport")
        if stale is not None:
            return stale
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return json.dumps({"success": False,
            "code": "viewport_mismatch" if status == 409 else "viewport_failed",
            "error": "Camofox could not apply the viewport; no browser relaunch was attempted."})


def _spartan_viewport_handler(args, **kw):
    return browser_viewport(args.get("width"), args.get("height"),
                            task_id=kw.get("session_id") or kw.get("task_id"))


registry.register(
    name="browser_viewport", toolset="browser", emoji="📐",
    check_fn=_is_camofox_mode, handler=_spartan_viewport_handler,
    schema={"name": "browser_viewport",
        "description": "Resize the current Camofox tab's real CSS viewport. Call browser_navigate first. Does not restart the browser. Returns effective dimensions; inspect success before capturing.",
        "parameters": {"type": "object", "properties": {
            "width": {"type": "integer", "minimum": 100, "maximum": 4096},
            "height": {"type": "integer", "minimum": 100, "maximum": 4096},
        }, "required": ["width", "height"], "additionalProperties": False}},
)
'''


def patch_source(source: str) -> str:
    if MARKER in source:
        if not source.endswith(ADDITION):
            raise ValueError('Viewport marker has incompatible content')
        return source
    if ('SPARTAN_CAMOFOX_REGISTRY_SCOPE_V1' not in source
            or '_BROWSER_TOOL_TABLE = ' not in source or 'def browser_viewport(' in source):
        raise ValueError('Expected modular browser tools with durable tab isolation')
    result = source.rstrip() + '\n' + ADDITION
    compile(result, 'browser_tool.py', 'exec')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-tool-path', type=Path,
                        default=Path('/opt/hermes/tools/browser_tool.py'))
    path = parser.parse_args().browser_tool_path
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError('Browser tool target must be a regular file')
    original = path.read_text()
    updated = patch_source(original)
    if updated == original:
        return
    fd, raw = tempfile.mkstemp(prefix='.browser-viewport-', dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(updated)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
