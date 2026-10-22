import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILES = (
    ROOT / "infra/compose/compose.yml",
    ROOT / "infra/compose/compose.dev.yml",
    ROOT / "infra/compose/compose.prod.example.yml",
    ROOT / "infra/compose/tiers/compose.l0.yml",
    ROOT / "infra/compose/tiers/compose.l1.yml",
    ROOT / "infra/compose/tiers/compose.l2.yml",
    ROOT / "infra/compose/tiers/compose.l3.yml",
    ROOT / "infra/compose/tiers/compose.spartan-route.yml",
)
EXPECTED_LOGGING = {
    "driver": "local",
    "options": {
        "max-size": "${SPARTAN_LOG_MAX_SIZE:-10m}",
        "max-file": "${SPARTAN_LOG_MAX_FILES:-3}",
    },
}


class ComposeLoggingContractTests(unittest.TestCase):
    def test_every_compose_service_has_bounded_logging(self):
        for path in COMPOSE_FILES:
            compose = yaml.safe_load(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.relative_to(ROOT), item="anchor"):
                self.assertEqual(EXPECTED_LOGGING, compose.get("x-spartan-logging"))
            for service_name, service in compose["services"].items():
                with self.subTest(
                    path=path.relative_to(ROOT), service=service_name
                ):
                    self.assertEqual(EXPECTED_LOGGING, service.get("logging"))


if __name__ == "__main__":
    unittest.main()
