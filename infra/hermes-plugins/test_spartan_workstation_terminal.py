from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


PLUGIN_PATH = (
    pathlib.Path(__file__).parent
    / "spartan-workstation-terminal"
    / "__init__.py"
)


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_terminal", PLUGIN_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module._reset_for_tests()
    return module


class FakeContext:
    def __init__(self):
        self.tools = []
        self.hooks = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class ApprovalHarness:
    def __init__(self, *, guard_approved=True, interactive_approved=True):
        self.guard_calls = []
        self.interactive_calls = []
        self.guard_approved = guard_approved
        self.interactive_approved = interactive_approved
        self.module = types.ModuleType("tools.approval")
        self.module.get_current_session_key = lambda default="": "telegram:user:chat"
        self.module.check_scoped_command_guards = self.guard
        self.module.request_one_time_tool_approval = self.interactive

    def guard(self, *args, **kwargs):
        self.guard_calls.append((args, kwargs))
        return {
            "approved": self.guard_approved,
            "message": None if self.guard_approved else "denied by owner",
        }

    def interactive(self, *args, **kwargs):
        self.interactive_calls.append((args, kwargs))
        return {
            "approved": self.interactive_approved,
            "message": None if self.interactive_approved else "PTY denied",
        }


def module_stubs(approvals):
    tools = types.ModuleType("tools")
    tools.__path__ = []
    agent = types.ModuleType("agent")
    agent.__path__ = []
    redact = types.ModuleType("agent.redact")
    redact.redact_sensitive_text = lambda value, **_kwargs: value.replace(
        "secret", "<redacted>"
    )
    redact.redact_terminal_output = lambda value, _command: value.replace(
        "secret", "<redacted>"
    )
    return {
        "tools": tools,
        "tools.approval": approvals.module,
        "agent": agent,
        "agent.redact": redact,
    }


class SpartanWorkstationTerminalPluginTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()

    def test_registers_distinct_tools_and_reset_hook_without_override(self):
        context = FakeContext()

        self.plugin.register(context)

        self.assertEqual(
            [item["name"] for item in context.tools],
            [
                "workstation_terminal",
                "workstation_process",
                "workstation_transfer",
                "workstation_transfer_status",
            ],
        )
        self.assertTrue(all(not item.get("override", False) for item in context.tools))
        self.assertEqual(context.hooks[0][0], "on_session_reset")
        schema = self.plugin.TERMINAL_SCHEMA["parameters"]["properties"]
        for forbidden in ("host", "port", "username", "identity_file", "ssh_options"):
            self.assertNotIn(forbidden, schema)

    def test_command_uses_scoped_real_guard_and_fixed_broker_request(self):
        approvals = ApprovalHarness()
        broker_calls = []

        def broker(payload, timeout=15):
            broker_calls.append((dict(payload), timeout))
            if payload["action"] == "reserve":
                return {"reservation": "b" * 64}
            return {
                "process_id": "wproc_" + "a" * 24,
                "running": False,
                "exit_code": 0,
                "command": payload["command"],
                "output": "ok",
            }

        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_check_available", return_value=True
        ), patch.object(self.plugin, "_broker_call", side_effect=broker):
            result = json.loads(
                self.plugin._handle_terminal(
                    {
                        "command": "printf ok",
                        "workdir": "/home/hermes-worker/workspace/project",
                    }
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(approvals.guard_calls), 1)
        guard_args, guard_kwargs = approvals.guard_calls[0]
        self.assertEqual(guard_args, ("printf ok", "ssh"))
        self.assertEqual(guard_kwargs["approval_scope"], "hermes_workstation")
        self.assertEqual(guard_kwargs["display_label"], "Hermes Workstation")
        payload = broker_calls[-1][0]
        self.assertNotIn("host", payload)
        self.assertNotIn("port", payload)
        self.assertNotIn("identity_file", payload)
        self.assertRegex(payload["owner"], r"^[a-f0-9]{32}:g0$")

    def test_denied_guard_cancels_reservation_and_never_executes(self):
        approvals = ApprovalHarness(guard_approved=False)
        calls = []

        def broker(payload, timeout=15):
            calls.append(dict(payload))
            if payload["action"] == "reserve":
                return {"reservation": "c" * 64}
            if payload["action"] == "cancel_reservation":
                return {"cancelled": True}
            raise AssertionError(payload)

        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_check_available", return_value=True
        ), patch.object(self.plugin, "_broker_call", side_effect=broker):
            result = json.loads(
                self.plugin._handle_terminal({"command": "rm -rf project"})
            )

        self.assertFalse(result["ok"])
        self.assertIn("denied", result["error"])
        self.assertEqual(
            [item["action"] for item in calls],
            ["reserve", "cancel_reservation"],
        )

    def test_pty_requires_one_time_approval_and_stdin_is_bound_to_process(self):
        approvals = ApprovalHarness()
        process_id = "wproc_" + "b" * 24
        calls = []

        def broker(payload, timeout=15):
            calls.append(dict(payload))
            if payload["action"] == "reserve":
                return {"reservation": "d" * 64}
            if payload["action"] == "execute":
                return {
                    "process_id": process_id,
                    "running": True,
                    "command": payload["command"],
                    "output": "",
                }
            return {"process_id": process_id, "written": len(payload.get("data", ""))}

        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_check_available", return_value=True
        ), patch.object(self.plugin, "_broker_call", side_effect=broker):
            started = json.loads(
                self.plugin._handle_terminal(
                    {
                        "command": "python3",
                        "background": True,
                        "pty": True,
                    }
                )
            )
            written = json.loads(
                self.plugin._handle_process(
                    {"action": "submit", "process_id": process_id, "data": "print(1)"}
                )
            )

        self.assertTrue(started["ok"])
        self.assertTrue(written["ok"])
        self.assertEqual(len(approvals.interactive_calls), 1)
        interactive_args, kwargs = approvals.interactive_calls[0]
        self.assertEqual(
            interactive_args,
            (
                "workstation_terminal",
                (
                    "Interactive PTY/stdin grants an ongoing input channel. "
                    "Later keystrokes cannot be inspected by command guards."
                ),
            ),
        )
        self.assertEqual(kwargs["approval_scope"], "hermes_workstation")
        self.assertEqual(kwargs["command"], "python3")
        self.assertEqual(kwargs["workdir"], "/home/hermes-worker/workspace")
        self.assertEqual(calls[-1]["action"], "submit")

    def test_unapproved_process_cannot_gain_stdin_later(self):
        approvals = ApprovalHarness()
        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_check_available", return_value=True
        ), patch.object(self.plugin, "_broker_call") as broker:
            result = json.loads(
                self.plugin._handle_process(
                    {
                        "action": "write",
                        "process_id": "wproc_" + "c" * 24,
                        "data": "hidden",
                    }
                )
            )

        self.assertFalse(result["ok"])
        self.assertIn("not explicitly approved", result["error"])
        broker.assert_not_called()

    def test_reset_revokes_old_generation_before_commit(self):
        approvals = ApprovalHarness()
        calls = []

        def broker(payload, timeout=15):
            calls.append(dict(payload))
            return {"revoked": 1}

        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_broker_call", side_effect=broker
        ):
            old_owner = self.plugin._owner_for("session-reset")
            self.plugin._on_session_reset(session_key="session-reset")
            new_owner = self.plugin._owner_for("session-reset")

        self.assertEqual(calls, [{"action": "revoke_owner", "owner": old_owner}])
        self.assertNotEqual(old_owner, new_owner)
        self.assertTrue(new_owner.endswith(":g1"))

    def test_reset_does_not_advance_generation_when_revocation_fails(self):
        approvals = ApprovalHarness()
        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_broker_call", side_effect=RuntimeError("down")
        ):
            old_owner = self.plugin._owner_for("session-failed")
            self.plugin._on_session_reset(session_key="session-failed")
            current_owner = self.plugin._owner_for("session-failed")

        self.assertEqual(old_owner, current_owner)

    def test_output_and_command_are_redacted_before_return(self):
        approvals = ApprovalHarness()

        def broker(payload, timeout=15):
            if payload["action"] == "reserve":
                return {"reservation": "e" * 64}
            return {
                "process_id": "wproc_" + "d" * 24,
                "running": False,
                "command": "printf secret",
                "output": "token=secret",
            }

        with patch.dict(sys.modules, module_stubs(approvals), clear=False), patch.object(
            self.plugin, "_check_available", return_value=True
        ), patch.object(self.plugin, "_broker_call", side_effect=broker):
            result = json.loads(
                self.plugin._handle_terminal({"command": "printf secret"})
            )

        self.assertNotIn("secret", result["command"])
        self.assertNotIn("secret", result["output"])


    def test_foreground_cancellation_kills_the_exact_remote_process(self):
        interrupt = types.ModuleType("tools.interrupt")
        interrupt.is_interrupted = lambda: True
        process_id = "wproc_" + "e" * 24
        with patch.dict(
            sys.modules,
            {"tools.interrupt": interrupt},
            clear=False,
        ), patch.object(
            self.plugin,
            "_broker_call",
            return_value={"process_id": process_id, "running": False},
        ) as broker:
            result = self.plugin._wait_foreground(
                "f" * 32 + ":g0", process_id, 180
            )

        self.assertTrue(result["interrupted"])
        self.assertEqual(
            broker.call_args.args[0],
            {
                "action": "kill",
                "owner": "f" * 32 + ":g0",
                "process_id": process_id,
            },
        )

if __name__ == "__main__":
    unittest.main()
