"""Carry Discord auto-thread rename metadata past an initial config command."""

from __future__ import annotations

import inspect
import threading
import time
from typing import Any, Callable


_ALLOWED_INITIAL_COMMANDS = {"model", "reasoning"}
_WORKLOAD_INDICATORS = ("🔴", "🟠", "🟡", "🟢")
_WORKLOAD_RULE = (
    "The title MUST begin with exactly one workload indicator: "
    "🔴 for very high complexity or an expected multi-hour/multi-stage session; "
    "🟠 for high complexity or roughly 30-120 minutes; "
    "🟡 for moderate complexity or roughly 10-30 minutes; "
    "🟢 for a simple task expected to take under 10 minutes. "
    "Judge workload, not urgency. "
)


def install_native_discord_rename_compatibility(adapter: Any) -> bool:
    """Accept the shared gateway rename kwargs on one materialized native adapter."""
    if adapter is None:
        return False

    adapter_class = type(adapter)
    original = getattr(adapter_class, "rename_thread", None)
    if not callable(original):
        return False
    if getattr(original, "_semantic_title_kwargs_compatible", False):
        return True

    try:
        parameters = inspect.signature(original).parameters
    except (TypeError, ValueError):
        parameters = {}
    shared_kwargs = {"prefer_connector_created", "parent_chat_id"}
    if shared_kwargs.issubset(parameters):
        return True

    async def rename_thread_compatible(
        self,
        thread_id: str,
        name: str,
        *,
        only_if_current_name: str | None = None,
        prefer_connector_created: bool = False,
        parent_chat_id: str | None = None,
    ) -> bool:
        # These two guards belong to relay connectors. Native Discord already
        # protects the target with only_if_current_name.
        del prefer_connector_created, parent_chat_id
        return await original(
            self,
            thread_id,
            name,
            only_if_current_name=only_if_current_name,
        )

    setattr(rename_thread_compatible, "_semantic_title_kwargs_compatible", True)
    setattr(rename_thread_compatible, "__wrapped__", original)
    adapter_class.rename_thread = rename_thread_compatible
    return True


def ensure_workload_indicator(title: str) -> str:
    """Guarantee exactly one leading workload indicator."""
    stripped = str(title or "").strip()
    leading = next((emoji for emoji in _WORKLOAD_INDICATORS if stripped.startswith(emoji)), None)
    body = stripped[len(leading):].strip() if leading else stripped
    for emoji in _WORKLOAD_INDICATORS:
        body = body.replace(emoji, "").strip()
    if not leading:
        leading = "🟡"  # Neutral fallback when the title model violates the contract.
    return f"{leading} {body}" if body else f"{leading} Hermes Chat"


def install_global_title_policy() -> None:
    """Add the workload contract to every title generated in this Hermes process."""
    from agent import title_generator

    if getattr(title_generator, "_workload_policy_installed", False):
        return

    # Pinned Hermes renders this template, substitutes its language rule, and
    # parses a JSON title. Keep that contract instead of the retired prose prompts.
    title_generator._TITLE_PROMPT_TEMPLATE += (
        "\n" + _WORKLOAD_RULE
        + "Put the indicator inside the JSON title string, including for the examples above. "
        "The indicator does not count toward the 3-7 title words."
    )

    original_generate_title = title_generator.generate_title

    def generate_title_with_workload(*args, **kwargs):
        title = original_generate_title(*args, **kwargs)
        return ensure_workload_indicator(title) if title else title

    title_generator.generate_title = generate_title_with_workload
    title_generator._workload_policy_installed = True


def _platform_name(source: Any) -> str:
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "").lower()


def _thread_key(source: Any) -> str | None:
    if _platform_name(source) != "discord" or getattr(source, "chat_type", "") != "thread":
        return None
    value = getattr(source, "thread_id", None) or getattr(source, "chat_id", None)
    return str(value) if value else None


def _command_name(text: str) -> str | None:
    text = str(text or "").strip()
    if not text.startswith("/"):
        return None
    token = text.split(maxsplit=1)[0][1:].split("@", 1)[0].lower()
    return token or None


class DeferredThreadTitleState:
    def __init__(
        self,
        ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pending: dict[str, tuple[str, float]] = {}
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def discard(self, source: Any) -> None:
        """A successfully closed/dropped thread must not consume a pending title."""
        with self._lock:
            self._pending.pop(_thread_key(source), None)

    def _prune_expired(self, now: float) -> None:
        expired = [
            key for key, (_, armed_at) in self._pending.items()
            if now - armed_at > self._ttl_seconds
        ]
        for key in expired:
            self._pending.pop(key, None)

    def process(self, event: Any) -> bool:
        """Restore auto-thread metadata on the first non-command after /model or /reasoning."""
        source = getattr(event, "source", None)
        if source is None:
            return False
        key = _thread_key(source)
        if not key:
            return False

        text = str(getattr(event, "text", "") or "")
        command = _command_name(text)
        initial_name = getattr(source, "auto_thread_initial_name", None)
        is_new_auto_thread = bool(getattr(source, "auto_thread_created", False) and initial_name)

        now = self._clock()
        with self._lock:
            self._prune_expired(now)
            if is_new_auto_thread:
                if command in _ALLOWED_INITIAL_COMMANDS:
                    self._pending[key] = (str(initial_name), now)
                return False
            if command is not None:
                return False
            pending = self._pending.pop(key, None)

        if not pending:
            return False
        pending_name, _ = pending
        source.auto_thread_created = True
        source.auto_thread_initial_name = pending_name
        return True
