"""Real IPC/process/converter ownership; only the expensive model is synthetic."""
from __future__ import annotations

import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import residency
from runtime import PocketTTSProfile, expected_manifest
from test_residency import wait_for

REAL_POPEN = subprocess.Popen
ROOT = Path(__file__).resolve().parent
WORKER = r'''
import io, os, signal, sys, wave
import audio, residency
from runtime import select_language
real_ffmpeg = audio.FFMPEG
real_popen = audio.subprocess.Popen
def stopped_converter(command, **kwargs):
    assert command[0] == real_ffmpeg
    child = real_popen(command, **kwargs)
    os.kill(child.pid, signal.SIGSTOP)  # Stall the actual converter, not a mock.
    open(os.environ['TEMPO_FIXTURE_PID'], 'w').write(str(child.pid))
    return child
class Model:
    def __init__(self, profile):
        self.speed = float(os.environ['POCKET_TTS_SPEED'])
        assert self.speed == 1.15
    def load_all(self):
        if os.environ.get('FIXTURE_HARD'):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
    def synthesize(self, text, language, *, deadline):
        language, text = select_language(text, language)
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 24000)
        audio.subprocess.Popen = real_popen if text == 'recovery' else stopped_converter
        if os.environ.get('FIXTURE_HARD'):
            deadline = None  # Deliberately misbehaving worker forces parent escalation.
        return language, audio.apply_speed(output.getvalue(), self.speed, deadline=deadline)
    def close(self): pass
residency.BilingualSynthesizer = Model
residency.run_worker(int(sys.argv[1]))
'''


class TempoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.tmp = self.root / 'anonymous'
        self.tmp.mkdir()
        self.pid_file = self.root / 'converter.pid'
        self.env = dict(os.environ, TMPDIR=str(self.tmp), TEMPO_FIXTURE_PID=str(self.pid_file),
                        POCKET_TTS_SPEED='1.15', POCKET_TTS_SPANISH_MODEL='spanish',
                        POCKET_TTS_SPANISH_VOICE='eponine')
        active = self.root / 'active'
        active.mkdir()
        (active / 'manifest.json').write_text(json.dumps(expected_manifest(PocketTTSProfile())))
        self.env['POCKET_TTS_CACHE_ROOT'] = str(self.root)

    def spawn(self, command, **kwargs):
        return REAL_POPEN([sys.executable, '-c', WORKER, command[-1]], cwd=ROOT, **kwargs)

    def assert_converter_gone(self):
        wait_for(lambda: not Path(f'/proc/{self.pid_file.read_text()}').exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_parent_deadline_kills_conversion_then_next_request_recovers(self):
        self.check_interruption('timeout')

    def test_dead_leader_does_not_leave_converter_behind(self):
        self.check_interruption('leader-death')

    def test_termination_escalates_for_owned_group(self):
        self.check_interruption('escalation')

    def check_interruption(self, mode):
        if mode == 'escalation':
            self.env['FIXTURE_HARD'] = '1'
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(residency.subprocess, 'Popen', side_effect=self.spawn):
            synth = residency.ResidentSynthesizer(PocketTTSProfile(), self.root, 1800, speed=1.15)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    with mock.patch.object(residency, 'REQUEST_TIMEOUT_SECONDS', 0.6):
                        pending = pool.submit(synth.synthesize, '[es] conversion')
                        wait_for(self.pid_file.exists)
                        executable = os.readlink(f'/proc/{self.pid_file.read_text()}/exe')
                        self.assertEqual(Path(executable).name, 'ffmpeg')
                        self.assertEqual(list(self.tmp.iterdir()), [])
                        worker = synth._worker
                        if mode == 'leader-death':
                            os.kill(worker.pid, signal.SIGKILL)
                        started = time.monotonic()
                        with self.assertRaisesRegex(RuntimeError, 'worker failed'):
                            pending.result(timeout=6)
                        self.assertLess(time.monotonic() - started, 5)
                    self.assertIsNotNone(worker.returncode)
                    self.assert_converter_gone()
                    # Captured1.15 survives later ambient environment changes.
                    with mock.patch.dict(os.environ, POCKET_TTS_SPEED='1.0'):
                        language, audio = synth.synthesize('[es] recovery')
                    self.assertEqual(language, 'spanish')
                    self.assertTrue(audio.startswith(b'RIFF'))
                    self.assertIsNone(synth.last_error)
            finally:
                synth.close()

    def test_listener_sigterm_during_conversion_reaps_worker_and_converter(self):
        script = r'''
import subprocess, sys
import residency, server
real = subprocess.Popen
worker_script = sys.argv[1]
def spawn(command, **kwargs):
    return real([sys.executable, '-c', worker_script, command[-1]], **kwargs)
residency.subprocess.Popen = spawn
original = server.PocketTTSServer
class Server(original):
    def __init__(self, address, handler):
        super().__init__(('127.0.0.1', 0), handler)
        print(self.server_port, flush=True)
server.PocketTTSServer = Server
server.main()
'''
        listener = REAL_POPEN([sys.executable, '-u', '-c', script, WORKER], cwd=ROOT,
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            port = int(listener.stdout.readline())
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.request, port)
                wait_for(self.pid_file.exists)
                self.assertEqual(Path(os.readlink(f'/proc/{self.pid_file.read_text()}/exe')).name, 'ffmpeg')
                listener.terminate()
                self.assertEqual(listener.wait(timeout=6), 0)
                with self.assertRaises((OSError, urllib.error.URLError)) as error:
                    pending.result(timeout=3)
                if isinstance(error.exception, urllib.error.HTTPError):
                    error.exception.close()
            self.assert_converter_gone()
        finally:
            if listener.poll() is None:
                listener.kill()
                listener.wait(timeout=3)
            listener.stdout.close()

    @staticmethod
    def request(port):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f'http://127.0.0.1:{port}/v1/tts',
                                         b'{"text":"[es] conversion"}',
                                         headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=8) as response:
            return response.read()


if __name__ == '__main__':
    unittest.main()
