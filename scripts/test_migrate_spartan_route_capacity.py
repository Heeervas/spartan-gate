import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_spartan_route_capacity import MigrationError, migrate_capacity_overrides


class CapacityMigrationTests(unittest.TestCase):
    def make_databases(self, root: Path) -> tuple[Path, Path]:
        legacy = root / "legacy.db"
        target = root / "target.db"
        with sqlite3.connect(legacy) as db:
            db.execute(
                "CREATE TABLE codex_account_capacity_overrides (account_key TEXT PRIMARY KEY, capacity_multiplier REAL NOT NULL, updated_at TEXT NOT NULL)"
            )
        with sqlite3.connect(target) as db:
            db.execute(
                "CREATE TABLE codex_account_capacity_overrides (account_key TEXT PRIMARY KEY, capacity_multiplier INTEGER NOT NULL CHECK(capacity_multiplier = 5), updated_at TEXT NOT NULL)"
            )
        return legacy, target

    def test_copies_only_verified_active_x5_key_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy, target = self.make_databases(Path(tmp))
            active = "a" * 16
            inactive = "b" * 16
            with sqlite3.connect(legacy) as db:
                db.executemany(
                    "INSERT INTO codex_account_capacity_overrides VALUES (?, ?, 'old')",
                    [(active, 5), (inactive, 5), ("c" * 16, 1)],
                )

            first = migrate_capacity_overrides(legacy, target, {active})
            second = migrate_capacity_overrides(legacy, target, {active})

            self.assertEqual(first, {
                "legacyX5": 2,
                "matchedActiveX5": 1,
                "skippedInactiveX5": 1,
                "changed": 1,
            })
            self.assertEqual(second["changed"], 0)
            with sqlite3.connect(target) as db:
                self.assertEqual(
                    db.execute("SELECT account_key, capacity_multiplier FROM codex_account_capacity_overrides").fetchall(),
                    [(active, 5)],
                )

    def test_rejects_missing_or_malformed_active_identity_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy, target = self.make_databases(Path(tmp))
            with self.assertRaises(MigrationError):
                migrate_capacity_overrides(legacy, target, set())
            with self.assertRaises(MigrationError):
                migrate_capacity_overrides(legacy, target, {"NOT-A-HASH"})
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM codex_account_capacity_overrides").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
