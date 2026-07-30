from __future__ import annotations

import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("patches").joinpath(
    "patch_workstation_approval_scope.py"
)


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "patch_workstation_approval_scope", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def upstream_fixture() -> str:
    return r'''
import contextvars
import re
import threading
import time

_lock = threading.Lock()
_gateway_notify_cbs = {"session": object()}
approved = set()
permanent_commands = {"dangerous --global"}
captured = {}

def env_var_enabled(name):
    return False

def _is_gateway_approval_context():
    return True

def _is_interactive_cli():
    return False

def get_current_session_key(default="default"):
    return "session"

def _fire_approval_hook(hook_name, **kwargs):
    captured["hook"] = (hook_name, dict(kwargs))

def is_approved(session_key, pattern_key):
    return pattern_key in approved

def approve_session(session_key, pattern_key):
    approved.add(pattern_key)

def approve_permanent(pattern_key):
    approved.add(pattern_key)

def _command_matches_permanent_allowlist(command):
    return command in permanent_commands

def _await_gateway_decision(session_key, notify_cb, approval_data, **kwargs):
    captured["approval_data"] = dict(approval_data)
    return {"resolved": True, "choice": "always"}

def submit_pending(session_key, data):
    captured["pending"] = dict(data)

def prompt_dangerous_approval(command, description, *args, **kwargs):
    captured["description"] = description
    return "once"

def request_tool_approval(tool_name, reason, **kwargs):
    return {"approved": True}

def check_all_command_guards(command, env_type, approval_callback=None, has_host_access=False):
    if _command_matches_permanent_allowlist(command):
        return {"approved": True, "global_command_bypass": True}
    if is_approved("session", "danger-pattern"):
        return {"approved": True, "cached": True}
    approve_session("session", "danger-pattern")
    return {"approved": True, "checked": True}
'''


class WorkstationApprovalScopePatchTests(unittest.TestCase):
    def test_patch_is_idempotent_and_fails_on_upstream_drift(self):
        patcher = load_patcher()
        source = upstream_fixture()
        patched = patcher.patch_source(source)

        self.assertIn(patcher.MARKER, patched)
        self.assertEqual(patcher.patch_source(patched), patched)
        with self.assertRaisesRegex(ValueError, "missing upstream approval anchor"):
            patcher.patch_source("def unrelated(): pass\n")

    def test_remote_scope_never_inherits_normal_command_or_pattern_approval(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)
        namespace["approved"].add("danger-pattern")

        result = namespace["check_scoped_command_guards"](
            "dangerous --global",
            "ssh",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertTrue(result["checked"])
        self.assertFalse(result.get("global_command_bypass", False))
        self.assertIn(
            "spartan_scope:workstation_hermes:danger-pattern",
            namespace["approved"],
        )

    def test_bare_and_cron_like_contexts_fail_closed_before_execution(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)
        namespace["_is_gateway_approval_context"] = lambda: False

        result = namespace["check_scoped_command_guards"](
            "printf safe",
            "ssh",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertFalse(result["approved"])
        self.assertIn("interactive owner", result["message"])

    def test_interactive_approval_is_one_time_and_non_persistable(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)

        result = namespace["request_one_time_tool_approval"](
            "workstation_terminal",
            "Interactive stdin cannot be re-guarded after launch.",
            command="python3 -i",
            workdir="/home/hermes-worker/workspace/project",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertTrue(result["approved"])
        data = namespace["captured"]["approval_data"]
        self.assertEqual(data["command"], "python3 -i")
        self.assertFalse(data["allow_session"])
        self.assertFalse(data["allow_permanent"])
        self.assertIn("Hermes Workstation", data["description"])
        self.assertIn(
            "\nWorking directory: /home/hermes-worker/workspace/project",
            data["description"],
        )
        self.assertNotIn("\\nWorking directory", data["description"])
        self.assertTrue(
            data["pattern_key"].startswith(
                "spartan_scope:workstation_hermes:one_time:"
            )
        )

    def test_registered_gateway_callback_proves_live_owner_without_platform_context(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)
        namespace["_is_gateway_approval_context"] = lambda: False

        result = namespace["request_one_time_tool_approval"](
            "workstation_transfer",
            "Transfer one bounded file.",
            command="download exchange/demo.txt -> default:demo.txt",
            workdir="/home/hermes-worker/workspace",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertTrue(result["approved"])
        self.assertIn("approval_data", namespace["captured"])

    def test_one_time_approval_without_gateway_callback_fails_closed(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)
        namespace["_gateway_notify_cbs"].clear()
        namespace["_is_gateway_approval_context"] = lambda: False

        result = namespace["request_one_time_tool_approval"](
            "workstation_transfer",
            "Transfer one bounded file.",
            command="download exchange/demo.txt -> default:demo.txt",
            workdir="/home/hermes-worker/workspace",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertFalse(result["approved"])
        self.assertIn("live gateway owner", result["message"])

    def test_one_time_approval_from_cron_fails_closed_with_live_callback(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)
        namespace["env_var_enabled"] = lambda name: name == "HERMES_CRON_SESSION"

        result = namespace["request_one_time_tool_approval"](
            "workstation_transfer",
            "Transfer one bounded file.",
            command="download exchange/demo.txt -> default:demo.txt",
            workdir="/home/hermes-worker/workspace",
            approval_scope="workstation_hermes",
            display_label="Hermes Workstation",
        )

        self.assertFalse(result["approved"])
        self.assertIn("live gateway owner", result["message"])

    def test_interactive_approval_requires_displayable_command_and_workdir(self):
        patcher = load_patcher()
        namespace = {}
        exec(patcher.patch_source(upstream_fixture()), namespace)

        for command, workdir in (
            ("", "/home/hermes-worker/workspace"),
            ("python3", ""),
            (None, "/home/hermes-worker/workspace"),
            ("python3", None),
            ("python3\0", "/home/hermes-worker/workspace"),
            ("python3", "/home/hermes-worker/workspace\nother"),
        ):
            with self.subTest(command=command, workdir=workdir):
                result = namespace["request_one_time_tool_approval"](
                    "workstation_terminal",
                    "Interactive stdin.",
                    command=command,
                    workdir=workdir,
                    approval_scope="workstation_hermes",
                    display_label="Hermes Workstation",
                )

                self.assertFalse(result["approved"])
                self.assertIn("displayable command and workdir", result["message"])

if __name__ == "__main__":
    unittest.main()
