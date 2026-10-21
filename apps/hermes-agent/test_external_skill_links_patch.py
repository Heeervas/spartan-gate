"""Native sync regressions; set HERMES_SYNC_SOURCE to supported upstream source."""
import importlib.util
import os
import shutil
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PATCH_PATH = Path(__file__).parent / "patches" / "patch_external_skill_links.py"
spec = importlib.util.spec_from_file_location("external_link_patch", PATCH_PATH)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
SOURCE_PATH = os.environ.get("HERMES_SYNC_SOURCE")


@unittest.skipUnless(SOURCE_PATH, "requires HERMES_SYNC_SOURCE and native Hermes dependencies")
class ExternalLinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(SOURCE_PATH).read_text(encoding="utf-8")

    def setUp(self):
        import hermes_constants
        import tools.skills_sync
        import agent.skill_utils
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "profile"
        self.skills = self.home / "skills"
        self.skills.mkdir(parents=True)
        self.bundled = self.root / "bundled"
        self.external = self.root / "external"
        self.token = hermes_constants.set_hermes_home_override(self.home)
        self.addCleanup(hermes_constants.reset_hermes_home_override, self.token)
        self.module = types.ModuleType("tools.skills_sync")
        self.module.__file__ = str(self.root / "tools" / "skills_sync.py")
        exec(compile(repair.patch_text(self.source), self.module.__file__, "exec"), self.module.__dict__)
        self.bind(patch.dict(sys.modules, {"tools.skills_sync": self.module}))
        self.bind(patch.object(tools, "skills_sync", self.module))
        self.bind(patch.object(self.module, "_get_bundled_dir", return_value=self.bundled))
        self.bind(patch.object(self.module, "_get_optional_dir", return_value=self.root / "absent-optional"))
        self.bind(patch.object(agent.skill_utils, "get_external_skills_dirs", return_value=[self.external]))

    def bind(self, binding):
        binding.start()
        self.addCleanup(binding.stop)

    def package(self, directory, name="example", body="same"):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")
        (directory / "helper.txt").write_text("helper\n")
        return directory

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode)
                for p in directory.rglob("*") if p.is_file()}

    def test_canonical_links_survive_flat_and_categorized_repeated_sync(self):
        for category in (Path("."), Path("category")):
            for body in ("same", "different"):
                with self.subTest(category=category, body=body):
                    bundled = self.package(self.bundled / category / "example")
                    external = self.package(self.external / "example", body=body)
                    dest = self.skills / category / "example"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.symlink_to(external, target_is_directory=True)
                    (external / "SKILL.md").chmod(0o444)
                    before = self.snapshot(external)
                    link = (os.readlink(dest), dest.lstat().st_mode)
                    try:
                        for _ in range(2):
                            result = self.module.sync_skills(quiet=True)
                            self.assertEqual(result["shadowed_by_external"], ["example"])
                            self.assertEqual(result["copied"], [])
                            self.assertEqual(result["updated"], [])
                            self.assertTrue(dest.is_symlink())
                            self.assertEqual((os.readlink(dest), dest.lstat().st_mode), link)
                            self.assertEqual(self.snapshot(external), before)
                    finally:
                        dest.unlink()
                        shutil.rmtree(bundled)
                        shutil.rmtree(external)

    def test_unrelated_or_undiscovered_target_does_not_bypass_guard(self):
        self.package(self.bundled / "example")
        self.package(self.external / "example")
        # Same bytes and underneath the registered root are insufficient: this
        # package's own directory/frontmatter names are unrelated to example.
        other = self.package(self.external / "other", name="other")
        (other / "SKILL.md").write_bytes((self.bundled / "example" / "SKILL.md").read_bytes())
        # Directory name collision elsewhere must not authorize an arbitrary path.
        elsewhere = self.package(self.root / "elsewhere" / "example")
        for target in (elsewhere, other / "nested"):
            with self.subTest(target=target):
                if target.name == "nested":
                    self.package(target)
                    # Exclude this package from external discovery through a
                    # real excluded subtree, rather than mocking the index.
                    target.rename(other / ".git")
                    target = other / ".git"
                dest = self.skills / "example"
                dest.symlink_to(target, target_is_directory=True)
                before = self.snapshot(target)
                with self.assertRaisesRegex(ValueError, "not strictly under"):
                    self.module.sync_skills(quiet=True)
                self.assertTrue(dest.is_symlink())
                self.assertEqual(self.snapshot(target), before)
                dest.unlink()

    def test_directory_alias_collision_does_not_authorize_other_package(self):
        bundled = self.package(self.bundled / "example")
        (bundled / "SKILL.md").write_text("# Package without a declared name\n")
        self.package(self.external / "example", name="declared-alias")
        other = self.package(self.external / "other")
        (other / "SKILL.md").write_bytes((bundled / "SKILL.md").read_bytes())
        dest = self.skills / "example"
        dest.symlink_to(other, target_is_directory=True)
        before = self.snapshot(other)
        with self.assertRaisesRegex(ValueError, "not strictly under"):
            self.module.sync_skills(quiet=True)
        self.assertTrue(dest.is_symlink())
        self.assertEqual(self.snapshot(other), before)

    def test_stale_plain_directory_is_removed_and_manifest_cleared(self):
        self.package(self.bundled / "example")
        self.package(self.external / "example")
        local = self.package(self.skills / "example")
        (self.skills / ".bundled_manifest").write_text("example:old\n")
        result = self.module.sync_skills(quiet=True)
        self.assertEqual(result["shadowed_by_external"], ["example"])
        self.assertFalse(local.exists())
        self.assertEqual((self.skills / ".bundled_manifest").read_text(), "")

    def test_patch_idempotence_and_fail_closed_shape(self):
        patched = repair.patch_text(self.source)
        self.assertEqual(repair.patch_text(patched), patched)
        original_guard = self.source[self.source.index("def _rmtree_writable("):]
        self.assertEqual(patched[patched.index("def _rmtree_writable("):], original_guard)
        for broken in (self.source.replace("external_names.discard", "changed.discard"),
                       patched.replace("external_paths: Set[Path]", "bad: Set[Path]")):
            with self.assertRaises(ValueError):
                repair.patch_text(broken)


if __name__ == "__main__":
    unittest.main()
