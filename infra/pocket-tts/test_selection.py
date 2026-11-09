from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

import runtime


class SpanishSelectionTests(unittest.TestCase):
    def test_legacy_profile_keeps_cache_and_manifest_contract(self):
        profile = runtime.PocketTTSProfile()
        self.assertEqual(profile.cache_version, runtime.CACHE_VERSION)
        manifest = runtime.expected_manifest(profile)
        self.assertNotIn("models", manifest)
        self.assertEqual(manifest["voices"], {"english": "anna", "spanish": "eponine"})

    def test_candidate_selections_have_distinct_caches_and_reject_legacy_cache(self):
        profiles = [runtime.PocketTTSProfile(model, voice)
                    for model in ("spanish", "spanish_24l")
                    for voice in ("eponine", "lola")]
        self.assertEqual(len({p.cache_version for p in profiles}), 4)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "active").mkdir()
            (root / "active" / "manifest.json").write_text(
                json.dumps(runtime.expected_manifest(profiles[0])), encoding="utf-8")
            runtime.validate_active_cache(root, profiles[0])
            for profile in profiles[1:]:
                with self.subTest(profile=profile), self.assertRaisesRegex(
                        runtime.PocketTTSConfigurationError, "manifest mismatch"):
                    runtime.validate_active_cache(root, profile)

    def test_operator_environment_is_finite_and_fail_closed(self):
        with mock.patch.dict(os.environ, {"POCKET_TTS_SPANISH_MODEL": "spanish_24l",
                                          "POCKET_TTS_SPANISH_VOICE": "lola"}):
            self.assertEqual(runtime.selected_profile(), runtime.PocketTTSProfile("spanish_24l", "lola"))
            for key, value in [("POCKET_TTS_SPANISH_MODEL", "../../custom"),
                               ("POCKET_TTS_SPANISH_VOICE", "https://voice.example/a.wav"),
                               ("POCKET_TTS_SPANISH_VOICE", "")]:
                with self.subTest(key=key, value=value), mock.patch.dict(os.environ, {key: value}):
                    with self.assertRaisesRegex(runtime.PocketTTSConfigurationError, "unsupported"):
                        runtime.selected_profile()

    def test_large_loader_uses_large_model_and_matching_noncloning_embedding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spanish_24l.yaml").write_text(
                "weights_path: hf://gated/model\nweights_path_without_voice_cloning: hf://public/model@pinned\n",
                encoding="utf-8")
            model = mock.Mock()
            model_api = mock.Mock()
            model_api.load_model.return_value = model
            voice_lookup = mock.Mock(return_value="hf://public/spanish_24l/lola.safetensors@pinned")
            download = mock.Mock(return_value=root / "lola.safetensors")
            modules = {
                "pocket_tts": types.SimpleNamespace(TTSModel=model_api),
                "pocket_tts.utils.config": types.SimpleNamespace(CONFIGS_DIR=root),
                "pocket_tts.utils.utils": types.SimpleNamespace(
                    get_predefined_voice=voice_lookup, download_if_necessary=download),
            }
            with mock.patch.dict("sys.modules", modules), mock.patch.object(
                    runtime.tempfile, "gettempdir", return_value=directory):
                loaded, _ = runtime.load_language_model(
                    "spanish", "lola", profile=runtime.PocketTTSProfile("spanish_24l", "lola"))
            self.assertIs(loaded, model)
            config = yaml.safe_load(model_api.load_model.call_args.kwargs["config"].read_text())
            self.assertEqual(config["weights_path"], "hf://public/model@pinned")
            self.assertTrue(model_api.load_model.call_args.kwargs["quantize"])
            voice_lookup.assert_called_once_with(language="spanish_24l", name="lola")
            model.get_state_for_audio_prompt.assert_called_once_with(root / "lola.safetensors")

    def test_service_uses_captured_operator_profile_for_both_languages(self):
        profile = runtime.PocketTTSProfile("spanish_24l", "lola")
        with mock.patch.object(runtime, "load_language_model", return_value=(object(), {})) as load:
            synth = runtime.BilingualSynthesizer(profile)
            synth.load_all()
        self.assertEqual(synth.voices, {"english": "anna", "spanish": "lola"})
        self.assertEqual(load.call_args_list, [mock.call("english", "anna", profile=profile),
                                              mock.call("spanish", "lola", profile=profile)])
        self.assertEqual(synth.loaded_languages, ["english", "spanish"])


if __name__ == "__main__":
    unittest.main()
