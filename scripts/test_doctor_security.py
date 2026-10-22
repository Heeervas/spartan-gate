"""Exercise doctor only in a disposable public tree with synthetic Docker output."""
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest


DOCTOR = Path(__file__).with_name("doctor.sh")
CANARY = "synthetic-compose-canary"


class DoctorSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.scratch = self.root / "scratch"
        self.bin = self.root / "bin"
        self.scratch.mkdir()
        self.bin.mkdir()
        for name in (
            "apps/hermes-agent", "packages/clawroute", "infra/spartan-route",
            "infra/compose", "infra/caddy", "infra/dns", "infra/outbound-proxy",
            "infra/reader", "infra/browserless", "config/clawroute", "config/searxng",
            "private.example", "docs", "scripts",
        ):
            (self.repo / name).mkdir(parents=True)
        for name in (
            "README.md", ".env.example", "infra/compose/compose.yml",
            "infra/outbound-proxy/Dockerfile", "infra/outbound-proxy/entrypoint.sh",
            "packages/clawroute/package.json", "packages/clawroute/Dockerfile",
        ):
            (self.repo / name).touch()
        shutil.copyfile(DOCTOR, self.repo / "scripts/doctor.sh")
        self.write_stub("rg", "#!/bin/sh\nexit 1\n")
        self.write_stub("docker", """#!/bin/sh
case "$*" in
  'compose version'|'info') exit 0 ;;
  *' config')
    printf 'name: synthetic\nservices:\n  fixture:\n    environment:\n      CANARY: synthetic-compose-canary\n'
    : >"$READY"
    while test ! -e "$RELEASE"; do sleep 0.02; done
    exit "${COMPOSE_STATUS:-0}"
    ;;
  'network ls '*) exit 0 ;;
esac
exit 0
""")
        self.env = {
            **os.environ, "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root), "TMPDIR": str(self.scratch),
            "READY": str(self.root / "ready"), "RELEASE": str(self.root / "release"),
        }

    def write_stub(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def start(self, mask=0o022):
        process = subprocess.Popen(
            ["sh", str(self.repo / "scripts/doctor.sh")], env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, umask=mask,
        )
        self.addCleanup(self.stop, process)
        deadline = time.monotonic() + 3
        while not Path(self.env["READY"]).exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail(f"fixture did not reach Compose: {process.communicate(timeout=1)}")
            time.sleep(0.01)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=3)

    def finish(self, process, expected=0):
        Path(self.env["RELEASE"]).touch()
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, expected, stdout + stderr)
        return stdout

    def assert_private_render(self):
        renders = [p for p in self.scratch.rglob("*.yml") if CANARY in p.read_text()]
        self.assertTrue(renders, "Compose canary must actually reach a temporary file")
        for path in renders:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotEqual(path.parent, self.scratch)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_private_scratch_and_cleanup_under_both_umasks(self):
        for mask in (0o022, 0o077):
            with self.subTest(mask=mask):
                process = self.start(mask)
                self.assert_private_render()
                self.assertIn("doctor: ok", self.finish(process))
                self.assertEqual(list(self.scratch.iterdir()), [])
                Path(self.env["READY"]).unlink()
                Path(self.env["RELEASE"]).unlink()

    def test_predictable_symlink_is_untouched(self):
        victim = self.root / "victim"
        victim.write_text("unchanged")
        link = self.scratch / "spartan-gate-compose-base.yml"
        link.symlink_to(victim)
        process = self.start()
        self.finish(process)
        self.assertEqual(victim.read_text(), "unchanged")
        self.assertTrue(link.is_symlink())
        self.assertEqual(list(self.scratch.iterdir()), [link])

    def test_concurrent_runs_do_not_share_scratch(self):
        first = self.start()
        Path(self.env["READY"]).unlink()
        second = self.start()
        self.assert_private_render()
        self.assertEqual(len(list(self.scratch.glob("spartan-gate-doctor.*"))), 2)
        self.finish(first)
        self.finish(second)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_compose_error_cleans_confidential_output(self):
        self.env["COMPOSE_STATUS"] = "23"
        process = self.start()
        self.finish(process, expected=1)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_signal_cleanup(self):
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                process = self.start()
                process.send_signal(sig)
                process.communicate(timeout=3)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(list(self.scratch.iterdir()), [])
                Path(self.env["READY"]).unlink()

    def test_preflight_error_cleans_scratch(self):
        (self.repo / "README.md").unlink()
        result = subprocess.run(
            ["sh", str(self.repo / "scripts/doctor.sh")], env=self.env,
            capture_output=True, text=True, timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required file", result.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
