"""Generation fencing for request outcomes, independent of mutable slot state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import json

if TYPE_CHECKING:
    from .codex import CodexAuth, CodexSlot


@dataclass(frozen=True)
class CodexAttempt:
    slot: CodexSlot
    auth: CodexAuth
    fingerprint: str | None

    def current_slot(self) -> CodexSlot | None:
        if self.slot.auth is self.auth and self.slot.auth_fingerprint == self.fingerprint:
            return self.slot
        return None


def apply_outcome(router: Any, attempt: CodexAttempt, status: int, text: str) -> None:
    if status == 401:
        router._quarantine_auth_failure(attempt)
    elif status == 429:
        router.mark_rate_limited(attempt, text)
    else:
        router.mark_transient(attempt)


def valid_tool_arguments(calls: list[dict[str, Any]]) -> bool:
    for call in calls:
        try:
            value = json.loads(call['function']['arguments'])
        except (ValueError, TypeError, KeyError):
            return False
        if not isinstance(value, dict):
            return False
    return True


def outcome_slot(slot: CodexSlot | CodexAttempt) -> CodexSlot | None:
    return slot.current_slot() if isinstance(slot, CodexAttempt) else slot
