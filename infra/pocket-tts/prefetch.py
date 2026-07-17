from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sys
import time
from pathlib import Path

from runtime import (
    CACHE_VERSION,
    DEFAULT_VOICES,
    LANGUAGES,
    configure_huggingface_cache,
    expected_manifest,
    load_language_model,
)


GIB = 1024**3
DEFAULT_MIN_FREE_BEFORE = 8 * GIB
DEFAULT_MIN_FREE_AFTER = 5 * GIB


def available_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def require_free_space(path: Path, minimum: int, stage: str) -> None:
    available = available_bytes(path)
    if available < minimum:
        raise RuntimeError(
            f"Pocket TTS {stage} requires {minimum / GIB:.1f} GiB free; "
            f"only {available / GIB:.1f} GiB is available"
        )


def activate_version(cache_root: Path, version_dir: Path) -> None:
    active = cache_root / "active"
    temporary = cache_root / f".active-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(Path("versions") / version_dir.name)
    os.replace(temporary, active)


def cache_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def valid_manifest(version_dir: Path) -> dict | None:
    if not version_dir.is_dir():
        return None
    try:
        manifest = json.loads(
            (version_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if all(manifest.get(key) == value for key, value in expected_manifest().items()):
        return manifest
    return None


def remove_version(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def seed_huggingface_cache(cache_root: Path, staging: Path) -> bool:
    active = cache_root / "active"
    if not active.is_symlink():
        return False
    try:
        versions = (cache_root / "versions").resolve(strict=True)
        active_target = active.resolve(strict=True)
        active_target.relative_to(versions)
    except (FileNotFoundError, OSError, ValueError):
        return False

    source = active_target / "huggingface"
    if not source.is_dir():
        return False

    def copy_cache_file(source_file: str, destination_file: str) -> str:
        if Path(source_file).parent.name == "blobs":
            os.link(source_file, destination_file)
            return destination_file
        return shutil.copy2(source_file, destination_file)

    shutil.copytree(
        source,
        staging / "huggingface",
        symlinks=True,
        copy_function=copy_cache_file,
    )
    return True


def run_prefetch(cache_root: Path, min_before: int, min_after: int) -> dict:
    cache_root.mkdir(parents=True, exist_ok=True)
    versions = cache_root / "versions"
    versions.mkdir(exist_ok=True)
    target = versions / CACHE_VERSION

    active_manifest = valid_manifest(cache_root / "active")
    if active_manifest is not None:
        return active_manifest

    target_manifest = valid_manifest(target)
    if target_manifest is not None:
        activate_version(cache_root, target)
        return target_manifest

    require_free_space(cache_root, min_before, "prefetch")
    staging = versions / f".{CACHE_VERSION}.staging-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    installed: Path | None = None
    try:
        seed_huggingface_cache(cache_root, staging)
        configure_huggingface_cache(staging, offline=False)
        smoke_text = {
            "english": "Pocket TTS is ready.",
            "spanish": "Pocket TTS está listo.",
        }
        for language in LANGUAGES:
            model, voice_state = load_language_model(language, DEFAULT_VOICES[language])
            audio = model.generate_audio(voice_state, smoke_text[language])
            if audio.numel() <= 0:
                raise RuntimeError(f"Pocket TTS {language} smoke synthesis was empty")
            del audio, voice_state, model
            gc.collect()
            require_free_space(cache_root, min_after, f"{language} download")

        manifest = expected_manifest()
        manifest.update(
            {
                "created_at_unix": int(time.time()),
                "cache_bytes": cache_size(staging),
            }
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        require_free_space(cache_root, min_after, "activation")

        stale_target = target if target.exists() or target.is_symlink() else None
        final_target = (
            versions / f"{CACHE_VERSION}-{time.time_ns()}-{os.getpid()}"
            if stale_target is not None
            else target
        )
        os.replace(staging, final_target)
        installed = final_target
        activate_version(cache_root, final_target)
        installed = None
        if stale_target is not None:
            try:
                remove_version(stale_target)
            except OSError:
                pass
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if installed is not None:
            try:
                remove_version(installed)
            except OSError:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(os.environ.get("POCKET_TTS_CACHE_ROOT", "/models")),
    )
    parser.add_argument(
        "--min-free-before",
        type=int,
        default=int(
            os.environ.get("POCKET_TTS_MIN_FREE_BEFORE_BYTES", str(DEFAULT_MIN_FREE_BEFORE))
        ),
    )
    parser.add_argument(
        "--min-free-after",
        type=int,
        default=int(
            os.environ.get("POCKET_TTS_MIN_FREE_AFTER_BYTES", str(DEFAULT_MIN_FREE_AFTER))
        ),
    )
    args = parser.parse_args()
    try:
        manifest = run_prefetch(args.cache_root, args.min_free_before, args.min_free_after)
    except Exception as exc:
        print(f"Pocket TTS prefetch failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
