import hashlib
import importlib.util
import os
import pathlib
import tempfile
import unittest
import zipfile


SCRIPT = pathlib.Path(__file__).with_name("patch-addon-readiness.py")


def load_patcher():
    spec = importlib.util.spec_from_file_location("patch_addon_readiness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AddonReadinessPatchTests(unittest.TestCase):
    def make_archive(self, root, source):
        path = pathlib.Path(root) / "omni.ja"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("unrelated.txt", b"preserved")
            archive.writestr("chrome/browser/content/browser/browser-init.js", source)
        return path

    def run_main(self, module, path):
        previous = os.environ.get("CAMOFOX_BROWSER_OMNI")
        os.environ["CAMOFOX_BROWSER_OMNI"] = str(path)
        try:
            module.main()
        finally:
            if previous is None:
                os.environ.pop("CAMOFOX_BROWSER_OMNI", None)
            else:
                os.environ["CAMOFOX_BROWSER_OMNI"] = previous

    def test_patch_is_atomic_and_idempotent(self):
        module = load_patcher()
        source = ("before\n" + module.OLD + "after\n").encode()
        module.SOURCE_SHA256 = hashlib.sha256(source).hexdigest()
        module.PATCHED_SHA256 = hashlib.sha256(source.replace(module.OLD.encode(), module.NEW.encode())).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            archive = self.make_archive(root, source)
            self.run_main(module, archive)
            first = archive.read_bytes()
            self.run_main(module, archive)
            self.assertEqual(archive.read_bytes(), first)
            with zipfile.ZipFile(archive) as result:
                patched = result.read(module.ENTRY).decode()
                self.assertIn(module.MARKER, patched)
                self.assertIn("Services.prefs.setBoolPref", patched)
                self.assertEqual(result.read("unrelated.txt"), b"preserved")

    def test_unexpected_source_fails_without_rewriting_archive(self):
        module = load_patcher()
        source = ("before\n" + module.OLD + module.OLD + "after\n").encode()
        module.SOURCE_SHA256 = hashlib.sha256(source).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            archive = self.make_archive(root, source)
            before = archive.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "exactly one addon-install anchor"):
                self.run_main(module, archive)
            self.assertEqual(archive.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
