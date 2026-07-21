#!/usr/bin/env python3
"""Copy verified legacy x5 Codex capacity overrides into Spartan Route."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ACCOUNT_KEY_RE = re.compile(r"^[0-9a-f]{16}$")
LEGACY_DEFAULT = Path("~/.custom_claw/clawroute/data/clawroute.db").expanduser()
TARGET_DEFAULT = Path("~/.custom_claw/spartan-route/data/spartan-route.db").expanduser()


class MigrationError(RuntimeError):
    pass


def _connect_existing(path: Path, *, read_only: bool) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise MigrationError(f"database does not exist: {resolved}")
    suffix = "?mode=ro" if read_only else "?mode=rw"
    connection = sqlite3.connect(f"file:{resolved}{suffix}", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _require_table(connection: sqlite3.Connection, table: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        raise MigrationError(f"required table is missing: {table}")


def migrate_capacity_overrides(
    legacy_path: Path,
    target_path: Path,
    active_account_keys: set[str],
) -> dict[str, int]:
    if not active_account_keys:
        raise MigrationError("at least one current active account key is required")
    invalid_active = sorted(key for key in active_account_keys if not ACCOUNT_KEY_RE.fullmatch(key))
    if invalid_active:
        raise MigrationError("active account keys must be 16 lowercase hexadecimal characters")

    with _connect_existing(legacy_path, read_only=True) as legacy:
        _require_table(legacy, "codex_account_capacity_overrides")
        rows = legacy.execute(
            "SELECT account_key FROM codex_account_capacity_overrides WHERE capacity_multiplier = 5"
        ).fetchall()
    legacy_keys = {str(row["account_key"]) for row in rows}
    if any(not ACCOUNT_KEY_RE.fullmatch(key) for key in legacy_keys):
        raise MigrationError("legacy x5 override contains an invalid account key")

    matched = sorted(legacy_keys & active_account_keys)
    skipped = legacy_keys - active_account_keys
    target = _connect_existing(target_path, read_only=False)
    try:
        _require_table(target, "codex_account_capacity_overrides")
        existing = {
            str(row["account_key"])
            for row in target.execute(
                "SELECT account_key FROM codex_account_capacity_overrides WHERE capacity_multiplier = 5"
            ).fetchall()
        }
        updated_at = datetime.now(timezone.utc).isoformat()
        target.execute("BEGIN IMMEDIATE")
        target.executemany(
            """
            INSERT INTO codex_account_capacity_overrides (account_key, capacity_multiplier, updated_at)
            VALUES (?, 5, ?)
            ON CONFLICT(account_key) DO NOTHING
            """,
            [(key, updated_at) for key in matched],
        )
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
    return {
        "legacyX5": len(legacy_keys),
        "matchedActiveX5": len(matched),
        "skippedInactiveX5": len(skipped),
        "changed": len(set(matched) - existing),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-db", type=Path, default=LEGACY_DEFAULT)
    parser.add_argument("--target-db", type=Path, default=TARGET_DEFAULT)
    parser.add_argument(
        "--active-account-key",
        action="append",
        default=[],
        help="Current 16-character account key shown by Spartan Route; repeat as needed.",
    )
    args = parser.parse_args()
    try:
        result = migrate_capacity_overrides(
            args.legacy_db,
            args.target_db,
            set(args.active_account_key),
        )
    except (MigrationError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
