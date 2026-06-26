from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def parse_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback


def parse_int(value: str | None, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value if value is not None else fallback))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def parse_float(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def epoch_millis(value: Any) -> int:
    numeric = parse_float(value, 0)
    if numeric <= 0:
        return 0
    return int(numeric if numeric > 10_000_000_000 else numeric * 1000)


def jwt_claims(token: str) -> dict[str, Any] | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def is_jwt_expired(token: str, skew_seconds: int = 300) -> bool:
    claims = jwt_claims(token)
    exp = claims.get("exp") if claims else None
    if not isinstance(exp, (int, float)):
        return False
    return exp <= time.time() + skew_seconds


def token_count_estimate(body: dict[str, Any]) -> int:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    size = len(stable_json(messages)) + len(stable_json(tools))
    return max(0, math.ceil(size / 4))


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def sanitize_preview(value: str, max_chars: int = 300) -> str:
    redacted = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
    redacted = re.sub(
        r"\b(api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|cookie|secret)\b(\s*[=:]\s*)([\"']?)[^\s,\"'};]+\3",
        r"\1\2[REDACTED]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted[:max_chars]


def json_error(message: str, status: int, code: str, error_type: str = "server_error") -> tuple[dict[str, Any], int]:
    return {"error": {"message": message, "type": error_type, "code": code}}, status
