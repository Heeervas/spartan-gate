"""Retention pacing through the actual telemetry worker; no production database."""
from __future__ import annotations

import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from test_telemetry import QueueTestStore, routing_entry
from spartan_route.telemetry import TelemetryQueue, TelemetryStore


class RetentionBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_storage_damage_is_degraded_and_not_retried_by_worker(self) -> None:
        error = sqlite3.DatabaseError("synthetic unreadable storage")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR | (1 << 8)
        with patch.object(TelemetryStore, "enforce_routing_retention", side_effect=error) as prune:
            store = TelemetryStore(":memory:")
            try:
                self.assertFalse(store.health_status()["maintenanceHealthy"])
                queue = TelemetryQueue(store, retention_batch_size=1)
                queue.start()
                try:
                    queue.log(routing_entry("2026-07-01T00:00:00Z", "after-startup-failure"))
                    await asyncio.wait_for(queue.queue.join(), timeout=1)
                    self.assertEqual(prune.call_count, 1)
                    self.assertTrue(queue.status()["retentionSuspended"])
                    self.assertEqual(queue.status()["maintenanceFailures"], 1)
                    self.assertEqual(store.health_status()["maintenanceFailures"], 1)
                    self.assertEqual(store._conn.execute("SELECT count(*) FROM routing_log").fetchone()[0], 1)
                finally:
                    await queue.stop(timeout=1)
            finally:
                store.close()

    async def test_startup_schema_and_policy_errors_still_refuse_startup(self) -> None:
        for target in ("TelemetryStore._init_schema", "load_policy", "QuotaLimitStore", "PiUsageStore"):
            with self.subTest(target=target), patch(
                f"spartan_route.telemetry.{target}", side_effect=sqlite3.DatabaseError("synthetic initialization failure"),
            ):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "synthetic initialization failure"):
                    TelemetryStore(":memory:")

    async def test_retention_failure_backs_off_while_writes_keep_draining(self) -> None:
        store = QueueTestStore()
        queue = TelemetryQueue(store, retention_batch_size=1)
        with patch("spartan_route.retention_maintenance.monotonic", return_value=0) as clock, patch.object(
            store, "enforce_routing_retention", side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                {},
                sqlite3.OperationalError("database is locked"),
            ],
        ) as prune:
            queue.start()
            try:
                for now, expected_calls in [
                    (0, 1), (1, 1), (59, 1), (60, 2), (179, 2), (180, 3),
                    (181, 4), (240, 4), (241, 5), (360, 5), (361, 6),
                    (600, 6), (601, 7), (900, 7), (901, 8),
                ]:
                    clock.return_value = now
                    if now == 241:
                        prune.side_effect = sqlite3.OperationalError("database is locked")
                    self.assertTrue(queue.log(routing_entry("2026-07-01T00:00:00Z", str(now))))
                    await asyncio.wait_for(queue.queue.join(), timeout=1)
                    self.assertEqual(prune.call_count, expected_calls, f"at {now}s")
                self.assertEqual(len(store.written), 15)
                self.assertEqual(queue.status()["maintenanceFailures"], 7)
                self.assertEqual(queue.status()["retentionRetryAfterSeconds"], 300)
            finally:
                await queue.stop(timeout=1)

    async def test_storage_damage_suspends_retention_without_stopping_writes(self) -> None:
        for code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_IOERR | (1 << 8)):
            with self.subTest(code=code):
                store = QueueTestStore()
                queue = TelemetryQueue(store, retention_batch_size=1)
                error = sqlite3.DatabaseError("synthetic storage damage")
                error.sqlite_errorcode = code
                with patch("spartan_route.retention_maintenance.monotonic", return_value=0) as clock, patch.object(
                    store, "enforce_routing_retention", side_effect=error,
                ) as prune:
                    queue.start()
                    try:
                        for now in (0, 1, 60, 3600):
                            clock.return_value = now
                            queue.log(routing_entry("2026-07-01T00:00:00Z", str(now)))
                            await asyncio.wait_for(queue.queue.join(), timeout=1)
                        self.assertEqual(prune.call_count, 1)
                        self.assertEqual(len(store.written), 4)
                        self.assertTrue(queue.status()["retentionSuspended"])
                        self.assertEqual(queue.status()["maintenanceFailures"], 1)
                        self.assertNotIn(("success", "maintenance", "retention"), store.health_events)
                    finally:
                        await queue.stop(timeout=1)
