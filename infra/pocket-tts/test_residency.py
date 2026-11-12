from __future__ import annotations

import concurrent.futures
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest import mock

import residency
import server
from runtime import PocketTTSProfile, expected_manifest

ROOT = Path(__file__).resolve().parent
REAL_POPEN = subprocess.Popen
# Real IPC/process lifecycle; only the expensive model producer is synthetic.
WORKER = r'''
import base64, io, os, socket, struct, sys, time, wave
from residency import receive_frame, send_frame
from runtime import select_language
channel = socket.socket(fileno=int(sys.argv[1]))
if os.environ.get("FIXTURE_PID_FILE"):
    open(os.environ["FIXTURE_PID_FILE"], "w").write(str(os.getpid()))
while True:
    message = receive_frame(channel, time.monotonic() + 60)
    language, text = select_language(message["text"], message["language"])
    if text == "crash": os._exit(7)
    if text == "stall": time.sleep(60)
    if text == "partial":
        channel.sendall(struct.pack("!I", 1000) + b"{")
        time.sleep(60)
    if text == "oversize":
        channel.sendall(struct.pack("!I", 32 * 1024 * 1024 + 1))
        time.sleep(60)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 240)
    send_frame(channel, {"language": language,
                        "audio": base64.b64encode(output.getvalue()).decode()},
               time.monotonic() + 5)
'''


def fixture_popen(command, **kwargs):
    return REAL_POPEN([sys.executable, "-c", WORKER, command[-1]], cwd=ROOT, **kwargs)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("fixture condition did not become true")


class ResidencyTests(unittest.TestCase):
    def setUp(self):
        self.spawn = mock.patch.object(residency.subprocess, "Popen", side_effect=fixture_popen)
        self.calls = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.synth = residency.ResidentSynthesizer(PocketTTSProfile(), Path("/synthetic"), 1800)
        self.addCleanup(self.synth.close)

    def test_cold_reuse_and_idle_reclamation(self):
        self.synth.reap_idle()
        self.assertEqual(self.calls.call_count, 0)
        self.assertEqual(self.synth.loaded_languages, [])
        language, audio = self.synth.synthesize("hello", "en")
        self.assertEqual(language, "english")
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertEqual(wav.getnframes(), 240)
        worker = self.synth._worker
        self.synth.synthesize("hola", "es")
        self.assertIs(self.synth._worker, worker)
        completed = self.synth._last_completed
        with mock.patch.object(residency.time, "monotonic", return_value=completed + 1799):
            self.synth.reap_idle()
        self.assertIsNone(worker.poll())
        with mock.patch.object(residency.time, "monotonic", return_value=completed + 1800):
            self.synth.reap_idle()
        self.assertIsNotNone(worker.returncode)
        self.assertEqual(self.synth.loaded_languages, [])
        self.synth.synthesize("hello again", "en")
        self.assertNotEqual(self.synth._worker.pid, worker.pid)
        self.assertEqual(self.calls.call_count, 2)

    def test_invalid_language_and_empty_directive_do_not_wake(self):
        for text, language in [("hello", "bad"), ("[es] ", "auto"), ("", "en")]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.synth.synthesize(text, language)
        self.assertEqual(self.calls.call_count, 0)

    def test_faults_reap_worker_and_allow_next_independent_request(self):
        for text in ("crash", "stall", "partial", "oversize"):
            with self.subTest(text=text):
                self.synth.synthesize("baseline", "en")
                worker = self.synth._worker
                with mock.patch.object(residency, "REQUEST_TIMEOUT_SECONDS", 0.15):
                    started = time.monotonic()
                    with self.assertRaisesRegex(RuntimeError, "worker failed"):
                        self.synth.synthesize(text, "en")
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsNotNone(worker.returncode)
                self.assertIsNotNone(self.synth.last_error)
                self.synth.synthesize("recovery", "en")
                self.assertIsNone(self.synth.last_error)

    def test_active_request_cannot_be_reaped_and_close_interrupts_it(self):
        self.synth.synthesize("baseline", "en")
        worker = self.synth._worker
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.synth.synthesize, "stall", "en")
            wait_for(lambda: self.synth._lock.locked())
            self.synth.idle_timeout = 0
            self.synth.reap_idle()
            self.assertIsNone(worker.poll())
            with self.assertRaisesRegex(RuntimeError, "busy"):
                self.synth.synthesize("concurrent", "en")
            self.synth.close()
            with self.assertRaises(RuntimeError):
                pending.result(timeout=3)
        self.assertIsNotNone(worker.returncode)
        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            self.synth.synthesize("closed", "en")


class WorkerBoundaryTests(unittest.TestCase):
    def test_actual_worker_offline_load_and_audio_protocol(self):
        script = r'''
import io, os, sys, wave
import residency
from runtime import select_language
class Model:
    def __init__(self, profile):
        self.profile = profile
    def load_all(self):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
        assert "torch" not in sys.modules
    def synthesize(self, text, language):
        language, text = select_language(text, language)
        assert text in ("Hola", "[en] Hello") and language == "spanish"
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 120)
        return language, output.getvalue()
    def close(self): pass
residency.BilingualSynthesizer = Model
residency.run_worker(int(sys.argv[1]))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "active").mkdir()
            (root / "active" / "manifest.json").write_text(
                json.dumps(expected_manifest(PocketTTSProfile())))
            synth = residency.ResidentSynthesizer(PocketTTSProfile(), root, 1800)
            def spawn(command, **kwargs):
                return REAL_POPEN([sys.executable, "-c", script, command[-1]], cwd=ROOT, **kwargs)
            try:
                with mock.patch.object(residency.subprocess, "Popen", side_effect=spawn):
                    for text in ("[es] Hola", "[es] [en] Hello"):
                        with self.subTest(text=text):
                            language, audio = synth.synthesize(text)
                            self.assertEqual(language, "spanish")
                            with wave.open(io.BytesIO(audio)) as wav:
                                self.assertEqual(wav.getnframes(), 120)
            finally:
                synth.close()

    def test_idle_configuration_rejects_disabled_or_unbounded_values(self):
        for value in ("0", "-1", "nan", "inf", "invalid"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"POCKET_TTS_IDLE_SECONDS": value}
            ), self.assertRaises(ValueError):
                residency.idle_seconds()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.synth = residency.ResidentSynthesizer(PocketTTSProfile(), Path("/synthetic"), 0.1)
        self.addCleanup(self.synth.close)
        self.patches = [
            mock.patch.object(server, "synthesizer", self.synth),
            mock.patch.object(server, "startup_error", None),
            mock.patch.object(residency.subprocess, "Popen", side_effect=fixture_popen),
        ]
        self.mocks = [patch.start() for patch in self.patches]
        self.addCleanup(lambda: [patch.stop() for patch in reversed(self.patches)])
        self.http = server.PocketTTSServer(("127.0.0.1", 0), server.PocketTTSHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()
        self.addCleanup(self.stop_http)
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def stop_http(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def health(self):
        with self.opener.open(self.url + "/health", timeout=2) as response:
            return json.load(response)

    def post(self, payload):
        request = urllib.request.Request(self.url + "/v1/tts", json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        return self.opener.open(request, timeout=3)

    def test_health_and_bad_payloads_never_wake_then_http_idle_reaps(self):
        for payload in ([], None, {"text": 3}, {"text": "hello", "language": "zz"},
                        {"text": "[es]"}):
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as error:
                self.post(payload)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.assertEqual(self.health()["resident_languages"], [])
        self.assertEqual(self.mocks[-1].call_count, 0)
        with self.post({"text": "[es] Hola"}) as response:
            self.assertEqual(response.headers["X-Pocket-TTS-Language"], "spanish")
            self.assertTrue(response.read().startswith(b"RIFF"))
        worker = self.synth._worker
        completed = self.synth._last_completed
        self.assertEqual(self.health()["languages"], ["english", "spanish"])
        self.assertEqual(self.synth._last_completed, completed)
        wait_for(lambda: worker.poll() is not None)
        self.assertEqual(self.health()["status"], "healthy")
        self.assertEqual(self.health()["resident_languages"], [])
        self.assertEqual(self.mocks[-1].call_count, 1)


class ShutdownTests(unittest.TestCase):
    def test_sigterm_asleep_and_active_reaps_child(self):
        for active in (False, True):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                self.check_sigterm(Path(directory), active)

    def check_sigterm(self, root, active):
        cache = root / "active"
        cache.mkdir()
        (cache / "manifest.json").write_text(json.dumps(expected_manifest(PocketTTSProfile())))
        script = '''
import os, subprocess, sys
from pathlib import Path
import residency, server
worker_script = sys.argv[1]
real_popen = subprocess.Popen
def spawn(command, **kwargs):
    return real_popen([sys.executable, "-c", worker_script, command[-1]], **kwargs)
residency.subprocess.Popen = spawn
original = server.PocketTTSServer
class FixtureServer(original):
    def __init__(self, address, handler):
        super().__init__(("127.0.0.1", 0), handler)
        print(self.server_port, flush=True)
server.PocketTTSServer = FixtureServer
server.main()
'''
        pid_file = root / "worker.pid"
        env = dict(os.environ, POCKET_TTS_CACHE_ROOT=str(root), FIXTURE_PID_FILE=str(pid_file),
                   POCKET_TTS_SPANISH_MODEL="spanish", POCKET_TTS_SPANISH_VOICE="eponine")
        listener = REAL_POPEN([sys.executable, "-u", "-c", script, WORKER], cwd=ROOT,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            port = int(listener.stdout.readline())
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.active_request, port) if active else None
                if active:
                    wait_for(pid_file.exists)
                listener.terminate()
                self.assertEqual(listener.wait(timeout=6), 0)
                if pending:
                    with self.assertRaises((OSError, urllib.error.URLError)) as error:
                        pending.result(timeout=3)
                    if isinstance(error.exception, urllib.error.HTTPError):
                        error.exception.close()
            if active:
                self.assertFalse(Path(f"/proc/{pid_file.read_text()}").exists())
        finally:
            if listener.poll() is None:
                listener.kill()
                listener.wait(timeout=3)
            listener.stdout.close()

    @staticmethod
    def active_request(port):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/tts",
                                         b'{"text":"stall"}',
                                         headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=5) as response:
            return response.read()


if __name__ == "__main__":
    unittest.main()
