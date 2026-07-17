import os
import subprocess
import tempfile
from unittest import mock

from scripts import migrate_caddy_safari_session as migration
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_caddy_safari_session.py"
TEMPLATE = ROOT / "private.example" / "caddy" / "Caddyfile.https.example"
TOKEN = "b" * 64


class CaddySafariMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.env = self.root / "local.env"
        self.caddy = self.root / "Caddyfile.https"
        self.ports = self.root / "ports.caddy"
        self.env.write_text(
            "SPARTAN_EDGE_SCHEME=https\n"
            f"CADDY_WS_EDGE_TOKEN='{TOKEN}'\n",
            encoding="utf-8",
        )
        self.caddy.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        self.ports.write_text(self.legacy_port(12000) + "\n\n" + self.legacy_port(12001) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def legacy_port(port):
        return f"""# spartan-gate:add-port:{port}
:{port} {{
\ttls {{$SPARTAN_CADDY_TLS_CERT_FILE:/certs/cert.pem}} {{$SPARTAN_CADDY_TLS_KEY_FILE:/certs/key.pem}}
\t@spartan_app_websocket {{
\t\theader Connection *Upgrade*
\t\theader Upgrade websocket
\t}}
\t@spartan_app_websocket_authorized {{
\t\tvars {{cookie.spartan_ws_edge}} {{$CADDY_WS_EDGE_TOKEN}}
\t}}
\thandle @spartan_app_websocket_authorized {{
\t\treverse_proxy http://hermes:{port}
\t}}
\thandle @spartan_app_websocket {{
\t\trespond "Unauthorized" 401
\t}}
\thandle {{
\t\tbasic_auth {{
\t\t\t{{$CADDY_AUTH_USER:admin}} {{$CADDY_AUTH_HASH}}
\t\t}}
\t\treverse_proxy http://hermes:{port}
\t}}
}}"""

    def run_migration(self, validate="/bin/true", recreate="/bin/true"):
        return subprocess.run(
            [
                SCRIPT,
                "--env", self.env,
                "--caddy", self.caddy,
                "--ports", self.ports,
                "--template", TEMPLATE,
                "--validate-command", validate,
                "--recreate-command", recreate,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def snapshot(self):
        return tuple(path.read_bytes() for path in (self.env, self.caddy, self.ports))

    def test_success_installs_all_candidates_together(self):
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.env.read_text(encoding="utf-8")
        caddy = self.caddy.read_text(encoding="utf-8")
        ports = self.ports.read_text(encoding="utf-8")
        self.assertRegex(env, r"CADDY_CAMOFOX_EDGE_TOKEN='[0-9a-f]{64}'")
        self.assertNotIn(f"CADDY_CAMOFOX_EDGE_TOKEN='{TOKEN}'", env)
        self.assertIn("spartan_edge_camofox", caddy)
        self.assertIn("spartan_edge_apps", ports)
        self.assertEqual(ports.count("vars {cookie.spartan_edge_apps}"), 2)
        self.assertIn("; Secure", ports)
        for path in (self.env, self.caddy, self.ports):
            self.assertTrue(path.with_name(path.name + ".safari-session.bak").is_file())

    def test_invalid_preflight_does_not_write_any_file(self):
        self.env.write_text("CADDY_WS_EDGE_TOKEN=short\n", encoding="utf-8")
        before = self.snapshot()
        result = self.run_migration()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_validation_failure_restores_every_installed_file(self):
        before = self.snapshot()
        result = self.run_migration(validate="/bin/false")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_second_replace_failure_restores_first_file(self):
        before = self.snapshot()
        candidates = migration.build_candidates(self.env, self.caddy, self.ports, TEMPLATE)
        real_replace = os.replace
        calls = 0

        def fail_second_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second replace failure")
            return real_replace(source, destination)

        with mock.patch.object(migration.os, "replace", side_effect=fail_second_replace):
            with self.assertRaises(migration.MigrationError):
                migration.install_transaction(candidates, ["/bin/true"], ["/bin/true"])

        self.assertEqual(self.snapshot(), before)

    def test_unmanaged_content_in_port_pool_fails_before_writes(self):
        self.ports.write_text(self.ports.read_text(encoding="utf-8") + "unexpected\n", encoding="utf-8")
        before = self.snapshot()
        result = self.run_migration()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged content", result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
