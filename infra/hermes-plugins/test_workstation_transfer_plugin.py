from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


MODULE_PATH = (
    pathlib.Path(__file__).parent
    / "spartan-workstation-terminal"
    / "transfer.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_transfer_plugin", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class WorkstationTransferPluginTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.owner = "a" * 32 + ":g0"

    def runtime(self, broker):
        return self.module.TransferRuntime(
            broker_call=broker,
            socket_path=lambda: "/unused",
            session_key=lambda: "session",
            owner_for=lambda _session: self.owner,
            error_response=lambda message: json.dumps(
                {"ok": False, "error": message}
            ),
        )

    def approvals(self, approved=True):
        approval = types.ModuleType("tools.approval")
        approval.request_one_time_tool_approval = lambda *_args, **_kwargs: {
            "approved": approved,
            "message": None if approved else "owner denied",
        }
        tools = types.ModuleType("tools")
        tools.__path__ = []
        return {"tools": tools, "tools.approval": approval}

    def test_registers_two_distinct_bounded_tools(self):
        runtime = self.runtime(lambda *_args, **_kwargs: {})
        context = FakeContext()
        self.module.register(context, runtime)

        self.assertEqual(
            [item["name"] for item in context.tools],
            ["workstation_transfer", "workstation_transfer_status"],
        )
        schema = self.module.TRANSFER_SCHEMA["parameters"]["properties"]
        for forbidden in (
            "host",
            "port",
            "username",
            "key_path",
            "ssh_options",
            "source_root",
            "destination_root",
        ):
            self.assertNotIn(forbidden, schema)

    def test_workspace_labels_map_only_to_structural_workspace_roots(self):
        self.assertEqual(
            self.module.TransferRuntime._workspace("default"),
            ("default", pathlib.Path("/opt/data/workspace")),
        )
        self.assertEqual(
            self.module.TransferRuntime._workspace("review_1"),
            (
                "review_1",
                pathlib.Path("/opt/data/profiles/review_1/workspace"),
            ),
        )
        for invalid in ("../escape", "/absolute", "bad/name", "x" * 65):
            with self.assertRaises(self.module.CORE.TransferError):
                self.module.TransferRuntime._workspace(invalid)

    def test_every_transfer_reserves_then_asks_one_time_approval(self):
        calls = []

        def broker(payload, timeout=15):
            calls.append(dict(payload))
            if payload["action"] == "transfer_destination":
                return {"exists": False}
            if payload["action"] == "reserve":
                return {"reservation": "b" * 64}
            if payload["action"] == "cancel_reservation":
                return {"cancelled": True}
            raise AssertionError(payload)

        runtime = self.runtime(broker)

        def completed(job, *_args):
            with job.lock:
                job.state = "completed"
                job.result = {
                    "kind": "file",
                    "entry_count": 1,
                    "total_bytes": 7,
                    "manifest_sha256": control_hash[0],
                }
                job.finished_at = 1
            job.done.set()

        control_hash = [""]
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "artifact.txt").write_text("payload", encoding="utf-8")
            summary = self.module.CORE.preflight_source(root, "artifact.txt")
            control_hash[0] = summary["manifest_sha256"]
            with patch.object(self.module, "DEFAULT_WORKSPACE", root), patch.object(
                runtime, "_run", side_effect=completed
            ), patch.dict(sys.modules, self.approvals(True), clear=False):
                result = json.loads(
                    runtime.start(
                        {
                            "operation": "upload",
                            "local_path": "artifact.txt",
                            "remote_root": "workspace",
                            "remote_path": "artifact.txt",
                        },
                        task_id="task_123",
                    )
                )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["action"] for item in calls],
            ["transfer_destination", "reserve"],
        )
        self.assertRegex(calls[-1]["operation_hash"], r"^[a-f0-9]{64}$")

    def test_status_accepts_hermes_injected_context(self):
        runtime = self.runtime(lambda *_args, **_kwargs: {})

        result = json.loads(
            runtime.status({"action": "list"}, task_id="task_123")
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["transfers"], [])

    def test_denial_consumes_no_stream_and_cancels_reservation(self):
        calls = []

        def broker(payload, timeout=15):
            calls.append(dict(payload))
            if payload["action"] == "transfer_destination":
                return {"exists": False}
            if payload["action"] == "reserve":
                return {"reservation": "c" * 64}
            if payload["action"] == "cancel_reservation":
                return {"cancelled": True}
            raise AssertionError(payload)

        runtime = self.runtime(broker)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "artifact.txt").write_text("payload", encoding="utf-8")
            with patch.object(self.module, "DEFAULT_WORKSPACE", root), patch.dict(
                sys.modules, self.approvals(False), clear=False
            ):
                result = json.loads(
                    runtime.start(
                        {
                            "operation": "upload",
                            "local_path": "artifact.txt",
                            "remote_root": "workspace",
                            "remote_path": "artifact.txt",
                        }
                    )
                )

        self.assertFalse(result["ok"])
        self.assertIn("denied", result["error"])
        self.assertEqual(calls[-1]["action"], "cancel_reservation")
        self.assertEqual(runtime.jobs, {})

    def test_private_key_is_denied_before_broker_or_approval(self):
        broker_calls = []
        runtime = self.runtime(
            lambda payload, timeout=15: broker_calls.append(dict(payload))
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "id_ed25519").write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8"
            )
            with patch.object(self.module, "DEFAULT_WORKSPACE", root), patch.dict(
                sys.modules, self.approvals(True), clear=False
            ):
                result = json.loads(
                    runtime.start(
                        {
                            "operation": "upload",
                            "local_path": "id_ed25519",
                            "remote_root": "workspace",
                            "remote_path": "id_ed25519",
                            "allow_sensitive": True,
                        }
                    )
                )

        self.assertFalse(result["ok"])
        self.assertIn("hard-denied", result["error"])
        self.assertEqual(broker_calls, [])

    def test_replace_approval_binds_complete_destination_metadata(self):
        destination = {
            "exists": True,
            "kind": "file",
            "size": 3,
            "sha256": "d" * 64,
            "device": 1,
            "inode": 2,
            "ctime_ns": 3,
        }
        control = {
            "operation": "upload",
            "owner": self.owner,
            "transfer_id": "wtx_" + "e" * 24,
            "local_workspace": "default",
            "local_path": "new.txt",
            "remote_root": "workspace",
            "remote_path": "old.txt",
            "replace": True,
            "allow_sensitive": False,
            "background": False,
            "timeout": 60,
            "kind": "file",
            "entry_count": 1,
            "total_bytes": 3,
            "manifest_sha256": "f" * 64,
            "soft_sensitive_count": 0,
            "sensitive_samples": [],
            "expected_destination": destination,
        }
        command, reason = self.module.TransferRuntime._approval_text(control)

        self.assertIn("old.txt", command)
        self.assertIn("d" * 64, reason)
        self.assertIn("not linearizable", reason)
        digest = self.module.PROTOCOL.operation_hash(control)
        changed = dict(control)
        changed["expected_destination"] = {**destination, "ctime_ns": 4}
        self.assertNotEqual(digest, self.module.PROTOCOL.operation_hash(changed))


    def test_exchange_upload_rejects_windows_reserved_destination_before_broker(self):
        broker_calls = []
        runtime = self.runtime(
            lambda payload, timeout=15: broker_calls.append(dict(payload))
        )
        result = json.loads(
            runtime.start(
                {
                    "operation": "upload",
                    "local_path": "artifact.txt",
                    "remote_root": "exchange",
                    "remote_path": "CON",
                }
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["error"])
        self.assertEqual(broker_calls, [])

if __name__ == "__main__":
    unittest.main()
