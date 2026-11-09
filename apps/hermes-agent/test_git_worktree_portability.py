"""Native Git interoperability across host/container roots, without live repos."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("HERMES_GIT_TEST_IMAGE"), "explicit isolated image required")
class GitWorktreePortabilityTests(unittest.TestCase):
    def test_both_consumers_create_and_continue_the_same_relative_worktrees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, shared = root / "data", root / "shared"
            (home / "workspace" / "projects" / "shared").mkdir(parents=True)
            shared.mkdir()
            project = shared / "project"
            self.git(shared, "init", "--initial-branch=main", str(project))
            self.git(project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-m", "fixture")
            self.git(project, "config", "worktree.useRelativePaths", "true")
            host_tree = project / ".worktrees" / "from-host"
            self.git(project, "worktree", "add", "--detach", str(host_tree))
            self.assertFalse((host_tree / ".git").read_text().startswith("gitdir: /"))
            target = "/opt/data/workspace/projects/shared"
            command = ["docker", "run", "--rm", "--network", "none", "--user",
                       f"{os.getuid()}:{os.getgid()}", "--entrypoint", "/bin/sh",
                       "--volume", f"{home}:/opt/data:Z",
                       "--volume", f"{shared}:{target}:Z",
                       os.environ["HERMES_GIT_TEST_IMAGE"], "-ec", '''
cd /opt/data/workspace/projects/shared/project
version=$(git --version)
printf '%s\\n' "$version"
git -C .worktrees/from-host status --porcelain
git worktree add --detach .worktrees/from-container
git -C .worktrees/from-container status --porcelain
git worktree repair --relative-paths
''']
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            container_tree = project / ".worktrees" / "from-container"
            self.assertFalse((container_tree / ".git").read_text().startswith("gitdir: /"))
            self.assertEqual(self.git(container_tree, "status", "--porcelain"), "")
            self.assertEqual(self.git(host_tree, "rev-parse", "HEAD"),
                             self.git(container_tree, "rev-parse", "HEAD"))
            self.git(project, "worktree", "repair", "--relative-paths")
            self.assertFalse((host_tree / ".git").read_text().startswith("gitdir: /"))

    @staticmethod
    def git(cwd, *args):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                text=True, check=False,
                                env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                                     "GIT_CONFIG_GLOBAL": os.devnull})
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
