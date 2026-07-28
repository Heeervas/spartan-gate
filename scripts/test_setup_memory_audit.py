import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "setup-memory-audit.sh"


class MemoryAuditInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo with space"
        self.scripts = self.root / "scripts"
        self.docs = self.root / "docs"
        self.bin_dir = Path(self.temporary.name) / "bin"
        self.home = Path(self.temporary.name) / "home"
        self.systemd_dir = Path(self.temporary.name) / "systemd"
        self.runtime_dir = Path(self.temporary.name) / "runtime"
        self.state_home = Path(self.temporary.name) / "state"
        self.systemctl_log = Path(self.temporary.name) / "systemctl.log"
        for directory in (self.scripts, self.docs, self.bin_dir, self.home):
            directory.mkdir(parents=True, exist_ok=True)
        (self.docs / "operations.md").write_text("operations\n", encoding="utf-8")
        (self.scripts / "setup-memory-audit.sh").write_bytes(INSTALLER.read_bytes())
        (self.scripts / "setup-memory-audit.sh").chmod(0o755)
        (self.scripts / "memory-status.py").write_text(
            "#!/usr/bin/env python3\nprint('Memory audit: healthy')\n",
            encoding="utf-8",
        )
        (self.scripts / "memory-status.py").chmod(0o755)

        systemctl = self.bin_dir / "systemctl"
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >>\"$SYSTEMCTL_LOG\"\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "XDG_RUNTIME_DIR": str(self.runtime_dir),
                "XDG_STATE_HOME": str(self.state_home),
                "SPARTAN_MEMORY_SYSTEMD_USER_DIR": str(self.systemd_dir),
                "SPARTAN_MEMORY_SYSTEMCTL": str(systemctl),
                "SYSTEMCTL_LOG": str(self.systemctl_log),
            }
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_installer(self, command="install"):
        return subprocess.run(
            [self.scripts / "setup-memory-audit.sh", command],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_install_writes_hardened_read_only_daily_units_and_is_idempotent(self):
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        service_path = self.systemd_dir / "spartan-gate-memory-audit.service"
        timer_path = self.systemd_dir / "spartan-gate-memory-audit.timer"
        service = service_path.read_text(encoding="utf-8")
        timer = timer_path.read_text(encoding="utf-8")
        combined = service + timer

        self.assertIn("memory-status.py\" collect", service)
        self.assertIn(
            "WorkingDirectory=" + str(self.root).replace(" ", "\\x20"),
            service,
        )
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=read-only", service)
        self.assertNotIn("ProtectKernelModules", service)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", service)
        self.assertIn("OnCalendar=*-*-* 04:20:00", timer)
        self.assertIn("RandomizedDelaySec=10m", timer)
        self.assertIn("Persistent=true", timer)
        for forbidden in (
            "swapoff",
            "drop_caches",
            "docker restart",
            "docker system prune",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertEqual(stat.S_IMODE(service_path.stat().st_mode), 0o644)
        self.assertEqual(
            stat.S_IMODE((self.state_home / "spartan-gate").stat().st_mode),
            0o700,
        )

        expected_service = service
        expected_timer = timer
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(service_path.read_text(encoding="utf-8"), expected_service)
        self.assertEqual(timer_path.read_text(encoding="utf-8"), expected_timer)

        calls = self.systemctl_log.read_text(encoding="utf-8")
        self.assertIn("--user daemon-reload", calls)
        self.assertIn("--user enable --now spartan-gate-memory-audit.timer", calls)
        self.assertIn("--user start spartan-gate-memory-audit.service", calls)

    def test_unsafe_second_target_fails_before_first_unit_write(self):
        self.systemd_dir.mkdir(parents=True)
        timer = self.systemd_dir / "spartan-gate-memory-audit.timer"
        timer.symlink_to(Path(self.temporary.name) / "elsewhere")

        result = self.run_installer()

        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing symlink unit target", result.stderr)
        self.assertFalse(
            (self.systemd_dir / "spartan-gate-memory-audit.service").exists()
        )


if __name__ == "__main__":
    unittest.main()
