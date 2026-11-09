"""Exercise the actual wrapper's meeting-directory initialization in isolation."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


WRAPPER = Path(__file__).with_name("entrypoint-wrapper.sh")


class WorkspaceEntrypointTests(unittest.TestCase):
    def test_existing_workspace_and_symlinked_meetings_are_not_normalized(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                workspace, shared = home / "workspace", home / "shared"
                shared.mkdir(mode=0o755)
                workspace.mkdir(mode=0o755)
                (shared / "code").write_bytes(b"user source")
                (shared / "code").chmod(0o644)
                meetings = workspace / "meetings"
                if symlink:
                    meetings.symlink_to(shared)
                else:
                    meetings.mkdir(mode=0o755)
                    (meetings / "notes").write_bytes(b"existing notes")
                    (meetings / "notes").chmod(0o644)
                before = self.snapshot(home)
                result = self.run_block(home)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.snapshot(home), before)

    def test_new_meeting_directory_is_private_at_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "workspace").mkdir(mode=0o755)
            result = self.run_block(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((home / "workspace" / "meetings").stat().st_mode & 0o777, 0o700)
            self.assertEqual((home / "workspace").stat().st_mode & 0o777, 0o755)

    @staticmethod
    def run_block(home):
        source = WRAPPER.read_text()
        start = source.index('meet_workspace="$HERMES_HOME/workspace/meetings"')
        end = source.index("# Preserve the venv path", start)
        script = 'set -eu\nrun_as_hermes() { "$@"; }\n' + source[start:end]
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={**os.environ, "HERMES_HOME": str(home),
                                   "SPARTAN_HERMES_RUNTIME_OWNER": f"{os.getuid()}:{os.getgid()}"},
                              check=False)

    @staticmethod
    def snapshot(root):
        return {str(p.relative_to(root)): (p.lstat().st_mode, p.lstat().st_uid,
                                          p.lstat().st_gid,
                                          p.read_bytes() if p.is_file() else None)
                for p in (root, *root.rglob("*"))}


if __name__ == "__main__":
    unittest.main()
