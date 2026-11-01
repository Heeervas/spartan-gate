"""Worker-owned retention pacing; telemetry failure must not cause retry storms."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .telemetry import TelemetryStore


def storage_damage(exc: Exception) -> bool:
    # Extended SQLite result codes retain the primary code in the low byte.
    code = getattr(exc, "sqlite_errorcode", 0) or 0
    return isinstance(exc, sqlite3.Error) and (code & 0xFF) in (
        sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_IOERR,
    )


class RetentionMaintenance:
    def __init__(self, store: TelemetryStore, batch_size: int) -> None:
        self.store = store
        self.batch_size = batch_size
        self.failures = 0
        self._writes = 0
        self._retry_at = 0.0
        self._backoff = 0.0
        self._suspended = False

    def initialize(self) -> None:
        try:
            self.store.enforce_routing_retention(force_rebuild=True)
        except sqlite3.Error as exc:
            if not storage_damage(exc):
                raise
            self._failed(exc)

    def status(self) -> dict[str, bool | float | int]:
        return {
            "maintenanceFailures": self.failures,
            "retentionSuspended": self._suspended,
            "retentionRetryAfterSeconds": max(0.0, self._retry_at - monotonic()),
        }

    async def after_write(self) -> None:
        self._writes = min(self.batch_size, self._writes + 1)
        if self._writes < self.batch_size or self._suspended or monotonic() < self._retry_at:
            return
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.store.enforce_routing_retention)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(exc)
        else:
            self.store.record_health_success("maintenance", "retention")
            self._writes = 0
            self._backoff = 0.0
            self._retry_at = 0.0

    def _failed(self, exc: Exception) -> None:
        self.failures += 1
        self.store.record_health_failure("maintenance", "retention")
        self._suspended = storage_damage(exc)
        self._backoff = min(300.0, max(60.0, self._backoff * 2))
        self._retry_at = monotonic() + self._backoff
        action = "suspended until storage repair and process restart" if self._suspended else "backing off"
        print(
            f"[spartan-route] telemetry retention failed ({type(exc).__name__}); {action}",
            file=sys.stderr,
        )
