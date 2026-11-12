from __future__ import annotations

import array
import io
import math
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import audio
import runtime


def tone(seconds=4):
    samples = array.array('h', (int(10000 * math.sin(2 * math.pi * 440 * n / 24000))
                                for n in range(int(seconds * 24000))))
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


CONVERTER_SCRIPT = '''import os, signal, sys, time
mode = os.environ['TEMPO_FIXTURE_MODE']
if mode == 'exit': sys.exit(7)
if mode == 'bad':
    open(sys.argv[-1], 'wb').write(b'bad WAV')
else:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(os.environ['TEMPO_FIXTURE_PID'], 'w').write(str(os.getpid()))
    time.sleep(60)
'''


def converter_path(root):
    # Native probes supply this same fixture on a read-only executable bind;
    # production /tmp remains noexec. Host tests create it in their own directory.
    if os.environ.get('TEMPO_TEST_CONVERTER'):
        return Path(os.environ['TEMPO_TEST_CONVERTER'])
    executable = root / 'converter'
    executable.write_text(f'#!{sys.executable}\n{CONVERTER_SCRIPT}')
    executable.chmod(0o700)
    return executable


class AudioTests(unittest.TestCase):
    def test_speed_validation_and_cache_independence(self):
        before = runtime.expected_manifest()
        for value in ('1', '1.15', '1.25'):
            with self.subTest(value=value), mock.patch.dict(os.environ, POCKET_TTS_SPEED=value):
                self.assertEqual(audio.selected_speed(), float(value))
                self.assertEqual(runtime.expected_manifest(), before)
        for value in ('nan', 'inf', '-inf', '', 'fast', '0.99', '1.251'):
            with self.subTest(value=value), mock.patch.dict(os.environ, POCKET_TTS_SPEED=value):
                with self.assertRaisesRegex(ValueError, 'POCKET_TTS_SPEED'):
                    audio.selected_speed()

    def test_invalid_speed_cannot_initialize_listener_or_spawn_worker(self):
        import server
        with (mock.patch.dict(os.environ, POCKET_TTS_SPEED='nan'),
              mock.patch.object(server, 'validate_active_cache'),
              mock.patch.object(server, 'synthesizer', None),
              mock.patch.object(server, 'startup_error', None),
              mock.patch.object(server, 'ResidentSynthesizer') as resident,
              mock.patch.object(server.logger, 'exception')):
            server.initialize()
            self.assertIsNone(server.synthesizer)
            self.assertIn('POCKET_TTS_SPEED', server.startup_error)
            resident.assert_not_called()

    def test_bypass_is_identical_and_never_launches_converter(self):
        original = tone(0.1)
        with mock.patch.object(audio.subprocess, 'run') as run:
            self.assertIs(audio.apply_speed(original, 1.0), original)
        run.assert_not_called()

    def test_real_atempo_preserves_pitch_format_and_truthful_length(self):
        converted = audio.apply_speed(tone(), 1.15, deadline=time.monotonic() + 10)
        self.assertEqual(struct.unpack('<I', converted[4:8])[0], len(converted) - 8)
        with wave.open(io.BytesIO(converted)) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 24000))
            duration = wav.getnframes() / wav.getframerate()
            pcm = array.array('h', wav.readframes(wav.getnframes()))
            self.assertEqual(len(pcm), wav.getnframes())
        self.assertAlmostEqual(duration, 4 / 1.15, delta=0.06)
        crossings = sum(a <= 0 < b for a, b in zip(pcm, pcm[1:]))
        self.assertAlmostEqual(crossings / duration, 440, delta=4)

    def test_deadline_and_input_limits_fail_before_launch(self):
        original = tone(0.1)
        with mock.patch.object(audio.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'deadline'):
                audio.apply_speed(original, 1.15, deadline=time.monotonic() - 1)
            with mock.patch.object(audio, 'MAX_INPUT_BYTES', len(original) - 1):
                with self.assertRaisesRegex(RuntimeError, 'input exceeds'):
                    audio.apply_speed(original, 1.15)
        run.assert_not_called()

    def test_real_output_cap_rejects_successful_truncation_with_small_overshoot(self):
        with mock.patch.object(audio, 'MAX_OUTPUT_BYTES', 4096):
            with self.assertRaisesRegex(RuntimeError, r'output limit reached \((\d+) bytes\)') as error:
                audio.apply_speed(tone(), 1.15)
        import re
        actual = int(re.search(r'\((\d+) bytes\)', str(error.exception)).group(1))
        self.assertLessEqual(actual, 4096 + 65536)

    def test_missing_executable_fails_enabled_but_not_bypass(self):
        with mock.patch.object(audio, 'FFMPEG', '/nonexistent/spartan-ffmpeg'):
            audio.check_processor(1.0)
            with self.assertRaisesRegex(RuntimeError, 'requires'):
                audio.check_processor(1.15)
            with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
                audio.apply_speed(tone(0.1), 1.15)

    def test_converter_failure_malformed_output_and_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = converter_path(root)
            pid_file = root / 'pid'
            for mode in ('exit', 'bad', 'sleep'):
                with self.subTest(mode=mode), mock.patch.object(audio, 'FFMPEG', str(executable)), \
                        mock.patch.dict(os.environ, TEMPO_FIXTURE_MODE=mode, TEMPO_FIXTURE_PID=str(pid_file)), \
                        mock.patch.object(audio, 'CONVERSION_SECONDS', 0.15):
                    with self.assertRaises(RuntimeError):
                        audio.apply_speed(tone(0.1), 1.15)
            self.assertFalse(Path(f'/proc/{pid_file.read_text()}').exists())
            expected = {'pid'} if os.environ.get('TEMPO_TEST_CONVERTER') else {'converter', 'pid'}
            self.assertEqual({p.name for p in root.iterdir()}, expected)
        # Failure is not a sticky fallback or poison state.
        self.assertTrue(audio.apply_speed(tone(0.2), 1.15).startswith(b'RIFF'))

    def test_truncated_pcm_is_not_accepted_despite_consistent_riff_length(self):
        original = tone(0.1)
        truncated = bytearray(original[:-2])
        struct.pack_into('<I', truncated, 4, len(truncated) - 8)
        with self.assertRaisesRegex(RuntimeError, 'frames are truncated'):
            audio.apply_speed(bytes(truncated), 1.15)


if __name__ == '__main__':
    unittest.main()
