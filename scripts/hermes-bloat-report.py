#!/usr/bin/env python3
"""Report Hermes prompt/request bloat from safe SQLite backups.

The script never writes to Hermes state directories. For each state.db it opens
the original in SQLite read-only mode, uses SQLite's backup API into a temporary
database, and runs all inspection queries against that temporary copy.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any


PROMPT_FILES = (
    "SOUL.md",
    "AGENTS.md",
    "HERMES.md",
    ".hermes.md",
    "MEMORY.md",
    "USER.md",
    "memories/MEMORY.md",
    "memories/USER.md",
)


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def query_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})")]


def first_present(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in columns), None)


def backup_state_db(state_db: Path, temp_dir: Path) -> Path:
    backup_path = temp_dir / f"{state_db.parent.name or 'default'}-state-copy.db"
    source = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5)
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def discover_profiles(root: Path) -> list[tuple[str, Path]]:
    profiles: list[tuple[str, Path]] = []
    if (root / "state.db").exists() or (root / "config.yaml").exists():
        profiles.append(("default", root))
    profiles_root = root / "profiles"
    if profiles_root.is_dir():
        for child in sorted(profiles_root.iterdir()):
            if child.is_dir() and ((child / "state.db").exists() or (child / "config.yaml").exists()):
                profiles.append((child.name, child))
    return profiles


def prompt_file_sizes(home: Path) -> list[dict[str, Any]]:
    rows = []
    for rel in PROMPT_FILES:
        path = home / rel
        if path.exists() and path.is_file():
            rows.append({
                "path": rel,
                "bytes": path.stat().st_size,
                "rough_tokens": (path.stat().st_size + 3) // 4,
            })
    return rows


def load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception:
        return None


def config_summary(home: Path) -> dict[str, Any]:
    config_path = home / "config.yaml"
    if not config_path.exists():
        return {"present": False}
    summary: dict[str, Any] = {
        "present": True,
        "bytes": config_path.stat().st_size,
        "rough_tokens": (config_path.stat().st_size + 3) // 4,
    }
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        summary["parsed"] = False
        return summary
    summary["parsed"] = True
    platform_toolsets = config.get("platform_toolsets")
    if isinstance(platform_toolsets, dict):
        summary["platform_toolsets"] = {
            str(key): len(value) if isinstance(value, list) else None
            for key, value in platform_toolsets.items()
        }
    mcp_servers = config.get("mcp_servers")
    if isinstance(mcp_servers, dict):
        servers = {}
        for name, value in mcp_servers.items():
            if isinstance(value, dict):
                servers[str(name)] = {
                    "enabled": value.get("enabled", True),
                    "include_tools": len(value.get("include_tools") or []),
                    "exclude_tools": len(value.get("exclude_tools") or []),
                }
            else:
                servers[str(name)] = {"enabled": bool(value)}
        summary["mcp_servers"] = servers
    return summary


def analyze_messages(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    tables = table_names(conn)
    message_table = next((name for name in ("messages", "message", "chat_messages") if name in tables), None)
    if not message_table:
        return {"present": False}
    columns = table_columns(conn, message_table)
    content_col = first_present(columns, ("content", "text", "message", "payload", "raw"))
    role_col = first_present(columns, ("role", "type", "author", "name", "tool_name"))
    session_col = first_present(columns, ("session_id", "conversation_id", "thread_id", "run_id"))
    result: dict[str, Any] = {
        "present": True,
        "table": message_table,
        "rows": query_one(conn, f"SELECT COUNT(*) FROM {quote_ident(message_table)}") or 0,
    }
    if content_col:
        result["stored_chars"] = query_one(
            conn,
            f"SELECT COALESCE(SUM(LENGTH({quote_ident(content_col)})), 0) FROM {quote_ident(message_table)}",
        ) or 0
        result["max_message_chars"] = query_one(
            conn,
            f"SELECT COALESCE(MAX(LENGTH({quote_ident(content_col)})), 0) FROM {quote_ident(message_table)}",
        ) or 0
    if content_col and role_col:
        result["top_roles"] = [
            {"role": row[0], "messages": row[1], "chars": row[2]}
            for row in conn.execute(
                f"""
                SELECT COALESCE(CAST({quote_ident(role_col)} AS TEXT), 'unknown') AS role_key,
                       COUNT(*) AS messages,
                       COALESCE(SUM(LENGTH({quote_ident(content_col)})), 0) AS chars
                FROM {quote_ident(message_table)}
                GROUP BY role_key
                ORDER BY chars DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]
    if content_col and session_col:
        result["top_sessions"] = [
            {"session": row[0], "messages": row[1], "chars": row[2], "max_chars": row[3]}
            for row in conn.execute(
                f"""
                SELECT COALESCE(CAST({quote_ident(session_col)} AS TEXT), 'unknown') AS session_key,
                       COUNT(*) AS messages,
                       COALESCE(SUM(LENGTH({quote_ident(content_col)})), 0) AS chars,
                       COALESCE(MAX(LENGTH({quote_ident(content_col)})), 0) AS max_chars
                FROM {quote_ident(message_table)}
                GROUP BY session_key
                ORDER BY chars DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]
    return result


def analyze_sessions(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    tables = table_names(conn)
    if "sessions" not in tables:
        return {"present": False}
    columns = table_columns(conn, "sessions")
    id_col = first_present(columns, ("session_id", "id", "conversation_id", "thread_id"))
    started_col = first_present(columns, ("started_at", "created_at", "updated_at"))
    source_col = first_present(columns, ("source", "platform"))
    model_col = first_present(columns, ("model", "model_name"))
    prompt_col = first_present(columns, ("system_prompt", "prompt", "instructions"))
    select_parts = []
    for alias, column in (
        ("session", id_col),
        ("started", started_col),
        ("source", source_col),
        ("model", model_col),
    ):
        select_parts.append(
            f"CAST({quote_ident(column)} AS TEXT) AS {alias}" if column else f"NULL AS {alias}"
        )
    if prompt_col:
        select_parts.append(f"LENGTH({quote_ident(prompt_col)}) AS system_prompt_chars")
    elif "system_prompt_chars" in columns:
        select_parts.append('"system_prompt_chars" AS system_prompt_chars')
    else:
        select_parts.append("0 AS system_prompt_chars")
    order_col = started_col or id_col
    sql = f"SELECT {', '.join(select_parts)} FROM sessions"
    if order_col:
        sql += f" ORDER BY {quote_ident(order_col)} DESC"
    sql += " LIMIT ?"
    return {
        "present": True,
        "rows": query_one(conn, "SELECT COUNT(*) FROM sessions") or 0,
        "recent": [
            {
                "session": row[0],
                "started": row[1],
                "source": row[2],
                "model": row[3],
                "system_prompt_chars": row[4] or 0,
            }
            for row in conn.execute(sql, (limit,))
        ],
    }


def analyze_state_copy(copy_path: Path, limit: int) -> dict[str, Any]:
    conn = sqlite3.connect(copy_path)
    try:
        return {
            "tables": table_names(conn),
            "messages": analyze_messages(conn, limit),
            "sessions": analyze_sessions(conn, limit),
        }
    finally:
        conn.close()


def analyze_profile(name: str, home: Path, limit: int, temp_dir: Path) -> dict[str, Any]:
    state_db = home / "state.db"
    report: dict[str, Any] = {
        "profile": name,
        "home": str(home),
        "prompt_files": prompt_file_sizes(home),
        "config": config_summary(home),
        "state_db": {"present": state_db.exists()},
    }
    if state_db.exists():
        try:
            copy_path = backup_state_db(state_db, temp_dir)
            report["state_db"].update({"copied": True, "copy_path": str(copy_path)})
            report["database"] = analyze_state_copy(copy_path, limit)
        except Exception as exc:
            report["state_db"].update({"copied": False, "error": str(exc)})
    return report


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hermes bloat report root: {report['root']}",
        "Safety: state.db files were inspected through temporary SQLite backups only.",
        "",
    ]
    for profile in report["profiles"]:
        lines.append(f"Profile: {profile['profile']} ({profile['home']})")
        state = profile["state_db"]
        lines.append(f"  state.db: {'present' if state.get('present') else 'missing'}, copied={state.get('copied', False)}")
        config = profile["config"]
        if config.get("present"):
            lines.append(f"  config.yaml: {config.get('bytes', 0)} bytes")
            if config.get("mcp_servers"):
                servers = ", ".join(
                    f"{name}(include={value.get('include_tools')}, exclude={value.get('exclude_tools')})"
                    for name, value in config["mcp_servers"].items()
                )
                lines.append(f"  mcp_servers: {servers}")
        if profile["prompt_files"]:
            files = ", ".join(
                f"{row['path']}={row['bytes']}B" for row in profile["prompt_files"]
            )
            lines.append(f"  prompt files: {files}")
        database = profile.get("database") or {}
        messages = database.get("messages") or {}
        if messages.get("present"):
            lines.append(
                f"  messages: rows={messages.get('rows', 0)} stored_chars={messages.get('stored_chars', 0)} max_chars={messages.get('max_message_chars', 0)}"
            )
            for row in messages.get("top_roles", [])[:5]:
                lines.append(f"    role {row['role']}: {row['messages']} msgs, {row['chars']} chars")
            for row in messages.get("top_sessions", [])[:5]:
                lines.append(f"    session {row['session']}: {row['messages']} msgs, {row['chars']} chars, max={row['max_chars']}")
        sessions = database.get("sessions") or {}
        if sessions.get("present"):
            lines.append(f"  sessions: rows={sessions.get('rows', 0)}")
            for row in sessions.get("recent", [])[:5]:
                lines.append(
                    f"    {row['session']} source={row['source']} model={row['model']} system_prompt_chars={row['system_prompt_chars']}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely report Hermes prompt/request bloat for all profiles.")
    parser.add_argument("--root", default=os.environ.get("HERMES_DATA_ROOT", "/opt/data"), help="Hermes data root, default /opt/data")
    parser.add_argument("--limit", type=int, default=5, help="Rows per top table")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="hermes-bloat-report-") as temp:
        temp_dir = Path(temp)
        report = {
            "root": str(root),
            "profiles": [
                analyze_profile(name, home, args.limit, temp_dir)
                for name, home in discover_profiles(root)
            ],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
