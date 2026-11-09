"""Offline Spanish comparison, using the service loader and synthesis path."""
from __future__ import annotations

import argparse
import io
import json
import os
import resource
import tempfile
import time
import wave
from pathlib import Path

from runtime import (
    BilingualSynthesizer,
    configure_huggingface_cache,
    selected_profile,
    validate_active_cache,
)


SPANISH_SAMPLES = (
    "Hola. Te resumo lo importante y después, si quieres, entramos en detalle.",
    "Mañana a las nueve y media revisaremos el presupuesto: son ciento veintitrés euros con cuarenta y cinco céntimos.",
    "¿Qué prefieres: una explicación breve o un ejemplo? Podemos hacerlo sin prisas, paso a paso.",
)


def validate_output(cache_root: Path, output_dir: Path) -> Path:
    output = output_dir.resolve()
    cache = cache_root.resolve()
    if output_dir.is_symlink() or output.exists():
        raise ValueError("output directory must not exist; choose a fresh candidate name")
    if output == cache or cache in output.parents or output in cache.parents:
        raise ValueError("output directory must be separate from the model cache")
    if not output.parent.is_dir():
        raise FileNotFoundError("output parent directory must already exist")
    return output


def generate_samples(synth: BilingualSynthesizer, staging: Path) -> list[dict]:
    samples = []
    for index, text in enumerate(SPANISH_SAMPLES, 1):
        started = time.perf_counter()
        language, audio = synth.synthesize(text, "spanish")
        elapsed = time.perf_counter() - started
        with wave.open(io.BytesIO(audio), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        if language != "spanish" or duration <= 0:
            raise ValueError("comparison requires nonempty Spanish WAV output")
        filename = f"{index:02d}.wav"
        (staging / filename).write_bytes(audio)
        samples.append({"file": filename, "text": text, "audio_seconds": duration,
                        "generation_seconds": elapsed, "rtf": elapsed / duration})
    return samples


def run_comparison(cache_root: Path, output_dir: Path) -> dict:
    profile = selected_profile()
    manifest = validate_active_cache(cache_root, profile)
    output = validate_output(cache_root, output_dir)
    configure_huggingface_cache(cache_root / "active", offline=True)
    synth = BilingualSynthesizer(profile)
    try:
        with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=output.parent) as directory:
            staging = Path(directory)
            started = time.perf_counter()
            synth.load_all()
            load_seconds = time.perf_counter() - started
            samples = generate_samples(synth, staging)
            report = {
                "manifest": manifest,
                "models": profile.models,
                "voices": profile.voices,
                "offline": True,
                "load_seconds": load_seconds,
                # Linux/Docker ru_maxrss is KiB; includes Python, warmup and both models.
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "rss_includes_english_model": True,
                "samples": samples,
            }
            (staging / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.rename(staging, output)
            return report
    finally:
        synth.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path,
                        default=Path(os.environ.get("POCKET_TTS_CACHE_ROOT", "/models")))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_comparison(args.cache_root, args.output_dir)
    except (RuntimeError, ValueError, OSError, wave.Error, EOFError) as exc:
        parser.exit(1, f"Pocket TTS comparison failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
