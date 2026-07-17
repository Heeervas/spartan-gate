#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ENDPOINT = "http://pocket-tts:8000/v1/tts"
DEFAULT_TIMEOUT_SECONDS = 180.0


def request_audio(text: str, timeout: float) -> bytes:
    payload = json.dumps({"text": text, "language": "auto"}).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Pocket TTS returned HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Pocket TTS is unavailable: {exc.reason}") from exc
    if len(audio) <= 44 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise RuntimeError("Pocket TTS returned an invalid WAV payload")
    return audio


def write_atomic(output_path: Path, audio: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(audio)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(
            os.environ.get("POCKET_TTS_CLIENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        ),
    )
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    audio = request_audio(text, args.timeout)
    write_atomic(args.output, audio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
