#!/usr/bin/env python3
"""Render the sanitized host-connectivity status published by check-wifi."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import stat
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_STATUS_FILE = Path("/run/check-wifi-status.json")
DEFAULT_MAX_AGE_SECONDS = 300
MAX_STATUS_BYTES = 16 * 1024
EXPECTED_KEYS = {
    "version",
    "observed_at",
    "local",
    "wan",
    "dns",
    "tailscale",
    "local_failures",
    "transport_failures",
    "tailscale_failures",
    "last_wifi_recovery_at",
    "last_tailscale_restart_at",
}
ALLOWED_STATES = {
    "local": {
        "up",
        "transport-down",
        "down",
        "no-device",
        "radio-disabled",
        "disconnected",
        "unknown",
    },
    "wan": {"up", "down", "icmp-filtered", "unknown"},
    "dns": {"up", "down", "unknown"},
    "tailscale": {
        "healthy",
        "warning",
        "manual",
        "recoverable",
        "unknown",
    },
}


class StatusError(ValueError):
    pass


def _nonnegative_integer(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatusError(f"{key} is invalid")
    return value


def _optional_timestamp(payload: dict[str, Any], key: str) -> int | None:
    value = payload[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StatusError(f"{key} is invalid")
    return value


def load_status(path: Path) -> dict[str, Any]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATUS_BYTES:
            raise StatusError("status file is not a bounded regular file")
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StatusError("status file is unreadable") from exc

    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise StatusError("status schema is invalid")
    if payload["version"] != 1:
        raise StatusError("status version is unsupported")

    observed_at = _nonnegative_integer(payload, "observed_at")
    if observed_at == 0:
        raise StatusError("observed_at is invalid")
    for key, allowed in ALLOWED_STATES.items():
        if payload[key] not in allowed:
            raise StatusError(f"{key} is invalid")
    for key in (
        "local_failures",
        "transport_failures",
        "tailscale_failures",
    ):
        _nonnegative_integer(payload, key)
    for key in ("last_wifi_recovery_at", "last_tailscale_restart_at"):
        _optional_timestamp(payload, key)
    return payload


def status_age(payload: dict[str, Any], now: int) -> int:
    age = now - payload["observed_at"]
    if age < -60:
        raise StatusError("observed_at is in the future")
    return max(age, 0)


def is_healthy(payload: dict[str, Any]) -> bool:
    return (
        payload["local"] == "up"
        and payload["wan"] in {"up", "icmp-filtered"}
        and payload["dns"] == "up"
        and payload["tailscale"] == "healthy"
    )


def format_status(
    payload: dict[str, Any],
    *,
    now: int,
    max_age: int,
) -> tuple[str, bool]:
    age = status_age(payload, now)
    current = age <= max_age
    healthy = current and is_healthy(payload)
    if not current:
        condition = "stale"
    elif healthy:
        condition = "healthy"
    else:
        condition = "degraded"

    observed = dt.datetime.fromtimestamp(
        payload["observed_at"],
        tz=dt.timezone.utc,
    ).isoformat(timespec="seconds")
    fields = [
        f"local={payload['local']}",
        f"wan={payload['wan']}",
        f"dns={payload['dns']}",
        f"tailscale={payload['tailscale']}",
        f"observed={observed}",
        f"age={age}s",
    ]
    failures = [
        f"local={payload['local_failures']}",
        f"transport={payload['transport_failures']}",
        f"tailscale={payload['tailscale_failures']}",
    ]
    if any(
        payload[key]
        for key in (
            "local_failures",
            "transport_failures",
            "tailscale_failures",
        )
    ):
        fields.append(f"failures={','.join(failures)}")
    return f"Host connectivity: {condition} {' '.join(fields)}", healthy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("show", "check"))
    parser.add_argument(
        "--status-file",
        type=Path,
        default=DEFAULT_STATUS_FILE,
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
    )
    args = parser.parse_args()
    if args.max_age <= 0:
        parser.error("--max-age must be positive")

    try:
        payload = load_status(args.status_file)
        line, healthy = format_status(
            payload,
            now=int(time.time()),
            max_age=args.max_age,
        )
    except FileNotFoundError:
        line = "Host connectivity: unavailable (watchdog status not published)"
        healthy = False
    except StatusError:
        line = "Host connectivity: unavailable (invalid watchdog status)"
        healthy = False

    print(line)
    return 0 if args.command == "show" or healthy else 1


if __name__ == "__main__":
    sys.exit(main())
