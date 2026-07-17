from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


SCRIPT = Path(__file__).with_name("configure-pocket-tts.py")
SPEC = importlib.util.spec_from_file_location("configure_pocket_tts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ConfigurePocketTTSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.yaml"
        self.client = self.root / "client.py"
        self.client.write_text("print('client')\n", encoding="utf-8")
        self.config.write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": "custom"},
                    "tts": {
                        "provider": "edge",
                        "edge": {"voice": "en-US-AriaNeural"},
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_enable_is_idempotent_and_preserves_original_backup(self):
        original = self.config.read_bytes()
        self.assertTrue(MODULE.enable(self.config, self.client))
        backup = MODULE.backup_path(self.config)
        self.assertEqual(backup.read_bytes(), original)

        first_enabled = self.config.read_bytes()
        self.assertFalse(MODULE.enable(self.config, self.client))
        self.assertEqual(self.config.read_bytes(), first_enabled)
        self.assertEqual(backup.read_bytes(), original)

        enabled = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        self.assertEqual(enabled["model"], {"provider": "custom"})
        self.assertEqual(enabled["tts"]["provider"], "pocket-tts")
        self.assertEqual(
            enabled["tts"]["providers"]["pocket-tts"],
            MODULE.PROVIDER_CONFIG,
        )

    def test_disable_restores_previous_provider_without_clobbering_other_config(self):
        MODULE.enable(self.config, self.client)
        enabled = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        enabled["new_setting"] = {"preserve": True}
        self.config.write_text(yaml.safe_dump(enabled, sort_keys=False), encoding="utf-8")

        self.assertTrue(MODULE.disable(self.config))
        disabled = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        self.assertEqual(disabled["tts"]["provider"], "edge")
        self.assertNotIn("providers", disabled["tts"])
        self.assertEqual(disabled["new_setting"], {"preserve": True})

    def test_invalid_tts_shape_fails_before_backup_or_write(self):
        self.config.write_text("tts: invalid\n", encoding="utf-8")
        original = self.config.read_bytes()
        with self.assertRaises(MODULE.ConfigurationError):
            MODULE.enable(self.config, self.client)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(MODULE.backup_path(self.config).exists())

    def test_enable_rolls_back_config_when_state_write_fails(self):
        original = self.config.read_bytes()
        with mock.patch.object(
            MODULE,
            "atomic_write_json",
            side_effect=OSError("simulated state write failure"),
        ):
            with self.assertRaises(MODULE.ConfigurationError):
                MODULE.enable(self.config, self.client)

        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(MODULE.state_path(self.config).exists())


if __name__ == "__main__":
    unittest.main()
