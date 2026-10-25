"""Transport default-root common MASTER into eligible Hermes provider requests.

Core's native section and persisted-section limits remain 4,000 characters.
Oversized policy travels intact through the supported llm_request middleware.
Persisted prompts are never edited: stale owned sections are replaced in the
outgoing request only. Reload the plugin to adopt source edits.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Mapping

from hermes_constants import get_default_hermes_root

logger = logging.getLogger(__name__)
# Common preferences belong to the process's default root, not the active profile.
# Native project AGENTS remains independently owned and discovered by Hermes.
POLICY_PATH = get_default_hermes_root() / 'MASTER.md'
SECTION_ID = 'shared-master'
SECTION_HEADING = '## Plugin Context: shared-master'
SECTIONS_START = '<!-- hermes-plugin-sections:start -->\n'
SECTIONS_END = '\n<!-- hermes-plugin-sections:end -->'
SECTION_FRAME = re.compile(
    r'## Plugin Context: (?P<id>[a-z0-9][a-z0-9._-]{0,127})\n'
    r'<!-- hermes-plugin-section-chars:(?P<chars>[0-9]{1,4}) -->\n\n'
)
POLICY_START = re.compile(r'<!-- shared-master-policy:start chars=([0-9]{1,9}) -->\n')
POLICY_END = '\n<!-- shared-master-policy:end -->'
NATIVE_SECTION_CHARS = 4_000
EXCLUDED_PROFILES = frozenset({'empty', 'workstation', 'ssr'})
MISSING_DIAGNOSTIC = (
    'Common MASTER.md is unavailable. Tell the operator that common instructions '
    'could not be loaded; restore the source and reload Hermes before dependent work.'
)


def _excluded(profile: str) -> bool:
    name = profile.strip().lower()
    return name in EXCLUDED_PROFILES or name.startswith(('ssr_', 'ssr-'))


def _active_profile() -> str:
    from hermes_cli.profiles import current_profile_name
    return str(current_profile_name(default='default') or 'default')


def _read_policy() -> str | None:
    try:
        text = POLICY_PATH.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError) as exc:
        logger.error('Common MASTER unavailable at %s (%s)', POLICY_PATH, type(exc).__name__)
        return None
    if not text:
        logger.error('Common MASTER is empty at %s', POLICY_PATH)
        return None
    return text


def _map_blocks(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        blocks = []
        for block in value:
            if (isinstance(block, dict) and isinstance(block.get('text'), str)
                    and block.get('type') in (None, 'text', 'input_text')):
                text = transform(block['text'])
                # An owned-only text block cannot remain as invalid empty provider text.
                # Keep foreign/nontext blocks and untouched empty input exactly as given.
                if text != block['text'] and not text.strip():
                    continue
                block = {**block, 'text': text}
            blocks.append(block)
        return blocks
    return value


def _map_system(request: Mapping[str, Any], transform: Callable[[str], str], api_mode: str) -> dict[str, Any]:
    """Copy only trusted instruction fields; never modify user/tool/history bodies."""
    updated = dict(request)
    for key in ('instructions', 'system'):
        if key in updated:
            updated[key] = _map_blocks(updated[key], transform)
    for key in (('messages', 'input') if api_mode == 'codex_responses' else ('messages',)):
        if not isinstance(updated.get(key), list):
            continue
        items = []
        for item in updated[key]:
            if isinstance(item, dict) and item.get('role') in ('system', 'developer'):
                content = _map_blocks(item.get('content'), transform)
                if content != item.get('content') and (content == [] or isinstance(content, str) and not content.strip()):
                    continue
                item = {**item, 'content': content}
            items.append(item)
        updated[key] = items
    return updated


def _native_container(text: str, start: int) -> tuple[int, str, list[str]] | None:
    """Parse by lengths, not headings: nested quoted plugin frames stay data."""
    offset = start + len(SECTIONS_START)
    kept, owned = [], []
    while True:
        match = SECTION_FRAME.match(text, offset)
        if match is None:
            return None
        count = int(match['chars'])
        end = match.end() + count
        if count > NATIVE_SECTION_CHARS or end > len(text):
            return None
        if match['id'] == SECTION_ID:
            owned.append(text[match.end():end])
        else:
            kept.append(text[offset:end])
        if text.startswith(SECTIONS_END, end):
            replacement = SECTIONS_START + '\n\n'.join(kept) + SECTIONS_END if kept else ''
            return end + len(SECTIONS_END), replacement, owned
        if not text.startswith('\n\n', end):
            return None
        offset = end + 2


def _strip_owned(text: str, owned: list[str]) -> str:
    """Remove only valid top-level owned envelopes; preserve other plugin bodies."""
    output, offset = [], 0
    while offset < len(text):
        native = text.find(SECTIONS_START, offset)
        wrapped = POLICY_START.search(text, offset)
        at = min(native if native >= 0 else len(text), wrapped.start() if wrapped else len(text))
        output.append(text[offset:at])
        if at == len(text):
            break
        if at == native:
            parsed = _native_container(text, at)
            if parsed is not None:
                end, replacement, bodies = parsed
                output.append(replacement)
                owned.extend(bodies)
                offset = end
                continue
        elif wrapped is not None:
            end = wrapped.end() + int(wrapped[1])
            if text.startswith(POLICY_END, end):
                owned.append(text[wrapped.end():end])
                offset = end + len(POLICY_END)
                continue
        # Malformed/quoted markers are not evidence of a loaded policy.
        output.append(text[at:at + 1])
        offset = at + 1
    return ''.join(output)


def _deduplicate_raw(text: str, content: str, seen: list[bool]) -> str:
    """Reuse only a complete matching text body, never a quoted substring."""
    if text != content:
        return text
    if seen[0]:
        return ''
    seen[0] = True
    return text


def _append_system(request: Mapping[str, Any], block: str, api_mode: str) -> dict[str, Any] | None:
    updated = dict(request)
    if api_mode == 'codex_responses':
        instructions = updated.get('instructions')
        if instructions is not None and not isinstance(instructions, str):
            return None
        updated['instructions'] = (instructions.rstrip() + '\n\n' if instructions else '') + block
        return updated
    if api_mode in ('anthropic_messages', 'bedrock_converse'):
        system = updated.get('system')
        if isinstance(system, str):
            updated['system'] = system.rstrip() + '\n\n' + block
        elif system is None or isinstance(system, list):
            item = {'text': block} if api_mode == 'bedrock_converse' else {'type': 'text', 'text': block}
            updated['system'] = [*(system or []), item]
        else:
            return None
        return updated
    if api_mode == 'chat_completions' and isinstance(updated.get('messages'), list):
        updated['messages'] = [{'role': 'system', 'content': block}, *updated['messages']]
        return updated
    return None


def register(ctx: Any) -> None:
    # Activation snapshot is deliberate: file edits need plugin/gateway reload.
    policy = _read_policy()
    content = policy if policy is not None else MISSING_DIAGNOSTIC
    envelope = f'<!-- shared-master-policy:start chars={len(content)} -->\n{content}{POLICY_END}'

    def section(session_info: Mapping[str, Any]) -> str:
        if _excluded(str(session_info.get('profile_name', ''))) or len(content) > NATIVE_SECTION_CHARS:
            return ''
        return content

    def at_session_start(**kwargs: Any) -> None:
        if not _excluded(_active_profile()) and policy is None:
            logger.error('Common MASTER missing; restore the source and reload Hermes')

    def ensure_request(request: Mapping[str, Any], api_mode: str = '', **kwargs: Any) -> dict[str, Any] | None:
        if _excluded(_active_profile()):
            return None
        owned: list[str] = []
        cleaned = _map_system(request, lambda text: _strip_owned(text, owned), api_mode)
        # A complete trusted text body can already be the current common policy.
        # Arbitrary surrounding prose/quotes are not proof of operative inclusion.
        seen = [False]
        cleaned = _map_system(cleaned, lambda text: _deduplicate_raw(text, content, seen), api_mode)
        present = seen[0]
        if present and not owned and cleaned == request:
            return None
        if len(owned) == 1 and owned[0] == content and not present:
            return None
        rewritten = cleaned if present else _append_system(cleaned, envelope, api_mode)
        if rewritten is None:
            logger.error('Common MASTER not delivered: unsupported provider request shape (%s)', api_mode)
            return None
        return {'request': rewritten, 'source': SECTION_ID, 'reason': 'canonical common policy reconciliation'}

    ctx.register_system_prompt_section(SECTION_ID, section, position='after_memory', max_chars=NATIVE_SECTION_CHARS)
    ctx.register_hook('on_session_start', at_session_start)
    ctx.register_middleware('llm_request', ensure_request)
