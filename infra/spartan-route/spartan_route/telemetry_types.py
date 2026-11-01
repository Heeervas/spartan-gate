"""Telemetry event and explicit omission contracts, re-exported by telemetry."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingLogEntry:
    timestamp: str
    original_model: str
    routed_model: str
    actual_model: str
    tier: str
    classification_reason: str = ""
    confidence: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    original_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    savings_usd: float = 0.0
    escalated: bool = False
    escalation_chain: str = "[]"
    response_time_ms: int = 0
    upstream_response_time_ms: int | None = None
    total_response_time_ms: int | None = None
    had_tool_calls: bool = False
    actual_tool_call_count: int | None = None
    actual_tool_names: list[str] | None = None
    is_dry_run: bool = False
    is_override: bool = False
    session_id: str | None = None
    error: str | None = None
    prompt_preview: str | None = None
    context_info: str | None = None
    request_api_kind: str = "chat_completions"
    requested_reasoning_effort: str | None = None
    selected_codex_slot_index: int | None = None
    selected_codex_account_key: str | None = None
    request_id: str | None = None
    turn_id: str | None = None


class OptionalTelemetryUnavailable(RuntimeError):
    """Explicit omission, not a physical database failure."""
