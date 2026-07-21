import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("update-private-env-secret.py")


def load_module():
    spec = importlib.util.spec_from_file_location("update_private_env_secret", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrivateEnvSecretTests(unittest.TestCase):
    def test_update_is_atomic_private_and_preserves_backup_without_secret_output(self):
        helper = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "private" / "env" / "local.env"
            env_file.parent.mkdir(parents=True)
            env_file.write_text("KEEP=value\nCLOUDFLARE_API_TOKEN=old\n", encoding="utf-8")
            env_file.chmod(0o600)

            backup = helper.update_secret(env_file, "CLOUDFLARE_API_TOKEN", "new-secret")

            self.assertEqual(backup.read_text(encoding="utf-8"), "KEEP=value\nCLOUDFLARE_API_TOKEN=old\n")
            self.assertEqual(env_file.read_text(encoding="utf-8"), "KEEP=value\nCLOUDFLARE_API_TOKEN=new-secret\n")
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_rejects_line_breaks_and_unsafe_path_without_replacing_state(self):
        helper = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                helper.update_secret(env_file, "CLOUDFLARE_API_TOKEN", "bad\nvalue")
            self.assertEqual(env_file.read_text(encoding="utf-8"), "KEEP=value\n")

            env_file.unlink()
            env_file.symlink_to("elsewhere")
            with self.assertRaises(ValueError):
                helper.update_secret(env_file, "CLOUDFLARE_API_TOKEN", "value")


if __name__ == "__main__":
    unittest.main()
