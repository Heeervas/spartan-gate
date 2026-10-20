import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("codex_clean", Path(__file__).with_name("codex-clean.py"))
clean = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clean)
REAL_PROCESS_REFERENCES = clean.process_references


class CodexCleanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.base = self.home / ".local/share/codex-shared/packages/app-server-daemon"
        self.root = self.base / "releases"
        self.releases = []
        for number in range(1, 6):
            path = self.root / f"0.{number}.0-x86_64-unknown-linux-musl"
            (path / "bin").mkdir(parents=True)
            (path / "bin/codex").write_bytes(b"x" * 8192)
            self.releases.append(path)
        (self.base / "current").symlink_to(self.releases[3])
        profile = self.home / ".codex_work/packages"
        profile.mkdir(parents=True)
        (profile / "app-server-daemon").symlink_to(self.base)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"CODEX_HOME": str(self.home / ".codex")}).start()
        self.references = patch.object(clean, "process_references", return_value=set()).start()

    def test_selected_running_newer_and_one_rollback_are_preserved(self):
        self.references.return_value = {str(self.releases[0] / "bin/codex")}
        paths, _ = clean.plan(self.home)
        self.assertEqual(paths, [self.releases[1]])

    def test_shared_install_is_counted_once_and_external_hardlinks_not_credited(self):
        os.link(self.releases[0] / "bin/codex", self.home / "retained-binary")
        paths, snapshot = clean.plan(self.home)
        self.assertEqual(set(paths), set(self.releases[:2]))
        allocated = sum(v[6] for v in snapshot.values())
        self.assertEqual(allocated - clean.recoverable(snapshot), 8192)

    def test_missing_or_malformed_selector_fails_closed(self):
        current = self.base / "current"
        current.unlink()
        with self.assertRaises(ValueError):
            clean.plan(self.home)
        current.symlink_to(self.home)
        with self.assertRaises(ValueError):
            clean.plan(self.home)

    def test_foreign_install_symlink_fails_closed(self):
        link = self.home / ".codex_work/packages/app-server-daemon"
        link.unlink()
        outside = self.home / "protected"
        outside.mkdir()
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            clean.plan(self.home)

    def test_symlinked_release_fails_closed(self):
        link = self.root / "0.0.0-x86_64-unknown-linux-musl"
        link.symlink_to(self.home)
        with self.assertRaises(ValueError):
            clean.plan(self.home)

    def test_changed_selector_prevents_all_deletions(self):
        paths, snapshot = clean.plan(self.home)
        (self.base / "current").unlink()
        (self.base / "current").symlink_to(paths[-1])
        with self.assertRaises(ValueError):
            clean.apply(self.home, paths, snapshot)
        self.assertTrue(all(p.exists() for p in self.releases))

    def test_changed_file_or_new_process_prevents_all_deletions(self):
        paths, snapshot = clean.plan(self.home)
        (paths[-1] / "bin/codex").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            clean.apply(self.home, paths, snapshot)
        paths, snapshot = clean.plan(self.home)
        self.references.return_value = {str(paths[-1] / "bin/codex")}
        with self.assertRaises(ValueError):
            clean.apply(self.home, paths, snapshot)
        self.assertTrue(all(p.exists() for p in self.releases))

    def test_apply_preserves_profile_data_and_symlink_destination_and_is_repeatable(self):
        history = self.home / ".codex_work/history.jsonl"
        history.write_text("preserve")
        (self.releases[0] / "history-link").symlink_to(history)
        paths, snapshot = clean.plan(self.home)
        clean.apply(self.home, paths, snapshot)
        self.assertTrue(all(not p.exists() for p in paths))
        self.assertEqual(history.read_text(), "preserve")
        self.assertEqual(clean.plan(self.home)[0], [])

    def test_incomplete_process_scan_prevents_deletion(self):
        self.references.side_effect = PermissionError("cannot inspect process")
        with self.assertRaises(PermissionError):
            clean.plan(self.home)
        self.assertTrue(all(p.exists() for p in self.releases))

    def test_process_metadata_finds_executables_mappings_and_open_files(self):
        # Exercise the real reader against synthetic proc metadata.
        proc = self.home / "proc"
        process = proc / "123"
        (process / "fd").mkdir(parents=True)
        (process / "exe").symlink_to(self.releases[0] / "bin/codex")
        (process / "cwd").symlink_to(self.home)
        (process / "fd/4").symlink_to(self.releases[1] / "bin/codex")
        (process / "maps").write_text(
            f"0000-1000 r-xp 0000 00:00 1 {self.releases[2]}/bin/codex\n")
        refs = REAL_PROCESS_REFERENCES(proc)
        for path in self.releases[:3]:
            self.assertIn(str(path / "bin/codex"), refs)

    def test_unknown_unreadable_process_stops_but_login_service_is_reported(self):
        proc = self.home / "proc"
        process = proc / "123"
        process.mkdir(parents=True)
        (process / "comm").write_text("codex\n")
        with patch.object(clean.os, "readlink", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                REAL_PROCESS_REFERENCES(proc)
            (process / "comm").write_text("sshd\n")
            self.assertEqual(REAL_PROCESS_REFERENCES(proc), set())

    def test_noninteractive_default_and_explicit_dry_run_never_delete(self):
        for argv in (["codex-clean"], ["codex-clean", "--dry-run"]):
            with self.subTest(argv=argv), \
                    patch.object(clean.sys, "argv", argv), \
                    patch.object(clean.Path, "home", return_value=self.home), \
                    patch.object(clean.os, "geteuid", return_value=1000), \
                    patch.object(clean.sys.stdin, "isatty", return_value=False), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(clean.main(), 0)
                self.assertTrue(all(p.exists() for p in self.releases))

    def test_explicit_yes_removes_only_eligible_builds(self):
        with patch.object(clean.sys, "argv", ["codex-clean", "--yes"]), \
                patch.object(clean.Path, "home", return_value=self.home), \
                patch.object(clean.os, "geteuid", return_value=1000), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(clean.main(), 0)
        self.assertTrue(all(not p.exists() for p in self.releases[:2]))
        self.assertTrue(all(p.exists() for p in self.releases[2:]))


if __name__ == "__main__":
    unittest.main()
