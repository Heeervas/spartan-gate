import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CAMOFOX = ROOT / "infra" / "camofox"
PIN = (
    "ghcr.io/jo-inc/camofox-browser:1.14.0@sha256:"
    "86c79eed8a6b3a78859f73bc70d6003c5566b85e969354ec454524b28197ffce"
)


class CamofoxPatchServerTests(unittest.TestCase):
    def test_modern_adapter_is_fail_closed_and_drops_legacy_migration(self):
        patch = (CAMOFOX / "patch-server.js").read_text()
        self.assertIn("SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1", patch)
        self.assertIn("createPageWithRecoveryForUser", patch)
        self.assertIn("closeLeasedPage(late.session, late.page, late.lease)", patch)
        self.assertIn("never scan Firefox temp profiles while a browser is live", patch)
        self.assertIn(
            "NAVIGATION_TIMEOUT_ACTIONS.has(action) && proxyPool?.canRotateSessions",
            patch,
        )
        self.assertIn("unsupported Camofox upstream source", patch)
        self.assertNotIn("const spartanCreateLimitMarker", patch)

    def test_unsupported_source_is_not_partially_written(self):
        original = "import fs from 'fs';\nlet browser = null;\n"
        with tempfile.TemporaryDirectory() as directory:
            server = pathlib.Path(directory) / "server.js"
            server.write_text(original)
            result = subprocess.run(
                ["node", str(CAMOFOX / "patch-server.js")],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(server.read_text(), original)

    def test_every_compose_shape_pins_1_14_and_wires_safe_default(self):
        compose_files = [
            ROOT / "infra" / "compose" / "compose.yml",
            ROOT / "infra" / "compose" / "tiers" / "compose.l1.yml",
            ROOT / "infra" / "compose" / "tiers" / "compose.l2.yml",
        ]
        for compose in compose_files:
            with self.subTest(compose=compose):
                source = compose.read_text()
                self.assertIn(PIN, source)
                self.assertIn(
                    "CAMOFOX_DISABLE_DEFAULT_ADDONS: "
                    "${CAMOFOX_DISABLE_DEFAULT_ADDONS:-false}",
                    source,
                )

    def test_dockerfile_uses_native_vnc_and_asserts_modern_patch_markers(self):
        dockerfile = (CAMOFOX / "Dockerfile").read_text()
        self.assertIn(PIN, dockerfile)
        self.assertIn("ENV CAMOFOX_DISABLE_DEFAULT_ADDONS=false", dockerfile)
        self.assertIn("SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1", dockerfile)
        self.assertIn("SPARTAN_PERSISTENCE_DURABILITY_V2", dockerfile)
        self.assertIn("SPARTAN_OPERATOR_LEASE_V2", dockerfile)
        self.assertNotIn("delete config.plugins.vnc.enabled", dockerfile)

    def test_pristine_upstream_chain_when_source_is_supplied(self):
        source = os.environ.get("CAMOFOX_UPSTREAM_SOURCE")
        if not source:
            self.skipTest("set CAMOFOX_UPSTREAM_SOURCE to pristine Camofox v1.14.0")
        result = subprocess.run(
            ["node", "--test", str(CAMOFOX / "test_patch_chain_upstream.js")],
            cwd=ROOT,
            env={**os.environ, "CAMOFOX_UPSTREAM_SOURCE": source},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
