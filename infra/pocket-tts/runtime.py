from __future__ import annotations

import gc
import io
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POCKET_TTS_VERSION = "2.1.0"
POCKET_TTS_SOURCE_REVISION = "058886528d0b6f2f2d4022de2e244a5260729e6e"
CACHE_VERSION = "v2.1.0-en-anna-es-eponine-int8"
LANGUAGES = ("english", "spanish")
DEFAULT_VOICES = {"english": "anna", "spanish": "eponine"}
LANGUAGE_ALIASES = {
    "en": "english",
    "english": "english",
    "es": "spanish",
    "spanish": "spanish",
    "auto": "auto",
}
DIRECTIVE_RE = re.compile(r"^\s*\[(en|es|english|spanish)]\s*", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-záéíóúüñ']+", re.IGNORECASE)

SPANISH_WORDS = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "que",
        "y",
        "en",
        "es",
        "por",
        "para",
        "con",
        "como",
        "pero",
        "más",
        "muy",
        "esta",
        "este",
        "esto",
        "hola",
        "gracias",
        "quiero",
        "puede",
        "puedes",
        "porque",
        "también",
        "buenos",
        "buenas",
        "días",
        "dias",
        "tardes",
        "noches",
        "cómo",
        "estás",
        "soy",
        "eres",
        "vamos",
        "hacer",
        "hoy",
        "mañana",
        "necesito",
    }
)
ENGLISH_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "in",
        "is",
        "it",
        "for",
        "with",
        "as",
        "but",
        "this",
        "that",
        "hello",
        "thanks",
        "want",
        "can",
        "because",
        "also",
        "please",
        "from",
    }
)


class PocketTTSConfigurationError(RuntimeError):
    pass


def normalize_language(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower()
    try:
        return LANGUAGE_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported language: {value!r}") from exc


def select_language(text: str, requested: str | None = "auto") -> tuple[str, str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")

    directive = DIRECTIVE_RE.match(text)
    if directive:
        language = normalize_language(directive.group(1))
        stripped = text[directive.end() :].strip()
        if not stripped:
            raise ValueError("language directive must be followed by text")
        return language, stripped

    language = normalize_language(requested)
    if language != "auto":
        return language, text.strip()

    lowered = text.lower()
    tokens = TOKEN_RE.findall(lowered)
    spanish_score = sum(token in SPANISH_WORDS for token in tokens)
    english_score = sum(token in ENGLISH_WORDS for token in tokens)
    if any(character in lowered for character in "áéíóúüñ¿¡"):
        spanish_score += 3

    selected = "spanish" if spanish_score > english_score else "english"
    return selected, text.strip()


def cache_manifest_path(cache_root: Path) -> Path:
    return cache_root / "active" / "manifest.json"


def expected_manifest() -> dict[str, Any]:
    return {
        "cache_version": CACHE_VERSION,
        "pocket_tts_version": POCKET_TTS_VERSION,
        "source_revision": POCKET_TTS_SOURCE_REVISION,
        "quantized": True,
        "languages": list(LANGUAGES),
        "voices": DEFAULT_VOICES,
    }


def validate_active_cache(cache_root: Path) -> dict[str, Any]:
    manifest_path = cache_manifest_path(cache_root)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PocketTTSConfigurationError(
            f"Pocket TTS cache is not active at {manifest_path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PocketTTSConfigurationError(
            f"Pocket TTS cache manifest is invalid at {manifest_path}: {exc}"
        ) from exc

    expected = expected_manifest()
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise PocketTTSConfigurationError(
                f"Pocket TTS cache manifest mismatch for {key}: "
                f"expected {value!r}, got {manifest.get(key)!r}"
            )
    return manifest


def configure_huggingface_cache(cache_dir: Path, *, offline: bool) -> None:
    hf_home = cache_dir / "huggingface"
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_home / "hub")
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"


def _public_model_config(language: str) -> Path:
    import yaml
    from pocket_tts.utils.config import CONFIGS_DIR

    source = CONFIGS_DIR / f"{language}.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    public_weights = config.get("weights_path_without_voice_cloning")
    if not isinstance(public_weights, str) or not public_weights.startswith("hf://"):
        raise PocketTTSConfigurationError(
            f"Pocket TTS {language} config has no public non-cloning weights"
        )
    config["weights_path"] = public_weights

    target = Path(tempfile.gettempdir()) / f"spartan-pocket-tts-{language}.yaml"
    content = yaml.safe_dump(config, sort_keys=False)
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")
    return target


def load_language_model(language: str, voice: str):
    from pocket_tts import TTSModel
    from pocket_tts.utils.utils import download_if_necessary, get_predefined_voice

    model = TTSModel.load_model(config=_public_model_config(language), quantize=True)
    voice_path = download_if_necessary(
        get_predefined_voice(language=language, name=voice)
    )
    voice_state = model.get_state_for_audio_prompt(voice_path)
    return model, voice_state


@dataclass
class LoadedLanguage:
    model: Any
    voice_state: dict[str, Any]


class BilingualSynthesizer:
    def __init__(self):
        self.voices = dict(DEFAULT_VOICES)
        self._loaded: dict[str, LoadedLanguage] = {}
        self._synthesis_lock = threading.Lock()

    @property
    def loaded_languages(self) -> list[str]:
        return sorted(self._loaded)

    def load_all(self) -> None:
        for language in LANGUAGES:
            if language not in self._loaded:
                model, voice_state = load_language_model(language, self.voices[language])
                self._loaded[language] = LoadedLanguage(model=model, voice_state=voice_state)

    def close(self) -> None:
        self._loaded.clear()
        gc.collect()

    def synthesize(self, text: str, requested_language: str | None = "auto") -> tuple[str, bytes]:
        language, normalized_text = select_language(text, requested_language)
        loaded = self._loaded.get(language)
        if loaded is None:
            raise PocketTTSConfigurationError(
                f"Pocket TTS language is not loaded: {language}"
            )
        if not self._synthesis_lock.acquire(blocking=False):
            raise RuntimeError("Pocket TTS is busy with another synthesis request")

        try:
            import numpy as np
            import scipy.io.wavfile

            audio = loaded.model.generate_audio(loaded.voice_state, normalized_text)
            samples = audio.detach().cpu().numpy()
            samples = np.clip(samples, -1.0, 1.0)
            pcm = (samples * 32767.0).astype(np.int16)
            output = io.BytesIO()
            scipy.io.wavfile.write(output, loaded.model.sample_rate, pcm)
            wav = output.getvalue()
            if len(wav) <= 44 or not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
                raise RuntimeError("Pocket TTS generated an invalid WAV payload")
            return language, wav
        finally:
            self._synthesis_lock.release()
