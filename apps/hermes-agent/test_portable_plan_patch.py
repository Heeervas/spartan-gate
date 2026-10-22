"""Exercise the native prompt producer and fail-closed patch CLI on disposable files."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
PATCHER = HERE / "patches/patch_portable_plan.py"
SOURCE_ROOT = os.environ.get("HERMES_UPSTREAM_SOURCE")
SOURCE = Path(SOURCE_ROOT) / "agent/plan_prompt.py" if SOURCE_ROOT else None


def load_patcher():
    spec = importlib.util.spec_from_file_location("portable_plan_patcher", PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def namespace(source):
    names = {}
    exec(compile(source, "agent/plan_prompt.py", "exec"), names)
    return names


class RefusedInputTests(unittest.TestCase):
    def test_cli_unknown_source_leaves_file_and_directory_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan_prompt.py"
            original = b'def build_plan_prompt(task=""):\n    return task\n'
            target.write_bytes(original)
            result = subprocess.run([sys.executable, str(PATCHER), str(target)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Unsupported Hermes plan prompt fingerprint", result.stderr)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [target])


@unittest.skipUnless(SOURCE is not None and SOURCE.is_file(), "set HERMES_UPSTREAM_SOURCE")
class PortablePlanPatchTests(unittest.TestCase):
    def setUp(self):
        self.patcher = load_patcher()
        self.source = SOURCE.read_text()
        self.patched = self.patcher.patch_source(self.source)

    def test_actual_prompt_has_enabled_loading_fallback_and_plan_only_contract(self):
        original_prompt = namespace(self.source)["build_plan_prompt"]("portable planning")
        prompt = namespace(self.patched)["build_plan_prompt"]("portable planning")
        # Establish the inspected regression at the actual prompt producer.
        self.assertIn("2-5 minutes", original_prompt)
        self.assertNotIn("Independent minimal native fallback", original_prompt)
        for forbidden in ("2-5 minutes", "complete copy-pasteable code",
                          "TDD cycle per task", "Principles: DRY, YAGNI, TDD"):
            self.assertNotIn(forbidden, prompt)
        for required in ("Do not implement code.", "Do not run mutating terminal commands",
                         "commit, push, or perform external", "only writable deliverable",
                         "Do not start executing in this turn", "even if earlier implementation authority",
                         "Do not bypass a disabled/unavailable method", "do not load its contents",
                         "State that the shared method did not run", "references/hermes.md"):
            self.assertIn(required, prompt)
        native_reads = [
            'skill_view(name="shared-skills")',
            'skill_view(name="shared-skills", file_path="references/hermes.md")',
            'skill_view(name="writing-plans")',
            'skill_view(name="writing-plans", file_path="references/hermes.md")',
        ]
        positions = [prompt.index(text) for text in native_reads]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(prompt.index("First resolve"), positions[0])
        owners = ["If the runtime provides a specific target path",
                  "Otherwise use the existing declared canonical plan owner",
                  "For a governed code project", "For an unmapped/non-code workspace"]
        owner_positions = [prompt.index(text) for text in owners]
        self.assertEqual(owner_positions, sorted(owner_positions))
        self.assertIn("use that exact path", prompt)
        self.assertIn("do not maintain two active plans", prompt.lower())
        self.assertIn("`.hermes/plans/YYYY-MM-DD_HHMMSS-<slug>.md`", prompt)
        self.assertIn("use `plans/`", prompt)

    def test_public_builder_api_task_interpolation_and_nonpolicy_code_are_preserved(self):
        before = ast.parse(self.source)
        after = ast.parse(self.patched)
        def nonpolicy(tree):
            return [ast.dump(node) for node in tree.body
                    if not (isinstance(node, ast.Assign)
                            and any(getattr(target, "id", "") in
                                    {"_PLAN_MODE_RULES", "_PLAN_CRAFT"}
                                    for target in node.targets))]
        self.assertEqual(nonpolicy(after), nonpolicy(before))
        build = namespace(self.patched)["build_plan_prompt"]
        for task in ("  migrate auth  ", "Unicode: España\nquotes ' \" ${value} `command`", "", None):
            with self.subTest(task=task):
                prompt = build(task)
                self.assertTrue(prompt.startswith("[/plan — plan mode]\n\n"))
                if task and task.strip():
                    self.assertIn(f"Task to plan:\n{task.strip()}\n", prompt)
                else:
                    self.assertIn("No explicit task was given with /plan", prompt)
                    self.assertIn("infer the task", prompt)
        self.assertEqual(build(), build(""))

    def test_known_patched_input_is_idempotent_and_drift_is_rejected(self):
        self.assertEqual(self.patcher.patch_source(self.patched), self.patched)
        for changed in (self.source + "# upstream changed\n",
                        self.source.replace('task: str = ""', 'task: str = "unknown"'),
                        self.patched.replace("Do not implement code.", "Implement code."),
                        self.patched.replace("use `plans/`", "use `other/`")):
            with self.subTest(changed=changed[-60:]):
                with self.assertRaisesRegex(ValueError, "Unsupported Hermes plan prompt fingerprint"):
                    self.patcher.patch_source(changed)

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan_prompt.py"
            target.write_text(self.source)
            with patch.object(self.patcher.os, "replace", side_effect=OSError("replacement denied")):
                with self.assertRaisesRegex(OSError, "replacement denied"):
                    self.patcher.patch_file(target)
            self.assertEqual(target.read_text(), self.source)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_cli_patches_once_preserves_mode_and_rejects_partial_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan_prompt.py"
            target.write_text(self.source)
            target.chmod(0o640)
            command = [sys.executable, str(PATCHER), str(target)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), self.patched)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            first_stat = target.stat()
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.stat().st_mtime_ns, first_stat.st_mtime_ns)
            self.assertEqual(target.stat().st_ino, first_stat.st_ino)
            partial = self.source.replace("Do not implement code.", "Do not implement anything.")
            target.write_text(partial)
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Unsupported Hermes plan prompt fingerprint", result.stderr)
            self.assertEqual(target.read_text(), partial)
            self.assertEqual(list(Path(directory).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
