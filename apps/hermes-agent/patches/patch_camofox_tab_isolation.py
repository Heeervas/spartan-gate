#!/usr/bin/env python3
"""Harden Hermes' Camofox tab identity and stale-tab recovery.

The upstream adapter keys its local tab cache by ``task_id`` and, when that
cache is empty, may adopt an arbitrary tab in the shared persistent context.
Registry calls also receive a durable ``session_id`` but currently discard it.

This deterministic build-time patch:

* keys Camofox registry calls by durable session_id (with task_id fallback),
* adopts only tabs whose listItemId exactly matches that durable key,
* lets explicit navigation recreate stale 404/410 tabs once,
* makes all other stale actions fail with an explicit navigation requirement,
* closes only the managed tab rather than the profile-wide context.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile


CAMOFOX_MARKER = "SPARTAN_CAMOFOX_DURABLE_TAB_SCOPE_V1"
REGISTRY_MARKER = "SPARTAN_CAMOFOX_REGISTRY_SCOPE_V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def _replace_in_function(
    source: str,
    function_name: str,
    old: str,
    new: str,
) -> str:
    start = source.find(f"def {function_name}(")
    if start < 0:
        raise RuntimeError(f"{function_name}: function anchor not found")
    end = source.find("\ndef ", start + 1)
    if end < 0:
        end = len(source)
    segment = source[start:end]
    replaced = _replace_once(segment, old, new, function_name)
    return source[:start] + replaced + source[end:]


def patch_camofox(source: str) -> str:
    if CAMOFOX_MARKER in source:
        return source

    source = _replace_once(
        source,
        "    candidates = matching_tabs or [tab for tab in tabs if isinstance(tab, dict)]\n"
        "    latest = candidates[-1] if candidates else None\n",
        "    # SPARTAN_CAMOFOX_DURABLE_TAB_SCOPE_V1: never cross-adopt a tab\n"
        "    # from another durable Hermes session sharing this profile context.\n"
        "    latest = matching_tabs[-1] if matching_tabs else None\n",
        "exact tab adoption",
    )

    helpers = '''\n\ndef _camofox_stale_action_result(\n    exc: Exception,\n    session: Optional[Dict[str, Any]],\n    action: str,\n) -> Optional[str]:\n    """Clear a stale tab generation and require explicit navigation.\n\n    Only browser_navigate may create a replacement tab. Replaying another\n    action or a cached URL is unsafe, especially for one-time OAuth URLs.\n    """\n    response = getattr(exc, "response", None)\n    status = getattr(response, "status_code", None)\n    if status not in (404, 410) or not isinstance(session, dict):\n        return None\n\n    stale_tab_id = session.get("tab_id")\n    with _sessions_lock:\n        if session.get("tab_id") == stale_tab_id:\n            session["tab_id"] = None\n    logger.warning(\n        "Camofox tab %s returned %s during %s; explicit navigation required",\n        stale_tab_id,\n        status,\n        action,\n    )\n    return json.dumps({\n        "success": False,\n        "code": "stale_browser_generation",\n        "error": (\n            "The Camofox tab no longer exists. Call browser_navigate "\n            "explicitly before retrying this action."\n        ),\n        "action": action,\n        "http_status": status,\n        "requires_navigation": True,\n    })\n'''
    source = _replace_once(
        source,
        "\n\n# ---------------------------------------------------------------------------\n"
        "# Tool implementations\n"
        "# ---------------------------------------------------------------------------\n",
        helpers
        + "\n\n# ---------------------------------------------------------------------------\n"
        "# Tool implementations\n"
        "# ---------------------------------------------------------------------------\n",
        "stale action helper insertion",
    )

    source = _replace_in_function(
        source,
        "camofox_navigate",
        "                if e.response is not None and e.response.status_code == 404:\n",
        "                if e.response is not None and e.response.status_code in (404, 410):\n",
    )
    source = _replace_in_function(
        source,
        "camofox_navigate",
        '                        "Camofox tab %s returned 404 — tab was garbage collected. "\n',
        '                        "Camofox tab %s returned 404/410 — tab generation is stale. "\n',
    )

    ordinary_actions = {
        "camofox_snapshot": "browser_snapshot",
        "camofox_click": "browser_click",
        "camofox_scroll": "browser_scroll",
        "camofox_back": "browser_back",
        "camofox_press": "browser_press",
        "camofox_get_images": "browser_get_images",
        "camofox_vision": "browser_vision",
    }
    for function_name, action in ordinary_actions.items():
        source = _replace_in_function(
            source,
            function_name,
            "    except Exception as e:\n"
            "        return tool_error(str(e), success=False)\n",
            "    except Exception as e:\n"
            f'        stale = _camofox_stale_action_result(e, locals().get("session"), "{action}")\n'
            "        if stale:\n"
            "            return stale\n"
            "        return tool_error(str(e), success=False)\n",
        )

    source = _replace_in_function(
        source,
        "camofox_type",
        "    except Exception as e:\n"
        "        from agent.display import redact_browser_typed_text_for_display\n\n"
        "        return tool_error(redact_browser_typed_text_for_display(str(e), text), success=False)\n",
        "    except Exception as e:\n"
        '        stale = _camofox_stale_action_result(e, locals().get("session"), "browser_type")\n'
        "        if stale:\n"
        "            return stale\n"
        "        from agent.display import redact_browser_typed_text_for_display\n\n"
        "        return tool_error(redact_browser_typed_text_for_display(str(e), text), success=False)\n",
    )

    source = _replace_in_function(
        source,
        "camofox_close",
        "        _delete(\n"
        "            f\"/sessions/{session['user_id']}\",\n"
        "        )\n",
        "        if session.get(\"managed\") and session.get(\"tab_id\"):\n"
        "            _delete(\n"
        "                f\"/tabs/{session['tab_id']}\",\n"
        "                {\"userId\": session[\"user_id\"]},\n"
        "            )\n"
        "        elif not session.get(\"managed\"):\n"
        "            _delete(f\"/sessions/{session['user_id']}\")\n",
    )
    return source


def patch_browser_tool(source: str) -> str:
    if REGISTRY_MARKER in source:
        return source

    source = _replace_in_function(
        source,
        "_camofox_eval",
        "    except Exception as e:\n"
        "        error_msg = str(e)\n",
        "    except Exception as e:\n"
        "        from tools.browser_camofox import _camofox_stale_action_result\n"
        '        stale = _camofox_stale_action_result(e, locals().get("tab_info"), "browser_console")\n'
        "        if stale:\n"
        "            return stale\n"
        "        error_msg = str(e)\n",
    )

    registry_anchor = (
        "_BROWSER_SCHEMA_MAP = {s[\"name\"]: s for s in BROWSER_TOOL_SCHEMAS}\n\n"
        "registry.register(\n"
    )
    registry_helper = '''_BROWSER_SCHEMA_MAP = {s["name"]: s for s in BROWSER_TOOL_SCHEMAS}\n\n\ndef _spartan_registry_browser_scope(kwargs: Dict[str, Any]) -> Optional[str]:\n    """Use the durable Hermes session only for Camofox tab grouping."""\n    # SPARTAN_CAMOFOX_REGISTRY_SCOPE_V1\n    if _is_camofox_mode():\n        return kwargs.get("session_id") or kwargs.get("task_id")\n    return kwargs.get("task_id")\n\n\nregistry.register(\n'''
    source = _replace_once(source, registry_anchor, registry_helper, "registry scope helper")

    registry_start = source.find("# Registry\n")
    if registry_start < 0:
        raise RuntimeError("registry section not found")
    head, tail = source[:registry_start], source[registry_start:]
    old = 'task_id=kw.get("task_id")'
    count = tail.count(old)
    if count != 10:
        raise RuntimeError(f"registry handlers: expected 10 task_id anchors, found {count}")
    tail = tail.replace(old, "task_id=_spartan_registry_browser_scope(kw)")
    return head + tail


def _write_pair_atomic(updates: list[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for target, content in updates:
            fd, raw_tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            tmp = Path(raw_tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((target, tmp))
        for target, tmp in staged:
            os.replace(tmp, target)
    finally:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--camofox-path",
        type=Path,
        default=Path("/opt/hermes/tools/browser_camofox.py"),
    )
    parser.add_argument(
        "--browser-tool-path",
        type=Path,
        default=Path("/opt/hermes/tools/browser_tool.py"),
    )
    args = parser.parse_args()

    original_camofox = args.camofox_path.read_text(encoding="utf-8")
    original_browser_tool = args.browser_tool_path.read_text(encoding="utf-8")
    patched_camofox = patch_camofox(original_camofox)
    patched_browser_tool = patch_browser_tool(original_browser_tool)

    # Finish every transformation and validation before the first write.
    compile(patched_camofox, str(args.camofox_path), "exec")
    compile(patched_browser_tool, str(args.browser_tool_path), "exec")
    _write_pair_atomic([
        (args.camofox_path, patched_camofox),
        (args.browser_tool_path, patched_browser_tool),
    ])
    print("Patched Hermes Camofox durable tab scope and stale-tab handling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
