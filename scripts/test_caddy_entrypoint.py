import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "infra" / "caddy" / "entrypoint.sh"
VALID_A = "a" * 64
VALID_B = "b" * 64


class CaddyEntrypointTests(unittest.TestCase):
    def run_entrypoint(self, **overrides):
        env = os.environ.copy()
        env.update(
            CADDY_BIN="/bin/true",
            CADDY_CAMOFOX_EDGE_TOKEN=VALID_A,
            CADDY_WS_EDGE_TOKEN=VALID_B,
        )
        env.update(overrides)
        return subprocess.run(
            ["/bin/sh", ENTRYPOINT],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_tokens_start_caddy(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_or_empty_tokens_fail_closed(self):
        for key in ("CADDY_CAMOFOX_EDGE_TOKEN", "CADDY_WS_EDGE_TOKEN"):
            with self.subTest(key=key):
                result = self.run_entrypoint(**{key: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

    def test_equal_tokens_fail_closed(self):
        result = self.run_entrypoint(CADDY_CAMOFOX_EDGE_TOKEN=VALID_B)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must differ", result.stderr)

    def test_malformed_tokens_fail_closed(self):
        for value in ("a" * 63, "A" * 64, "g" * 64, "change-me"):
            with self.subTest(value=value):
                result = self.run_entrypoint(CADDY_CAMOFOX_EDGE_TOKEN=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("64 lowercase hexadecimal", result.stderr)


if __name__ == "__main__":
    unittest.main()
