import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name('hermes-api-port-preflight.py')


class HermesApiPortPreflightTests(unittest.TestCase):
    def run_preflight(self, home: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(home)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_fails_on_duplicate_enabled_profile_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / '.env').write_text('API_SERVER_ENABLED=true\nAPI_SERVER_PORT=8642\n')
            (home / 'config.yaml').write_text('platforms:\n  api_server:\n    enabled: true\n')
            profile = home / 'profiles' / 'coach'
            profile.mkdir(parents=True)
            (profile / 'SOUL.md').write_text('coach')
            (profile / 'config.yaml').write_text('platforms:\n  api_server:\n    enabled: true\n')

            result = self.run_preflight(home)

        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate Hermes API ports', result.stderr)
        self.assertIn('port 8642', result.stderr)

    def test_passes_with_unique_profile_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / '.env').write_text('API_SERVER_ENABLED=true\nAPI_SERVER_PORT=8642\n')
            (home / 'config.yaml').write_text('platforms:\n  api_server:\n    enabled: true\n')
            profile = home / 'profiles' / 'coach'
            profile.mkdir(parents=True)
            (profile / 'SOUL.md').write_text('coach')
            (profile / '.env').write_text('API_SERVER_PORT=8643\n')
            (profile / 'config.yaml').write_text('platforms:\n  api_server:\n    enabled: true\n')

            result = self.run_preflight(home)

        self.assertEqual(result.returncode, 0)
        self.assertIn('profile default API port 8642', result.stdout)
        self.assertIn('profile coach API port 8643', result.stdout)


if __name__ == '__main__':
    unittest.main()
