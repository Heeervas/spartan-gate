from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "infra" / "compose" / "compose.yml"


class PocketTTSComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    def test_runtime_has_only_dedicated_internal_network(self):
        service = self.compose["services"]["pocket-tts"]
        self.assertEqual(service["networks"], ["pocket_tts_internal"])
        self.assertNotIn("ports", service)
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["user"], "10001:10001")
        self.assertNotIn("HTTP_PROXY", service.get("environment", {}))
        self.assertNotIn("HTTPS_PROXY", service.get("environment", {}))
        self.assertNotIn("POCKET_TTS_ENGLISH_VOICE", service["environment"])
        self.assertNotIn("POCKET_TTS_SPANISH_VOICE", service["environment"])

    def test_prefetch_uses_gate_network_but_runtime_does_not(self):
        runtime = self.compose["services"]["pocket-tts"]
        prefetch = self.compose["services"]["pocket-tts-prefetch"]
        self.assertNotIn("spartan_internal", runtime["networks"])
        self.assertEqual(prefetch["networks"], ["spartan_internal"])
        self.assertEqual(prefetch["environment"]["HTTP_PROXY"], "http://proxy:8888")
        self.assertNotIn("HF_TOKEN", prefetch.get("environment", {}))

    def test_hermes_gets_baked_client_and_private_tts_network(self):
        hermes = self.compose["services"]["hermes"]
        self.assertIn("pocket_tts_internal", hermes["networks"])
        self.assertNotIn("pocket-tts", hermes["depends_on"])
        self.assertFalse(
            any(
                "pocket-tts-client.py" in (
                    volume if isinstance(volume, str)
                    else str(volume.get("target", ""))
                )
                for volume in hermes.get("volumes", [])
            )
        )
        dockerfile = (ROOT / "apps" / "hermes-agent" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "COPY --chown=0:0 browserless-profile-live.js browserless-cdp-broker.py "
            "pocket-tts-client.py wrangler-auth.py /opt/hermes/bootstrap/",
            dockerfile,
        )
        self.assertIn(
            "find /opt/hermes/bootstrap -maxdepth 1 -type f -exec chmod 0644 {} +",
            dockerfile,
        )

    def test_dedicated_network_is_internal(self):
        self.assertTrue(self.compose["networks"]["pocket_tts_internal"]["internal"])

    def test_runtime_base_image_is_digest_pinned(self):
        dockerfile = (ROOT / "infra" / "pocket-tts" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "FROM python:3.13-slim-bookworm@sha256:"
            "dd86541a59b252667f4c12f8b2ee17216de37dd65ac773bf097bef996fa78860",
            dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
