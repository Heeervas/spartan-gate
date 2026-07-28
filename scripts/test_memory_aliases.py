import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def healthy_status():
    return {
        "version": 1,
        "observed_at": int(time.time()),
        "window_seconds": 86400,
        "state": "healthy",
        "reasons": [],
        "current": {
            "mem_available_bytes": 3 * 1024**3,
            "swap_total_bytes": 2 * 1024**3,
            "swap_used_bytes": 1024**3,
            "memory_psi_full_avg60": 0.0,
        },
        "history": {
            "available": True,
            "sample_count": 12,
            "avg_host_cpu_percent": 20.0,
            "peak_host_cpu_percent": 70.0,
            "avg_load1": 1.0,
            "peak_load1": 2.0,
            "min_mem_available_bytes": 2 * 1024**3,
            "avg_swap_used_percent": 50.0,
            "peak_swap_used_percent": 80.0,
            "max_swap_in_bytes_per_second": 0,
            "max_swap_out_bytes_per_second": 0,
            "peak_iowait_percent": 2.0,
            "critical_swap_out_low_memory_samples": 0,
        },
        "contract": {
            "active": True,
            "size_ok": True,
            "mode_ok": True,
            "swappiness_ok": True,
            "sysctl_persistent": True,
            "fstab_persistent": True,
        },
        "containers": [],
        "host_oom_events": 0,
    }


class MemoryAliasTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in (
            "aliases.sh",
            "memory-status.py",
            "connectivity-status.py",
            "spartan-route-data.py",
        ):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        self.status_file = self.root / "memory-status.json"
        self.state_file = self.root / "memory-state.json"
        self.status_file.write_text(json.dumps(healthy_status()), encoding="utf-8")
        self.env = os.environ.copy()
        self.env["SPARTAN_MEMORY_STATUS_FILE"] = str(self.status_file)
        self.env["SPARTAN_MEMORY_STATE_FILE"] = str(self.state_file)
        self.env["SPARTAN_CONNECTIVITY_STATUS_FILE"] = str(
            self.root / "missing-connectivity.json"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_sourced(self, command):
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_memory_alias_is_discoverable_and_strict_check_executes(self):
        result = self.run_sourced("sg-help; sg-memory; sg-memory check")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sg-memory [show|check|collect]", result.stdout)
        self.assertEqual(result.stdout.count("Memory audit: healthy"), 2)

    def test_sg_health_appends_memory_without_masking_hermes_failure(self):
        result = self.run_sourced(
            """
sg_status() { return 9; }
docker() { return 0; }
sg_hermes_exec() { return 7; }
sg_health
"""
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("Memory audit: healthy", result.stdout)

    def test_unknown_memory_subcommand_is_rejected(self):
        result = self.run_sourced("sg-memory purge")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage: sg-memory", result.stderr)


if __name__ == "__main__":
    unittest.main()
