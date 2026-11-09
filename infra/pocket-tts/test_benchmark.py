from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import benchmark
import runtime


def one_second_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24000)
    return output.getvalue()


class OfflineComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache = self.root / "cache"
        (self.cache / "active").mkdir(parents=True)
        (self.cache / "active" / "manifest.json").write_text(
            json.dumps(runtime.expected_manifest(runtime.PocketTTSProfile())), encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"POCKET_TTS_SPANISH_MODEL": "spanish",
                                               "POCKET_TTS_SPANISH_VOICE": "eponine"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_comparison_sets_offline_before_loading_and_writes_audio_and_measurements(self):
        synth = mock.Mock()
        synth.synthesize.return_value = ("spanish", one_second_wav())

        def make_synth(profile):
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["HF_HOME"], str(self.cache / "active" / "huggingface"))
            return synth

        output = self.root / "samples"
        with mock.patch.object(benchmark, "BilingualSynthesizer", side_effect=make_synth), \
                mock.patch.object(benchmark.time, "perf_counter", side_effect=[0, 2, 2, 3, 3, 4, 4, 5]):
            report = benchmark.run_comparison(self.cache, output)
        self.assertEqual(report["load_seconds"], 2)
        self.assertTrue(report["rss_includes_english_model"])
        self.assertGreater(report["peak_rss_bytes"], 0)
        self.assertEqual(len(report["samples"]), 3)
        for sample in report["samples"]:
            self.assertEqual(sample["audio_seconds"], 1)
            self.assertEqual(sample["generation_seconds"], 1)
            self.assertEqual(sample["rtf"], 1)
            with wave.open(str(output / sample["file"]), "rb") as wav:
                self.assertEqual(wav.getnframes(), 24000)
        self.assertEqual(json.loads((output / "report.json").read_text()), report)
        self.assertEqual((self.cache / "active" / "manifest.json").read_text(),
                         json.dumps(runtime.expected_manifest(runtime.PocketTTSProfile())))
        synth.close.assert_called_once()

    def test_existing_or_cache_overlapping_output_rejects_before_loading(self):
        for output in (self.cache, self.cache / "nested", self.root, self.root / "missing" / "samples"):
            with self.subTest(output=output), mock.patch.object(benchmark, "BilingualSynthesizer") as load:
                with self.assertRaises((ValueError, FileNotFoundError)):
                    benchmark.run_comparison(self.cache, output)
                load.assert_not_called()
        self.assertFalse((self.cache / "nested").exists())

    def test_wrong_cache_rejects_before_output_or_loading(self):
        with mock.patch.dict(os.environ, {"POCKET_TTS_SPANISH_MODEL": "spanish_24l"}), \
                mock.patch.object(benchmark, "BilingualSynthesizer") as load:
            with self.assertRaisesRegex(runtime.PocketTTSConfigurationError, "manifest mismatch"):
                benchmark.run_comparison(self.cache, self.root / "samples")
        load.assert_not_called()
        self.assertFalse((self.root / "samples").exists())

    def test_failed_synthesis_leaves_no_published_samples_or_report(self):
        synth = mock.Mock()
        synth.synthesize.side_effect = [("spanish", one_second_wav()), RuntimeError("broken model")]
        with mock.patch.object(benchmark, "BilingualSynthesizer", return_value=synth):
            with self.assertRaisesRegex(RuntimeError, "broken model"):
                benchmark.run_comparison(self.cache, self.root / "samples")
        self.assertFalse((self.root / "samples").exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["cache"])
        synth.close.assert_called_once()

    def test_invalid_wav_does_not_publish_a_success_report(self):
        synth = mock.Mock()
        synth.synthesize.return_value = ("spanish", b"not audio")
        with mock.patch.object(benchmark, "BilingualSynthesizer", return_value=synth):
            with self.assertRaises((wave.Error, EOFError)):
                benchmark.run_comparison(self.cache, self.root / "samples")
        self.assertFalse((self.root / "samples").exists())
        synth.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
