"""Bound non-progress in streamed JSON tool arguments, without retaining content."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_BYTES = 1024


class DegenerateToolArguments(RuntimeError):
    error = {
        "message": "Upstream server error: excessive tool argument whitespace outside JSON strings; connection terminated",
        "type": "server_error",
        "code": "codex_tool_arguments_degenerate",
    }


def whitespace_limit() -> int:
    try:
        return max(256, min(1048576, int(os.environ.get("SPARTAN_ROUTE_TOOL_WHITESPACE_MAX_BYTES", DEFAULT_MAX_BYTES))))
    except ValueError:
        return DEFAULT_MAX_BYTES


@dataclass
class JsonTail:
    in_string: bool = False
    escaped: bool = False
    whitespace_bytes: int = 0

    def feed(self, text: str, limit: int) -> None:
        for char in text:
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.in_string = False
            elif char in " \t\r\n":
                self.whitespace_bytes += 1  # JSON whitespace is ASCII: bytes == chars.
                if self.whitespace_bytes > limit:
                    raise DegenerateToolArguments()
            else:
                self.whitespace_bytes = 0
                self.in_string = char == '"'


class ToolArgumentGuard:
    def __init__(self, model: str, request_id: str | None = None):
        self.model = model
        self.request_id = request_id
        self.limit = whitespace_limit()
        self.started = time.monotonic()
        self.calls: dict[str, JsonTail] = {}

    def observe(self, event: str | None, payload: dict[str, Any]) -> None:
        name = event or payload.get("type")
        if name == "response.output_item.added":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call" and isinstance(item.get("id"), str):
                self.feed(item["id"], item.get("arguments"))
        elif name == "response.function_call_arguments.delta":
            self.feed(payload.get("item_id"), payload.get("delta"))
        elif name == "response.output_item.done":
            item = payload.get("item")
            if isinstance(item, dict):
                self.calls.pop(item.get("id"), None)

    def feed(self, item_id: Any, text: Any) -> None:
        if not isinstance(item_id, str) or not isinstance(text, str):
            return
        tail = self.calls.setdefault(item_id, JsonTail())
        try:
            tail.feed(text, self.limit)
        except DegenerateToolArguments:
            # JSON quoting prevents log injection; never log IDs or argument content.
            LOGGER.warning("tool_arguments_degenerate model=%s whitespace_bytes=%d duration_ms=%d request_id=%s",
                           json.dumps(self.model), tail.whitespace_bytes,
                           int((time.monotonic() - self.started) * 1000), json.dumps(self.request_id))
            raise
