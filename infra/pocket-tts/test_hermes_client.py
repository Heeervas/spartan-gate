from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("hermes_client.py")
SPEC = importlib.util.spec_from_file_location("pocket_tts_hermes_client", MODULE_PATH)
CLIENT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CLIENT)


class HermesClientTests(unittest.TestCase):
    def test_request_disables_environment_proxies(self):
        wav = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 40
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = wav
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch.object(CLIENT.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(CLIENT.request_audio("hello", 3), wav)
        proxy_handler = build.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})

    def test_invalid_payload_is_rejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"not-wave"
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch.object(CLIENT.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(RuntimeError):
                CLIENT.request_audio("hello", 3)

    def test_atomic_write_replaces_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "voice.wav"
            output.write_bytes(b"old")
            wav = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 40
            CLIENT.write_atomic(output, wav)
            self.assertEqual(output.read_bytes(), wav)


if __name__ == "__main__":
    unittest.main()
