from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("quarantine-workstation-key.py")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "quarantine_workstation_key", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class QuarantineWorkstationKeyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / "source"
        self.exposed = self.root / "exposed"
        self.destination = self.root / "quarantine"
        self.source.write_bytes(b"same-private-key")
        self.exposed.write_bytes(b"same-private-key")
        self.source.chmod(0o600)
        self.exposed.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_moves_only_hash_identical_exposed_key_and_preserves_source(self):
        target = self.module.quarantine(
            self.source, self.exposed, self.destination
        )

        self.assertTrue(self.source.exists())
        self.assertFalse(self.exposed.exists())
        self.assertEqual(target.read_bytes(), b"same-private-key")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_hash_mismatch_fails_without_moving_either_file(self):
        self.exposed.write_bytes(b"different")
        self.exposed.chmod(0o600)

        with self.assertRaisesRegex(self.module.QuarantineError, "hashes differ"):
            self.module.quarantine(self.source, self.exposed, self.destination)

        self.assertTrue(self.source.exists())
        self.assertTrue(self.exposed.exists())

    def test_rejects_symlink_and_group_readable_key(self):
        self.exposed.unlink()
        self.exposed.symlink_to(self.source)
        with self.assertRaisesRegex(self.module.QuarantineError, "non-symlink"):
            self.module.quarantine(self.source, self.exposed, self.destination)

        self.exposed.unlink()
        self.exposed.write_bytes(b"same-private-key")
        self.exposed.chmod(0o640)
        with self.assertRaisesRegex(self.module.QuarantineError, "group/other"):
            self.module.quarantine(self.source, self.exposed, self.destination)


if __name__ == "__main__":
    unittest.main()
