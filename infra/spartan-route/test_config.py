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


if __name__ == "__main__":
    unittest.main()
