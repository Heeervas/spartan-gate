from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.config import AUTO_ROUTING_TIERS, load_config

GPT56_MODELS = {
    "codex/gpt-5.6",
    "codex/gpt-5.6-sol",
    "codex/gpt-5.6-terra",
    "codex/gpt-5.6-luna",
}


class ConfigTests(unittest.TestCase):
    def test_astra_is_selectable_without_changing_defaults(self) -> None:
        config = load_config({"SPARTAN_ROUTE_CONFIG_DIR": "/missing"})
        astra = config.models["codex/gpt-6-astra"]
        self.assertEqual(astra.provider, "codex")
        self.assertEqual(astra.max_context, 872_000)
        self.assertTrue(astra.tool_capable)
        self.assertTrue(astra.multimodal)
        self.assertNotEqual(config.baseline_model, astra.id)
        self.assertFalse(any(astra.id in tier.values() for tier in config.tier_models.values()))

    def test_auto_routing_tiers_match_the_classifier_surface(self) -> None:
        self.assertEqual(AUTO_ROUTING_TIERS, ("heartbeat", "simple", "moderate", "complex"))

    def test_parallel_default_db_path_does_not_reuse_clawroute_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "default.json").write_text(
                json.dumps({
                    "providerProfile": "codex",
                    "baselineModel": "codex/gpt-5.5",
                    "logging": {"dbPath": "./data/clawroute.db"},
                }),
                encoding="utf-8",
            )
            (config_dir / "clawroute.json").write_text("{}", encoding="utf-8")
            (config_dir / "providers").mkdir()
            (config_dir / "providers" / "codex.json").write_text("{}", encoding="utf-8")

            config = load_config({"SPARTAN_ROUTE_CONFIG_DIR": str(config_dir), "CLAWROUTE_TOKEN": "secret"})

        self.assertEqual(config.db_path, "./data/spartan-route.db")
        self.assertEqual(config.baseline_model, "codex/gpt-5.5")
        self.assertEqual(config.token, "secret")
        self.assertEqual(config.tier_models["moderate"], {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"})
        for model_id in GPT56_MODELS:
            with self.subTest(model_id=model_id):
                self.assertIn(model_id, config.models)
                self.assertEqual(config.models[model_id].provider, "codex")
                self.assertEqual(config.models[model_id].max_context, 1_050_000)
                self.assertTrue(config.models[model_id].tool_capable)
                self.assertTrue(config.models[model_id].multimodal)

    def test_explicit_spartan_route_db_path_wins(self) -> None:
        config = load_config({
            "SPARTAN_ROUTE_CONFIG_DIR": "/missing",
            "SPARTAN_ROUTE_DB_PATH": "/tmp/custom.db",
        })
        self.assertEqual(config.db_path, "/tmp/custom.db")

    def test_telemetry_runtime_bounds_are_configurable_and_clamped(self) -> None:
        config = load_config({
            "SPARTAN_ROUTE_CONFIG_DIR": "/missing",
            "SPARTAN_ROUTE_TELEMETRY_QUEUE_CAPACITY": "999999",
            "SPARTAN_ROUTE_TELEMETRY_RETENTION_BATCH_SIZE": "0",
            "SPARTAN_ROUTE_TELEMETRY_SHUTDOWN_TIMEOUT_MS": "250",
        })

        self.assertEqual(config.telemetry_queue_capacity, 8192)
        self.assertEqual(config.telemetry_retention_batch_size, 1)
        self.assertEqual(config.telemetry_shutdown_timeout_ms, 250)

        defaults = load_config({"SPARTAN_ROUTE_CONFIG_DIR": "/missing"})
        self.assertEqual(defaults.telemetry_queue_capacity, 1024)
        self.assertEqual(defaults.telemetry_retention_batch_size, 256)
        self.assertEqual(defaults.telemetry_shutdown_timeout_ms, 5000)



if __name__ == "__main__":
    unittest.main()
