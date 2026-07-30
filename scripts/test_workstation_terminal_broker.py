from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock


MODULE_PATH = pathlib.Path(__file__).with_name("workstation-terminal-broker.py")


def load_module():
    spec = importlib.util.spec_from_file_location("workstation_terminal_broker", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LocalProcessBroker:
    """Thin test wrapper which keeps lifecycle code but replaces only SSH."""

    def __init__(self, module):
        class Broker(module.ProcessBroker):
            def ssh_command(self, command, workdir, pty_mode):
                return ["/bin/bash", "-lc", command]

        self.module = module
        self.broker = Broker(
            {
                "_ssh_binary": "/usr/bin/ssh",
                "_broker_known_hosts": "/tmp/known_hosts",
                "execution": {
                    "_identity_path": pathlib.Path("/private/execution"),
                    "gateway_port": 22022,
                },
                "remote": {"user": "hermes-worker"},
            },
            "172.29.0.1",
            peer_authorizer=lambda *_args: None,
        )


class WorkstationTerminalBrokerTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.local = LocalProcessBroker(self.module)
        self.owner = "a" * 32 + ":g0"

    def tearDown(self):
        self.local.broker.stop()

    def execute(self, **updates):
        request = {
            "action": "execute",
            "owner": self.owner,
            "command": "printf broker-ok",
        }
        request.update(updates)
        request.setdefault("workdir", str(self.module.DEFAULT_WORKDIR))
        request.setdefault("background", False)
        request.setdefault("pty", False)
        request.setdefault(
            "timeout",
            3600 if request["background"] else 180,
        )
        digest = self.module.TRANSFER.PROTOCOL.terminal_operation_hash(request)
        reservation = self.local.broker.handle(
            {
                "action": "reserve",
                "owner": self.owner,
                "operation_hash": digest,
            }
        )["reservation"]
        request["operation_hash"] = digest
        request["reservation"] = reservation
        try:
            return self.local.broker.handle(request)
        except Exception:
            self.local.broker.handle(
                {
                    "action": "cancel_reservation",
                    "owner": self.owner,
                    "reservation": reservation,
                }
            )
            raise

    def test_ssh_command_is_strict_pinned_and_has_no_forwarding(self):
        command = self.local.broker.ssh_command(
            "id", "/home/hermes-worker/workspace", False
        )
        # The local test subclass intentionally replaces SSH.
        self.assertEqual(command[:2], ["/bin/bash", "-lc"])

        config = {
            "_ssh_binary": "/usr/bin/ssh",
            "_broker_known_hosts": "/private/known_hosts",
            "execution": {
                "_identity_path": pathlib.Path("/private/execution"),
                "gateway_port": 22022,
            },
            "remote": {"user": "hermes-worker"},
        }
        broker = self.module.ProcessBroker(
            config, "172.29.0.1", peer_authorizer=lambda *_args: None
        )
        ssh = broker.ssh_command("id", "/home/hermes-worker/workspace", False)
        rendered = " ".join(str(item) for item in ssh)
        self.assertIn("StrictHostKeyChecking=yes", rendered)
        self.assertIn("UserKnownHostsFile=/private/known_hosts", rendered)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", rendered)
        self.assertIn("ClearAllForwardings=yes", rendered)
        self.assertIn("ForwardAgent=no", rendered)
        self.assertIn("ForwardX11=no", rendered)
        self.assertIn("-F /dev/null", rendered)
        self.assertNotIn("100.70.", rendered)
        self.assertNotIn("-L ", f"{rendered} ")
        self.assertNotIn("-R ", f"{rendered} ")
        self.assertNotIn("-D ", f"{rendered} ")

    def test_foreground_runs_and_returns_bounded_clean_output(self):
        result = self.execute(command="printf '\\033[31mhello\\033[0m'")

        self.assertFalse(result["running"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"], "hello")
        self.assertLessEqual(len(result["output"]), self.module.MAX_OUTPUT_CHARS)

    def test_background_lifecycle_incremental_log_and_stdin(self):
        started = self.execute(
            command="read line; printf 'got:%s' \"$line\"",
            background=True,
            timeout=30,
        )
        process_id = started["process_id"]
        self.assertTrue(started["running"])

        written = self.local.broker.handle(
            {
                "action": "submit",
                "owner": self.owner,
                "process_id": process_id,
                "data": "answer",
            }
        )
        self.assertEqual(written["written"], 6)
        waited = self.local.broker.handle(
            {
                "action": "wait",
                "owner": self.owner,
                "process_id": process_id,
                "timeout": 5,
            }
        )
        self.assertFalse(waited["running"])
        self.assertIn("got:answer", waited["output"])
        log = self.local.broker.handle(
            {
                "action": "log",
                "owner": self.owner,
                "process_id": process_id,
                "offset": waited["next_offset"],
            }
        )
        self.assertEqual(log["output"], "")

    def test_owner_isolation_and_generation_revocation(self):
        started = self.execute(command="sleep 30", background=True, timeout=30)
        with self.assertRaisesRegex(self.module.BrokerError, "process not found"):
            self.local.broker.handle(
                {
                    "action": "poll",
                    "owner": "b" * 32 + ":g0",
                    "process_id": started["process_id"],
                }
            )

        revoked = self.local.broker.handle(
            {"action": "revoke_owner", "owner": self.owner}
        )
        self.assertEqual(revoked["revoked"], 1)
        waited = self.local.broker.handle(
            {
                "action": "wait",
                "owner": self.owner,
                "process_id": started["process_id"],
                "timeout": 5,
            }
        )
        self.assertFalse(waited["running"])
        self.assertEqual(waited["completion_reason"], "killed")

    def test_rejects_arbitrary_cwd_unknown_fields_and_oversized_timeout(self):
        for workdir in ("/tmp", "/home/hermes-worker/workspace/../.ssh", "relative"):
            with self.subTest(workdir=workdir):
                with self.assertRaises(self.module.BrokerError):
                    self.execute(workdir=workdir)
        with self.assertRaisesRegex(self.module.BrokerError, "unknown request field"):
            self.execute(host="attacker.example")
        with self.assertRaisesRegex(self.module.BrokerError, "between 1 and 900"):
            self.execute(timeout=901)
        with self.assertRaisesRegex(self.module.BrokerError, "between 1 and 86400"):
            self.execute(background=True, timeout=86401)

    def test_enforces_global_process_cap(self):
        sessions = [
            self.execute(command="sleep 30", background=True, timeout=30)
            for _ in range(self.module.MAX_ACTIVE_PROCESSES)
        ]
        with self.assertRaisesRegex(self.module.BrokerError, "at most"):
            self.execute(command="sleep 30", background=True, timeout=30)
        for session in sessions:
            self.local.broker.handle(
                {
                    "action": "kill",
                    "owner": self.owner,
                    "process_id": session["process_id"],
                }
            )

    def test_socket_permissions_and_peer_authorization_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            directory.chmod(0o700)
            socket_path = directory / "broker.sock"
            calls = []

            class StopAfterPeer:
                def __call__(self, pid, uid, gid, pidfd):
                    calls.append((pid, uid, gid, pidfd))
                    raise self_module.BrokerError("denied peer")

            self_module = self.module
            broker = self.module.ProcessBroker(
                self.local.broker.config,
                "172.29.0.1",
                peer_authorizer=StopAfterPeer(),
            )
            # The lower-level connection test proves peer auth happens before
            # request dispatch without starting a long-lived server.
            left, right = __import__("socket").socketpair()
            try:
                right.sendall(
                    (
                        '{"action":"list","owner":"'
                        + self.owner
                        + '"}\n'
                    ).encode()
                )
                thread = __import__("threading").Thread(
                    target=self.module.serve_connection, args=(left, broker)
                )
                thread.start()
                response = right.recv(4096).decode()
                thread.join(2)
            finally:
                right.close()
            self.assertTrue(calls)
            self.assertIn('"ok": false', response)
            self.assertIn("denied peer", response)


    def test_gateway_authorizer_parser_accepts_only_exact_default_gateway(self):
        output = (
            "PID USER COMMAND\n"
            "101 alberto " + self.module.DEFAULT_GATEWAY_COMMAND + "\n"
            "102 alberto /opt/hermes/.venv/bin/hermes -p work gateway run --replace\n"
            "103 alberto python -c gateway\n"
        )
        self.assertEqual(self.module.parse_default_gateway_pid(output), 101)
        with self.assertRaisesRegex(self.module.BrokerError, "exactly one"):
            self.module.parse_default_gateway_pid(
                output + "104 alberto " + self.module.DEFAULT_GATEWAY_COMMAND + "\n"
            )
        with self.assertRaisesRegex(self.module.BrokerError, "found 0"):
            self.module.parse_default_gateway_pid(
                "PID USER COMMAND\n105 alberto python -c gateway\n"
            )

    def test_gateway_authorizer_rotates_only_after_the_pinned_peer_dies(self):
        baseline = {
            "version": 1,
            "container_name": "spartan_gate_hermes",
            "image_id": "sha256:image",
            "compose_project": "spartan-gate",
            "compose_service": "hermes",
            "compose_oneoff": "False",
            "compose_config_hash": "config-hash",
        }
        snapshots = [
            self.module.GatewaySnapshot(
                container_id="container-a",
                provenance=baseline,
                gateway_pid=101,
                gateway_uid=str(os.getuid()),
            ),
            self.module.GatewaySnapshot(
                container_id="container-b",
                provenance=baseline,
                gateway_pid=202,
                gateway_uid=str(os.getuid()),
            ),
        ]
        resolver = Mock(side_effect=snapshots)
        alive = {101: True, 202: True}
        rotations = []
        health = []
        authorizer = self.module.DockerGatewayAuthorizer(
            "spartan_gate_hermes",
            baseline,
            resolver=resolver,
            pidfd_open=lambda pid: pid,
            pidfd_alive=lambda fd: alive[fd],
            peer_pidfd_pid=lambda fd: fd,
            peer_validator=lambda *_args: None,
            on_rotate=lambda: rotations.append("rotated"),
            health_publisher=lambda state, epoch, pid: health.append(
                (state, epoch, pid)
            ),
        )

        self.assertEqual(authorizer(101, os.getuid(), os.getgid(), 101), 1)
        self.assertEqual(health[-1], ("ready", 1, 101))
        with self.assertRaisesRegex(
            self.module.BrokerError, "pinned Hermes gateway"
        ):
            authorizer(202, os.getuid(), os.getgid(), 202)
        self.assertEqual(resolver.call_count, 1)

        alive[101] = False
        time.sleep(0.4)
        self.assertEqual(health[-1], ("waiting-for-hermes", 1, None))
        self.assertEqual(authorizer(202, os.getuid(), os.getgid(), 202), 2)
        self.assertEqual(rotations, ["rotated"])
        self.assertEqual(resolver.call_count, 2)

    def test_gateway_authorizer_rejects_changed_container_provenance(self):
        baseline = {
            "version": 1,
            "container_name": "spartan_gate_hermes",
            "image_id": "sha256:image-a",
            "compose_project": "spartan-gate",
            "compose_service": "hermes",
            "compose_oneoff": "False",
            "compose_config_hash": "config-hash",
        }
        changed = dict(baseline, image_id="sha256:image-b")
        authorizer = self.module.DockerGatewayAuthorizer(
            "spartan_gate_hermes",
            baseline,
            resolver=lambda _name: self.module.GatewaySnapshot(
                container_id="container-b",
                provenance=changed,
                gateway_pid=202,
                gateway_uid=str(os.getuid()),
            ),
            pidfd_open=lambda pid: pid,
            pidfd_alive=lambda _fd: True,
            peer_pidfd_pid=lambda fd: fd,
            peer_validator=lambda *_args: None,
        )

        with self.assertRaisesRegex(self.module.BrokerError, "repin required"):
            authorizer(202, os.getuid(), os.getgid(), 202)

    def test_gateway_rotation_revokes_all_managed_work(self):
        started = self.execute(command="sleep 30", background=True, timeout=30)
        reservation = self.local.broker.handle(
            {
                "action": "reserve",
                "owner": self.owner,
                "operation_hash": "a" * 64,
            }
        )

        result = self.local.broker.revoke_for_gateway_rotation()

        self.assertEqual(result["processes"], 1)
        self.assertGreaterEqual(result["reservations"], 1)
        waited = self.local.broker.handle(
            {
                "action": "wait",
                "owner": self.owner,
                "process_id": started["process_id"],
                "timeout": 5,
            }
        )
        self.assertFalse(waited["running"])
        self.assertEqual(waited["termination_source"], "gateway_rotation")
        with self.assertRaisesRegex(
            self.module.TRANSFER.TransferBrokerError,
            "missing or consumed",
        ):
            self.local.broker.transfer_manager.reservations.consume(
                reservation["reservation"], self.owner, "a" * 64
            )

    def test_deferred_foreground_returns_process_id_before_completion(self):
        result = self.execute(
            command="sleep 30",
            deferred=True,
            timeout=30,
        )
        self.assertTrue(result["running"])
        self.local.broker.handle(
            {
                "action": "kill",
                "owner": self.owner,
                "process_id": result["process_id"],
            }
        )

if __name__ == "__main__":
    unittest.main()
