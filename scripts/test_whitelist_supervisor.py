"""Execute source supervisor functions/footer with disposable local children.

This is a /bin/sh component regression, not deployed-image integration.
Policy generation remains covered by test_whitelist_domain.
"""
import contextlib
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = (
    (ROOT / "infra/outbound-proxy/entrypoint.sh", "proxy_pid", "reload_whitelist"),
    (ROOT / "infra/dns/entrypoint.sh", "dns_pid", "reload_servers"),
)


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for fixture event")


@contextlib.contextmanager
def supervisor(entrypoint, pid_var, reload_name, *, scheduled=False, fail=False):
    source = entrypoint.read_text()
    # Execute verbatim runtime functions/traps and final wait/reap block, replacing
    # only policy I/O and daemon startup with a controlled local child.
    functions = source[source.index("policy_freshness_lost() {"):source.index("\nvalidate_refresh_config\n")]
    footer = source[source.rindex("\nset +e\n"):]
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        child_code = """
import pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
def stop(signum, frame):
    (root / 'terminated').touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGUSR2, lambda *_: sys.exit(23))
signal.signal(signal.SIGWINCH, lambda *_: sys.exit(0))
(root / 'child-ready').touch()
while True:
    time.sleep(60)
"""
        script = f"""#!/bin/sh
set -eu
cd {shlex.quote(str(directory))}
supervisor_pid=$$
{pid_var}=""
refresh_pid=""
refresh_performed=0
reload_active=0
reload_pending=0
REFRESH_TIME=02:00
REFRESH_TZ=UTC
{functions}
{reload_name}() {{
  read writer_pid rest < /proc/self/stat
  echo "$writer_pid" >> reloads
  {'return 1' if fail else 'return 0'}
}}
current_refresh_time() {{ echo 02:00; }}
{shlex.quote(sys.executable)} -c {shlex.quote(child_code)} {shlex.quote(str(directory))} &
{pid_var}=$!
echo "${{{pid_var}}}" > child-pid
"""
        if scheduled:
            script += f"""
slept=0
sleep() {{
  if [ "$slept" -eq 0 ]; then slept=1; return 0; fi
  /bin/sleep 30
}}
refresh_loop "${{{pid_var}}}" &
refresh_pid=$!
"""
        script += "touch wrapper-ready\n" + footer
        with (directory / "stderr").open("w+") as stderr:
            process = subprocess.Popen(
                ["/bin/sh", "-c", script], stdout=subprocess.DEVNULL,
                stderr=stderr, start_new_session=True,
            )
            try:
                eventually(lambda: (directory / "child-ready").exists())
                eventually(lambda: (directory / "wrapper-ready").exists())
                child_pid = int((directory / "child-pid").read_text())
                yield process, child_pid, directory
            finally:
                # Includes orphaned children when reproducing the old broken wait.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)


class WhitelistSupervisorTests(unittest.TestCase):
    def test_usr1_keeps_wrapper_and_same_child_alive(self):
        for entrypoint, pid_var, reload_name in ENTRYPOINTS:
            with self.subTest(service=pid_var), supervisor(entrypoint, pid_var, reload_name) as (wrapper, child, directory):
                for count in (1, 2):
                    os.kill(wrapper.pid, signal.SIGUSR1)
                    eventually(lambda: (directory / "reloads").exists() and len((directory / "reloads").read_text().splitlines()) == count)
                    # Give the interrupted wait its opportunity to incorrectly exit.
                    time.sleep(0.05)
                    self.assertIsNone(wrapper.poll(), (directory / "stderr").read_text())
                    os.kill(child, 0)
                    self.assertEqual(int((directory / "child-pid").read_text()), child)
                os.kill(child, signal.SIGUSR2)
                self.assertEqual(wrapper.wait(timeout=3), 23)

    def test_child_exit_and_term_shutdown(self):
        for entrypoint, pid_var, reload_name in ENTRYPOINTS:
            for exit_signal, expected in ((signal.SIGWINCH, 0), (signal.SIGUSR2, 23), (signal.SIGTERM, 143)):
                with self.subTest(service=pid_var, exit_signal=exit_signal), supervisor(entrypoint, pid_var, reload_name) as (wrapper, child, directory):
                    terminate_wrapper = exit_signal == signal.SIGTERM
                    os.kill(wrapper.pid if terminate_wrapper else child, exit_signal)
                    self.assertEqual(wrapper.wait(timeout=3), expected)
                    if terminate_wrapper:
                        self.assertTrue((directory / "terminated").exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child, 0)

    def test_reload_failure_uses_policy_freshness_shutdown(self):
        for entrypoint, pid_var, reload_name in ENTRYPOINTS:
            with self.subTest(service=pid_var), supervisor(entrypoint, pid_var, reload_name, fail=True) as (wrapper, child, directory):
                os.kill(wrapper.pid, signal.SIGUSR1)
                self.assertEqual(wrapper.wait(timeout=3), 143)
                self.assertIn("Policy freshness lost", (directory / "stderr").read_text())
                self.assertTrue((directory / "terminated").exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)

    def test_scheduled_refresh_runs_reload_in_supervisor_not_timer(self):
        for entrypoint, pid_var, reload_name in ENTRYPOINTS:
            with self.subTest(service=pid_var), supervisor(entrypoint, pid_var, reload_name, scheduled=True) as (wrapper, child, directory):
                eventually(lambda: (directory / "reloads").exists())
                self.assertEqual((directory / "reloads").read_text().splitlines(), [str(wrapper.pid)])
                self.assertIsNone(wrapper.poll())
                os.kill(child, 0)


if __name__ == "__main__":
    unittest.main()
