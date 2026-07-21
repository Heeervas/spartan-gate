import importlib.util
from pathlib import Path
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("wrangler-auth.py")


def load_module():
    spec = importlib.util.spec_from_file_location("wrangler_auth", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WranglerAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = load_module()

    def test_accepts_only_wranger_loopback_callback(self):
        for host in ("localhost", "127.0.0.1"):
            value = f"http://{host}:8976/oauth/callback?code=secret&state=opaque"
            self.assertEqual(self.auth.validate_callback_url(value), value)

        rejected = (
            "https://localhost:8976/oauth/callback?code=x&state=y",
            "http://hermes:8976/oauth/callback?code=x&state=y",
            "http://localhost:8977/oauth/callback?code=x&state=y",
            "http://localhost:8976/other?code=x&state=y",
            "http://user@localhost:8976/oauth/callback?code=x&state=y",
            "http://localhost:8976/oauth/callback?code=x",
            "http://localhost:8976/oauth/callback?state=y",
            "http://localhost:8976/oauth/callback?code=x&state=y#fragment",
            "http://localhost:8976/oauth/callback?code=x&state=y\nheader:value",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.auth.validate_callback_url(value)

    def test_termination_targets_process_group_and_escalates(self):
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = None
        process.wait.side_effect = [self.auth.subprocess.TimeoutExpired("wrangler", 3), 0]

        with mock.patch.object(self.auth.os, "killpg") as killpg:
            self.auth._terminate_process_group(process)

        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4321, self.auth.signal.SIGTERM),
                mock.call(4321, self.auth.signal.SIGKILL),
            ],
        )
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=3), mock.call(timeout=3)])

    def test_script_keeps_callback_off_argv_env_and_echoed_input(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('WRANGLER_PACKAGE = "wrangler@4.45.0"', source)
        self.assertIn('getpass.getpass("Paste callback URL (hidden): ")', source)
        self.assertIn("start_new_session=True", source)
        self.assertIn("urllib.request.urlopen(callback_url, timeout=10)", source)
        self.assertNotIn("callback_url]", source)
        self.assertNotIn('env={"callback', source)


if __name__ == "__main__":
    unittest.main()
