#!/usr/bin/env python3
"""Hot-migrate local Hermes route provider config to native openai-api."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import tempfile
import yaml
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROUTE_PROVIDER = "openai-api"
ROUTE_AUTO_MODEL = "clawroute/auto"
LEGACY_PROVIDER_VALUES = {"custom-1", "custom:custom-1", "custom:spartan-route"}
LEGACY_MODEL_VALUES = {"custom-1/clawroute/auto", "spartan-route/clawroute/auto"}
PROVIDER_KEYS = {
    "provider",
    "provider_snapshot",
    "providerSnapshot",
    "provider_id",
    "providerId",
    "model_provider",
    "modelProvider",
    "summary_provider",
    "summaryProvider",
}
MODEL_KEYS = {"default", "model", "model_snapshot", "modelSnapshot", "default_model", "defaultModel", "summary_model", "summaryModel"}
ROUTE_PROVIDER_NAMES = {"custom-1", "Spartan Route"}
ROUTE_BASE_URLS = {"${OPENAI_BASE_URL}", "http://spartan-route:18790/v1", "http://clawroute:18790/v1"}
ROUTE_KEY_MARKERS = {"CUSTOM_1_API_KEY"}
ROUTE_AUTO_ALIASES = {"clawroute/auto", "custom-1/clawroute/auto", "spartan-route/clawroute/auto"}
REASONING_PLUGIN = "spartan-cache-lineage"


@dataclass(frozen=True)
class MigrationResult:
    path: Path
    kind: str
    changed: bool
    backup_path: Path | None = None


def discover_targets(root: Path) -> list[tuple[str, Path]]:
    profiles = root / "profiles"
    targets: list[tuple[str, Path]] = [
        ("env", root / ".env"),
        ("config", root / "config.yaml"),
        ("jobs", root / "cron" / "jobs.json"),
    ]
    if profiles.exists():
        for profile in sorted(path for path in profiles.iterdir() if path.is_dir()):
            targets.append(("env", profile / ".env"))
            targets.append(("config", profile / "config.yaml"))
            targets.append(("jobs", profile / "cron" / "jobs.json"))
    return [(kind, path) for kind, path in targets if path.exists()]


def _line_value(line: str) -> str:
    return line.split(":", 1)[1].strip().strip("'\"") if ":" in line else ""


def _replace_yaml_value(line: str, keys: tuple[str, ...], legacy_values: set[str], new_value: str) -> str:
    key_pattern = "|".join(re.escape(key) for key in keys)
    value_pattern = "|".join(re.escape(value) for value in sorted(legacy_values, key=len, reverse=True))
    match = re.match(rf"^(?P<prefix>\s*(?:{key_pattern}):\s*)(?P<quote>['\"]?)(?P<value>{value_pattern})(?P=quote)(?P<suffix>\s*)$", line)
    if not match:
        return line
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{new_value}{quote}{match.group('suffix')}"


def _strip_yaml_route_model_prefix(line: str, keys: tuple[str, ...]) -> str:
    key_pattern = "|".join(re.escape(key) for key in keys)
    match = re.match(rf"^(?P<prefix>\s*(?:{key_pattern}):\s*)(?P<quote>['\"]?)(?P<route>custom-1|spartan-route)/(?P<model>[^'\"#\s]+)(?P=quote)(?P<suffix>\s*)$", line)
    if not match:
        return line
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{match.group('model')}{quote}{match.group('suffix')}"


def _route_custom_provider_block(block: list[str]) -> bool:
    text = "".join(block)
    name_match = re.search(r"^\s*-?\s*name:\s*['\"]?(custom-1|Spartan Route)['\"]?\s*$", text, re.MULTILINE)
    if not name_match:
        return False
    has_base_url = any(marker in text for marker in ROUTE_BASE_URLS)
    has_key = any(marker in text for marker in ROUTE_KEY_MARKERS)
    has_route_model = any(alias in text for alias in ROUTE_AUTO_ALIASES)
    return has_base_url or has_key or has_route_model


def _remove_route_custom_provider_blocks(lines: list[str]) -> list[str]:
    output: list[str] = []
    index = 0
    while index < len(lines):
        if not re.match(r"^custom_providers:\s*(?:#.*)?$", lines[index].rstrip("\n")):
            output.append(lines[index])
            index += 1
            continue

        header = lines[index]
        index += 1
        child_lines: list[str] = []
        while index < len(lines):
            line = lines[index]
            is_sequence_item = bool(re.match(r"^-\s+name:\s*", line))
            if line.strip() and not line.startswith((" ", "\t", "#")) and not is_sequence_item:
                break
            child_lines.append(line)
            index += 1

        kept: list[str] = []
        cursor = 0
        while cursor < len(child_lines):
            if re.match(r"^\s*-\s*name:\s*", child_lines[cursor]):
                block = [child_lines[cursor]]
                cursor += 1
                while cursor < len(child_lines) and not re.match(r"^\s*-\s*name:\s*", child_lines[cursor]):
                    block.append(child_lines[cursor])
                    cursor += 1
                if not _route_custom_provider_block(block):
                    kept.extend(block)
            else:
                kept.append(child_lines[cursor])
                cursor += 1

        if any(line.strip() for line in kept):
            output.append(header)
            output.extend(kept)
    return output


def _model_block_route_evidence(lines: list[str]) -> bool:
    text = "\n".join(line.strip() for line in lines)
    return (
        any(alias in text for alias in ROUTE_AUTO_ALIASES)
        or any(marker in text for marker in ROUTE_BASE_URLS)
        or any(marker in text for marker in ROUTE_KEY_MARKERS)
    )


def _migrate_plain_custom_route_provider(lines: list[str]) -> list[str]:
    updated = list(lines)
    index = 0
    while index < len(updated):
        if updated[index].strip() != "model:" or updated[index].startswith((" ", "\t")):
            index += 1
            continue
        start = index
        end = len(updated)
        for cursor in range(start + 1, len(updated)):
            if updated[cursor].strip() and not updated[cursor].startswith((" ", "\t")):
                end = cursor
                break
        block = updated[start:end]
        if _model_block_route_evidence(block):
            for cursor in range(start, end):
                line = updated[cursor].rstrip("\n")
                newline = "\n" if updated[cursor].endswith("\n") else ""
                updated[cursor] = _replace_yaml_value(line, ("provider", "summary_provider"), {"custom"}, ROUTE_PROVIDER) + newline
        index = end
    return updated


def _migrate_openai_model_key_env(lines: list[str]) -> list[str]:
    updated = list(lines)
    index = 0
    while index < len(updated):
        if updated[index].strip() != "model:" or updated[index].startswith((" ", "\t")):
            index += 1
            continue
        start = index
        end = len(updated)
        for cursor in range(start + 1, len(updated)):
            if updated[cursor].strip() and not updated[cursor].startswith((" ", "\t")):
                end = cursor
                break
        block = updated[start:end]
        if any(_line_value(line) == ROUTE_PROVIDER for line in block if line.lstrip().startswith("provider:")):
            for cursor in range(start, end):
                line = updated[cursor].rstrip("\n")
                newline = "\n" if updated[cursor].endswith("\n") else ""
                updated[cursor] = _replace_yaml_value(
                    line,
                    ("api_key_env", "key_env"),
                    {"CUSTOM_1_API_KEY"},
                    "OPENAI_API_KEY",
                ) + newline
        index = end
    return updated


def _ensure_route_responses_api_mode(lines: list[str]) -> list[str]:
    updated = list(lines)
    index = 0
    while index < len(updated):
        if updated[index].strip() != "model:" or updated[index].startswith((" ", "\t")):
            index += 1
            continue
        start = index
        end = len(updated)
        for cursor in range(start + 1, len(updated)):
            if updated[cursor].strip() and not updated[cursor].startswith((" ", "\t")):
                end = cursor
                break
        block = updated[start:end]
        values = {
            line.split(":", 1)[0].strip(): _line_value(line)
            for line in block
            if ":" in line and not line.lstrip().startswith("#")
        }
        # Only an explicit Codex model proves the subscription realm. Auto
        # aliases can currently resolve to OpenRouter/OpenAI profiles and must
        # stay on Chat Completions unless an operator changes them explicitly.
        route_model = values.get("default", "").startswith("codex/")
        route_model = route_model or values.get("model", "").startswith("codex/")
        if values.get("provider") != ROUTE_PROVIDER or not route_model:
            index = end
            continue
        api_mode_index = next(
            (cursor for cursor in range(start + 1, end) if updated[cursor].lstrip().startswith("api_mode:")),
            None,
        )
        if api_mode_index is not None:
            raw = updated[api_mode_index]
            indent = raw[: len(raw) - len(raw.lstrip())]
            newline = "\n" if raw.endswith("\n") else ""
            updated[api_mode_index] = f"{indent}api_mode: codex_responses{newline}"
        else:
            provider_index = next(
                cursor for cursor in range(start + 1, end) if updated[cursor].lstrip().startswith("provider:")
            )
            indent = updated[provider_index][: len(updated[provider_index]) - len(updated[provider_index].lstrip())]
            updated.insert(provider_index + 1, f"{indent}api_mode: codex_responses\n")
            end += 1
        index = end
    return updated


def _uses_route_responses(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    selected = str(model.get("default") or model.get("model") or "").strip()
    return (
        model.get("provider") == ROUTE_PROVIDER
        and model.get("api_mode") == "codex_responses"
        and selected.startswith("codex/")
    )


def _ensure_reasoning_plugin(source: str) -> str:
    data = yaml.safe_load(source)
    if not _uses_route_responses(data):
        return source
    plugins = data.get("plugins") if isinstance(data.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if REASONING_PLUGIN in enabled:
        return source

    lines = source.splitlines(keepends=True)
    plugin_index = next(
        (index for index, line in enumerate(lines) if re.match(r"^plugins:\s*(?:#.*)?$", line.rstrip("\n"))),
        None,
    )
    if plugin_index is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend(["plugins:\n", "  enabled:\n", f"    - {REASONING_PLUGIN}\n", "  disabled: []\n"])
        return "".join(lines)

    block_end = len(lines)
    for index in range(plugin_index + 1, len(lines)):
        if lines[index].strip() and not lines[index].startswith((" ", "\t", "#")):
            block_end = index
            break
    enabled_index = next(
        (index for index in range(plugin_index + 1, block_end) if re.match(r"^\s+enabled:\s*", lines[index])),
        None,
    )
    if enabled_index is None:
        lines.insert(plugin_index + 1, f"  enabled:\n    - {REASONING_PLUGIN}\n")
        return "".join(lines)

    enabled_line = lines[enabled_index]
    indent = enabled_line[: len(enabled_line) - len(enabled_line.lstrip())]
    inline_value = enabled_line.split(":", 1)[1].strip().split("#", 1)[0].strip()
    if inline_value:
        existing = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
        replacement = [f"{indent}enabled:\n"]
        replacement.extend(f"{indent}  - {item}\n" for item in existing)
        replacement.append(f"{indent}  - {REASONING_PLUGIN}\n")
        lines[enabled_index : enabled_index + 1] = replacement
        return "".join(lines)

    insert_at = enabled_index + 1
    while insert_at < block_end:
        line = lines[insert_at]
        if line.strip() and len(line) - len(line.lstrip()) <= len(indent):
            break
        insert_at += 1
    lines.insert(insert_at, f"{indent}  - {REASONING_PLUGIN}\n")
    return "".join(lines)


def _validate_background_review_realm(data: Any, path: Path) -> None:
    if not isinstance(data, dict):
        return
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    if model.get("api_mode") != "codex_responses":
        return
    auxiliary = data.get("auxiliary") if isinstance(data.get("auxiliary"), dict) else {}
    review = auxiliary.get("background_review") if isinstance(auxiliary.get("background_review"), dict) else {}
    provider = str(review.get("provider") or "auto").strip()
    if provider in {"", "auto"}:
        return
    api_mode = str(review.get("api_mode") or "").strip()
    base_url = str(review.get("base_url") or "").strip()
    review_model = str(review.get("model") or "").strip()
    same_route = (
        api_mode == "codex_responses"
        and (base_url in ROUTE_BASE_URLS or review_model.startswith(("clawroute/", "codex/")))
    )
    if not same_route:
        raise ValueError(
            f"{path}: auxiliary.background_review crosses the codex_subscription realm; "
            "use provider: auto or an explicit codex_responses Spartan Route runtime"
        )


def migrate_config_text(source: str) -> str:
    lines = source.splitlines(keepends=True)
    lines = _remove_route_custom_provider_blocks(lines)
    lines = _migrate_plain_custom_route_provider(lines)
    migrated: list[str] = []
    for raw_line in lines:
        newline = "\n" if raw_line.endswith("\n") else ""
        line = raw_line[:-1] if newline else raw_line
        updated = _replace_yaml_value(line, ("provider", "summary_provider"), LEGACY_PROVIDER_VALUES, ROUTE_PROVIDER)
        updated = _replace_yaml_value(updated, ("default", "model", "summary_model"), LEGACY_MODEL_VALUES, ROUTE_AUTO_MODEL)
        updated = _strip_yaml_route_model_prefix(updated, ("default", "model", "summary_model"))
        migrated.append(updated + newline)
    updated = "".join(_ensure_route_responses_api_mode(_migrate_openai_model_key_env(migrated)))
    return _ensure_reasoning_plugin(updated)


def _dict_has_route_model(value: dict[str, Any]) -> bool:
    for key, child in value.items():
        if key in MODEL_KEYS and isinstance(child, str):
            if child in ROUTE_AUTO_ALIASES or child.startswith(("custom-1/", "spartan-route/")):
                return True
    return False


def _migrate_model_value(value: str) -> tuple[str, bool]:
    if value in LEGACY_MODEL_VALUES:
        return ROUTE_AUTO_MODEL, True
    for prefix in ("custom-1/", "spartan-route/"):
        if value.startswith(prefix):
            return value.removeprefix(prefix), True
    return value, False


def migrate_json_value(key: str | None, value: Any) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        updated: dict[str, Any] = {}
        for child_key, child_value in value.items():
            updated_value, child_changed = migrate_json_value(str(child_key), child_value)
            updated[child_key] = updated_value
            changed = changed or child_changed
        route_model = _dict_has_route_model(updated)
        for child_key, child_value in list(updated.items()):
            if child_key in PROVIDER_KEYS and isinstance(child_value, str):
                if child_value in LEGACY_PROVIDER_VALUES or (child_value == "custom" and route_model):
                    updated[child_key] = ROUTE_PROVIDER
                    changed = True
        return updated, changed
    if isinstance(value, list):
        changed = False
        updated_list = []
        for item in value:
            updated_item, item_changed = migrate_json_value(None, item)
            updated_list.append(updated_item)
            changed = changed or item_changed
        return updated_list, changed
    if not isinstance(value, str) or key is None:
        return value, False

    if key in PROVIDER_KEYS and value in LEGACY_PROVIDER_VALUES:
        return ROUTE_PROVIDER, True
    if key in MODEL_KEYS:
        return _migrate_model_value(value)
    return value, False


def migrate_jobs_text(source: str, path: Path) -> str:
    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    migrated, changed = migrate_json_value(None, data)
    if not changed:
        return source
    return json.dumps(migrated, indent=2, ensure_ascii=False) + "\n"


def _env_value_is_literal_ref(value: str) -> bool:
    value = value.strip()
    return value.startswith("${") and value.endswith("}")


def migrate_env_text(source: str) -> str:
    lines = source.splitlines(keepends=True)
    custom_index: int | None = None
    custom_value = ""
    openai_index: int | None = None
    openai_value = ""

    for index, raw_line in enumerate(lines):
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        key = key.strip()
        value = value.rstrip("\r\n")
        if key == "OPENAI_API_KEY" and openai_index is None:
            openai_index = index
            openai_value = value
        elif key == "CUSTOM_1_API_KEY" and custom_index is None:
            custom_index = index
            custom_value = value

    if openai_index is not None:
        return source

    if custom_index is None or not custom_value:
        return source

    original = lines[custom_index]
    body = original.rstrip("\r\n")
    newline = original[len(body):] or "\n"
    lines[custom_index] = body + newline
    openai_value = "${OPENAI_API_KEY}" if _env_value_is_literal_ref(custom_value) else custom_value
    lines.insert(custom_index + 1, f"OPENAI_API_KEY={openai_value}{newline}")
    return "".join(lines)


def _backup_path(root: Path, path: Path, backup_root: Path) -> Path:
    relative = path.relative_to(root)
    return backup_root / relative


def _stage_text(path: Path, content: str, mode: int) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.route-migrate.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(mode))
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _stage_symlink(path: Path, target: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.route-link.",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        os.symlink(target, temporary)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _restore_bytes(path: Path, payload: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.route-rollback.",
        dir=path.parent,
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


def migrate_root(
    root: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
    backup_stamp: str | None = None,
) -> list[MigrationResult]:
    root = root.expanduser().resolve()
    targets = discover_targets(root)
    planned: list[tuple[str, Path, str, str]] = []
    plugin_links: list[tuple[Path, str, bool]] = []

    for kind, path in targets:
        source = path.read_text(encoding="utf-8")
        if kind == "config":
            try:
                yaml.safe_load(source)
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: invalid YAML before migration: {exc}") from exc
            updated = migrate_config_text(source)
            try:
                migrated_data = yaml.safe_load(updated)
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: migration produced invalid YAML: {exc}") from exc
            _validate_background_review_realm(migrated_data, path)
            if _uses_route_responses(migrated_data) and path.parent.parent == root / "profiles":
                link_path = path.parent / "plugins" / REASONING_PLUGIN
                if not link_path.parent.is_dir():
                    raise ValueError(f"{path}: profile plugin directory is missing: {link_path.parent}")
                link_target = os.path.relpath(root / "plugins" / REASONING_PLUGIN, link_path.parent)
                if link_path.is_symlink():
                    if os.readlink(link_path) != link_target:
                        raise ValueError(f"{path}: reasoning plugin link has an unexpected target: {link_path}")
                    link_changed = False
                elif link_path.exists():
                    raise ValueError(f"{path}: reasoning plugin path already exists and is not a managed link: {link_path}")
                else:
                    link_changed = True
                plugin_links.append((link_path, link_target, link_changed))
        elif kind == "jobs":
            updated = migrate_jobs_text(source, path)
        elif kind == "env":
            updated = migrate_env_text(source)
        else:
            raise ValueError(f"unsupported target kind {kind!r} for {path}")
        planned.append((kind, path, source, updated))

    stamp = backup_stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_root = root / "backups" / f"openai-api-provider-hot-migration-{stamp}"
    results: list[MigrationResult] = []

    changed_entries = [entry for entry in planned if entry[2] != entry[3]]
    changed_links = [entry for entry in plugin_links if entry[2]]
    if dry_run or (not changed_entries and not changed_links):
        results = [
            MigrationResult(path=path, kind=kind, changed=source != updated)
            for kind, path, source, updated in planned
        ]
        results.extend(
            MigrationResult(path=path, kind="plugin_link", changed=changed)
            for path, _target, changed in plugin_links
        )
        return results

    originals = {
        path: (path.read_bytes(), path.stat().st_mode)
        for _kind, path, _source, _updated in changed_entries
    }
    staged: dict[Path, Path] = {}
    staged_links: dict[Path, Path] = {}
    installed: list[Path] = []
    installed_links: list[Path] = []
    backup_paths: dict[Path, Path] = {}
    try:
        # Finish every parse, serialization, backup, and staged-file preflight
        # before replacing the first live Hermes file.
        for _kind, path, _source, updated in changed_entries:
            staged[path] = _stage_text(path, updated, originals[path][1])
        for path, target, _changed in changed_links:
            staged_links[path] = _stage_symlink(path, target)

        if backup and changed_entries:
            backup_root.mkdir(parents=True, exist_ok=False)
            for _kind, path, _source, _updated in changed_entries:
                destination = _backup_path(root, path, backup_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                backup_paths[path] = destination

        for _kind, path, _source, _updated in changed_entries:
            os.replace(staged[path], path)
            installed.append(path)
        for path, _target, _changed in changed_links:
            os.replace(staged_links[path], path)
            installed_links.append(path)
    except Exception as error:
        for path in reversed(installed_links):
            path.unlink(missing_ok=True)
        rollback_error: Exception | None = None
        for path in reversed(installed):
            payload, mode = originals[path]
            try:
                _restore_bytes(path, payload, mode)
            except Exception as restore_error:
                rollback_error = rollback_error or restore_error
        if backup and backup_root.exists() and not installed:
            shutil.rmtree(backup_root, ignore_errors=True)
        if rollback_error is not None:
            raise RuntimeError(
                f"route migration failed and rollback was incomplete: {rollback_error}"
            ) from error
        raise
    finally:
        for temporary in (*staged.values(), *staged_links.values()):
            temporary.unlink(missing_ok=True)

    for kind, path, source, updated in planned:
        changed = source != updated
        results.append(
            MigrationResult(
                path=path,
                kind=kind,
                changed=changed,
                backup_path=backup_paths.get(path),
            )
        )
    results.extend(
        MigrationResult(path=path, kind="plugin_link", changed=changed)
        for path, _target, changed in plugin_links
    )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / ".custom_claw" / "hermes",
        help="Hermes data root to migrate. Defaults to the legacy Hermes home.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create backups for changed files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = migrate_root(args.root, dry_run=args.dry_run, backup=not args.no_backup)
    except ValueError as exc:
        print(f"FATAL: {exc}")
        return 1

    changed = [result for result in results if result.changed]
    verb = "Would patch" if args.dry_run else "Patched"
    for result in results:
        if result.changed:
            suffix = f" backup={result.backup_path}" if result.backup_path else ""
            print(f"{verb}: {result.path}{suffix}")
        else:
            print(f"Skip: {result.path} already migrated or no legacy route provider/env")
    print(f"Summary: {len(changed)} changed, {len(results) - len(changed)} unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
