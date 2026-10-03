import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HermesRestartAliasTests(unittest.TestCase):
    def test_waits_through_negative_and_zero_pids_until_replacement(self):
        result = subprocess.run(
            ["bash", "-c", 'source scripts/aliases.sh; sg_compose() { printf "%s" "${8}"; }; sg-hermes-restart'],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service" / "gateway-default").mkdir(parents=True)
            mocks = {
                "svstat": '\n'.join([
                    '#!/bin/sh',
                    'if [ "$2" = up ]; then echo true; exit; fi',
                    'n=$(cat "$MOCK_ROOT/count" 2>/dev/null || echo 0)',
                    'echo $((n + 1)) > "$MOCK_ROOT/count"',
                    'case "$n" in 0) echo 527;; 1) echo -1;; 2) echo 0;; *) echo 64120;; esac',
                ]),
                "stat": '#!/bin/sh\necho 1000\n',
                "signal": '#!/bin/sh\nprintf "%s\\n" "$@" > "$MOCK_ROOT/signal-args"\n',
                "sleep": '#!/bin/sh\nexit 0\n',
            }
            for name, script in mocks.items():
                path = root / name
                path.write_text(script)
                path.chmod(0o755)
            script = result.stdout
            for old, new in [
                ("/run/service", str(root / "service")),
                ("/command/s6-svstat", str(root / "svstat")),
                ("/command/s6-applyuidgid", str(root / "signal")),
                ("stat -c", str(root / "stat") + " -c"),
                ("sleep 1", str(root / "sleep") + " 1"),
            ]:
                script = script.replace(old, new)
            result = subprocess.run(
                ["sh", "-c", script, "sg-hermes-restart", "default"],
                env={**os.environ, "MOCK_ROOT": tmp},
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PID 527 -> 64120", result.stdout)
            self.assertNotIn("PID 527 -> -1", result.stdout)
            self.assertEqual((root / "signal-args").read_text().splitlines(),
                             ["-u", "1000", "-g", "1000", "/bin/kill", "-USR1", "527"])


if __name__ == "__main__":
    unittest.main()
