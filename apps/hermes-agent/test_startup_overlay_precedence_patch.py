import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("patches").joinpath(
    "archive/2026-10-01-patch_startup_overlay_precedence.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "patch_startup_overlay_precedence",
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class StartupOverlayPrecedencePatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.original_path = list(sys.path)
        self.original_target = os.environ.get("PIP_TARGET")

    def tearDown(self):
        sys.path[:] = self.original_path
        if self.original_target is None:
            os.environ.pop("PIP_TARGET", None)
        else:
            os.environ["PIP_TARGET"] = self.original_target

    def _load_patched_function(self):
        source = "import os\nimport sys\n\n" + self.module.OLD
        namespace = {
            "__file__": "/opt/hermes/hermes_cli/_startup_fast.py",
            "project_root_str": lambda: "/opt/hermes",
        }
        exec(self.module.patch_source(source), namespace)
        return namespace["ensure_project_root_on_path"]

    def test_keeps_configured_overlay_ahead_of_project_root(self):
        ensure_path = self._load_patched_function()
        os.environ["PIP_TARGET"] = "/opt/data/hermes-extra-site"
        sys.path[:] = [
            "/opt/hermes/.venv/bin",
            "/opt/hermes/bootstrap",
            "/opt/data/hermes-extra-site",
            "/opt/hermes",
            "/stdlib",
        ]

        ensure_path()

        self.assertEqual(
            sys.path[:5],
            [
                "/opt/hermes/.venv/bin",
                "/opt/hermes/bootstrap",
                "/opt/data/hermes-extra-site",
                "/opt/hermes",
                "/stdlib",
            ],
        )
        self.assertEqual(sys.path.count("/opt/hermes"), 1)

    def test_preserves_upstream_root_first_behavior_without_active_overlay(self):
        ensure_path = self._load_patched_function()
        os.environ["PIP_TARGET"] = "/missing-overlay"
        sys.path[:] = ["/bootstrap", "/opt/hermes", "/stdlib"]

        ensure_path()

        self.assertEqual(sys.path, ["/opt/hermes", "/bootstrap", "/stdlib"])

    def test_deduplicates_overlay_and_project_root(self):
        ensure_path = self._load_patched_function()
        os.environ["PIP_TARGET"] = "/opt/data/hermes-extra-site/"
        sys.path[:] = [
            "",
            "/opt/data/hermes-extra-site",
            "/opt/hermes",
            "/stdlib",
            "/opt/data/hermes-extra-site/",
            "/opt/hermes/",
        ]

        ensure_path()
        ensure_path()

        self.assertEqual(
            sys.path,
            [
                "",
                "/opt/data/hermes-extra-site",
                "/opt/hermes",
                "/stdlib",
            ],
        )

    def test_equal_overlay_and_project_root_keeps_root_first(self):
        ensure_path = self._load_patched_function()
        os.environ["PIP_TARGET"] = "/opt/hermes/"
        sys.path[:] = ["/bootstrap", "/opt/hermes", "/stdlib"]

        ensure_path()

        self.assertEqual(sys.path, ["/opt/hermes", "/bootstrap", "/stdlib"])

    def test_matches_symlink_equivalent_overlay_path(self):
        ensure_path = self._load_patched_function()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            overlay = root / "overlay"
            alias = root / "alias"
            overlay.mkdir()
            alias.symlink_to(overlay, target_is_directory=True)
            os.environ["PIP_TARGET"] = str(alias)
            sys.path[:] = ["/bootstrap", str(overlay), "/opt/hermes", "/stdlib"]

            ensure_path()

            self.assertEqual(
                sys.path,
                ["/bootstrap", str(overlay), "/opt/hermes", "/stdlib"],
            )

    def test_is_idempotent(self):
        patched = self.module.patch_source(self.module.OLD)

        self.assertEqual(self.module.patch_source(patched), patched)

    def test_rejects_unknown_upstream_shape(self):
        with self.assertRaisesRegex(ValueError, "anchor not found"):
            self.module.patch_source("def unrelated():\n    pass\n")

    def test_rejects_partial_marker(self):
        with self.assertRaisesRegex(ValueError, "marker is incomplete"):
            self.module.patch_source(self.module.MARKER + "\npartial\n")


if __name__ == "__main__":
    unittest.main()
