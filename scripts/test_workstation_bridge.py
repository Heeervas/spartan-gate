import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "workstation-bridge.py"
ALIASES = ROOT / "scripts" / "aliases.sh"

HOST_KEY_BLOB = base64.b64encode(b"spartan-gate-test-host-key").decode()
HOST_PUBLIC_KEY = f"ssh-ed25519 {HOST_KEY_BLOB}"
HOST_FINGERPRINT = (
    "SHA256:"
    + base64.b64encode(hashlib.sha256(base64.b64decode(HOST_KEY_BLOB)).digest())
    .decode()
    .rstrip("=")
)



def free_loopback_port():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()

class WorkstationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "private" / "workstation"
        self.keys_dir = self.config_dir / "keys"
        self.state_dir = self.root / "state"
        self.bin_dir = self.root / "bin"
        self.config_dir.mkdir(parents=True)
        self.keys_dir.mkdir()
        self.bin_dir.mkdir()
        self.execution_key = self.keys_dir / "execution_ed25519"
        self.tunnel_key = self.keys_dir / "app_tunnel_ed25519"
        self.execution_key.write_text("execution-private-key\n", encoding="utf-8")
        self.tunnel_key.write_text("tunnel-private-key\n", encoding="utf-8")
        self.execution_key.chmod(0o600)
        self.tunnel_key.chmod(0o600)
        self.command_log = self.root / "commands.log"
        self.app_local_port = free_loopback_port()
        self.config = self.config_dir / "bridges.json"
        self.write_config()

    def tearDown(self):
        if TOOL.exists() and self.state_dir.exists():
            self.run_tool("down")
        self.temp_dir.cleanup()

    def payload(self):
        return {
            "version": 1,
            "remote": {
                "host": "100.100.100.100",
                "ssh_port": 22022,
                "user": "hermes-worker",
                "host_public_key": HOST_PUBLIC_KEY,
                "host_key_sha256": HOST_FINGERPRINT,
            },
            "execution": {
                "gateway_port": 22022,
                "identity_file": "keys/execution_ed25519",
            },
            "tunnel": {
                "identity_file": "keys/app_tunnel_ed25519",
            },
            "apps": [
                {
                    "name": "blender",
                    "enabled": True,
                    "gateway_port": 9876,
                    "local_port": self.app_local_port,
                    "remote_host": "127.0.0.1",
                    "remote_port": 9876,
                }
            ],
        }

    def write_config(self, mutate=None):
        payload = self.payload()
        if mutate:
            mutate(payload)
        self.config.write_text(json.dumps(payload), encoding="utf-8")

    def install_fake_tools(self, *, scanned_key=HOST_PUBLIC_KEY, fail_socat_port=None):
        ssh_keyscan = self.bin_dir / "ssh-keyscan"
        ssh_keyscan.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$WORKSTATION_TEST_COMMAND_LOG\"\n"
            f"printf '%s %s\\n' '[100.100.100.100]:22022' '{scanned_key}'\n",
            encoding="utf-8",
        )
        ssh_keyscan.chmod(0o755)

        ssh = self.bin_dir / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$WORKSTATION_TEST_COMMAND_LOG\"\n"
            "case \" $* \" in\n"
            "  *' -N '*) exec sleep 30 ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        ssh.chmod(0o755)

        socat = self.bin_dir / "socat"
        failure = (
            f"case \" $* \" in *':{fail_socat_port},'*) exit 9 ;; esac\n"
            if fail_socat_port
            else ""
        )
        socat.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$WORKSTATION_TEST_COMMAND_LOG\"\n"
            f"{failure}"
            "exec sleep 30\n",
            encoding="utf-8",
        )
        socat.chmod(0o755)

        docker = self.bin_dir / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  top)\n"
            "    printf 'PID USER COMMAND\\n'\n"
            "    printf '%s alberto %s\\n' \"$PPID\" "
            "'/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes gateway run --replace'\n"
            "    ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)

    def run_tool(self, command, *extra, fake_tools=False):
        env = os.environ.copy()
        env["SPARTAN_WORKSTATION_STATE_DIR"] = str(self.state_dir)
        env["WORKSTATION_TEST_COMMAND_LOG"] = str(self.command_log)
        if fake_tools:
            env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        return subprocess.run(
            [
                "python3",
                TOOL,
                command,
                "--config",
                self.config,
                "--gateway",
                "127.0.0.2",
                *extra,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_validate_accepts_strict_private_schema(self):
        result = self.run_tool("validate")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("valid", result.stdout)

    def test_validate_rejects_non_tailnet_remote_and_unknown_fields(self):
        self.write_config(
            lambda payload: payload["remote"].update(
                {"host": "192.168.1.20", "unexpected": True}
            )
        )

        result = self.run_tool("validate")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown field", result.stderr)

        self.write_config(
            lambda payload: payload["remote"].update({"host": "192.168.1.20"})
        )
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        payload["remote"].pop("unexpected", None)
        self.config.write_text(json.dumps(payload), encoding="utf-8")
        result = self.run_tool("validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tailscale IPv4", result.stderr)

    def test_validate_rejects_wildcard_app_target_and_duplicate_gateway_port(self):
        def mutate(payload):
            payload["apps"][0]["remote_host"] = "0.0.0.0"
            payload["apps"][0]["gateway_port"] = payload["execution"]["gateway_port"]

        self.write_config(mutate)

        result = self.run_tool("validate")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remote_host must be 127.0.0.1", result.stderr)

        self.write_config(
            lambda payload: payload["apps"][0].update(
                {"gateway_port": payload["execution"]["gateway_port"]}
            )
        )
        result = self.run_tool("validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate gateway port", result.stderr)

    def test_validate_rejects_host_key_fingerprint_mismatch(self):
        self.write_config(
            lambda payload: payload["remote"].update(
                {"host_key_sha256": "SHA256:not-the-key"}
            )
        )

        result = self.run_tool("validate")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fingerprint does not match", result.stderr)

    def test_up_checks_live_host_key_before_creating_state_or_starting_relays(self):
        other_blob = base64.b64encode(b"different-host-key").decode()
        self.install_fake_tools(scanned_key=f"ssh-ed25519 {other_blob}")

        result = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("host key mismatch", result.stderr)
        self.assertFalse((self.state_dir / "state.json").exists())
        logged = self.command_log.read_text(encoding="utf-8")
        self.assertIn("100.100.100.100", logged)
        self.assertNotIn("TCP-LISTEN", logged)

    def test_up_status_down_use_scoped_relays_and_are_idempotent(self):
        self.write_config(
            lambda payload: payload.update(
                {
                    "terminal_broker": {
                        "enabled": True,
                        "container_name": "spartan_gate_hermes",
                    }
                }
            )
        )
        self.install_fake_tools()

        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("started", started.stdout)

        repeated = self.run_tool("up", fake_tools=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already running", repeated.stdout)

        status = self.run_tool("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("app-tunnel: running", status.stdout)
        self.assertIn("execution: running", status.stdout)
        self.assertIn("blender: running", status.stdout)
        self.assertIn("workstation-terminal: running", status.stdout)

        before_restart = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        before_pids = {
            entry["name"]: entry["pid"] for entry in before_restart["processes"]
        }
        restarted = self.run_tool("restart-terminal", fake_tools=True)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertIn("other bridges were unchanged", restarted.stdout)
        after_restart = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        after_pids = {
            entry["name"]: entry["pid"] for entry in after_restart["processes"]
        }
        self.assertNotEqual(
            before_pids["workstation-terminal"],
            after_pids["workstation-terminal"],
        )
        self.assertEqual(
            {key: value for key, value in before_pids.items() if key != "workstation-terminal"},
            {key: value for key, value in after_pids.items() if key != "workstation-terminal"},
        )

        commands = self.command_log.read_text(encoding="utf-8")
        self.assertIn("bind=127.0.0.2", commands)
        self.assertIn("TCP:100.100.100.100:22022", commands)
        self.assertIn(f"127.0.0.1:{self.app_local_port}:127.0.0.1:9876", commands)
        self.assertIn(" -M ", f" {commands} ")
        self.assertIn("-F /dev/null", commands)
        self.assertNotIn("ClearAllForwardings=yes", commands)
        self.assertNotIn("0.0.0.0", commands)
        self.assertNotIn("-D ", commands)

        state_mode = (self.state_dir / "state.json").stat().st_mode & 0o777
        known_hosts_mode = (
            self.state_dir / "known_hosts.execution"
        ).stat().st_mode & 0o777
        self.assertEqual(state_mode, 0o600)
        self.assertEqual(known_hosts_mode, 0o600)
        self.assertEqual(
            (self.state_dir / "workstation-terminal.sock").stat().st_mode & 0o777,
            0o600,
        )

        stopped = self.run_tool("down")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("stopped", stopped.stdout)

        repeated_stop = self.run_tool("down")
        self.assertEqual(repeated_stop.returncode, 0, repeated_stop.stderr)
        self.assertIn("not running", repeated_stop.stdout)

    def test_partial_start_rolls_back_every_process(self):
        self.install_fake_tools(fail_socat_port=9876)

        result = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to start", result.stderr)
        self.assertFalse((self.state_dir / "state.json").exists())

    def test_status_rejects_group_accessible_state_directory(self):
        self.state_dir.mkdir()
        self.state_dir.chmod(0o770)
        (self.state_dir / "state.json").write_text(
            json.dumps({"processes": []}), encoding="utf-8"
        )

        result = self.run_tool("status")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("accessible by group or other", result.stderr)


class WorkstationAliasTests(unittest.TestCase):
    def test_alias_group_is_discoverable_and_delegates_to_helper(self):
        if not TOOL.exists():
            self.fail(f"missing {TOOL}")
        source = ALIASES.read_text(encoding="utf-8")
        self.assertIn("sg-workstation", source)
        self.assertIn("sg_workstation()", source)
        self.assertIn('"$SPARTAN_GATE_DIR/scripts/workstation-bridge.py"', source)


if __name__ == "__main__":
    unittest.main()
