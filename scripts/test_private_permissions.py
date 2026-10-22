import json
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "private-permissions.py"


def load_permissions_module():
    spec = importlib.util.spec_from_file_location("private_permissions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


class PrivatePermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.private = self.root / "private"
        self.env_dir = self.private / "env"
        self.env_file = self.env_dir / "local.env"
        self.env_dir.mkdir(parents=True)

        self.data = self.root / "data"
        self.hermes = self.data / "hermes"
        self.route = self.data / "spartan-route"
        self.clawroute = self.data / "clawroute"
        self.browserless = self.data / "browserless-profiles"
        self.camofox = self.data / "camofox"
        self.codex = self.data / "codex-home"
        self.caddy_certs = self.data / "caddy-certs"
        for path in (
            self.hermes,
            self.route,
            self.clawroute,
            self.browserless,
            self.camofox,
            self.codex,
            self.caddy_certs,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.write_env()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_env(self):
        self.env_file.write_text(
            "\n".join(
                (
                    "SECRET_VALUE='never-print-this-value'",
                    f"SPARTAN_HERMES_DATA_PATH='{self.hermes}'",
                    f"SPARTAN_ROUTE_DATA_PATH='{self.route}'",
                    f"SPARTAN_CLAWROUTE_DATA_PATH='{self.clawroute}'",
                    f"SPARTAN_BROWSERLESS_PROFILES_PATH='{self.browserless}'",
                    f"SPARTAN_CAMOFOX_DATA_PATH='{self.camofox}'",
                    f"SPARTAN_CODEX_HOME_PATH='{self.codex}'",
                    f"SPARTAN_CADDY_CERTS_PATH='{self.caddy_certs}'",
                    f"BROWSERLESS_PROFILES_GID='{os.getgid()}'",
                    f"SPARTAN_CADDY_UID='{os.getuid()}'",
                    f"SPARTAN_CADDY_GID='{os.getgid()}'",
                    f"SPARTAN_ROUTE_UID='{os.getuid()}'",
                    f"SPARTAN_ROUTE_GID='{os.getgid()}'",
                    f"SPARTAN_CLAWROUTE_UID='{os.getuid()}'",
                    f"SPARTAN_CLAWROUTE_GID='{os.getgid()}'",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def run_script(self, action: str, *extra: str):
        return subprocess.run(
            (
                sys.executable,
                SCRIPT,
                action,
                "--private-dir",
                self.private,
                "--env-file",
                self.env_file,
                *extra,
            ),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_reports_unsafe_metadata_without_paths_or_values(self):
        self.env_file.chmod(0o664)

        result = self.run_script("check")

        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("unsafe mode", output)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("never-print-this-value", output)

    def test_fix_applies_service_specific_modes_without_store_traversal(self):
        compose = self.private / "compose.local.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        policy_dir = self.private / "outbound-proxy"
        policy_dir.mkdir()
        policy = policy_dir / "whitelist.private.txt"
        policy.write_text("private.example\n", encoding="utf-8")
        caddy_dir = self.private / "caddy"
        caddy_dir.mkdir()
        backup_key = caddy_dir / "tls.key.backup"
        backup_key.write_text("not-a-real-key\n", encoding="utf-8")

        backup_root = self.private / "backups" / "tailscale-https"
        backup_dir = backup_root / "generation"
        backup_dir.mkdir(parents=True)
        archived_key = backup_dir / "key.pem"
        archived_key.write_text("not-a-real-key\n", encoding="utf-8")

        profile_cookie = self.browserless / "cookie-store"
        profile_cookie.write_text("credential-state\n", encoding="utf-8")
        codex_auth = self.codex / "auth.json"
        codex_auth.write_text("credential-state\n", encoding="utf-8")

        for path in (self.private, self.env_dir, policy_dir, caddy_dir, backup_root, backup_dir):
            path.chmod(0o755)
        for path in (self.env_file, compose, policy, backup_key, archived_key):
            path.chmod(0o664)
        for path in (
            self.hermes,
            self.route,
            self.clawroute,
            self.browserless,
            self.camofox,
            self.codex,
            self.caddy_certs,
        ):
            path.chmod(0o775)
        profile_cookie.chmod(0o666)
        codex_auth.chmod(0o666)
        snapshot = self.root / "permission-snapshot.json"

        result = self.run_script("fix", "--snapshot", str(snapshot))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mode(self.private), 0o700)
        self.assertEqual(mode(self.env_dir), 0o700)
        self.assertEqual(mode(policy_dir), 0o700)
        self.assertEqual(mode(caddy_dir), 0o700)
        for path in (self.env_file, compose, policy, backup_key, archived_key):
            self.assertEqual(mode(path), 0o600)
        for path in (
            self.hermes,
            self.route,
            self.camofox,
            self.codex,
            self.caddy_certs,
        ):
            self.assertEqual(mode(path), 0o700)
        self.assertEqual(mode(backup_dir), 0o700)
        self.assertEqual(mode(backup_root), 0o755)
        self.assertEqual(mode(self.browserless), 0o775)
        self.assertEqual(mode(self.clawroute), 0o775)
        self.assertEqual(self.browserless.stat().st_gid, os.getgid())
        self.assertEqual(mode(profile_cookie), 0o666)
        self.assertEqual(mode(codex_auth), 0o666)
        self.assertEqual(mode(snapshot), 0o600)
        self.assertTrue(json.loads(snapshot.read_text(encoding="utf-8"))["targets"])

        check = self.run_script("check")
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_service_specific_identities_own_router_and_certificate_roots(self):
        values = self.env_file.read_text(encoding="utf-8")
        values += "\n".join(
            (
                "SPARTAN_CADDY_UID='4101'",
                "SPARTAN_CADDY_GID='4102'",
                "SPARTAN_ROUTE_UID='4201'",
                "SPARTAN_ROUTE_GID='4202'",
                "SPARTAN_CLAWROUTE_UID='4301'",
                "SPARTAN_CLAWROUTE_GID='4302'",
                "",
            )
        )
        self.env_file.write_text(values, encoding="utf-8")

        permissions = load_permissions_module()
        targets = {
            target.label: target
            for target in permissions.collect_targets(self.private, self.env_file)
        }

        expected = {
            "SPARTAN_CADDY_CERTS_PATH": (4101, 4102),
            "SPARTAN_ROUTE_DATA_PATH": (4201, 4202),
        }
        for label, identity in expected.items():
            with self.subTest(label=label):
                target = targets[label]
                self.assertEqual(
                    (target.expected_uid, target.expected_gid),
                    identity,
                )

    def test_symlink_preflight_fails_before_any_mode_change(self):
        self.env_file.chmod(0o664)
        caddy_dir = self.private / "caddy.local.d"
        caddy_dir.mkdir()
        outside = self.root / "outside.conf"
        outside.write_text("private data\n", encoding="utf-8")
        (caddy_dir / "linked.conf").symlink_to(outside)

        result = self.run_script("fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(mode(self.env_file), 0o664)
        output = result.stdout + result.stderr
        self.assertIn("symlink", output)
        self.assertNotIn(str(self.root), output)
    def test_missing_configured_root_fails_before_mode_changes(self):
        self.env_file.chmod(0o664)
        self.route.rmdir()

        result = self.run_script("fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(mode(self.env_file), 0o664)
        output = result.stdout + result.stderr
        self.assertIn("target is missing", output)
        self.assertNotIn(str(self.root), output)


    def test_exempt_backup_parent_symlink_is_rejected_before_changes(self):
        self.env_file.chmod(0o664)
        backups = self.private / "backups"
        backups.mkdir()
        outside = self.root / "outside-backups"
        outside.mkdir()
        marker = outside / "key.pem"
        marker.write_text("credential-state\n", encoding="utf-8")
        marker.chmod(0o666)
        (backups / "tailscale-https").symlink_to(outside, target_is_directory=True)

        result = self.run_script("fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(mode(self.env_file), 0o664)
        self.assertEqual(mode(marker), 0o666)
        output = result.stdout + result.stderr
        self.assertIn("symlink", output)
        self.assertNotIn(str(self.root), output)

    def test_configured_store_root_symlink_is_rejected_without_traversal(self):
        real_profiles = self.root / "real-profiles"
        real_profiles.mkdir()
        self.camofox.rmdir()
        self.camofox.symlink_to(real_profiles, target_is_directory=True)
        marker = real_profiles / "must-not-change"
        marker.write_text("credential-state\n", encoding="utf-8")
        marker.chmod(0o666)

        result = self.run_script("fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(mode(marker), 0o666)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_failed_fix_rolls_back_earlier_mode_changes(self):
        permissions = load_permissions_module()
        first = self.root / "first"
        second = self.root / "second"
        first.write_text("one\n", encoding="utf-8")
        second.write_text("two\n", encoding="utf-8")
        first.chmod(0o644)
        second.chmod(0o644)
        targets = [
            permissions.Target(first, "first", 0o600, os.getuid(), os.getgid(), False),
            permissions.Target(second, "second", 0o600, os.getuid(), os.getgid(), False),
        ]
        states = permissions.preflight(targets)
        calls = 0

        def fail_second_chmod(path, target_mode):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("injected chmod failure")
            os.chmod(path, target_mode)

        with self.assertRaises(permissions.PermissionPolicyError) as raised:
            permissions.apply_fixes(
                targets,
                states,
                chmod_func=fail_second_chmod,
                chown_func=lambda *_args: None,
            )

        self.assertIn("prior metadata restored", raised.exception.message)
        self.assertEqual(mode(first), 0o644)
        self.assertEqual(mode(second), 0o644)


    def test_doctor_checks_private_permissions_before_private_values(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        permission_check = source.find("private-permissions.py check")
        private_validation = source.find("validate_private_env private/env/local.env")

        self.assertGreaterEqual(permission_check, 0)
        self.assertGreater(private_validation, permission_check)
        self.assertIn("--private-dir private", source)
        self.assertIn("--env-file private/env/local.env", source)

    def test_doctor_does_not_traverse_ignored_credential_stores(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        migration = (ROOT / "scripts" / "hermes_spartan_route_hot_migrate.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("-path './private'", source)
        self.assertIn("-path './runtime'", source)
        self.assertIn("Defaults to the legacy Hermes home.", migration)
        self.assertNotIn('$key=$value', source)


if __name__ == "__main__":
    unittest.main()
