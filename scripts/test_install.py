import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

def path_mode(path):
    return stat.S_IMODE(path.stat().st_mode)



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
if [[ "$1" == "run" && "$3" == caddy:2-alpine@sha256:* ]]; then
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

    def test_retired_addon_fails_before_any_writes(self):
        result = self.run_install("L1", "linux", "--with", "clawroute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retired", result.stderr)
        self.assertFalse(self.private_dir.exists())
        self.assertFalse(self.data_root.exists())

    def test_invalid_compose_does_not_publish_candidate(self):
        docker = self.bin_dir / "docker"
        source = docker.read_text()
        docker.write_text(source.replace('if [[ "$1" == "compose" ]]; then exit 0; fi',
                                        'if [[ "$*" == *"config --quiet"* ]]; then exit 42; fi\nif [[ "$1" == "compose" ]]; then exit 0; fi'))
        result = self.run_install("L2")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.private_dir / "env/local.env").exists())
        self.assertFalse((self.private_dir / "install-state.json").exists())

    def test_publication_permission_failure_restores_existing_selection(self):
        first = self.run_install('L2')
        self.assertEqual(first.returncode, 0, first.stderr)
        paths = [self.private_dir / 'env/local.env', self.private_dir / 'install-state.json']
        before = {path: path.read_bytes() for path in paths}
        chmod = self.bin_dir / 'chmod'
        chmod.write_text('#!/bin/sh\nexit 1\n')
        chmod.chmod(0o755)
        result = self.run_install('L3')
        self.assertNotEqual(result.returncode, 0)
        for path, expected in before.items():
            self.assertEqual(path.read_bytes(), expected)

    def test_l0_generates_private_env_with_real_caddy_hash(self):
        result = self.run_install("L0", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["SPARTAN_TIER"], "L0")
        self.assertEqual(values["SPARTAN_ADDONS"], "")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "free")
        self.assertEqual(values["COMPOSE_PROJECT_NAME"], "spartan-gate")
        self.assertEqual(values["CAMOFOX_URL"], "")
        self.assertEqual(values["CAMOFOX_USER_ID"], "")
        self.assertEqual(values["CAMOFOX_SESSION_KEY"], "")
        self.assertEqual(values["CAMOFOX_ADOPT_EXISTING_TAB"], "false")
        self.assertIn("HERMES_API_KEY", values)
        self.assertTrue((self.data_root / "current" / "hermes").is_dir())
        self.assertEqual(values["CADDY_AUTH_HASH"], "$2a$test-generated-caddy-hash")
        self.assertIn("SPARTAN_CADDY_PASSWORD", values)
        self.assertNotIn("BROWSERLESS_EDGE_TOKEN", values)
        self.assertIn("CADDY_CAMOFOX_EDGE_TOKEN", values)
        self.assertIn("CADDY_WS_EDGE_TOKEN", values)
        self.assertRegex(values["CADDY_CAMOFOX_EDGE_TOKEN"], r"^[0-9a-f]{64}$")
        self.assertRegex(values["CADDY_WS_EDGE_TOKEN"], r"^[0-9a-f]{64}$")
        self.assertNotIn(values["HERMES_API_KEY"], result.stdout)
        self.assertNotIn(values["HERMES_GATEWAY_TOKEN"], result.stdout)
        self.assertNotIn(values["SPARTAN_CADDY_PASSWORD"], result.stdout)

        for key in (
            "SPARTAN_CADDY_UID",
            "SPARTAN_ROUTE_UID",
        ):
            self.assertEqual(values[key], str(os.getuid()), key)
        for key in (
            "SPARTAN_CADDY_GID",
            "SPARTAN_ROUTE_GID",
        ):
            self.assertEqual(values[key], str(os.getgid()), key)
        self.assertEqual(path_mode(self.private_dir), 0o700)
        self.assertEqual(path_mode(self.private_dir / "env"), 0o700)
        self.assertEqual(path_mode(self.private_dir / "env" / "local.env"), 0o600)
        self.assertEqual(path_mode(self.private_dir / "install-state.json"), 0o600)
        for key in (
            "SPARTAN_HERMES_DATA_PATH",
            "SPARTAN_ROUTE_DATA_PATH",
            "SPARTAN_GOGCLI_DATA_PATH",
            "SPARTAN_CAMOFOX_DATA_PATH",
            "SPARTAN_CODEX_HOME_PATH",
        ):
            path = Path(values[key])
            self.assertEqual(path_mode(path), 0o700, key)
        self.assertNotIn("SPARTAN_BROWSERLESS_PROFILES_PATH", values)

    def test_browser_tiers_leave_global_camofox_identity_unowned(self):
        for tier in ("L1", "L2", "L4"):
            with self.subTest(tier=tier):
                result = self.run_install(tier, "linux")

                self.assertEqual(result.returncode, 0, result.stderr)
                values = self.read_env()
                self.assertEqual(values["CAMOFOX_URL"], "http://camofox:9377")
                self.assertEqual(values["CAMOFOX_USER_ID"], "")
                self.assertEqual(values["CAMOFOX_SESSION_KEY"], "")
                self.assertEqual(values["CAMOFOX_ADOPT_EXISTING_TAB"], "false")

    def test_rerun_preserves_explicit_analytics_mode(self):
        first = self.run_install("L2", "linux")
        self.assertEqual(first.returncode, 0, first.stderr)
        env_file = self.private_dir / "env" / "local.env"
        source = env_file.read_text(encoding="utf-8")
        env_file.write_text(
            source.replace("CAMOFOX_DISABLE_DEFAULT_ADDONS='false'", "CAMOFOX_DISABLE_DEFAULT_ADDONS='true'"),
            encoding="utf-8",
        )

        second = self.run_install("L2", "linux")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.read_env()["CAMOFOX_DISABLE_DEFAULT_ADDONS"], "true")
        self.assertEqual(
            env_file.read_text(encoding="utf-8").count("CAMOFOX_DISABLE_DEFAULT_ADDONS="),
            1,
        )

    def test_install_fails_if_private_env_chmod_fails(self):
        chmod = self.bin_dir / "chmod"
        chmod.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        chmod.chmod(0o755)

        result = self.run_install("L0", "linux")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(" compose ", self.log.read_text(encoding="utf-8"))

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

    def test_existing_caddy_hash_without_plaintext_is_preserved_without_inventing_password(self):
        env_dir = self.private_dir / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "local.env").write_text(
            "CADDY_AUTH_USER='admin'\nCADDY_AUTH_HASH='$2a$existing-private-hash'\n",
            encoding="utf-8",
        )

        result = self.run_install("L2", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["CADDY_AUTH_HASH"], "$2a$existing-private-hash")
        self.assertNotIn("SPARTAN_CADDY_PASSWORD", values)

    def test_invalid_edge_tokens_are_rotated_to_valid_distinct_values(self):
        env_dir = self.private_dir / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "local.env").write_text(
            "CADDY_CAMOFOX_EDGE_TOKEN=short\n"
            "CADDY_WS_EDGE_TOKEN=GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG\n",
            encoding="utf-8",
        )

        result = self.run_install("L2", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertRegex(values["CADDY_CAMOFOX_EDGE_TOKEN"], r"^[0-9a-f]{64}$")
        self.assertRegex(values["CADDY_WS_EDGE_TOKEN"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(values["CADDY_CAMOFOX_EDGE_TOKEN"], values["CADDY_WS_EDGE_TOKEN"])

    def test_equal_valid_edge_tokens_are_separated(self):
        env_dir = self.private_dir / "env"
        env_dir.mkdir(parents=True)
        token = "a" * 64
        (env_dir / "local.env").write_text(
            f"CADDY_CAMOFOX_EDGE_TOKEN={token}\nCADDY_WS_EDGE_TOKEN={token}\n",
            encoding="utf-8",
        )

        result = self.run_install("L2", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_env()
        self.assertEqual(values["CADDY_WS_EDGE_TOKEN"], token)
        self.assertRegex(values["CADDY_CAMOFOX_EDGE_TOKEN"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(values["CADDY_CAMOFOX_EDGE_TOKEN"], token)

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

    def test_install_preserves_cloudflare_api_token_without_printing_it(self):
        env_dir = self.private_dir / "env"
        env_dir.mkdir(parents=True)
        token = "private-cloudflare-token"
        (env_dir / "local.env").write_text(
            f"CLOUDFLARE_API_TOKEN={token}\n",
            encoding="utf-8",
        )

        result = self.run_install("L1", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_env()["CLOUDFLARE_API_TOKEN"], token)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_install_does_not_recreate_retired_browser_profiles(self):
        result = self.run_install("L1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.data_root / "current/browserless-profiles").exists())
        self.assertNotIn("BROWSERLESS_IMAGE", self.read_env())

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
        self.assertEqual(values["SPARTAN_ADDONS"], "")
        self.assertEqual(values["SPARTAN_HERMES_MODE"], "gated")

    def test_persisted_retired_addon_requires_explicit_migration(self):
        env_file = self.private_dir / "env/local.env"
        env_file.parent.mkdir(parents=True)
        original = "SPARTAN_ADDONS=clawroute\n"
        env_file.write_text(original)
        result = self.run_install("L3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retired", result.stderr)
        self.assertEqual(env_file.read_text(), original)
        self.assertFalse((self.private_dir / "install-state.json").exists())

    def test_l4_generates_minimal_private_compose_without_tailscale(self):
        result = self.run_install("L4", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        private_compose = self.private_dir / "compose.local.yml"
        self.assertTrue(private_compose.is_file())
        self.assertTrue((self.private_dir / "outbound-proxy" / "whitelist.private.txt").is_file())
        self.assertEqual(path_mode(private_compose), 0o600)
        self.assertEqual(
            path_mode(self.private_dir / "outbound-proxy" / "whitelist.private.txt"),
            0o600,
        )
        self.assertEqual(path_mode(self.private_dir / "caddy.local.d"), 0o700)
        content = private_compose.read_text(encoding="utf-8")
        self.assertIn("SPARTAN_BIND_LOCALHOST", content)
        self.assertIn("spartan-route:", content)
        self.assertIn("SPARTAN_ROUTE_DATA_PATH", content)
        self.assertNotIn("  clawroute:", content)
        self.assertNotIn("TAILSCALE_IP", content)
        self.assertNotIn("/absolute/path", content)
        values = self.read_env()
        self.assertEqual(values["SPARTAN_ADDONS"], "")
        self.assertEqual(values["COMPOSE_PROFILES"], "camofox")
        self.assertIn("SPARTAN_ROUTE_DATA_PATH", values)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("infra/compose/compose.yml" in call and str(private_compose) in call for call in calls))

    def test_l4_install_does_not_auto_configure_spartan_reader(self):
        result = self.run_install("L4", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("web/spartan-reader", calls)
        self.assertNotIn("extract_backend", calls)

    def test_l4_rejects_legacy_clawroute_addon(self):
        result = self.run_install("L4", "linux", "--with", "clawroute")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retired", result.stderr)

    def test_non_l4_install_does_not_configure_spartan_reader(self):
        result = self.run_install("L3", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("web/spartan-reader", calls)

    def test_l4_existing_hermes_config_skips_spartan_reader_auto_config(self):
        hermes_home = self.data_root / "current" / "hermes"
        hermes_home.mkdir(parents=True)
        (hermes_home / "config.yaml").write_text("web:\n  extract_backend: firecrawl\n", encoding="utf-8")

        result = self.run_install("L4", "linux")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("web/spartan-reader", calls)


if __name__ == "__main__":
    unittest.main()
