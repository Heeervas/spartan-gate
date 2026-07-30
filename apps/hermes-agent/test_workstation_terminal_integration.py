from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class WorkstationTerminalIntegrationTests(unittest.TestCase):
    def test_image_applies_scoped_approval_patch(self):
        dockerfile = (ROOT / "apps/hermes-agent/Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "RUN python3 /opt/hermes/patches/patch_workstation_approval_scope.py",
            dockerfile,
        )
        self.assertLess(
            dockerfile.index("COPY --chown=0:0 --chmod=a+rX,go-w patches/"),
            dockerfile.index("patch_workstation_approval_scope.py"),
        )

    def test_compose_mounts_only_named_plugin_read_only(self):
        compose = (ROOT / "infra/compose/compose.yml").read_text(encoding="utf-8")
        mount = (
            "../hermes-plugins/spartan-workstation-terminal:"
            "/opt/data/plugins/spartan-workstation-terminal:ro"
        )
        self.assertIn(mount, compose)
        self.assertNotIn(":/opt/data/plugins:ro", compose)
        # The public stack must not gain a workstation socket, port, host
        # network, or execution-key mount. Those are explicit private wiring.
        self.assertNotIn("SPARTAN_WORKSTATION_BROKER_SOCKET", compose)
        self.assertNotIn("workstation-terminal.sock", compose)

    def test_plugin_manifest_and_docs_do_not_create_a_profile(self):
        manifest = (
            ROOT
            / "infra/hermes-plugins/spartan-workstation-terminal/plugin.yaml"
        ).read_text(encoding="utf-8")
        docs = (ROOT / "docs/workstation.md").read_text(encoding="utf-8")
        self.assertIn("name: spartan-workstation-terminal", manifest)
        self.assertIn("without creating a", docs)
        self.assertNotIn("profile-new workstation", docs)
        self.assertNotIn("TERMINAL_ENV=ssh", docs)

    def test_example_keeps_broker_opt_in(self):
        example = (
            ROOT / "private.example/workstation/bridges.json"
        ).read_text(encoding="utf-8")
        self.assertIn('"terminal_broker"', example)
        self.assertIn('"enabled": false', example)


if __name__ == "__main__":
    unittest.main()
