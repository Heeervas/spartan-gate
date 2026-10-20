"""Shared, profile-independent Hermes instructions.

The native prompt section is persisted with the session.  Request middleware is
only a safety net for Hermes' silent section-size/aggregate-budget skips and
for older sessions whose stored prompt predates this plugin.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any, Mapping


logger = logging.getLogger(__name__)
MASTER_PATH = Path("/opt/data/MASTER.md")
SECTION_ID = "shared-master"
SECTION_HEADING = "## Plugin Context: shared-master"
FALLBACK_MARKER = "## Shared MASTER.md\n"
SECTIONS_START = "<!-- hermes-plugin-sections:start -->\n"
SECTIONS_END = "\n<!-- hermes-plugin-sections:end -->"
SECTION_FRAME = re.compile(
    r"## Plugin Context: (?P<id>[a-z0-9][a-z0-9._-]{0,127})\n"
    r"<!-- hermes-plugin-section-chars:(?P<chars>[0-9]{1,4}) -->\n\n"
)
ADVISORY_CHARS = 4_000
NATIVE_SECTION_CHARS = 4_000
EXCLUDED_PROFILES = frozenset({"empty", "workstation", "ssr"})
MISSING_DIAGNOSTIC = (
    "Shared MASTER.md is unavailable. Tell the operator at the start of this session "
    "that common instructions could not be loaded and need restoration."
)


def _excluded(profile: str) -> bool:
    name = profile.strip().lower()
    return name in EXCLUDED_PROFILES or name.startswith(("ssr_", "ssr-"))


def _active_profile() -> str:
    # Hermes binds the active profile home around CLI, gateway and delegated
    # turns.  The plugin manager itself is shared by the gateway process.
    from hermes_cli.profiles import current_profile_name

    return str(current_profile_name(default="default") or "default")


def _read_master() -> str | None:
    try:
        text = MASTER_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        logger.error("Shared MASTER.md unavailable at %s (%s); eligible sessions lack shared instructions", MASTER_PATH, type(exc).__name__)
        return None
    if not text:
        logger.error("Shared MASTER.md at %s is empty; eligible sessions lack shared instructions", MASTER_PATH)
        return None
    return text


def _system_parts(request: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in ("instructions", "system"):
        value = request.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(
                block.get("text", "") for block in value
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
    messages = request.get("messages")
    if isinstance(messages, list):
        parts.extend(
            item.get("content", "") for item in messages
            if isinstance(item, dict) and item.get("role") in ("system", "developer")
            and isinstance(item.get("content"), str)
        )
    return parts


def _has_native_section(system_parts: list[str], content: str) -> bool:
    # Parse the installed Hermes length-framed container. A copied heading in
    # another prompt section or quoted page text cannot prove native inclusion.
    for part in system_parts:
        start = part.find(SECTIONS_START)
        if start < 0:
            continue
        start += len(SECTIONS_START)
        end = part.find(SECTIONS_END, start)
        if end < 0:
            continue
        framed = part[start:end]
        offset = 0
        found = False
        while offset < len(framed):
            match = SECTION_FRAME.match(framed, offset)
            if match is None:
                break
            body_end = match.end() + int(match.group("chars"))
            if body_end > len(framed):
                break
            if match.group("id") == SECTION_ID and framed[match.end():body_end] == content:
                found = True
            if body_end == len(framed):
                if found:
                    return True
                break
            if framed[body_end:body_end + 2] != "\n\n":
                break
            offset = body_end + 2
    return False


def _native_block(content: str) -> str:
    # Match the installed Hermes canonical frame exactly. A copied heading or
    # truncated body is not evidence that the policy reached the provider.
    return f"{SECTION_HEADING}\n<!-- hermes-plugin-section-chars:{len(content)} -->\n\n{content}"


def _append_system(request: Mapping[str, Any], block: str, api_mode: str) -> dict[str, Any] | None:
    updated = dict(request)
    if isinstance(updated.get("instructions"), str):
        updated["instructions"] = updated["instructions"].rstrip() + "\n\n" + block
        return updated
    system = updated.get("system")
    if isinstance(system, str):
        updated["system"] = system.rstrip() + "\n\n" + block
        return updated
    if isinstance(system, list):
        if api_mode == "bedrock_converse":
            updated["system"] = [*system, {"text": block}]
        else:
            updated["system"] = [*system, {"type": "text", "text": block}]
        return updated
    messages = updated.get("messages")
    if isinstance(messages, list):
        copied = list(messages)
        for index, item in enumerate(copied):
            if isinstance(item, dict) and item.get("role") == "system" and isinstance(item.get("content"), str):
                copied[index] = {**item, "content": item["content"].rstrip() + "\n\n" + block}
                updated["messages"] = copied
                return updated
        updated["messages"] = [{"role": "system", "content": block}, *copied]
        return updated
    if api_mode == "codex_responses":
        updated["instructions"] = block
        return updated
    if api_mode == "anthropic_messages":
        updated["system"] = block
        return updated
    if api_mode == "bedrock_converse":
        updated["system"] = [{"text": block}]
        return updated
    logger.error("Shared MASTER.md could not be inserted: unsupported provider request shape (%s)", api_mode)
    return None


def register(ctx: Any) -> None:
    # The file is read at plugin activation. A gateway reload is required to
    # adopt an edit; each active session keeps its prior persisted prompt.
    master = _read_master()
    advisory = (
        f"MASTER.md is {len(master)} characters (target 3000-3500). "
        "Mention this once at session start; preserve rules and suggest consolidation only during authorized maintenance."
        if master is not None and len(master) > ADVISORY_CHARS else ""
    )
    content = master + ("\n\n" + advisory if advisory else "") if master is not None else MISSING_DIAGNOSTIC
    fallback_logged: OrderedDict[str, None] = OrderedDict()
    fallback_log_lock = Lock()

    def section(session_info: Mapping[str, Any]) -> str:
        if _excluded(str(session_info.get("profile_name", ""))):
            return ""
        if len(content) > NATIVE_SECTION_CHARS:
            return ""
        return content

    def at_session_start(**kwargs: Any) -> None:
        del kwargs
        profile = _active_profile()
        if _excluded(profile):
            return
        if master is None:
            logger.error("Shared MASTER.md missing for profile %s; restore the file and reload Hermes", profile)
        elif len(master) > ADVISORY_CHARS:
            logger.warning(
                "Shared MASTER.md is %d characters for profile %s; aim for 3000-3500 without dropping essential rules",
                len(master), profile,
            )

    def ensure_request(request: Mapping[str, Any], api_mode: str = "", **kwargs: Any) -> dict[str, Any] | None:
        if _excluded(_active_profile()):
            return None
        system_parts = _system_parts(request)
        if _has_native_section(system_parts, content) or any(
            part.endswith(FALLBACK_MARKER + content) for part in system_parts
        ):
            return None
        rewritten = _append_system(request, FALLBACK_MARKER + content, api_mode)
        if rewritten is None:
            return None
        # Log once per bounded session key. Retries and tool-loop calls keep the
        # same fallback but must not flood the gateway log.
        session_id = str(kwargs.get("session_id") or "")
        with fallback_log_lock:
            if session_id not in fallback_logged:
                logger.warning("Shared MASTER.md native section absent; applied full system context through request middleware")
                fallback_logged[session_id] = None
                if len(fallback_logged) > 256:
                    fallback_logged.popitem(last=False)
        return {"request": rewritten, "source": SECTION_ID, "reason": "native section absent"}

    ctx.register_system_prompt_section(SECTION_ID, section, position="after_memory", max_chars=NATIVE_SECTION_CHARS)
    ctx.register_hook("on_session_start", at_session_start)
    ctx.register_middleware("llm_request", ensure_request)
