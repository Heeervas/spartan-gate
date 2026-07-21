import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
PATCH_PATH = ROOT / "patches" / "patch_camofox_tab_isolation.py"


def load_patch_module():
    spec = importlib.util.spec_from_file_location("patch_camofox_tab_isolation", PATCH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def camofox_fixture() -> str:
    ordinary = []
    for name in (
        "camofox_snapshot",
        "camofox_click",
        "camofox_scroll",
        "camofox_back",
        "camofox_press",
        "camofox_get_images",
        "camofox_vision",
    ):
        ordinary.append(
            f'''def {name}():
    try:
        session = {{"tab_id": "tab"}}
        call()
    except Exception as e:
        return tool_error(str(e), success=False)

'''
        )
    return '''import json
import threading
from typing import Any, Dict, Optional

_sessions_lock = threading.Lock()

def _adopt_existing_tab(session):
    tabs = []
    session_key = session.get("session_key")
    matching_tabs = [
        tab
        for tab in tabs
        if isinstance(tab, dict) and tab.get("listItemId") == session_key
    ]
    candidates = matching_tabs or [tab for tab in tabs if isinstance(tab, dict)]
    latest = candidates[-1] if candidates else None
    return latest

def _delete(path: str, body: dict = None, timeout: Optional[int] = None) -> dict:
    return {}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def camofox_navigate():
    try:
        try:
            call()
        except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    logger.warning(
                        "Camofox tab %s returned 404 — tab was garbage collected. "
                    "Creating a fresh tab.",
                    session["tab_id"],
                )
                else:
                    raise
    except Exception:
        pass

''' + "".join(ordinary) + '''def camofox_type():
    try:
        session = {"tab_id": "tab"}
        call()
    except Exception as e:
        from agent.display import redact_browser_typed_text_for_display

        return tool_error(redact_browser_typed_text_for_display(str(e), text), success=False)

def camofox_close():
    try:
        session = _drop_session(None)
        _delete(
            f"/sessions/{session['user_id']}",
        )
        return "ok"
    except Exception:
        return "closed"
'''


def browser_tool_fixture() -> str:
    registrations = []
    for index in range(10):
        registrations.append(
            f'''registry.register(
    name="browser_{index}",
    handler=lambda args, **kw: browser_call(task_id=kw.get("task_id")),
)
'''
        )
    return '''from typing import Any, Dict, Optional

def _camofox_eval():
    try:
        tab_info = {}
        call()
    except Exception as e:
        error_msg = str(e)
        return error_msg

# Registry
from tools.registry import registry, tool_error

_BROWSER_SCHEMA_MAP = {s["name"]: s for s in BROWSER_TOOL_SCHEMAS}

''' + "".join(registrations)


class CamofoxTabIsolationPatchTests(unittest.TestCase):
    def setUp(self):
        self.patch = load_patch_module()

    def test_patch_is_idempotent_and_enforces_durable_scope(self):
        camofox = self.patch.patch_camofox(camofox_fixture())
        browser_tool = self.patch.patch_browser_tool(browser_tool_fixture())

        self.assertNotIn("candidates = matching_tabs or", camofox)
        self.assertIn("latest = matching_tabs[-1] if matching_tabs else None", camofox)
        self.assertIn("status_code in (404, 410)", camofox)
        self.assertEqual(camofox.count("_camofox_stale_action_result(e,"), 8)
        self.assertIn('"code": "stale_browser_generation"', camofox)
        self.assertIn('f"/tabs/{session[\'tab_id\']}"', camofox)
        self.assertIn("elif not session.get(\"managed\")", camofox)

        self.assertIn('return kwargs.get("session_id") or kwargs.get("task_id")', browser_tool)
        self.assertEqual(browser_tool.count("task_id=_spartan_registry_browser_scope(kw)"), 10)
        self.assertIn('"browser_console")', browser_tool)

        self.assertEqual(self.patch.patch_camofox(camofox), camofox)
        self.assertEqual(self.patch.patch_browser_tool(browser_tool), browser_tool)
        compile(camofox, "browser_camofox.py", "exec")
        compile(browser_tool, "browser_tool.py", "exec")

    def test_transform_failure_does_not_write_either_file(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            camofox_path = tmp / "browser_camofox.py"
            browser_tool_path = tmp / "browser_tool.py"
            original = camofox_fixture()
            camofox_path.write_text(original, encoding="utf-8")
            browser_tool_path.write_text("# drifted upstream\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(PATCH_PATH),
                    "--camofox-path",
                    str(camofox_path),
                    "--browser-tool-path",
                    str(browser_tool_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(camofox_path.read_text(encoding="utf-8"), original)
            self.assertEqual(
                browser_tool_path.read_text(encoding="utf-8"),
                "# drifted upstream\n",
            )


if __name__ == "__main__":
    unittest.main()
