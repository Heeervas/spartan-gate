#!/usr/bin/env python3
"""Install a Hermes self-improvement governor into a writable HERMES_HOME."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import textwrap


PLUGIN_NAME = "self-improvement-governor"
PLUGIN_VERSION = "0.1.0"
REFINER_REL = Path("autonomous-ai-agents") / "skill-workflow-refiner"


PLUGIN_YAML = f"""\
name: {PLUGIN_NAME}
version: "{PLUGIN_VERSION}"
description: "Runtime governor for Hermes background skill-review prompts."
author: Spartan Gate
kind: standalone
requires_env: []
provides_tools: []
provides_hooks: []
"""


PLUGIN_INIT = r'''from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


_GENERIC_SKILL_REVIEW_PROMPT = """Review the conversation above and update the skill library only when the session contains durable, reusable process learning.

This review is not a quota. Many sessions should end with exactly:
Nothing to save.

Preserve explicit user requests to create or modify skills. If the user directly asked for skill work, do that work within the requested scope.

Target shape:
- Prefer generic, class-level skills that describe how to handle a repeatable kind of work.
- Convert concrete session details into generic process guidance.
- Do not encode project, client, account, vendor, workspace, ticket, PR, or one-off task specifics as standing skill rules unless the user explicitly requested that specific skill.
- Keep active SKILL.md files concise and operational. Put reusable but lengthy detail into support files, and put raw/client/session history outside active skills when it is only reference material.
- Avoid creating narrow one-session skills. Update an existing umbrella skill first when one fits.

When a skill update is warranted:
1. Prefer a currently loaded or directly relevant existing skill.
2. If the skill is bloated, overlapping, or too specific, use skill-optimizer to review the shape before writing.
3. For workflow-shaped changes, compaction, or changes that need execution validation, use skill-workflow-refiner when it is available.
4. If a support file is needed, add a concise pointer from SKILL.md. Do not mirror full tool logs or long upstream docs into active skills.

Good skill lessons are generic transformations from the session, such as a reusable validation sequence, decision rule, failure-mode check, or communication/process preference. Bad skill lessons are merely a description of what happened today.

Do not capture as skills:
- temporary setup failures, missing local binaries, unconfigured credentials, or post-migration path mismatches;
- broad negative claims that a tool or feature does not work when the problem might be local or temporary;
- raw client/project/account narratives;
- task-specific instructions that should live in memory, a project document, or an external reference.

If useful learning exists but belongs outside active skills, state the recommended destination briefly instead of creating skill bloat.
"""


_GENERIC_COMBINED_REVIEW_PROMPT = """Review the conversation above for two possible durable updates.

Memory: save durable facts about who the user is, their preferences, or the current state of ongoing work when appropriate.

Skills: update the skill library only when the session contains durable, reusable process learning. This review is not a quota. Many sessions should end with exactly:
Nothing to save.

Preserve explicit user requests to create or modify skills. If the user directly asked for skill work, do that work within the requested scope.

For skill updates:
- Prefer generic, class-level skills that describe how to handle a repeatable kind of work.
- Convert concrete session details into generic process guidance.
- Do not encode project, client, account, vendor, workspace, ticket, PR, or one-off task specifics as standing skill rules unless the user explicitly requested that specific skill.
- Keep active SKILL.md files concise and operational. Put reusable but lengthy detail into support files, and put raw/client/session history outside active skills when it is only reference material.
- Avoid creating narrow one-session skills. Update an existing umbrella skill first when one fits.

When a skill update is warranted:
1. Prefer a currently loaded or directly relevant existing skill.
2. If the skill is bloated, overlapping, or too specific, use skill-optimizer to review the shape before writing.
3. For workflow-shaped changes, compaction, or changes that need execution validation, use skill-workflow-refiner when it is available.
4. If a support file is needed, add a concise pointer from SKILL.md. Do not mirror full tool logs or long upstream docs into active skills.

Do not capture temporary setup failures, raw client/project/account narratives, broad negative tool claims, or one-off task details as standing skill rules.

Act only on real signal. If nothing durable stands out for memory or skills, say "Nothing to save." and stop.
"""


def _patch_background_review() -> bool:
    try:
        import agent.background_review as background_review
    except Exception as exc:  # pragma: no cover - depends on Hermes import path
        logger.warning("self-improvement-governor: cannot import background_review: %s", exc)
        return False

    background_review._SKILL_REVIEW_PROMPT = _GENERIC_SKILL_REVIEW_PROMPT
    background_review._COMBINED_REVIEW_PROMPT = _GENERIC_COMBINED_REVIEW_PROMPT

    original = getattr(background_review, "spawn_background_review_thread", None)
    if original is None:
        logger.warning("self-improvement-governor: spawn_background_review_thread missing")
        return False
    if getattr(original, "_self_improvement_governor_patched", False):
        return True

    def wrapped_spawn_background_review_thread(
        agent: Any,
        messages_snapshot: list[dict],
        review_memory: bool = False,
        review_skills: bool = False,
    ):
        if review_skills:
            setattr(agent, "_SKILL_REVIEW_PROMPT", _GENERIC_SKILL_REVIEW_PROMPT)
            setattr(agent, "_COMBINED_REVIEW_PROMPT", _GENERIC_COMBINED_REVIEW_PROMPT)
        return original(
            agent,
            messages_snapshot,
            review_memory=review_memory,
            review_skills=review_skills,
        )

    wrapped_spawn_background_review_thread._self_improvement_governor_patched = True
    wrapped_spawn_background_review_thread._self_improvement_governor_original = original
    background_review.spawn_background_review_thread = wrapped_spawn_background_review_thread
    return True


def _patch_run_agent_class() -> bool:
    try:
        import run_agent
    except Exception as exc:  # pragma: no cover - depends on Hermes import path
        logger.debug("self-improvement-governor: run_agent not ready: %s", exc)
        return False

    agent_cls = getattr(run_agent, "AIAgent", None)
    if agent_cls is None:
        return False

    agent_cls._SKILL_REVIEW_PROMPT = _GENERIC_SKILL_REVIEW_PROMPT
    agent_cls._COMBINED_REVIEW_PROMPT = _GENERIC_COMBINED_REVIEW_PROMPT
    return True


def apply_governor() -> bool:
    """Patch Hermes background skill reviews in the current Python process."""
    patched_review = _patch_background_review()
    patched_agent = _patch_run_agent_class()
    if patched_review:
        logger.info(
            "self-improvement-governor: generic skill-review prompt active "
            "(AIAgent patched=%s)",
            patched_agent,
        )
    return patched_review


def register(ctx: Any) -> None:
    apply_governor()
'''


def _config_files(root: Path) -> list[Path]:
    files = [root / "config.yaml"]
    profiles = root / "profiles"
    if profiles.is_dir():
        files.extend(sorted(profiles.glob("*/config.yaml")))
    return [path for path in files if path.exists()]


def _target_interval(path: Path, root: Path, default_interval: int, work_interval: int) -> int:
    try:
        profile = path.relative_to(root / "profiles").parts[0]
    except ValueError:
        return default_interval
    return work_interval if profile == "work" else default_interval


def _enable_plugin_in_config(text: str) -> str:
    if re.search(rf"^\s*-\s+{re.escape(PLUGIN_NAME)}\s*$", text, re.MULTILINE):
        return text
    lines = text.splitlines(keepends=True)
    try:
        plugins_idx = next(i for i, line in enumerate(lines) if line == "plugins:\n")
        enabled_idx = next(
            i for i in range(plugins_idx + 1, len(lines)) if lines[i] == "  enabled:\n"
        )
        disabled_idx = next(
            i for i in range(enabled_idx + 1, len(lines)) if lines[i] == "  disabled: []\n"
        )
    except StopIteration as exc:
        raise ValueError("config must contain plugins.enabled followed by plugins.disabled") from exc
    lines.insert(disabled_idx, f"  - {PLUGIN_NAME}\n")
    return "".join(lines)


def update_config(path: Path, root: Path, default_interval: int, work_interval: int) -> bool:
    text = path.read_text(encoding="utf-8")
    if text.count("creation_nudge_interval:") != 1:
        raise ValueError(f"{path}: expected exactly one creation_nudge_interval")
    interval = _target_interval(path, root, default_interval, work_interval)
    updated = re.sub(
        r"creation_nudge_interval:\s*\d+",
        f"creation_nudge_interval: {interval}",
        text,
        count=1,
    )
    updated = _enable_plugin_in_config(updated)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def install_plugin(root: Path) -> bool:
    plugin_dir = root / "plugins" / PLUGIN_NAME
    plugin_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    files = {
        plugin_dir / "plugin.yaml": PLUGIN_YAML,
        plugin_dir / "__init__.py": PLUGIN_INIT,
    }
    for path, content in files.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            changed = True
    return changed


def ensure_refiner_symlinks(root: Path) -> list[Path]:
    central = root / "skills" / REFINER_REL
    if not (central / "SKILL.md").exists():
        raise FileNotFoundError(f"central refiner skill is missing: {central}")

    created: list[Path] = []
    profiles = root / "profiles"
    if not profiles.is_dir():
        return created
    for skills_dir in sorted(profiles.glob("*/skills")):
        parent = skills_dir / REFINER_REL.parent
        if not parent.exists():
            continue
        dest = parent / REFINER_REL.name
        target = Path("/opt/data/skills") / REFINER_REL
        if dest.is_symlink():
            if dest.readlink() != target:
                raise ValueError(f"{dest} points to {dest.readlink()}, expected {target}")
            continue
        if dest.exists():
            continue
        dest.symlink_to(target)
        created.append(dest)
    return created


def install(root: Path, default_interval: int, work_interval: int) -> dict[str, object]:
    root = root.resolve()
    changed_plugin = install_plugin(root)
    changed_configs = [
        path for path in _config_files(root) if update_config(path, root, default_interval, work_interval)
    ]
    created_symlinks = ensure_refiner_symlinks(root)
    return {
        "plugin_changed": changed_plugin,
        "configs_changed": [str(path) for path in changed_configs],
        "symlinks_created": [str(path) for path in created_symlinks],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the Hermes self-improvement governor plugin, enable it in "
            "base/profile configs, set skill-review cadence, and add missing "
            "skill-workflow-refiner profile symlinks."
        )
    )
    parser.add_argument("--root", default="/opt/data", help="Hermes home root")
    parser.add_argument("--default-interval", type=int, default=30)
    parser.add_argument("--work-interval", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = install(Path(args.root), args.default_interval, args.work_interval)
    print(
        textwrap.dedent(
            f"""\
            self-improvement-governor installed
            plugin_changed: {result['plugin_changed']}
            configs_changed: {len(result['configs_changed'])}
            symlinks_created: {len(result['symlinks_created'])}
            """
        ).strip()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
