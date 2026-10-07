import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


SPEC = importlib.util.spec_from_file_location("hermes_log_remediation", Path(__file__).with_name("hermes_log_remediation.py"))
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "home"
        self.root.mkdir()
        (self.root / "profiles").mkdir()
        self.backup = self.base / "backup"
        self.backup.mkdir(mode=0o700)

    def profile(self, name=None, config=None, env="UNRELATED_KEY=fixture-value\n"):
        home = self.root if name is None else self.root / "profiles" / name
        home.mkdir(exist_ok=True)
        (home / "config.yaml").write_text(yaml.safe_dump(config or {}, sort_keys=False))
        (home / ".env").write_text(env)
        (home / ".env").chmod(0o600)
        return home

    def test_repair_is_idempotent_preserves_secrets_and_intentional_targets(self):
        home = self.profile(config={
            "plugins": {"enabled": ["alberto-tools", "adan-discord-launcher", "web-search-plus", "toolaria"]},
            "session_reset": {"mode": "both", "at_hour": 4},
            "platform_toolsets": {"discord": ["file"], "webhook": [], "cli": ["none"]},
        })
        (home / "cron").mkdir()
        job = {"id": "fixture", "deliver": "discord:explicit-destination", "origin": {"thread_id": "another-thread"}, "enabled_toolsets": ["file"]}
        (home / "cron/jobs.json").write_text(json.dumps({"jobs": [job]}))
        changes = MODULE.plan(self.root)
        self.assertEqual(len(changes), 2)
        MODULE.apply(changes, self.backup)
        config = yaml.safe_load((home / "config.yaml").read_text())
        self.assertEqual(config["plugins"]["enabled"], ["alberto-tools", "toolaria"])
        self.assertNotIn("session_reset", config)
        self.assertEqual(config["platform_toolsets"], {"discord": ["file"], "webhook": [], "cli": ["none"]})
        updated = json.loads((home / "cron/jobs.json").read_text())["jobs"][0]
        self.assertEqual(updated["deliver"], job["deliver"])
        self.assertEqual(updated["origin"], job["origin"])
        self.assertEqual(updated["enabled_toolsets"], ["file"])
        self.assertEqual((home / ".env").read_text(), "UNRELATED_KEY=fixture-value\nSEARXNG_URL=http://searxng:8080\n")
        self.assertEqual((home / ".env").stat().st_mode & 0o777, 0o600)
        self.assertEqual(MODULE.plan(self.root), [])

    def test_present_plugin_and_explicit_search_settings_remain(self):
        home = self.profile(config={"plugins": {"enabled": ["alberto-tools", "adan-discord-launcher"]}}, env="export SEARXNG_URL='http://custom.internal:8080'\n")
        (home / "plugins/adan-discord-launcher").mkdir(parents=True)
        self.assertEqual(MODULE.plan(self.root), [])

    def test_last_profile_validation_failure_does_not_write_first(self):
        first = self.profile(config={"session_reset": {"mode": "daily"}})
        last = self.profile("last")
        (last / "config.yaml").write_text("plugins: {}\nplugins: {}\n")
        original = (first / "config.yaml").read_bytes()
        with self.assertRaises(ValueError):
            MODULE.plan(self.root)
        self.assertEqual((first / "config.yaml").read_bytes(), original)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_symlink_and_duplicate_search_settings_refused(self):
        home = self.profile()
        (home / ".env").write_text("SEARXNG_URL=a\nexport SEARXNG_URL=b\n")
        with self.assertRaises(ValueError):
            MODULE.plan(self.root)
        (home / ".env").unlink()
        (home / ".env").symlink_to(self.base / "missing")
        with self.assertRaises(ValueError):
            MODULE.plan(self.root)

    def test_changed_after_preflight_refused_without_backups_or_writes(self):
        home = self.profile(config={"session_reset": {"mode": "daily"}})
        changes = MODULE.plan(self.root)
        (home / ".env").write_text("USER_CHANGE=preserve\n")
        with self.assertRaises(ValueError):
            MODULE.apply(changes, self.backup)
        self.assertEqual((home / ".env").read_text(), "USER_CHANGE=preserve\n")
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_second_write_failure_rolls_back_completed_writes(self):
        self.profile(config={"session_reset": {"mode": "daily"}})
        changes = MODULE.plan(self.root)
        real = MODULE._atomic
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture failure")
            return real(*args)

        with patch.object(MODULE, "_atomic", side_effect=fail_second):
            with self.assertRaises(OSError):
                MODULE.apply(changes, self.backup)
        for change in changes:
            self.assertEqual(change.path.read_bytes(), change.original)

    def test_automatic_reset_plugin_is_not_silently_overridden(self):
        self.profile(config={"plugins": {"enabled": ["hermes-session-reset-policy"]}})
        with self.assertRaises(ValueError):
            MODULE.plan(self.root)

    def test_missing_env_refused_without_writes(self):
        home = self.profile()
        (home / ".env").unlink()
        with self.assertRaises(ValueError):
            MODULE.plan(self.root)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_owner_is_required_and_backup_manifest_retains_metadata(self):
        home = self.profile()
        with patch.object(MODULE.os, "geteuid", return_value=home.stat().st_uid + 1):
            with self.assertRaises(ValueError):
                MODULE.plan(self.root)
        changes = MODULE.plan(self.root)
        MODULE.apply(changes, self.backup)
        manifest = json.loads((self.backup / "manifest.json").read_text())
        self.assertEqual(manifest[0]["path"], str(home / ".env"))
        self.assertEqual(manifest[0]["uid"], home.stat().st_uid)
        self.assertEqual((self.backup / manifest[0]["backup"]).read_bytes(), changes[0].original)


if __name__ == "__main__":
    unittest.main()
