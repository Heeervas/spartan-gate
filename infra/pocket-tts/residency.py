"""One disposable model process, retained only while audio requests use it."""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from runtime import (
    BilingualSynthesizer,
    LANGUAGES,
    configure_huggingface_cache,
    select_language,
    selected_profile,
    validate_active_cache,
)

MAX_FRAME_BYTES = 32 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 150.0
logger = logging.getLogger("spartan-pocket-tts")


def idle_seconds() -> float:
    value = float(os.environ.get("POCKET_TTS_IDLE_SECONDS", "1800"))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("POCKET_TTS_IDLE_SECONDS must be finite and positive")
    return value


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Pocket TTS request deadline exceeded")
    return remaining


def _read_exact(channel: socket.socket, length: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < length:
        channel.settimeout(_remaining(deadline))
        chunk = channel.recv(min(length - len(result), 65536))
        if not chunk:
            raise EOFError("Pocket TTS worker disconnected")
        result.extend(chunk)
    return bytes(result)


def receive_frame(channel: socket.socket, deadline: float) -> dict:
    length = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
    if length > MAX_FRAME_BYTES:
        raise RuntimeError("Pocket TTS worker response exceeds IPC limit")
    payload = json.loads(_read_exact(channel, length, deadline))
    if not isinstance(payload, dict):
        raise RuntimeError("Pocket TTS worker returned an invalid response")
    return payload


def send_frame(channel: socket.socket, payload: dict, deadline: float) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise RuntimeError("Pocket TTS response exceeds IPC limit")
    channel.settimeout(_remaining(deadline))
    channel.sendall(struct.pack("!I", len(body)) + body)


class ResidentSynthesizer:
    def __init__(self, profile, cache_root: Path, idle_timeout: float):
        self.profile = profile
        self.voices = profile.voices
        self.cache_root = cache_root
        self.idle_timeout = idle_timeout
        self.last_error: str | None = None
        self._worker: subprocess.Popen | None = None
        self._channel: socket.socket | None = None
        self._ready = False
        self._last_completed = 0.0
        self._lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def loaded_languages(self) -> list[str]:
        if self._ready and self._worker is not None and self._worker.poll() is None:
            return list(LANGUAGES)
        return []

    def _start(self) -> None:
        self._stop()
        parent, child = socket.socketpair()
        self._channel = parent
        environment = dict(os.environ,
                           POCKET_TTS_CACHE_ROOT=str(self.cache_root),
                           POCKET_TTS_SPANISH_MODEL=self.profile.spanish_model,
                           POCKET_TTS_SPANISH_VOICE=self.profile.spanish_voice)
        try:
            self._worker = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(child.fileno())],
                pass_fds=(child.fileno(),), env=environment,
                stdin=subprocess.DEVNULL,
            )
        finally:
            child.close()
        if self._closed.is_set():
            raise RuntimeError("Pocket TTS is shutting down")
        logger.info("Pocket TTS model worker started pid=%s", self._worker.pid)

    def _stop(self) -> None:
        # Called under the admission lock; don't release ownership before reaping.
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        worker = self._worker
        if worker is not None:
            if worker.poll() is None:
                worker.terminate()
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=2)
            self._worker = None
        self._ready = False

    def synthesize(self, text: str, requested_language="auto") -> tuple[str, bytes]:
        language, _ = select_language(text, requested_language)
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Pocket TTS is busy with another synthesis request")
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        try:
            if self._closed.is_set():
                raise RuntimeError("Pocket TTS is shutting down")
            if self._worker is None or self._worker.poll() is not None:
                self._start()
            # Validate before waking, but let the model interpret directives once.
            send_frame(self._channel, {"text": text, "language": requested_language}, deadline)
            response = receive_frame(self._channel, deadline)
            if response.get("error"):
                raise RuntimeError(response["error"])
            audio = base64.b64decode(response["audio"], validate=True)
            _remaining(deadline)
            if response.get("language") != language:
                raise RuntimeError("Pocket TTS worker language mismatch")
            self._ready = True
            self.last_error = None
            self._last_completed = time.monotonic()
            return language, audio
        except Exception as exc:
            self.last_error = "Pocket TTS worker failed or exceeded its request deadline"
            self._stop()
            raise RuntimeError(self.last_error) from exc
        finally:
            self._lock.release()

    def reap_idle(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self._worker is not None:
                expired = time.monotonic() - self._last_completed >= self.idle_timeout
                if self._worker.poll() is not None or expired:
                    self._stop()
                    logger.info("Pocket TTS model worker released")
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closed.set()
        # Interrupt a request's socket I/O before waiting for its admission lock.
        channel = self._channel
        if channel is not None:
            try:
                channel.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with self._lock:
            self._stop()


def run_worker(descriptor: int) -> None:
    channel = socket.socket(fileno=descriptor)
    cache_root = Path(os.environ["POCKET_TTS_CACHE_ROOT"])
    profile = selected_profile()
    validate_active_cache(cache_root, profile)
    configure_huggingface_cache(cache_root / "active", offline=True)
    synthesizer = BilingualSynthesizer(profile)
    try:
        synthesizer.load_all()
        while True:
            # Parent owns idle/deadline enforcement and terminates this process.
            payload = receive_frame(channel, time.monotonic() + 86400)
            language, audio = synthesizer.synthesize(payload["text"], payload["language"])
            send_frame(channel, {"language": language,
                                 "audio": base64.b64encode(audio).decode("ascii")},
                       time.monotonic() + REQUEST_TIMEOUT_SECONDS)
    except EOFError:
        pass
    finally:
        channel.close()
        synthesizer.close()


if __name__ == "__main__":
    run_worker(int(sys.argv[1]))
