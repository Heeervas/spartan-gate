import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


PATH = Path(__file__).parent / "patches/freeze_image_features.py"
SPEC = importlib.util.spec_from_file_location("freeze_image_features", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ImageFeaturesTests(unittest.TestCase):
    def test_preserves_actual_inventory_without_enabling_absent_features(self):
        inventory = Mock(return_value=["web", "cron", "discord", "mcp", "telegram", "web"])
        writer = Mock()
        root = Path("/image")
        extras = MODULE.freeze_features(root, inventory=inventory, writer=writer)
        inventory.assert_called_once_with(root, root / ".venv", python_exe=root / ".venv/bin/python")
        writer.assert_called_once_with(["cron", "discord", "mcp", "telegram", "web"], root)
        self.assertNotIn("all", extras)

    def test_inventory_failure_never_writes_metadata(self):
        writer = Mock()
        with self.assertRaises(RuntimeError):
            MODULE.freeze_features(Path("/image"), inventory=Mock(side_effect=RuntimeError("probe failed")), writer=writer)
        writer.assert_not_called()

    def test_incomplete_or_malformed_inventory_never_writes_metadata(self):
        for extras in ([], None, "discord", ["discord"], [None], [" "], list(MODULE.REQUIRED_FEATURES) + [7]):
            with self.subTest(extras=extras):
                writer = Mock()
                with self.assertRaises(ValueError):
                    MODULE.freeze_features(Path("/image"), inventory=Mock(return_value=extras), writer=writer)
                writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
