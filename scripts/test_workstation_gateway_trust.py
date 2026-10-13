import unittest
from unittest.mock import patch

from scripts import workstation_gateway_trust as trust


NATIVE_COMMAND = (
    "/opt/hermes/.venv/bin/python -P -c import os, site, sys; "
    "sys.argv[0]='hermes'; site.addsitedir(os.environ['HERMES_SITE']); "
    "from hermes_cli.main import main; sys.exit(main()) gateway run --replace"
)


class GatewayTrustTests(unittest.TestCase):
    def process_table(self, *commands):
        rows = [
            "PID PPID USER COMMAND",
            f"10 1 root {trust.DEFAULT_SVSCAN_COMMAND}",
            f"20 10 root {trust.DEFAULT_SUPERVISOR_COMMAND}",
        ]
        rows.extend(f"{30 + i} 20 1000 {command}" for i, command in enumerate(commands))
        return "\n".join(rows)

    def test_accepts_reviewed_launchers(self):
        for command in (trust.DEFAULT_GATEWAY_COMMAND, NATIVE_COMMAND):
            with self.subTest(command=command):
                gateway, supervisor, svscan = trust.parse_supervised_gateway(
                    self.process_table(command)
                )
                self.assertEqual((gateway.pid, supervisor.pid, svscan.pid), (30, 20, 10))

    def test_rejects_unreviewed_commands_and_ambiguous_gateways(self):
        for command in (
            NATIVE_COMMAND.replace("gateway run", "-p work gateway run"),
            NATIVE_COMMAND.replace("-P -c", "-c"),
            NATIVE_COMMAND.replace("sys.exit(main())", "sys.exit(other())"),
            "python -c gateway",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(trust.GatewayTrustError, "found 0"):
                    trust.parse_supervised_gateway(self.process_table(command))
        with self.assertRaisesRegex(trust.GatewayTrustError, "found 2"):
            trust.parse_supervised_gateway(
                self.process_table(trust.DEFAULT_GATEWAY_COMMAND, NATIVE_COMMAND)
            )

    def test_native_launcher_requires_root_supervisor_chain(self):
        table = self.process_table(NATIVE_COMMAND)
        for unsafe in (
            table.replace("20 10 root", "20 10 1000"),
            table.replace("30 20 1000", "30 10 1000"),
            table.replace("10 1 root", "10 1 1000"),
        ):
            with self.assertRaises(trust.GatewayTrustError):
                trust.parse_supervised_gateway(unsafe)

    def test_peer_validation_uses_the_same_exact_launchers(self):
        snapshot = trust.GatewaySnapshot("container", {}, 30, "1000", 20, 10)
        for command, accepted in ((NATIVE_COMMAND, True), (NATIVE_COMMAND + " --help", False)):
            with self.subTest(command=command), \
                patch.object(trust, "pidfd_pid", return_value=30), \
                patch.object(trust, "pidfd_alive", return_value=True), \
                patch.object(trust.os, "getuid", return_value=1000), \
                patch.object(trust, "_proc_uid", side_effect=lambda pid: 1000 if pid == 30 else 0), \
                patch.object(trust, "_proc_ppid", side_effect=lambda pid: {30: 20, 20: 10}[pid]), \
                patch.object(trust, "_proc_command", side_effect=lambda pid: {
                    30: command, 20: trust.DEFAULT_SUPERVISOR_COMMAND,
                    10: trust.DEFAULT_SVSCAN_COMMAND,
                }[pid]), \
                patch.object(trust.Path, "read_text", return_value="same-cgroup"):
                if accepted:
                    trust.validate_peer_process(30, 99, snapshot)
                else:
                    with self.assertRaisesRegex(trust.GatewayTrustError, "command identity"):
                        trust.validate_peer_process(30, 99, snapshot)

    def test_native_peer_rejects_changed_identity_or_process_chain(self):
        snapshot = trust.GatewaySnapshot("container", {}, 30, "1000", 20, 10)
        cases = (
            ("pidfd", {"pidfd_pid": 31}),
            ("dead", {"pidfd_alive": False}),
            ("snapshot_pid", {"peer_pid": 31, "pidfd_pid": 31}),
            ("gateway_uid", {"uids": {30: 1001}}),
            ("supervisor_uid", {"uids": {20: 1000}}),
            ("svscan_uid", {"uids": {10: 1000}}),
            ("gateway_parent", {"parents": {30: 10}}),
            ("supervisor_parent", {"parents": {20: 1}}),
            ("supervisor_command", {"commands": {20: "s6-supervise other"}}),
            ("svscan_command", {"commands": {10: "other-svscan"}}),
            ("cgroup", {"cgroup": "different-cgroup"}),
        )
        for name, changes in cases:
            uids = {30: 1000, 31: 1000, 20: 0, 10: 0, **changes.get("uids", {})}
            parents = {30: 20, 20: 10, **changes.get("parents", {})}
            commands = {30: NATIVE_COMMAND, 20: trust.DEFAULT_SUPERVISOR_COMMAND,
                        10: trust.DEFAULT_SVSCAN_COMMAND, **changes.get("commands", {})}
            def cgroup(path, **kwargs):
                return changes.get("cgroup", "same-cgroup") if str(path) == "/proc/20/cgroup" else "same-cgroup"
            with self.subTest(name=name), \
                patch.object(trust, "pidfd_pid", return_value=changes.get("pidfd_pid", 30)), \
                patch.object(trust, "pidfd_alive", return_value=changes.get("pidfd_alive", True)), \
                patch.object(trust.os, "getuid", return_value=1000), \
                patch.object(trust, "_proc_uid", side_effect=uids.__getitem__), \
                patch.object(trust, "_proc_ppid", side_effect=parents.__getitem__), \
                patch.object(trust, "_proc_command", side_effect=commands.__getitem__), \
                patch.object(trust.Path, "read_text", autospec=True, side_effect=cgroup):
                with self.assertRaises(trust.GatewayTrustError):
                    trust.validate_peer_process(changes.get("peer_pid", 30), 99, snapshot)
