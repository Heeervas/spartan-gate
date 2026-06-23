import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.private_dir = self.root / "private"
        self.data_root = self.root / "data"
        self.bin_dir = self.root / "bin"
        self.log = self.root / "docker.log"
        self.bin_dir.mkdir()
        docker = self.bin_dir / "docker"
        docker.write_text(
            f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {self.log}
if [[ "$1" == "compose" && "$2" == "version" ]]; then exit 0; fi
if [[ "$1" == "info" ]]; then exit 0; fi
if [[ "$1" == "run" && "$3" == "caddy:2-alpine" ]]; then
  printf '$2a$test-generated-caddy-hash\\n'
  exit 0
fi
if [[ "$1" == "compose" ]]; then exit 0; fi
printf 'unexpected docker invocation: %s\\n' "$*" >&2
exit 2
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_install(self, tier, platform="linux", *extra_args):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env["SPARTAN_INSTALL_PRIVATE_DIR"] = str(self.private_dir)
        env["SPARTAN_INSTALL_DATA_ROOT"] = str(self.data_root / "current")
        env["SPARTAN_INSTALL_PLATFORM"] = platform
        return subprocess.run(
            [ROOT / "scripts" / "install.sh", "--tier", tier, *extra_args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_env(self):
        values = {}
        for line in (self.private_dir / "env" / "local.env").read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip("'\"")
        return values

    def test_l0_generates_private_env_without_caddy_hash_container(self):
        result = self.run_install("L0", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["SPARTAN_TIER"], "L0")
        self.assertEqual(values["SPARTAN_ADDONS"], "")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "free")
        self.assertEqual(values["COMPOSE_PROJECT_NAME"], "spartan-gate")
        self.assertEqual(values["CAMOFOX_URL"], "")
        self.assertIn("HERMES_API_KEY", values)
        self.assertTrue((self.data_root / "current" / "hermes").is_dir())
        self.assertNotIn(values["HERMES_API_KEY"], result.stdout)
        self.assertNotIn(values["HERMES_GATEWAY_TOKEN"], result.stdout)
        self.assertNotIn("caddy:2-alpine", self.log.read_text(encoding="utf-8"))

    def test_l2_generates_caddy_hash_and_keeps_plaintext_private(self):
        result = self.run_install("L2", "macos")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["SPARTAN_TIER"], "L2")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "gated")
        self.assertEqual(values["CADDY_AUTH_HASH"], "$2a$test-generated-caddy-hash")
        self.assertIn("SPARTAN_CADDY_PASSWORD", values)
        self.assertNotIn(values["SPARTAN_CADDY_PASSWORD"], result.stdout)
        self.assertIn('"platform": "macos"', (self.private_dir / "install-state.json").read_text(encoding="utf-8"))

    def test_install_is_idempotent_for_existing_secret_values(self):
        first = self.run_install("L1", "wsl")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.read_env()

        second = self.run_install("L1", "wsl")
        self.assertEqual(second.returncode, 0, second.stderr)
        after = self.read_env()

        for key in ("HERMES_API_KEY", "HERMES_GATEWAY_TOKEN", "CAMOFOX_API_KEY", "CAMOFOX_VNC_PASSWORD"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["SPARTAN_HERMES_DATA_PATH"], before["SPARTAN_HERMES_DATA_PATH"])
        self.assertIn('"platform": "wsl"', (self.private_dir / "install-state.json").read_text(encoding="utf-8"))

    def test_runs_setup_before_start_and_hermes_recreate(self):
        result = self.run_install("L3", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        setup = next(i for i, call in enumerate(calls) if " run --rm --no-deps hermes setup" in call)
        start = next(i for i, call in enumerate(calls) if call.endswith(" up -d --remove-orphans"))
        recreate = next(i for i, call in enumerate(calls) if call.endswith(" up -d --force-recreate hermes"))
        self.assertLess(setup, start)
        self.assertLess(start, recreate)
        self.assertTrue(any("compose.l2.yml" in call and "compose.l3.yml" in call for call in calls))
        values = self.read_env()
        self.assertEqual(values["SPARTAN_ADDONS"], "clawroute")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "gated")

    def test_l1_can_enable_clawroute_addon_without_changing_project_or_data_root(self):
        result = self.run_install("L1", "linux", "--with", "clawroute")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["SPARTAN_TIER"], "L1")
        self.assertEqual(values["SPARTAN_ADDONS"], "clawroute")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "free")
        self.assertEqual(values["COMPOSE_PROJECT_NAME"], "spartan-gate")
        self.assertEqual(values["SPARTAN_HERMES_DATA_PATH"], str(self.data_root / "current" / "hermes"))
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("compose.l1.yml" in call and "compose.clawroute.yml" in call for call in calls))

    def test_l4_generates_minimal_private_compose_without_tailscale(self):
        result = self.run_install("L4", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        private_compose = self.private_dir / "compose.local.yml"
        self.assertTrue(private_compose.is_file())
        self.assertTrue((self.private_dir / "outbound-proxy" / "whitelist.private.txt").is_file())
        content = private_compose.read_text(encoding="utf-8")
        self.assertIn("SPARTAN_BIND_LOCALHOST", content)
        self.assertNotIn("TAILSCALE_IP", content)
        self.assertNotIn("/absolute/path", content)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("infra/compose/compose.yml" in call and str(private_compose) in call for call in calls))


if __name__ == "__main__":
    unittest.main()
