"""Bounded, pitch-preserving WAV tempo processing; no model/cache ownership."""
from __future__ import annotations

import io
import math
import os
import struct
import subprocess
import tempfile
import time
import wave

FFMPEG = "/usr/bin/ffmpeg"
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
CONVERSION_SECONDS = 10.0


def validate_speed(value: object) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("POCKET_TTS_SPEED must be finite and between 1.0 and 1.25") from exc
    if not math.isfinite(speed) or not 1.0 <= speed <= 1.25:
        raise ValueError("POCKET_TTS_SPEED must be finite and between 1.0 and 1.25")
    return speed


def selected_speed() -> float:
    return validate_speed(os.environ.get("POCKET_TTS_SPEED", "1.0"))


def check_processor(speed: float) -> None:
    if speed != 1.0 and not os.access(FFMPEG, os.X_OK):
        raise RuntimeError("Pocket TTS speed processing requires /usr/bin/ffmpeg")


def _pcm_shape(audio: bytes) -> tuple[int, int]:
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError("Pocket TTS tempo processing requires a PCM WAV")
    if struct.unpack("<I", audio[4:8])[0] != len(audio) - 8:
        raise RuntimeError("Pocket TTS tempo WAV length is invalid")
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            frames, channels = wav.getnframes(), wav.getnchannels()
            if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or channels != 1 or frames <= 0:
                raise RuntimeError("Pocket TTS tempo processing requires nonempty mono PCM16")
            if len(wav.readframes(frames + 1)) != frames * channels * 2:
                raise RuntimeError("Pocket TTS tempo WAV frames are truncated")
            return wav.getframerate(), channels
    except (wave.Error, EOFError) as exc:
        raise RuntimeError("Pocket TTS tempo WAV is invalid") from exc


def _conversion_timeout(deadline: float | None) -> float:
    remaining = CONVERSION_SECONDS if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Pocket TTS tempo request deadline exceeded")
    return min(CONVERSION_SECONDS, remaining)


def apply_speed(audio: bytes, speed: float, *, deadline: float | None = None) -> bytes:
    if speed == 1.0:
        return audio
    if len(audio) > MAX_INPUT_BYTES:
        raise RuntimeError("Pocket TTS tempo input exceeds 20 MiB")
    rate, channels = _pcm_shape(audio)
    _conversion_timeout(deadline)
    # Anonymous, seekable files disappear on descriptor closure, even on SIGKILL.
    # -fs caps output during conversion (one muxer packet may overshoot); reject
    # cap hits even when FFmpeg exits 0. Two 20 MiB files plus packet margin fit
    # the existing 64 MiB tmpfs; output also fits the base64/32 MiB IPC envelope.
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
        source.write(audio)
        source.flush()
        source.seek(0)
        command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-threads", "1", "-filter_threads", "1", "-f", "wav",
                   "-i", f"/proc/self/fd/{source.fileno()}", "-map", "0:a:0", "-vn",
                   "-af", f"atempo={speed}", "-map_metadata", "-1",
                   "-c:a", "pcm_s16le", "-ar", str(rate), "-ac", str(channels),
                   "-threads", "1", "-bitexact", "-f", "wav", "-rf64", "never",
                   "-fs", str(MAX_OUTPUT_BYTES), f"/proc/self/fd/{target.fileno()}"]
        try:
            subprocess.run(command, check=True, timeout=_conversion_timeout(deadline),
                           pass_fds=(source.fileno(), target.fileno()),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Pocket TTS tempo conversion failed or timed out") from exc
        size = os.fstat(target.fileno()).st_size
        if size >= MAX_OUTPUT_BYTES:
            raise RuntimeError(f"Pocket TTS tempo output limit reached ({size} bytes)")
        target.seek(0)
        result = target.read(MAX_OUTPUT_BYTES)
    if _pcm_shape(result) != (rate, channels):
        raise RuntimeError("Pocket TTS tempo output format changed")
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError("Pocket TTS tempo request deadline exceeded")
    return result
