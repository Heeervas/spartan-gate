from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("open-hermes-workstation-root.cmd")


class OpenHermesWorkstationConsoleTests(unittest.TestCase):
    def test_launcher_is_fixed_local_root_maintenance_console(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count("@echo off"), 1)
        self.assertEqual(source.count("wsl.exe --distribution"), 1)

        self.assertIn("choice /C YN", source)
        self.assertIn(
            "wsl.exe --distribution Hermes-Workstation --user root "
            "--cd /home/hermes-worker/workspace",
            source,
        )
        self.assertIn("DisableDelayedExpansion", source)

    def test_launcher_does_not_add_remote_or_network_capability(self):
        source = SCRIPT.read_text(encoding="utf-8").lower()

        for forbidden in (
            "ssh ",
            "tailscale",
            "portproxy",
            "netsh",
            "0.0.0.0",
            "authorized_keys",
            "private key",
            "--network",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
