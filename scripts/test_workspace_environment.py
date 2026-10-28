"""Exercise environment consumers with synthetic executables; never install packages."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        self.tools = Path(self.temp.name) / "tools"
        self.tools.mkdir()
        self.log = Path(self.temp.name) / "calls"
        self.env = {"PATH": f"{self.tools}:/usr/bin:/bin", "CALLS": str(self.log),
                    "ULTIMATE_SOURCE": str(self.tools), "HOME": str(self.tools)}
        for name in (".ultimate/worktree-setup.sh", ".ultimate/run.sh",
                     "scripts/check-pi-route-quality.sh"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for name in ("package-lock.json", "infra/spartan-route/requirements.txt",
                     ".agents/test-requirements.txt"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic input\n")
        self.executable(self.tools / "git", '#!/bin/sh\npwd -P\n')
        self.executable(self.tools / "node-22", '#!/bin/sh\nprintf "22\\n"\n')
        self.executable(self.tools / "npm", '#!/bin/sh\necho "npm $*" >> "$CALLS"\n')
        self.executable(self.tools / "python3", '''#!/bin/sh
printf 'python %s\n' "$*" >> "$CALLS"
if [ "$1" = -m ] && [ "$2" = venv ]; then
    mkdir -p "$3/bin"
    cp "$0" "$3/bin/python"
    cp "$0" "$3/bin/python3"
fi
''')

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, name, *args):
        return subprocess.run(["sh", name, *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=10)

    def setup_ok(self):
        result = self.run_script(".ultimate/worktree-setup.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.log.read_text()

    def test_setup_is_local_repeatable_and_wrapper_preserves_argv(self):
        calls = self.setup_ok()
        self.assertIn("python -m venv .venv/test\n", calls)
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund\n", calls)
        self.assertIn("python -m pip install --disable-pip-version-check -r .agents/test-requirements.txt\n", calls)
        self.assertFalse((self.root / ".agents/test-venv").exists())
        self.assertEqual((self.root / ".venv/test/bin/node").resolve(), self.tools / "node-22")
        self.setup_ok()
        self.assertEqual(self.log.read_text().count("python -m venv"), 1)
        result = self.run_script(".ultimate/run.sh", "sh", "-c",
                                 'printf "%s\\n%s\\n" "$(command -v python)" "$1"; exit 7',
                                 "fixture", "two words")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout.splitlines(),
                         [str(self.root / ".venv/test/bin/python"), "two words"])

    def test_setup_refuses_shared_directories_before_any_tool_or_write(self):
        # Keep the dependency-input preflight valid when .agents is linked.
        (self.tools / "test-requirements.txt").write_text("synthetic input\n")
        for relative in (".agents", ".venv", ".venv/test", "node_modules"):
            with self.subTest(path=relative):
                path = self.root / relative
                if path.exists():
                    path.rename(path.with_name(path.name + "-saved"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(self.tools, target_is_directory=True)
                result = self.run_script(".ultimate/worktree-setup.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refusing shared/symlinked " + relative, result.stderr)
                self.assertFalse(self.log.exists())
                path.unlink()
                saved = path.with_name(path.name + "-saved")
                if saved.exists():
                    saved.rename(path)

    def test_setup_refuses_incomplete_environment_without_installing(self):
        (self.root / ".venv/test").mkdir(parents=True)
        result = self.run_script(".ultimate/worktree-setup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing test environment is incomplete", result.stderr)
        self.assertNotIn("npm", self.log.read_text())
        self.assertNotIn("-m venv", self.log.read_text())

    def test_wrapper_refuses_missing_or_symlinked_environment(self):
        missing = self.run_script(".ultimate/run.sh", "true")
        self.assertNotEqual(missing.returncode, 0)
        self.setup_ok()
        for relative in (".venv/test", ".venv"):
            with self.subTest(path=relative):
                path = self.root / relative
                saved = path.with_name(path.name + "-saved")
                path.rename(saved)
                path.symlink_to(saved, target_is_directory=True)
                result = self.run_script(".ultimate/run.sh", "true")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refusing shared/symlinked " + relative, result.stderr)
                path.unlink()
                saved.rename(path)

    def quality_python(self, relative, label):
        path = self.root / relative / "bin/python"
        self.executable(path, f'#!/bin/sh\necho "{label} $*" >> "$CALLS"\n')
        return path

    def run_quality(self):
        result = subprocess.run(["bash", "scripts/check-pi-route-quality.sh"],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.log.unlink()
        self.assertEqual(len(calls), 3)
        self.assertIn("--base 75651cc0c5baa1db5a6523b790564988698f2a43 --scope all --level top", calls[-1])
        self.assertIn("--output .cache/quality/oxlint-metrics.json", calls[1])
        self.assertIn("--output .cache/quality/pi-route-quality.json", calls[2])
        self.assertTrue((self.root / ".cache/quality").is_dir())
        self.assertNotIn(".agents/oxlint-metrics.json", "\n".join(calls))
        self.assertNotIn(".agents/pi-route-quality.json", "\n".join(calls))
        return calls

    def test_quality_prefers_new_then_legacy_and_preserves_override(self):
        self.quality_python(".agents/quality-venv", "legacy")
        self.assertTrue(all(line.startswith("legacy ") for line in self.run_quality()))
        self.quality_python(".venv/quality", "new")
        self.assertTrue(all(line.startswith("new ") for line in self.run_quality()))
        override = self.quality_python("explicit", "override")
        self.env["BUILDER_ASSURANCE_PYTHON"] = str(override)
        self.assertTrue(all(line.startswith("override ") for line in self.run_quality()))

    def test_quality_refuses_default_shared_environment(self):
        self.quality_python("shared", "shared")
        (self.root / ".venv").mkdir()
        (self.root / ".venv/quality").symlink_to(self.root / "shared", target_is_directory=True)
        result = subprocess.run(["bash", "scripts/check-pi-route-quality.sh"],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing shared/symlinked", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
