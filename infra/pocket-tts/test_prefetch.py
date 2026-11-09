from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prefetch
from runtime import CACHE_VERSION, PocketTTSConfigurationError, PocketTTSProfile, expected_manifest


class PrefetchAtomicityTests(unittest.TestCase):
    def test_cache_seed_hardlinks_blobs_but_copies_mutable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_target = root / "versions" / "old"
            model_cache = active_target / "huggingface" / "hub" / "models--example"
            blobs = model_cache / "blobs"
            refs = model_cache / "refs"
            blobs.mkdir(parents=True)
            refs.mkdir()
            source_blob = blobs / "model"
            source_ref = refs / "main"
            source_blob.write_bytes(b"immutable model")
            source_ref.write_text("old-revision", encoding="utf-8")
            (root / "active").symlink_to(Path("versions") / "old")
            staging = root / "versions" / ".staging"
            staging.mkdir()

            self.assertTrue(prefetch.seed_huggingface_cache(root, staging))

            seeded = staging / "huggingface" / "hub" / "models--example"
            seeded_blob = seeded / "blobs" / "model"
            seeded_ref = seeded / "refs" / "main"
            self.assertEqual(source_blob.stat().st_ino, seeded_blob.stat().st_ino)
            self.assertNotEqual(source_ref.stat().st_ino, seeded_ref.stat().st_ino)
            seeded_ref.write_text("new-revision", encoding="utf-8")
            self.assertEqual(source_ref.read_text(encoding="utf-8"), "old-revision")

    def test_cache_seed_rejects_active_target_outside_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            (root / "versions").mkdir(parents=True)
            outside = base / "outside" / "huggingface"
            outside.mkdir(parents=True)
            (root / "active").symlink_to(Path("..") / "outside")
            staging = root / "versions" / ".staging"
            staging.mkdir()

            self.assertFalse(prefetch.seed_huggingface_cache(root, staging))
            self.assertFalse((staging / "huggingface").exists())

    def test_invalid_active_cache_survives_failed_replacement_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "versions" / CACHE_VERSION
            target.mkdir(parents=True)
            (target / "manifest.json").write_text(
                json.dumps({"cache_version": "invalid"}), encoding="utf-8"
            )
            (root / "active").symlink_to(Path("versions") / CACHE_VERSION)

            with mock.patch.object(
                prefetch,
                "require_free_space",
                side_effect=RuntimeError("simulated low disk"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated low disk"):
                    prefetch.run_prefetch(root, 1, 1)

            self.assertTrue(target.is_dir())
            self.assertTrue((root / "active").is_symlink())
            self.assertEqual((root / "active").resolve(), target.resolve())
            self.assertEqual(
                json.loads((root / "active" / "manifest.json").read_text()),
                {"cache_version": "invalid"},
            )

    def test_successful_replacement_switches_active_before_removing_stale_target(self):
        class FakeAudio:
            def numel(self):
                return 1

        class FakeModel:
            def generate_audio(self, voice_state, text):
                return FakeAudio()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / "versions" / CACHE_VERSION
            stale.mkdir(parents=True)
            (stale / "manifest.json").write_text(
                json.dumps({"cache_version": "invalid"}), encoding="utf-8"
            )
            (root / "active").symlink_to(Path("versions") / CACHE_VERSION)

            with (
                mock.patch.object(prefetch, "require_free_space"),
                mock.patch.object(prefetch, "configure_huggingface_cache"),
                mock.patch.object(
                    prefetch,
                    "load_language_model",
                    return_value=(FakeModel(), {}),
                ),
            ):
                manifest = prefetch.run_prefetch(root, 1, 1)

            self.assertEqual(manifest["cache_version"], CACHE_VERSION)
            self.assertNotEqual((root / "active").resolve(), stale.resolve())
            self.assertFalse(stale.exists())
            self.assertEqual(prefetch.valid_manifest(root / "active"), manifest)

    def test_alternative_prefetch_uses_matching_profile_and_retains_legacy_version(self):
        model = mock.Mock()
        model.generate_audio.return_value.numel.return_value = 1
        profile = PocketTTSProfile("spanish_24l", "lola")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "versions" / CACHE_VERSION
            legacy.mkdir(parents=True)
            (legacy / "manifest.json").write_text(json.dumps(expected_manifest(PocketTTSProfile())))
            (root / "active").symlink_to(Path("versions") / CACHE_VERSION)
            with (mock.patch.dict(os.environ, {"POCKET_TTS_SPANISH_MODEL": "spanish_24l",
                                              "POCKET_TTS_SPANISH_VOICE": "lola"}),
                  mock.patch.object(prefetch, "require_free_space"),
                  mock.patch.object(prefetch, "configure_huggingface_cache"),
                  mock.patch.object(prefetch, "load_language_model", return_value=(model, {})) as load):
                manifest = prefetch.run_prefetch(root, 1, 1)
            self.assertEqual(load.call_args_list, [mock.call("english", "anna", profile=profile),
                                                  mock.call("spanish", "lola", profile=profile)])
            self.assertEqual(manifest["models"]["spanish"], "spanish_24l")
            self.assertEqual((root / "active").resolve(), root / "versions" / profile.cache_version)
            self.assertTrue((legacy / "manifest.json").is_file())

    def test_invalid_selection_fails_before_creating_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "cache"
            with mock.patch.dict(os.environ, {"POCKET_TTS_SPANISH_MODEL": "unapproved"}):
                with self.assertRaisesRegex(PocketTTSConfigurationError, "unsupported"):
                    prefetch.run_prefetch(root, 1, 1)
            self.assertFalse(root.exists())

    def test_valid_active_cache_is_reused_without_prefetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "versions" / f"{CACHE_VERSION}-replacement"
            version.mkdir(parents=True)
            manifest = expected_manifest()
            manifest["created_at_unix"] = 1
            (version / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (root / "active").symlink_to(
                Path("versions") / f"{CACHE_VERSION}-replacement"
            )

            with mock.patch.object(prefetch, "require_free_space") as disk_check:
                self.assertEqual(prefetch.run_prefetch(root, 1, 1), manifest)
            disk_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
