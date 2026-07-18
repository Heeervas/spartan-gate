from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spartan-route-data.py"
ALIASES = ROOT / "scripts" / "aliases.sh"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def load_module():
    spec = importlib.util.spec_from_file_location("spartan_route_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SpartanRouteDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data = self.root / "route-data"
        self.data.mkdir(mode=0o700)
        self.db = self.data / "spartan-route.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker(value) VALUES ('preserved')")
        self.db.chmod(0o600)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_script(self, action: str, *, uid: int | None = None, gid: int | None = None):
        return subprocess.run(
            (
                sys.executable,
                SCRIPT,
                action,
                "--data-dir",
                self.data,
                "--uid",
                str(os.getuid() if uid is None else uid),
                "--gid",
                str(os.getgid() if gid is None else gid),
            ),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_accepts_exact_sqlite_family_without_traversing_other_content(self):
        unrelated = self.data / "operator-notes"
        unrelated.mkdir()
        secret = unrelated / "do-not-read"
        secret.write_text("sentinel-private-content\n", encoding="utf-8")
        secret.chmod(0)

        result = self.run_script("check")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertNotIn("sentinel-private-content", result.stdout + result.stderr)

    def test_check_rejects_wrong_owner_without_creating_backup(self):
        result = self.run_script("check", uid=os.getuid() + 1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owner", (result.stdout + result.stderr).lower())
        self.assertFalse((self.data / "backups").exists())
        self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_prepare_creates_a_missing_data_directory_for_the_runtime_identity(self):
        shutil.rmtree(self.data)

        result = self.run_script("prepare")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.data.is_dir())
        self.assertEqual(mode(self.data), 0o700)
        self.assertEqual((self.data.stat().st_uid, self.data.stat().st_gid), (os.getuid(), os.getgid()))

    def test_migrate_preflight_rejects_database_symlink_before_writes(self):
        outside = self.root / "outside.db"
        self.db.rename(outside)
        self.db.symlink_to(outside)

        result = self.run_script("migrate")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", (result.stdout + result.stderr).lower())
        self.assertFalse((self.data / "backups").exists())
        self.assertEqual(mode(outside), 0o600)

    def test_migrate_rejects_unsafe_existing_backup_directory_before_writes(self):
        backups = self.data / "backups"
        backups.mkdir(mode=0o755)
        self.data.chmod(0o755)
        self.db.chmod(0o664)

        result = self.run_script("migrate")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup directory", (result.stdout + result.stderr).lower())
        self.assertEqual(mode(backups), 0o755)
        self.assertEqual(mode(self.data), 0o755)
        self.assertEqual(mode(self.db), 0o664)
        self.assertEqual(list(backups.iterdir()), [])

    def test_migrate_backs_up_then_repairs_only_managed_metadata_and_is_idempotent(self):
        unrelated = self.data / "unrelated.bin"
        unrelated.write_bytes(b"unchanged")
        unrelated.chmod(0o640)
        self.data.chmod(0o755)
        self.db.chmod(0o664)

        first = self.run_script("migrate")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(mode(self.data), 0o700)
        self.assertEqual(mode(self.db), 0o600)
        self.assertEqual(mode(unrelated), 0o640)
        backups = list((self.data / "backups").glob("spartan-route-*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(mode(backups[0]), 0o600)
        with sqlite3.connect(backups[0]) as connection:
            value = connection.execute("SELECT value FROM marker").fetchone()[0]
        self.assertEqual(value, "preserved")

        second = self.run_script("migrate")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(list((self.data / "backups").glob("spartan-route-*.db"))), 1)

    def test_migrate_repairs_sqlite_sidecars_created_during_backup(self):
        module = load_module()
        self.data.chmod(0o755)
        original_create_backup = module._create_backup

        def create_backup_with_sidecars(*args, **kwargs) -> None:
            original_create_backup(*args, **kwargs)
            for suffix in ("-wal", "-shm"):
                sidecar = self.data / f"spartan-route.db{suffix}"
                sidecar.touch(mode=0o644)
                sidecar.chmod(0o644)

        with patch.object(
            module,
            "_create_backup",
            side_effect=create_backup_with_sidecars,
        ):
            module.migrate_data_dir(self.data, os.getuid(), os.getgid())

        self.assertEqual(mode(self.data / "spartan-route.db-wal"), 0o600)
        self.assertEqual(mode(self.data / "spartan-route.db-shm"), 0o600)
        module.check_data_dir(self.data, os.getuid(), os.getgid())

    def test_metadata_failure_rolls_back_all_managed_targets(self):
        module = load_module()
        self.data.chmod(0o755)
        self.db.chmod(0o664)
        before = {self.data: mode(self.data), self.db: mode(self.db)}
        calls = 0

        def fail_second_chmod(path: Path, target_mode: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("injected metadata failure")
            os.chmod(path, target_mode)

        with self.assertRaises(module.DataPolicyError) as raised:
            module.migrate_data_dir(
                self.data,
                os.getuid(),
                os.getgid(),
                chmod_func=fail_second_chmod,
            )

        self.assertIn("restored", raised.exception.message)
        self.assertEqual({path: mode(path) for path in before}, before)


class SpartanRouteAliasPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        (self.root / "private" / "env").mkdir(parents=True)
        shutil.copy2(ALIASES, self.root / "scripts" / "aliases.sh")
        shutil.copy2(SCRIPT, self.root / "scripts" / "spartan-route-data.py")
        self.data = self.root / "route-data"
        self.data.mkdir(mode=0o700)
        self.log = self.root / "docker.log"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        docker = self.bin_dir / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {self.log}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_env(self, uid: int) -> None:
        (self.root / "private" / "env" / "local.env").write_text(
            "SPARTAN_TIER=L4\n"
            f"SPARTAN_ROUTE_DATA_PATH={self.data}\n"
            f"SPARTAN_ROUTE_UID={uid}\n"
            f"SPARTAN_ROUTE_GID={os.getgid()}\n",
            encoding="utf-8",
        )

    def run_sourced(self, command: str):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sg_up_checks_state_before_docker(self):
        self.write_env(os.getuid() + 1)

        result = self.run_sourced("sg up -d")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertIn("sg-spartan-route-data-migrate", result.stderr)
        self.assertNotIn(str(self.data), result.stdout + result.stderr)

    def test_all_route_start_wrappers_share_the_preflight(self):
        self.write_env(os.getuid())

        for command in ("sg up -d", "sg route up -d", "sg-up", "sg-build-up"):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_sourced(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.log.exists())

    def test_migration_alias_is_discoverable_and_uses_narrow_capabilities(self):
        source = ALIASES.read_text(encoding="utf-8")
        migration = source.split("sg_spartan_route_data_migrate()", 1)[1].split("\n}", 1)[0]

        self.assertIn("sg-spartan-route-data-check", source)
        self.assertIn("sg-spartan-route-data-migrate", source)
        self.assertIn("--cap-add CHOWN", migration)
        self.assertIn("--cap-add FOWNER", migration)
        self.assertIn("--cap-add DAC_OVERRIDE", migration)
        self.assertNotIn("chown -R", migration)

    def test_doctor_and_installer_check_route_state_before_start(self):
        doctor = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        doctor_check = doctor.find("spartan-route-data.py\" check")
        private_validation = doctor.find("validate_private_env private/env/local.env")
        self.assertGreaterEqual(doctor_check, 0)
        self.assertGreater(private_validation, doctor_check)

        install_check = installer.find("spartan-route-data.py\" prepare")
        compose_start = installer.find("compose up -d --remove-orphans")
        self.assertGreaterEqual(install_check, 0)
        self.assertGreater(compose_start, install_check)

    def test_route_process_uses_private_file_creation_mask(self):
        entrypoint = (
            ROOT / "infra" / "spartan-route" / "spartan_route" / "__main__.py"
        ).read_text(encoding="utf-8")

        self.assertIn("os.umask(0o077)", entrypoint)


if __name__ == "__main__":
    unittest.main()
