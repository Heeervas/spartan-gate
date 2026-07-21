import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate-camofox-logins.py"


def load_module():
    spec = importlib.util.spec_from_file_location("migrate_camofox_logins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CamofoxLoginMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.hermes = self.root / "hermes"
        self.camofox = self.root / "camofox"
        (self.hermes / "profiles" / "market").mkdir(parents=True)
        (self.hermes / "profiles" / ".skill_symlink_backups").mkdir()
        (self.camofox / "profiles").mkdir(parents=True)
        self.original_config = {
            "browser": {
                "camofox": {
                    "managed_persistence": False,
                    "adopt_existing_tab": True,
                    "user_id": "legacy-owner",
                    "session_key": "legacy-session",
                }
            },
            "unrelated": {"keep": True},
        }
        for config in (
            self.hermes / "config.yaml",
            self.hermes / "profiles" / "market" / "config.yaml",
        ):
            config.write_text(yaml.safe_dump(self.original_config, sort_keys=False))
            config.chmod(0o600)

        self.migration = load_module()
        source = self.camofox / "profiles" / self.migration.hashed_user_dir(
            self.migration.LEGACY_USER_ID
        )
        (source / "opaque").mkdir(parents=True)
        (source / "storage-state.json").write_bytes(b"legacy-cookie-opaque-bytes")
        (source / "opaque" / "state.bin").write_bytes(b"\x00\x01legacy")
        self.source = source

    def tearDown(self):
        self.temporary.cleanup()

    def target(self, profile):
        user_id = self.migration.hermes_user_id(profile)
        return self.camofox / "profiles" / self.migration.hashed_user_dir(user_id)

    def run_migration(self, action="apply", *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                action,
                "--hermes-root",
                str(self.hermes),
                "--camofox-root",
                str(self.camofox),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def tree_digest(self, root):
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            digest.update(path.relative_to(root).as_posix().encode())
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_copy_all_is_idempotent_and_explicit_rollback_restores_every_profile(self):
        default = self.target("default")
        default.mkdir()
        (default / "storage-state.json").write_bytes(b"default-before")

        applied = self.run_migration()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertNotIn(self.migration.LEGACY_USER_ID, applied.stdout + applied.stderr)
        self.assertNotIn("legacy-cookie-opaque-bytes", applied.stdout + applied.stderr)
        for profile in ("default", "market"):
            self.assertEqual(self.tree_digest(self.target(profile)), self.tree_digest(self.source))
            config_path = (
                self.hermes / "config.yaml"
                if profile == "default"
                else self.hermes / "profiles" / profile / "config.yaml"
            )
            config = yaml.safe_load(config_path.read_text())
            camofox = config["browser"]["camofox"]
            self.assertIs(camofox["managed_persistence"], True)
            self.assertIs(camofox["adopt_existing_tab"], False)
            self.assertEqual(camofox["user_id"], "")
            self.assertEqual(camofox["session_key"], "")
            self.assertEqual(config["unrelated"], {"keep": True})

        (self.target("default") / "storage-state.json").write_bytes(b"new-login-after-migration")
        repeated = self.run_migration()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already applied", repeated.stdout)
        self.assertEqual(
            (self.target("default") / "storage-state.json").read_bytes(),
            b"new-login-after-migration",
        )

        rolled_back = self.run_migration("rollback")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual((default / "storage-state.json").read_bytes(), b"default-before")
        self.assertFalse(self.target("market").exists())
        for config in (
            self.hermes / "config.yaml",
            self.hermes / "profiles" / "market" / "config.yaml",
        ):
            self.assertEqual(yaml.safe_load(config.read_text()), self.original_config)

    def test_injected_publish_failure_restores_all_targets_and_configs(self):
        originals = {}
        for profile, content in (("default", b"default-old"), ("market", b"market-old")):
            target = self.target(profile)
            target.mkdir()
            (target / "storage-state.json").write_bytes(content)
            originals[profile] = self.tree_digest(target)

        failed = self.run_migration("apply", "--test-fail-after-publish", "1")
        self.assertNotEqual(failed.returncode, 0)
        for profile in ("default", "market"):
            self.assertEqual(self.tree_digest(self.target(profile)), originals[profile])
        for config in (
            self.hermes / "config.yaml",
            self.hermes / "profiles" / "market" / "config.yaml",
        ):
            self.assertEqual(yaml.safe_load(config.read_text()), self.original_config)

    def assert_pre_journal_fault_restores_all_state(self, fault):
        originals = {}
        for profile, content in (("default", b"default-old"), ("market", b"market-old")):
            target = self.target(profile)
            target.mkdir()
            (target / "storage-state.json").write_bytes(content)
            originals[profile] = self.tree_digest(target)
        configs_before = [
            path.read_bytes()
            for path in (
                self.hermes / "config.yaml",
                self.hermes / "profiles" / "market" / "config.yaml",
            )
        ]

        failed = self.run_migration(
            "apply",
            "--test-fail-before-journal",
            fault,
        )

        self.assertNotEqual(failed.returncode, 0)
        for profile in ("default", "market"):
            self.assertEqual(self.tree_digest(self.target(profile)), originals[profile])
        self.assertEqual(
            [
                path.read_bytes()
                for path in (
                    self.hermes / "config.yaml",
                    self.hermes / "profiles" / "market" / "config.yaml",
                )
            ],
            configs_before,
        )

    def test_target_pre_journal_failure_restores_all_state(self):
        self.assert_pre_journal_fault_restores_all_state("target")

    def test_config_pre_journal_failure_restores_all_state(self):
        self.assert_pre_journal_fault_restores_all_state("config")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlink_in_credential_tree_fails_before_profile_or_config_publish(self):
        os.symlink("storage-state.json", self.source / "linked-state")
        before = [
            path.read_bytes()
            for path in (
                self.hermes / "config.yaml",
                self.hermes / "profiles" / "market" / "config.yaml",
            )
        ]

        result = self.run_migration()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target("default").exists())
        self.assertFalse(self.target("market").exists())
        self.assertFalse((self.camofox / ".spartan-backups").exists())
        self.assertEqual(list((self.camofox / "profiles").glob(".spartan-stage-*")), [])
        after = [
            path.read_bytes()
            for path in (
                self.hermes / "config.yaml",
                self.hermes / "profiles" / "market" / "config.yaml",
            )
        ]
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
