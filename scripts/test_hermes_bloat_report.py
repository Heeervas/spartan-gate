import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "hermes-bloat-report.py"


def create_state_db(path: Path, session_id: str, message_chars: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at TEXT, system_prompt TEXT)"
        )
        conn.execute(
            "CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, 'discord', 'custom-1/clawroute/auto', '2026-06-17T08:00:00Z', ?)",
            (session_id, "system" * 100),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, 'user', ?)",
            (session_id, "x" * message_chars),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, 'skill_view', ?)",
            (session_id, "y" * (message_chars // 2)),
        )
        conn.commit()
    finally:
        conn.close()


class HermesBloatReportTests(unittest.TestCase):
    def test_reports_default_and_named_profiles_from_safe_database_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_state_db(root / "state.db", "default-session", 120)
            (root / "SOUL.md").write_text("soul text", encoding="utf-8")
            work = root / "profiles" / "work"
            work.mkdir(parents=True)
            create_state_db(work / "state.db", "work-session", 240)
            (work / "config.yaml").write_text(
                "mcp_servers:\n  gtm:\n    enabled: true\n    include_tools: []\n",
                encoding="utf-8",
            )
            before = (work / "state.db").stat().st_mtime_ns

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root), "--json"],
                check=True,
                text=True,
                capture_output=True,
            )

            after = (work / "state.db").stat().st_mtime_ns
            body = json.loads(proc.stdout)
            self.assertEqual(before, after)
            self.assertEqual([profile["profile"] for profile in body["profiles"]], ["default", "work"])
            work_report = body["profiles"][1]
            self.assertTrue(work_report["state_db"]["copied"])
            self.assertEqual(work_report["database"]["messages"]["rows"], 2)
            self.assertEqual(work_report["database"]["messages"]["top_sessions"][0]["session"], "work-session")
            self.assertEqual(work_report["database"]["sessions"]["recent"][0]["system_prompt_chars"], 600)


if __name__ == "__main__":
    unittest.main()
