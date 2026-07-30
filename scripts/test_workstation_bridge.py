import fcntl
import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
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
            "  inspect)\n"
            "    printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "
            "'container-test' 'sha256:image-test' 'true' "
            "'spartan-gate' 'hermes' 'False' 'config-hash'\n"
            "    ;;\n"
            "  top)\n"
            "    printf 'PID PPID USER COMMAND\\n'\n"
            "    printf '10 1 root /package/admin/s6/command/s6-svscan -d4 -- /run/service\\n'\n"
            "    printf '11 10 root s6-supervise gateway-default\\n'\n"
            "    printf '12 11 alberto %s\\n' "
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

    def stop_managed_process(self, state, name):
        entry = next(item for item in state["processes"] if item["name"] == name)
        os.killpg(entry["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 2
        while Path(f"/proc/{entry['pid']}").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{entry['pid']}").exists())

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

        repinned = self.run_tool("repin", fake_tools=True)
        self.assertEqual(repinned.returncode, 0, repinned.stderr)
        self.assertIn("trust baseline updated", repinned.stdout)

        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("started", started.stdout)

        repeated = self.run_tool("up", fake_tools=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already running", repeated.stdout)

        status = self.run_tool("status")
        self.assertEqual(status.returncode, 1, status.stderr)
        self.assertIn("app-tunnel: running", status.stdout)
        self.assertIn("execution: running", status.stdout)
        self.assertIn("blender: running", status.stdout)
        self.assertIn(
            "workstation-terminal: running (waiting-for-hermes)", status.stdout
        )
        health_path = self.state_dir / "workstation-terminal.health.json"
        health = json.loads(health_path.read_text(encoding="utf-8"))
        health["state"] = "ready"
        health_path.write_text(json.dumps(health), encoding="utf-8")
        health_path.chmod(0o600)
        ready_status = self.run_tool("status")
        self.assertEqual(ready_status.returncode, 0, ready_status.stderr)
        self.assertIn(
            "workstation-terminal: running (ready)", ready_status.stdout
        )

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
        trust = json.loads(
            (self.state_dir / "gateway-trust.json").read_text(encoding="utf-8")
        )
        self.assertEqual(trust["image_id"], "sha256:image-test")
        self.assertEqual(
            (self.state_dir / "gateway-trust.json").stat().st_mode & 0o777,
            0o600,
        )

        stopped = self.run_tool("down")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("stopped", stopped.stdout)

        repeated_stop = self.run_tool("down")
        self.assertEqual(repeated_stop.returncode, 0, repeated_stop.stderr)
        self.assertIn("not running", repeated_stop.stdout)

    def test_app_tunnel_supervisor_restarts_only_its_ssh_child(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        pids = {entry["name"]: entry["pid"] for entry in state["processes"]}
        health_path = self.state_dir / "app-tunnel.health.json"
        deadline = time.monotonic() + 2
        health = None
        while time.monotonic() < deadline:
            if health_path.exists():
                health = json.loads(health_path.read_text(encoding="utf-8"))
                if health.get("state") == "ready":
                    break
            time.sleep(0.05)
        self.assertIsNotNone(health)
        self.assertEqual(health["state"], "ready")
        old_child = health["child_pid"]

        os.kill(old_child, signal.SIGTERM)
        deadline = time.monotonic() + 3
        replacement = None
        while time.monotonic() < deadline:
            health = json.loads(health_path.read_text(encoding="utf-8"))
            if (
                health.get("state") == "ready"
                and health.get("child_pid") != old_child
            ):
                replacement = health["child_pid"]
                break
            time.sleep(0.05)

        self.assertIsNotNone(replacement)
        persisted = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        after = {
            entry["name"]: entry["pid"] for entry in persisted["processes"]
        }
        self.assertEqual(after, pids)
        self.assertTrue(Path(f"/proc/{replacement}").exists())

    def test_up_migrates_legacy_app_tunnel_without_restarting_peers(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state_path = self.state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        before = {entry["name"]: entry["pid"] for entry in state["processes"]}
        state.pop("implementation_version", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)

        migrated = self.run_tool("up", fake_tools=True)

        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.assertIn("app-tunnel migrated", migrated.stdout)
        after_state = json.loads(state_path.read_text(encoding="utf-8"))
        after = {
            entry["name"]: entry["pid"] for entry in after_state["processes"]
        }
        self.assertNotEqual(after["app-tunnel"], before["app-tunnel"])
        self.assertEqual(
            {key: value for key, value in after.items() if key != "app-tunnel"},
            {key: value for key, value in before.items() if key != "app-tunnel"},
        )
        self.assertEqual(after_state["implementation_version"], 2)

    def test_app_tunnel_child_dies_if_supervisor_is_sigkilled(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        supervisor = next(
            entry for entry in state["processes"] if entry["name"] == "app-tunnel"
        )
        health = json.loads(
            (self.state_dir / "app-tunnel.health.json").read_text(encoding="utf-8")
        )
        child_pid = health["child_pid"]
        try:
            os.kill(supervisor["pid"], signal.SIGKILL)
            deadline = time.monotonic() + 3
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())
        finally:
            if Path(f"/proc/{child_pid}").exists():
                os.kill(child_pid, signal.SIGKILL)

    def test_down_reconciles_a_published_orphan_app_tunnel_child(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        supervisor = next(
            entry for entry in state["processes"] if entry["name"] == "app-tunnel"
        )
        orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            orphan_start_time = (
                Path(f"/proc/{orphan.pid}/stat")
                .read_text(encoding="utf-8")
                .rsplit(")", 1)[1]
                .split()[19]
            )
            os.kill(supervisor["pid"], signal.SIGKILL)
            deadline = time.monotonic() + 3
            while (
                Path(f"/proc/{supervisor['pid']}").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            health_path = self.state_dir / "app-tunnel.health.json"
            health_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "state": "starting",
                        "child_pid": orphan.pid,
                        "child_start_time": orphan_start_time,
                        "restart_count": 0,
                        "retry_seconds": 0,
                        "updated_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            health_path.chmod(0o600)

            stopped = self.run_tool("down", fake_tools=True)

            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            orphan.wait(timeout=3)
            self.assertIsNotNone(orphan.returncode)
        finally:
            if orphan.poll() is None:
                orphan.kill()
                orphan.wait(timeout=2)

    def test_down_recovers_legacy_supervisor_without_state_file(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        health = json.loads(
            (self.state_dir / "app-tunnel.health.json").read_text(encoding="utf-8")
        )
        supervisor_pid = health.pop("supervisor_pid")
        health.pop("supervisor_start_time")
        health_path = self.state_dir / "app-tunnel.health.json"
        health_path.write_text(json.dumps(health), encoding="utf-8")
        health_path.chmod(0o600)
        for entry in state["processes"]:
            if entry["name"] != "app-tunnel":
                self.stop_managed_process(state, entry["name"])
        (self.state_dir / "state.json").unlink()

        stopped = self.run_tool("down", fake_tools=True)

        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("Orphaned", stopped.stdout)
        self.assertFalse(Path(f"/proc/{supervisor_pid}").exists())
        self.assertFalse(Path(f"/proc/{health['child_pid']}").exists())

    def test_mutating_commands_fail_fast_while_operation_lock_is_held(self):
        self.state_dir.mkdir(mode=0o700)
        lock_path = self.state_dir / "operation.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

            blocked = self.run_tool("down")

            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("already running", blocked.stderr)
        finally:
            os.close(descriptor)

    def test_partial_start_rolls_back_every_process(self):
        self.install_fake_tools(fail_socat_port=9876)

        result = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to start", result.stderr)
        self.assertFalse((self.state_dir / "state.json").exists())

    def test_up_reconciles_matching_partial_state_as_one_managed_set(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        before = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        before_pids = {entry["name"]: entry["pid"] for entry in before["processes"]}
        self.stop_managed_process(before, "app-tunnel")
        marker = "preserve prior diagnostics\n"
        with (self.state_dir / "app-tunnel.log").open("a", encoding="utf-8") as handle:
            handle.write(marker)

        recovered = self.run_tool("up", fake_tools=True)

        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("recovered from partial state", recovered.stdout)
        after = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        after_pids = {entry["name"]: entry["pid"] for entry in after["processes"]}
        self.assertEqual(set(before_pids), set(after_pids))
        self.assertTrue(
            all(after_pids[name] != before_pids[name] for name in before_pids)
        )
        self.assertIn(
            marker.strip(),
            (self.state_dir / "app-tunnel.log").read_text(encoding="utf-8"),
        )
        status = self.run_tool("status")
        self.assertEqual(status.returncode, 0, status.stderr)

    def test_partial_recovery_preflight_failure_preserves_survivors(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        self.stop_managed_process(state, "app-tunnel")
        survivor_pids = {
            entry["name"]: entry["pid"]
            for entry in state["processes"]
            if entry["name"] != "app-tunnel"
        }
        other_blob = base64.b64encode(b"different-host-key").decode()
        self.install_fake_tools(scanned_key=f"ssh-ed25519 {other_blob}")

        recovered = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("host key mismatch", recovered.stderr)
        self.assertTrue(
            all(Path(f"/proc/{pid}").exists() for pid in survivor_pids.values())
        )
        persisted = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted, state)

    def test_partial_recovery_rejects_changed_configuration_without_stopping(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        self.stop_managed_process(state, "app-tunnel")
        survivor_pids = {
            entry["name"]: entry["pid"]
            for entry in state["processes"]
            if entry["name"] != "app-tunnel"
        }
        self.write_config(
            lambda payload: payload["apps"][0].update(
                {"local_port": free_loopback_port()}
            )
        )

        recovered = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("different configuration", recovered.stderr)
        self.assertTrue(
            all(Path(f"/proc/{pid}").exists() for pid in survivor_pids.values())
        )

    def test_partial_recovery_failure_rolls_back_and_clears_runtime_state(self):
        self.install_fake_tools()
        started = self.run_tool("up", fake_tools=True)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8")
        )
        self.stop_managed_process(state, "app-tunnel")
        self.install_fake_tools(fail_socat_port=9876)

        recovered = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("failed to start", recovered.stderr)
        self.assertFalse((self.state_dir / "state.json").exists())
        self.assertFalse((self.state_dir / "app-tunnel.control").exists())
        self.assertFalse((self.state_dir / "workstation-terminal.sock").exists())

    def test_status_rejects_group_accessible_state_directory(self):
        self.state_dir.mkdir()
        self.state_dir.chmod(0o770)
        (self.state_dir / "state.json").write_text(
            json.dumps({"processes": []}), encoding="utf-8"
        )

        result = self.run_tool("status")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("accessible by group or other", result.stderr)

    def test_up_requires_explicit_gateway_trust_baseline(self):
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

        result = self.run_tool("up", fake_tools=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sg-workstation repin", result.stderr)
        self.assertFalse((self.state_dir / "state.json").exists())


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
