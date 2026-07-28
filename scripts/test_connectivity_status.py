import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_TOOL = ROOT / "scripts" / "connectivity-status.py"


def healthy_payload(**overrides):
    payload = {
        "version": 1,
        "observed_at": int(time.time()),
        "local": "up",
        "wan": "up",
        "dns": "up",
        "tailscale": "healthy",
        "local_failures": 0,
        "transport_failures": 0,
        "tailscale_failures": 0,
        "last_wifi_recovery_at": None,
        "last_tailscale_restart_at": None,
    }
    payload.update(overrides)
    return payload


class ConnectivityStatusToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.status_file = Path(self.temp_dir.name) / "status.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_tool(self, command, *extra):
        return subprocess.run(
            [
                "python3",
                STATUS_TOOL,
                command,
                "--status-file",
                self.status_file,
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_status(self, payload):
        self.status_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_show_reports_sanitized_healthy_status(self):
        self.write_status(healthy_payload())

        result = self.run_tool("show")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Host connectivity: healthy", result.stdout)
        self.assertIn("local=up", result.stdout)
        self.assertIn("wan=up", result.stdout)
        self.assertIn("dns=up", result.stdout)
        self.assertIn("tailscale=healthy", result.stdout)

    def test_show_is_informational_when_status_is_missing_or_invalid(self):
        missing = self.run_tool("show")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("unavailable", missing.stdout)

        self.status_file.write_text("{not-json", encoding="utf-8")
        invalid = self.run_tool("show")
        self.assertEqual(invalid.returncode, 0, invalid.stderr)
        self.assertIn("invalid", invalid.stdout)

    def test_check_fails_closed_for_missing_invalid_stale_or_degraded_status(self):
        missing = self.run_tool("check")
        self.assertEqual(missing.returncode, 1)

        self.write_status(healthy_payload(interface="wlan0"))
        extra_field = self.run_tool("check")
        self.assertEqual(extra_field.returncode, 1)
        self.assertIn("invalid", extra_field.stdout)

        self.write_status(healthy_payload(observed_at=int(time.time()) - 301))
        stale = self.run_tool("check", "--max-age", "300")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("stale", stale.stdout)

        self.write_status(
            healthy_payload(
                local="transport-down",
                wan="down",
                transport_failures=2,
            )
        )
        degraded = self.run_tool("check")
        self.assertEqual(degraded.returncode, 1)
        self.assertIn("degraded", degraded.stdout)
        self.assertIn("transport=2", degraded.stdout)

    def test_check_accepts_icmp_filtered_when_https_and_dns_are_healthy(self):
        self.write_status(healthy_payload(wan="icmp-filtered"))

        result = self.run_tool("check")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Host connectivity: healthy", result.stdout)


class ConnectivityAliasTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "aliases.sh", self.root / "scripts")
        shutil.copy2(STATUS_TOOL, self.root / "scripts")
        shutil.copy2(
            ROOT / "scripts" / "spartan-route-data.py",
            self.root / "scripts",
        )
        self.status_file = self.root / "status.json"
        self.status_file.write_text(
            json.dumps(healthy_payload()),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_sourced(self, command):
        env = os.environ.copy()
        env["SPARTAN_CONNECTIVITY_STATUS_FILE"] = str(self.status_file)
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sg_health_adds_host_connectivity_without_masking_hermes_failure(self):
        result = self.run_sourced(
            """
sg_status() { return 9; }
docker() { return 0; }
sg_hermes_exec() { return 7; }
sg_health
"""
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("Host connectivity: healthy", result.stdout)

    def test_strict_connectivity_alias_is_discoverable_and_executable(self):
        result = self.run_sourced(
            "sg-help; sg-connectivity-check; declare -F sg_connectivity_check"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sg-connectivity-check", result.stdout)
        self.assertIn("Host connectivity: healthy", result.stdout)
        self.assertIn("sg_connectivity_check", result.stdout)


if __name__ == "__main__":
    unittest.main()
