from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).with_name("audit-install-tree.py")


def load_module():
    spec = importlib.util.spec_from_file_location("audit_install_tree", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class InstallTreeAuditTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name) / "hermes"
        self.root.mkdir(mode=0o755)
        (self.root / "code.py").write_text("pass\n", encoding="utf-8")
        (self.root / "code.py").chmod(0o644)
        self.uid = os.getuid()
        self.gid = os.getgid()

    def tearDown(self):
        self.tempdir.cleanup()

    def audit(self):
        return self.module.audit_tree(
            self.root, expected_uid=self.uid, expected_gid=self.gid
        )

    def test_accepts_root_owned_shape_and_safe_external_symlink(self):
        (self.root / "python").symlink_to("/usr/bin/python3")
        self.assertGreaterEqual(self.audit(), 3)

    def test_rejects_group_or_world_writable_object(self):
        (self.root / "code.py").chmod(0o664)
        with self.assertRaisesRegex(self.module.InstallTreeError, "unsafe mode"):
            self.audit()

    def test_rejects_unexpected_owner(self):
        original_lstat = pathlib.Path.lstat

        def lstat(path):
            value = original_lstat(path)
            if pathlib.Path(path).name != "code.py":
                return value
            replacement = mock.Mock(wraps=value)
            replacement.st_uid = self.uid + 1
            replacement.st_gid = value.st_gid
            replacement.st_mode = value.st_mode
            replacement.st_dev = value.st_dev
            return replacement

        with mock.patch.object(pathlib.Path, "lstat", lstat):
            with self.assertRaisesRegex(self.module.InstallTreeError, "unexpected owner"):
                self.audit()

    def test_rejects_posix_acl(self):
        def listxattr(path, *, follow_symlinks=True):
            if pathlib.Path(path).name == "code.py":
                return ["system.posix_acl_access"]
            return []

        with mock.patch.object(self.module.os, "listxattr", listxattr):
            with self.assertRaisesRegex(self.module.InstallTreeError, "unsafe xattr"):
                self.audit()

    def test_rejects_broken_symlink(self):
        (self.root / "broken").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(self.module.InstallTreeError, "broken or cyclic"):
            self.audit()

    def test_rejects_symlink_into_runtime_writable_root(self):
        (self.root / "mutable").symlink_to("/tmp")
        with self.assertRaisesRegex(self.module.InstallTreeError, "writable runtime root"):
            self.audit()

    def test_rejects_external_symlink_through_non_root_owned_parent(self):
        (self.root / "external.py").symlink_to(MODULE_PATH)
        with self.assertRaisesRegex(
            self.module.InstallTreeError, "outside system roots"
        ):
            self.audit()

    def test_rejects_setid_install_object(self):
        (self.root / "code.py").chmod(0o4755)
        with self.assertRaisesRegex(self.module.InstallTreeError, "unsafe mode"):
            self.audit()

    def test_rejects_file_capability_xattr(self):
        def listxattr(path, *, follow_symlinks=True):
            if pathlib.Path(path).name == "code.py":
                return ["security.capability"]
            return []

        with mock.patch.object(self.module.os, "listxattr", listxattr):
            with self.assertRaisesRegex(self.module.InstallTreeError, "security.capability"):
                self.audit()


if __name__ == "__main__":
    unittest.main()
