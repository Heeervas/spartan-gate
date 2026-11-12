from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime import (
    CACHE_VERSION,
    DEFAULT_VOICES,
    PocketTTSConfigurationError,
    expected_manifest,
    select_language,
    validate_active_cache,
)


class LanguageSelectionTests(unittest.TestCase):
    def test_spanish_markers_select_spanish(self):
        language, text = select_language("¿Puedes explicármelo, por favor?")
        self.assertEqual(language, "spanish")
        self.assertEqual(text, "¿Puedes explicármelo, por favor?")

    def test_english_words_select_english(self):
        language, _ = select_language("Please explain this with a short example.")
        self.assertEqual(language, "english")

    def test_common_spanish_greeting_selects_spanish(self):
        language, _ = select_language("Buenos dias, como estas?")
        self.assertEqual(language, "spanish")

    def test_directive_overrides_detection_and_is_removed(self):
        language, text = select_language("[es] This text must use the Spanish voice.")
        self.assertEqual(language, "spanish")
        self.assertEqual(text, "This text must use the Spanish voice.")

    def test_explicit_language_wins_without_directive(self):
        language, _ = select_language("Hola", "english")
        self.assertEqual(language, "english")


class CacheManifestTests(unittest.TestCase):
    def test_expected_manifest_pins_eponine_for_spanish(self):
        self.assertEqual(DEFAULT_VOICES, {"english": "anna", "spanish": "eponine"})
        self.assertEqual(expected_manifest()["voices"], DEFAULT_VOICES)

    def test_valid_active_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "versions" / CACHE_VERSION
            version.mkdir(parents=True)
            (version / "manifest.json").write_text(
                json.dumps(expected_manifest()), encoding="utf-8"
            )
            (root / "active").symlink_to(Path("versions") / CACHE_VERSION)
            self.assertEqual(validate_active_cache(root)["cache_version"], CACHE_VERSION)

    def test_old_release_and_changed_provenance_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "active").mkdir()
            path = root / "active" / "manifest.json"
            valid = expected_manifest()
            path.write_text(json.dumps(valid), encoding="utf-8")
            validate_active_cache(root)
            changes = {
                "pocket_tts_version": "2.1.0",
                "cache_version": "v2.1.0-en-anna-es-eponine-int8",
                "distribution": {"url": "https://example.invalid/wrong.whl", "sha256": "0" * 64},
                "models": {"english": "english", "spanish": "spanish_24l"},
                "quantization": "int4",
            }
            for key, value in changes.items():
                with self.subTest(field=key):
                    path.write_text(json.dumps(dict(valid, **{key: value})), encoding="utf-8")
                    with self.assertRaisesRegex(PocketTTSConfigurationError, f"mismatch for {key}"):
                        validate_active_cache(root)

    def test_manifest_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active"
            active.mkdir()
            manifest = expected_manifest()
            manifest["voices"] = {"english": "anna", "spanish": "lola"}
            (active / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaises(PocketTTSConfigurationError):
                validate_active_cache(root)


if __name__ == "__main__":
    unittest.main()
