import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "camofox-isolate-hermes-profiles.sh"


class CamofoxProfileIsolationTests(unittest.TestCase):
    def make_fixture(self, *, colliding_identity=False):
        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        data = root / "data"
        (data / "profiles" / "market").mkdir(parents=True)
        original = {
            "browser": {
                "camofox": {
                    "managed_persistence": False,
                    "adopt_existing_tab": True,
                    "user_id": "legacy-shared-owner",
                    "session_key": "legacy-shared-session",
                }
            }
        }
        for path in (data / "config.yaml", data / "profiles" / "market" / "config.yaml"):
            path.write_text(yaml.safe_dump(original, sort_keys=False))
            path.chmod(0o600)

        fake_bin = root / "bin"
        fake_bin.mkdir()
        hermes = fake_bin / "hermes"
        hermes.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import os
            import pathlib
            import sys
            import yaml

            args = sys.argv[1:]
            profile = "default"
            if args[:1] == ["-p"]:
                profile = args[1]
                args = args[2:]
            if len(args) != 4 or args[:2] != ["config", "set"]:
                raise SystemExit(2)
            root = pathlib.Path(os.environ["SPARTAN_CAMOFOX_DATA_ROOT"])
            path = root / "config.yaml" if profile == "default" else root / "profiles" / profile / "config.yaml"
            data = yaml.safe_load(path.read_text()) or {}
            target = data
            parts = args[2].split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            raw = args[3]
            value = True if raw == "true" else False if raw == "false" else raw
            target[parts[-1]] = value
            path.write_text(yaml.safe_dump(data, sort_keys=False))
        """))
        hermes.chmod(0o755)

        library = root / "lib"
        tools = library / "tools"
        tools.mkdir(parents=True)
        (tools / "__init__.py").write_text("")
        if colliding_identity:
            identity_body = """\
def get_camofox_identity(task_id=None):
    return {"user_id": "same", "session_key": "same"}
"""
        else:
            identity_body = """\
import os
import uuid

def get_camofox_identity(task_id=None):
    home = os.environ["HERMES_HOME"]
    logical = task_id or "default"
    return {
        "user_id": uuid.uuid5(uuid.NAMESPACE_URL, f"camofox-user:{home}").hex,
        "session_key": uuid.uuid5(uuid.NAMESPACE_URL, f"camofox-session:{home}:{logical}").hex,
    }
"""
        (tools / "browser_camofox_state.py").write_text(textwrap.dedent(identity_body))

        env = os.environ.copy()
        env.update({
            "SPARTAN_CAMOFOX_DATA_ROOT": str(data),
            "SPARTAN_CAMOFOX_HERMES_BIN": str(hermes),
            "HERMES_AUTOSTART_PROFILES": "market,missing",
            "PYTHONPATH": str(library),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return temporary, data, original, env

    def run_script(self, env, *args):
        return subprocess.run(
            [str(SCRIPT), *args],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_apply_discovers_existing_profiles_proves_distinct_and_rolls_back_original(self):
        temporary, data, original, env = self.make_fixture()
        with temporary:
            first = self.run_script(env, "apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("distinct=true profiles=2", first.stdout)
            self.assertNotIn("legacy-shared", first.stdout + first.stderr)
            for path in (data / "config.yaml", data / "profiles" / "market" / "config.yaml"):
                camofox = yaml.safe_load(path.read_text())["browser"]["camofox"]
                self.assertIs(camofox["managed_persistence"], True)
                self.assertIs(camofox["adopt_existing_tab"], False)
                self.assertEqual(camofox["user_id"], "")
                self.assertEqual(camofox["session_key"], "")

            backup = data / ".spartan-backups" / "camofox-profile-isolation-original"
            latest = data / ".spartan-backups" / "camofox-profile-isolation.latest"
            self.assertEqual(latest.read_text().strip(), str(backup))
            saved_latest = latest.stat().st_mtime_ns

            second = self.run_script(env, "apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(latest.stat().st_mtime_ns, saved_latest)

            rolled_back = self.run_script(env, "rollback")
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            for path in (data / "config.yaml", data / "profiles" / "market" / "config.yaml"):
                self.assertEqual(yaml.safe_load(path.read_text()), original)

    def test_explicit_missing_profile_fails_before_creating_backup(self):
        temporary, data, _original, env = self.make_fixture()
        with temporary:
            result = self.run_script(env, "apply", "default", "absent")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((data / ".spartan-backups").exists())

    def test_identity_collision_fails_and_restores_every_profile(self):
        temporary, data, original, env = self.make_fixture(colliding_identity=True)
        with temporary:
            result = self.run_script(env, "apply")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            for path in (data / "config.yaml", data / "profiles" / "market" / "config.yaml"):
                self.assertEqual(yaml.safe_load(path.read_text()), original)

    def test_symlink_profile_directory_is_rejected_before_backup_or_write(self):
        temporary, data, original, env = self.make_fixture()
        with temporary:
            external = data.parent / "external-market"
            (data / "profiles" / "market").rename(external)
            (data / "profiles" / "market").symlink_to(external, target_is_directory=True)
            result = self.run_script(env, "apply")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((data / ".spartan-backups").exists())
            self.assertEqual(yaml.safe_load((external / "config.yaml").read_text()), original)

    def test_rollback_rejects_profile_ancestor_replaced_by_symlink(self):
        temporary, data, _original, env = self.make_fixture()
        with temporary:
            applied = self.run_script(env, "apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            external = data.parent / "external-market"
            (data / "profiles" / "market").rename(external)
            config = external / "config.yaml"
            changed = yaml.safe_load(config.read_text())
            changed["outside_marker"] = "preserve"
            config.write_text(yaml.safe_dump(changed, sort_keys=False))
            (data / "profiles" / "market").symlink_to(external, target_is_directory=True)

            rolled_back = self.run_script(env, "rollback")
            self.assertNotEqual(rolled_back.returncode, 0)
            self.assertEqual(yaml.safe_load(config.read_text())["outside_marker"], "preserve")

    def test_alias_does_not_hardcode_optional_profiles(self):
        aliases = (ROOT / "scripts" / "aliases.sh").read_text()
        body = aliases.split("sg_camofox_profile_isolate() {", 1)[1].split("sg_private_env_file()", 1)[0]
        self.assertNotIn("default market work dreamer coach", body)
        self.assertIn('migrate-camofox-logins.py', body)
        self.assertIn('sg_compose stop hermes', body)
        self.assertIn('sg_compose stop camofox', body)
        self.assertIn('SPARTAN_HERMES_DATA_PATH', body)
        self.assertIn('SPARTAN_CAMOFOX_DATA_PATH', body)
        self.assertIn('docker run --rm', body)
        self.assertIn('--network none', body)
        self.assertIn('--read-only', body)
        self.assertIn('--cap-drop ALL', body)
        self.assertIn('--entrypoint python3', body)


if __name__ == "__main__":
    unittest.main()
