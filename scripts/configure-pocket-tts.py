#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml


PROVIDER_NAME = "pocket-tts"
PROVIDER_CONFIG = {
    "type": "command",
    "command": (
        "python3 /opt/hermes/bootstrap/pocket-tts-client.py "
        "--input {input_path} --output {output_path}"
    ),
    "output_format": "wav",
    "voice_compatible": True,
    "timeout": 210,
    "max_text_length": 2000,
}


class ConfigurationError(RuntimeError):
    pass


def load_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Hermes config does not exist: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read Hermes config {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError("Hermes config root must be a YAML mapping")
    return value


def validate_tts_shape(config: dict[str, Any]) -> None:
    tts = config.get("tts")
    if tts is not None and not isinstance(tts, dict):
        raise ConfigurationError("Hermes tts config must be a YAML mapping")
    if isinstance(tts, dict):
        providers = tts.get("providers")
        if providers is not None and not isinstance(providers, dict):
            raise ConfigurationError("Hermes tts.providers config must be a YAML mapping")


def state_path(config_path: Path) -> Path:
    return config_path.with_name(f".{config_path.name}.pocket-tts-state.json")


def backup_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.name}.before-pocket-tts")


def atomic_write_yaml(path: Path, config: dict[str, Any], mode: int) -> None:
    serialized = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: dict[str, Any], mode: int) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, payload: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def commit_config_and_state(
    config_path: Path,
    config: dict[str, Any],
    config_mode: int,
    existing_state_path: Path,
    state: dict[str, Any] | None,
) -> None:
    original_config = config_path.read_bytes()
    original_state = (
        (existing_state_path.read_bytes(), existing_state_path.stat().st_mode)
        if existing_state_path.exists()
        else None
    )
    try:
        atomic_write_yaml(config_path, config, config_mode)
        if state is None:
            existing_state_path.unlink(missing_ok=True)
        else:
            atomic_write_json(existing_state_path, state, config_mode)
    except Exception as exc:
        try:
            atomic_write_bytes(config_path, original_config, config_mode)
            if original_state is None:
                existing_state_path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(existing_state_path, *original_state)
        except Exception as rollback_exc:
            raise ConfigurationError(
                "Pocket TTS config update failed and rollback was incomplete"
            ) from rollback_exc
        raise ConfigurationError(
            "Pocket TTS config update failed; changes rolled back"
        ) from exc


def enable(config_path: Path, client_path: Path) -> bool:
    if not client_path.is_file():
        raise ConfigurationError(f"Pocket TTS Hermes client does not exist: {client_path}")

    config = load_mapping(config_path)
    validate_tts_shape(config)
    original_mode = config_path.stat().st_mode
    tts = config.setdefault("tts", {})
    providers = tts.setdefault("providers", {})
    existing_state_path = state_path(config_path)

    desired = tts.get("provider") == PROVIDER_NAME and providers.get(PROVIDER_NAME) == PROVIDER_CONFIG
    if desired:
        return False

    previous_provider = tts.get("provider")
    state = {
        "previous_provider": previous_provider,
        "provider_name": PROVIDER_NAME,
    }
    if existing_state_path.exists():
        try:
            prior_state = json.loads(existing_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"Pocket TTS state file is invalid: {existing_state_path}: {exc}"
            ) from exc
        if isinstance(prior_state, dict):
            state = prior_state

    providers[PROVIDER_NAME] = dict(PROVIDER_CONFIG)
    tts["provider"] = PROVIDER_NAME

    destination_backup = backup_path(config_path)
    if not destination_backup.exists():
        atomic_write_bytes(destination_backup, config_path.read_bytes(), original_mode)

    commit_config_and_state(
        config_path, config, original_mode, existing_state_path, state
    )
    return True


def disable(config_path: Path) -> bool:
    config = load_mapping(config_path)
    validate_tts_shape(config)
    original_mode = config_path.stat().st_mode
    tts = config.setdefault("tts", {})
    providers = tts.setdefault("providers", {})
    existing_state_path = state_path(config_path)
    state: dict[str, Any] = {}
    if existing_state_path.exists():
        try:
            loaded = json.loads(existing_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"Pocket TTS state file is invalid: {existing_state_path}: {exc}"
            ) from exc
        if isinstance(loaded, dict):
            state = loaded

    changed = providers.pop(PROVIDER_NAME, None) is not None
    if tts.get("provider") == PROVIDER_NAME:
        previous = state.get("previous_provider")
        tts["provider"] = previous if isinstance(previous, str) and previous else "edge"
        changed = True
    if not providers:
        tts.pop("providers", None)

    if not changed:
        return False
    commit_config_and_state(
        config_path, config, original_mode, existing_state_path, None
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable", action="store_true")
    action.add_argument("--disable", action="store_true")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--client",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "infra"
        / "pocket-tts"
        / "hermes_client.py",
    )
    args = parser.parse_args()

    lock_path = args.config.with_name(f".{args.config.name}.pocket-tts.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changed = enable(args.config, args.client) if args.enable else disable(args.config)
    print(
        "Pocket TTS configuration updated."
        if changed
        else "Pocket TTS configuration already matched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
