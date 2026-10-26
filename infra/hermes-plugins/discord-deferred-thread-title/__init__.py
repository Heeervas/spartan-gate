"""Hermes title policy, deferred Discord titles, and manual thread renaming."""

from __future__ import annotations

import asyncio
import logging
import re


logger = logging.getLogger(__name__)

try:
    from .deferred_thread_title import (
        DeferredThreadTitleState,
        ensure_workload_indicator,
        install_native_discord_rename_compatibility,
        install_global_title_policy,
    )
except ImportError:
    from deferred_thread_title import (
        DeferredThreadTitleState,
        ensure_workload_indicator,
        install_native_discord_rename_compatibility,
        install_global_title_policy,
    )

_STATE = DeferredThreadTitleState()
_RENAME_COMMAND = "rename-thread"
_CLOSE_ARG = "close"
_DROP_ARG = "drop"
_LEADING_STATUS = re.compile(r"^(?:(?:✅|🗑️|⏱️|🔴|🟠|🟡|🟢)\s*)+")


def _platform_name(source) -> str:
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "").lower()


def _rename_args(text: str) -> str | None:
    stripped = str(text or "").strip()
    token, _, args = stripped.partition(" ")
    command = token[1:].split("@", 1)[0].lower() if token.startswith("/") else ""
    return args.strip() if command == _RENAME_COMMAND else None


def _terminal_title(title: str, marker: str) -> str:
    """Replace any leading status/workload markers with one terminal marker."""
    body = _LEADING_STATUS.sub("", str(title or "").strip()).strip()
    return f"{marker} {body or 'Hermes Chat'}"


async def _archive_current_thread(adapter, thread_id: str, *, marker: str, action: str) -> bool:
    """Apply a terminal marker and archive a Discord thread in one edit."""
    client = getattr(adapter, "_client", None)
    if client is None:
        return False
    try:
        numeric_id = int(str(thread_id))
        thread = client.get_channel(numeric_id)
        if thread is None:
            thread = await client.fetch_channel(numeric_id)
        edit = getattr(thread, "edit", None)
        if edit is None:
            return False
        wanted = _terminal_title(getattr(thread, "name", ""), marker)
        updated = await edit(
            name=wanted,
            archived=True,
            reason=f"Hermes manual thread {action}",
        )
        return bool(
            getattr(updated, "name", None) == wanted
            and getattr(updated, "archived", True)
        )
    except Exception:
        logger.debug("Failed to complete Discord thread %s", thread_id, exc_info=True)
        return False


def _eligible_title_messages(messages):
    eligible = []
    for message in messages:
        role = str(message.get("role") or "").lower()
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if not content or content.lower().startswith(f"/{_RENAME_COMMAND}"):
            continue
        if content.lower().startswith("[recent summary"):
            continue
        eligible.append({"role": role, "content": content})
    return eligible


def _title_context(messages) -> str:
    eligible = _eligible_title_messages(messages)
    if not eligible:
        return ""

    # Preserve the original ask, sample the evolution, and favor the latest
    # state. This is more representative than a tail-only slice after compression.
    selected_indexes = {0}
    tail_start = max(1, len(eligible) - 6)
    middle = list(range(1, tail_start))
    if middle:
        slots = min(3, len(middle))
        for slot in range(slots):
            selected_indexes.add(middle[((slot + 1) * len(middle)) // (slots + 1)])
    selected_indexes.update(range(tail_start, len(eligible)))

    selected = [eligible[index] for index in sorted(selected_indexes)]
    rendered = [f"{message['role'].title()}: {message['content']}" for message in selected]
    context = "\n\n".join(rendered)
    if len(context) <= 4000:
        return context

    # Never lose the initial user ask when applying the character cap.
    first = rendered[0]
    remaining = "\n\n".join(rendered[1:])
    budget = max(0, 4000 - len(first) - 2)
    return first[:2000] + ("\n\n" + remaining[-budget:] if budget else "")


async def _compression_lineage_messages(session_db, session_id: str):
    """Load title evidence across compression ancestors, including archived turns."""
    if session_db is None or not hasattr(session_db, "get_messages"):
        return []

    tip = session_id
    if hasattr(session_db, "get_compression_tip"):
        tip = await session_db.get_compression_tip(session_id) or session_id

    lineage = []
    current_id = tip
    seen = set()
    while current_id and current_id not in seen and len(lineage) < 20:
        seen.add(current_id)
        row = await session_db.get_session(current_id) or {}
        lineage.append(current_id)
        parent_id = row.get("parent_session_id")
        if not parent_id:
            break
        parent = await session_db.get_session(parent_id) or {}
        if parent.get("end_reason") != "compression":
            break
        current_id = parent_id

    messages = []
    for lineage_id in reversed(lineage):
        rows = await session_db.get_messages(lineage_id, include_inactive=True)
        messages.extend(
            row for row in rows
            if row.get("active", 1) or row.get("compacted", 0)
        )
    return messages


async def _previous_thread_session(session_db, source, session_id):
    """Find the latest older session of this exact thread."""
    if session_db is None or not hasattr(session_db, "list_sessions_rich"):
        return None
    rows = await session_db.list_sessions_rich(
        source="discord",
        limit=100,
        include_children=True,
        include_archived=True,
        order_by_last_active=True,
        compact_rows=True,
    )
    source_thread_id = str(getattr(source, "thread_id", "") or "")
    source_chat_id = str(getattr(source, "chat_id", "") or source_thread_id)
    for row in rows:
        if row.get("id") == session_id:
            continue
        if str(row.get("chat_id") or "") != source_chat_id:
            continue
        if str(row.get("thread_id") or "") != source_thread_id:
            continue
        return row
    return None


async def _generate_title_from_session(gateway, source) -> str | None:
    """Generate a title, recovering the previous thread session after a reset."""
    store = getattr(gateway, "async_session_store", None)
    session_db = getattr(gateway, "_session_db", None)
    if store is None:
        return None

    fallback_title = None
    try:
        entry = await store.get_or_create_session(source)
        context = ""

        if session_db is not None:
            current = await session_db.get_session(entry.session_id) or {}
            fallback_title = current.get("title")
            lineage_messages = await _compression_lineage_messages(
                session_db, entry.session_id
            )
            context = _title_context(lineage_messages)

        if not context:
            context = _title_context(await store.load_transcript(entry.session_id))

        # A restart/reset can create a blank replacement session before this
        # zero-model command runs. Recover the latest older session for the same
        # Discord thread instead of claiming that the thread has no context.
        if not context:
            previous = await _previous_thread_session(session_db, source, entry.session_id)
            if previous is not None:
                fallback_title = fallback_title or previous.get("title")
                context = _title_context(await store.load_transcript(previous["id"]))
    except Exception as exc:
        logger.warning("rename-thread context recovery failed: %s", exc)
        return ensure_workload_indicator(fallback_title) if fallback_title else None

    if context:
        from agent.title_generator import generate_title

        try:
            generated = await asyncio.to_thread(generate_title, context)
        except Exception as exc:
            logger.warning("rename-thread auxiliary title generation failed: %s", exc)
            generated = None
        if generated:
            return ensure_workload_indicator(generated)
    return ensure_workload_indicator(fallback_title) if fallback_title else None


def _authorized_source(event, gateway) -> bool:
    source = getattr(event, "source", None)
    authorize = getattr(gateway, "_is_user_authorized_for_source", None)
    if not source or not getattr(source, "user_id", None) or getattr(event, "internal", False):
        return False
    return callable(authorize) and authorize(source)


def _receiving_adapter(source, gateway):
    resolve = getattr(gateway, "_intake_adapter_for", None)
    return resolve(source) if callable(resolve) else None


async def _rename_current_thread(event, gateway, requested_title: str) -> None:
    if not _authorized_source(event, gateway):
        return
    source = event.source
    adapter = _receiving_adapter(source, gateway)
    thread_id = getattr(source, "thread_id", None) or getattr(source, "chat_id", None)
    channel_id = getattr(source, "chat_id", None) or thread_id

    async def reply(message: str) -> None:
        if adapter and hasattr(adapter, "send") and channel_id:
            await adapter.send(str(channel_id), message, metadata={"thread_id": str(thread_id)})

    if getattr(source, "chat_type", "") != "thread" or not thread_id:
        await reply("Rename error: use /rename-thread inside a Discord thread.")
        return
    if not adapter:
        await reply("Rename error: Discord adapter unavailable.")
        return

    terminal_action = requested_title.lower()
    if terminal_action in {_CLOSE_ARG, _DROP_ARG}:
        marker = "✅" if terminal_action == _CLOSE_ARG else "🗑️"
        archived = await _archive_current_thread(
            adapter,
            str(thread_id),
            marker=marker,
            action="completion" if terminal_action == _CLOSE_ARG else "drop",
        )
        if archived:
            _STATE.discard(source)
        else:
            await reply(f"{terminal_action.title()} failed. Check the bot's Manage Threads permission.")
        return

    if not hasattr(adapter, "rename_thread"):
        await reply("Rename error: Discord adapter unavailable.")
        return

    if requested_title:
        title = ensure_workload_indicator(requested_title)
    else:
        title = await _generate_title_from_session(gateway, source)
        if not title:
            await reply("Rename error: auxiliary title generation failed or the session has no context yet.")
            return
    renamed = await adapter.rename_thread(str(thread_id), title)
    if renamed:
        await reply(f"Thread renamed to: {title}")
    else:
        await reply("Rename failed. Check the bot's Manage Threads permission.")


def register(ctx):
    install_global_title_policy()

    def pre_gateway_dispatch(event=None, gateway=None, **kwargs):
        source = getattr(event, "source", None)
        if not _authorized_source(event, gateway):
            return None

        if _platform_name(source) == "discord":
            adapter = _receiving_adapter(source, gateway)
            if adapter is None:
                return None
            if not install_native_discord_rename_compatibility(adapter):
                logger.error(
                    "Discord semantic-title compatibility unavailable for active adapter %r",
                    type(adapter).__name__ if adapter is not None else None,
                )

        requested_title = _rename_args(getattr(event, "text", ""))
        if _platform_name(source) == "discord" and requested_title is not None:
            try:
                asyncio.get_running_loop().create_task(
                    _rename_current_thread(event, gateway, requested_title)
                )
            except RuntimeError:
                return {"action": "skip", "reason": "rename-thread unavailable outside event loop"}
            return {"action": "skip", "reason": "Discord thread rename handled"}

        _STATE.process(event)
        return None

    def command_handler(args: str = "") -> str:
        return "Use /rename-thread [new title] inside a Discord thread, /rename-thread close to complete it, or /rename-thread drop to abandon it. Terminal actions archive the thread. Leave blank for an auxiliary-model title."

    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_command(
        _RENAME_COMMAND,
        handler=command_handler,
        description="Rename, complete, or drop the current Discord thread",
        args_hint="[new title|close|drop]",
    )
