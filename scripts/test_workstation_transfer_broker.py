from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = pathlib.Path(__file__).with_name("workstation-transfer-broker.py")
PLUGIN_DIR = (
    pathlib.Path(__file__).parents[1]
    / "infra"
    / "hermes-plugins"
    / "spartan-workstation-terminal"
)
CORE_PATH = PLUGIN_DIR / "transfer_core.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "workstation_transfer_broker", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeBroker:
    def __init__(self):
        self.active = 0
        self.operation_slots = threading.BoundedSemaphore(4)
        self.guard_entries = 0

    @contextmanager
    def gateway_guard(self, _epoch):
        self.guard_entries += 1
        yield

    def _active_count(self):
        return self.active


class WorkstationTransferBrokerTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.owner = "a" * 32 + ":g0"

    def test_reservation_is_owner_bound_one_time_and_revocable(self):
        store = self.module.ReservationStore()
        digest = "b" * 64
        token = store.reserve(self.owner, digest)["reservation"]

        with self.assertRaises(self.module.TransferBrokerError):
            store.consume(token, "c" * 32 + ":g0", digest)
        store.consume(token, self.owner, digest)
        with self.assertRaises(self.module.TransferBrokerError):
            store.consume(token, self.owner, digest)

        second = store.reserve(self.owner, digest)["reservation"]
        self.assertEqual(store.revoke_owner(self.owner), 1)
        with self.assertRaises(self.module.TransferBrokerError):
            store.consume(second, self.owner, digest)

    def test_expired_reservation_fails_closed(self):
        store = self.module.ReservationStore(ttl_seconds=0.01)
        token = store.reserve(self.owner, "d" * 64)["reservation"]
        time.sleep(0.02)
        with self.assertRaisesRegex(self.module.TransferBrokerError, "expired"):
            store.consume(token, self.owner, "d" * 64)

    def test_rejects_arbitrary_remote_root_and_unknown_control_fields(self):
        manager = self.module.TransferManager(
            FakeBroker(),
            remote_roots={"workspace": pathlib.Path("/tmp/workspace")},
            ssh_factory=lambda *_args: [],
        )
        with self.assertRaises(self.module.TransferBrokerError):
            manager.handle_control(
                {
                    "action": "transfer_preflight",
                    "owner": self.owner,
                    "remote_root": "attacker",
                    "remote_path": "file",
                }
            )
        with self.assertRaisesRegex(self.module.TransferBrokerError, "unknown"):
            manager.handle_control(
                {
                    "action": "reserve",
                    "owner": self.owner,
                    "operation_hash": "e" * 64,
                    "host": "attacker",
                }
            )

    def test_upload_stream_uses_real_helper_and_publishes_verified_file(self):
        protocol = self.module.PROTOCOL
        core = self.module.CORE
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as remote_tmp:
            source_root = pathlib.Path(source_tmp)
            remote_root = pathlib.Path(remote_tmp)
            (source_root / "artifact.bin").write_bytes(bytes(range(256)) * 20)
            summary = core.preflight_source(source_root, "artifact.bin")

            def ssh_factory(mode, request):
                return [
                    sys.executable,
                    str(CORE_PATH),
                    mode,
                    json.dumps(request, sort_keys=True),
                ]

            broker = FakeBroker()
            manager = self.module.TransferManager(
                broker,
                remote_roots={"workspace": remote_root},
                ssh_factory=ssh_factory,
                minimum_free_bytes=0,
            )
            control = {
                "operation": "upload",
                "owner": self.owner,
                "transfer_id": "wtx_" + "f" * 24,
                "local_workspace": "default",
                "local_path": "artifact.bin",
                "remote_root": "workspace",
                "remote_path": "received.bin",
                "replace": False,
                "allow_sensitive": False,
                "background": False,
                "timeout": 60,
                **core.public_summary(summary),
                "expected_destination": {"exists": False},
            }
            digest = protocol.operation_hash(control)
            control["operation_hash"] = digest
            control["reservation"] = manager.reservations.reserve(
                self.owner, digest
            )["reservation"]
            client, server = socket.socketpair()
            worker = threading.Thread(
                target=manager.handle_stream,
                args=(server, 1),
                daemon=True,
            )
            worker.start()
            try:
                protocol.send_preamble(client)
                protocol.send_control(client, control)
                writer = protocol.FramedWriter(client)
                core.stream_tar(source_root, "artifact.bin", summary, writer)
                protocol.send_frame(client, protocol.FRAME_END)
                frame_type, payload = protocol.recv_frame(client)
                self.assertEqual(frame_type, protocol.FRAME_RESULT)
                result = json.loads(payload)
                self.assertTrue(result["ok"])
            finally:
                client.close()
                worker.join(timeout=5)
            self.assertGreater(broker.guard_entries, 0)
            self.assertEqual(
                (remote_root / "received.bin").read_bytes(),
                bytes(range(256)) * 20,
            )

    def test_revoke_all_waits_for_active_stream_cleanup(self):
        manager = self.module.TransferManager(
            FakeBroker(),
            remote_roots={"workspace": pathlib.Path("/tmp/workspace")},
            ssh_factory=lambda *_args: [],
        )
        active = self.module.ActiveTransfer(
            transfer_id="wtx_" + "f" * 24,
            owner=self.owner,
            process=Mock(pid=123, poll=Mock(return_value=None)),
            connection=Mock(),
            cancelled=threading.Event(),
            gateway_epoch=1,
            done=threading.Event(),
        )
        manager.active[active.transfer_id] = active
        result = {}

        def revoke():
            result.update(manager.revoke_all())

        with patch.object(self.module.os, "killpg"):
            worker = threading.Thread(target=revoke)
            worker.start()
            time.sleep(0.1)
            self.assertTrue(worker.is_alive())
            active.done.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result["transfers"], 1)

    def test_exchange_upload_requires_cached_atomic_capability_probe(self):
        modes = []
        with tempfile.TemporaryDirectory() as exchange_tmp:
            exchange = pathlib.Path(exchange_tmp)

            def ssh_factory(mode, request):
                modes.append(mode)
                return [
                    sys.executable,
                    str(CORE_PATH),
                    mode,
                    json.dumps(request, sort_keys=True),
                ]

            manager = self.module.TransferManager(
                FakeBroker(),
                remote_roots={"exchange": exchange},
                ssh_factory=ssh_factory,
                minimum_free_bytes=0,
            )
            request = {
                "action": "transfer_destination",
                "owner": self.owner,
                "remote_root": "exchange",
                "remote_path": "new.bin",
            }
            self.assertEqual(manager.handle_control(request), {"exists": False})
            self.assertEqual(manager.handle_control(request), {"exists": False})

        self.assertEqual(modes.count("probe"), 1)
        self.assertEqual(modes.count("destination"), 2)

    def test_failed_exchange_probe_stays_download_only(self):
        manager = self.module.TransferManager(
            FakeBroker(),
            remote_roots={"exchange": pathlib.Path("/tmp/exchange")},
            ssh_factory=lambda *_args: [],
        )
        calls = []

        def fail_probe(mode, request, timeout=900):
            calls.append(mode)
            raise self.module.TransferBrokerError("unsupported")

        manager._remote_json = fail_probe
        request = {
            "action": "transfer_destination",
            "owner": self.owner,
            "remote_root": "exchange",
            "remote_path": "new.bin",
        }
        for _ in range(2):
            with self.assertRaisesRegex(
                self.module.TransferBrokerError, "download-only"
            ):
                manager.handle_control(request)
        self.assertEqual(calls, ["probe"])

    def test_raw_stream_rejects_forged_limits_and_sensitive_summary(self):
        manager = self.module.TransferManager(
            FakeBroker(),
            remote_roots={"workspace": pathlib.Path("/tmp/workspace")},
            ssh_factory=lambda *_args: [],
        )
        control = {
            "operation": "upload",
            "owner": self.owner,
            "transfer_id": "wtx_" + "1" * 24,
            "local_workspace": "default",
            "local_path": "artifact.bin",
            "remote_root": "workspace",
            "remote_path": "artifact.bin",
            "replace": False,
            "allow_sensitive": False,
            "background": False,
            "timeout": 60,
            "kind": "file",
            "entry_count": 1,
            "total_bytes": -1,
            "manifest_sha256": "2" * 64,
            "soft_sensitive_count": 0,
            "sensitive_samples": [],
            "expected_destination": {"exists": False},
        }
        control["operation_hash"] = self.module.PROTOCOL.operation_hash(control)
        control["reservation"] = "3" * 64
        with self.assertRaisesRegex(
            self.module.TransferBrokerError, "byte count"
        ):
            manager._validate_stream_control(control)

        control["total_bytes"] = 1
        control["soft_sensitive_count"] = 1
        control["sensitive_samples"] = [
            {"path": ".env", "level": "soft", "reason": "configuration"}
        ]
        control["operation_hash"] = self.module.PROTOCOL.operation_hash(control)
        with self.assertRaisesRegex(
            self.module.TransferBrokerError, "not explicitly allowed"
        ):
            manager._validate_stream_control(control)

if __name__ == "__main__":
    unittest.main()
