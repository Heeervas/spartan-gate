import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "whitelist-domain.sh"
PROXY_ENTRYPOINT = ROOT / "infra" / "outbound-proxy" / "entrypoint.sh"
DNS_ENTRYPOINT = ROOT / "infra" / "dns" / "entrypoint.sh"
NOW = 1_700_000_000


class WhitelistDomainTests(unittest.TestCase):
    def run_helper(self, *args, whitelist=None, now=NOW, extra_env=None):
        env = os.environ.copy()
        env["SPARTAN_WHITELIST_FILE"] = str(whitelist or self.whitelist)
        env["SPARTAN_WHITELIST_NOW"] = str(now)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(SCRIPT), "--no-restart", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def fake_docker_bin(self, *, reload_fail_service=None):
        bin_dir = pathlib.Path(self.tmp.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = pathlib.Path(self.tmp.name) / "docker.log"
        docker = bin_dir / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
            "case \" $* \" in\n"
            "  *' ps --status running -q '*) [ \"$FAKE_DOCKER_NO_CONSUMER\" = 1 ] || printf 'fixture-container\\n'; exit 0 ;;\n"
            "  *' inspect --format '*) printf '%s\\n' \"$FAKE_DOCKER_MOUNTS\"; exit 0 ;;\n"
            "  *' policy-path '*) printf '%s\\n' \"$FAKE_DOCKER_POLICY_TARGET\"; exit 0 ;;\n"
            "  *' ps --services --status running '*) printf 'proxy\\ndns\\n'; exit 0 ;;\n"
            "  *' kill -s USR1 '*) exit 9 ;;\n"
            "  *' exec -T '*)\n"
            "    if [ -n \"$SPARTAN_WHITELIST_TRANSACTION_DIR\" ] && ! flock -n \"$SPARTAN_WHITELIST_TRANSACTION_DIR/whitelist.private.txt.lock\" true; then\n"
            "      echo lock-held >> \"$FAKE_DOCKER_LOG\"; exit 8\n"
            "    fi\n"
            "    service=''\n"
            "    previous=''\n"
            "    for arg in \"$@\"; do\n"
            "      if [ \"$previous\" = '-T' ]; then service=\"$arg\"; break; fi\n"
            "      previous=\"$arg\"\n"
            "    done\n"
            "    if [ -n \"$FAKE_DOCKER_RELOAD_FAIL_SERVICE\" ] && [ \"$service\" = \"$FAKE_DOCKER_RELOAD_FAIL_SERVICE\" ]; then exit 1; fi\n"
            "    exit 0 ;;\n"
            "  *' up -d --no-build --force-recreate --no-deps '*) exit 0 ;;\n"
            "esac\n"
            "exit 0\n"
        )
        docker.chmod(0o755)
        return bin_dir, log, reload_fail_service or ""

    def fake_mv_bin(self, *, fail=False):
        bin_dir = pathlib.Path(self.tmp.name) / "mv-bin"
        bin_dir.mkdir(exist_ok=True)
        log = pathlib.Path(self.tmp.name) / "mv.log"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        fake_mv = bin_dir / "mv"
        script = "#!/bin/sh\nprintf 'mv\\n' >> \"$FAKE_MV_LOG\"\n"
        if fail:
            script += "exit 9\n"
        else:
            script += f'exec "{real_mv}" "$@"\n'
        fake_mv.write_text(script)
        fake_mv.chmod(0o755)
        return bin_dir, log

    def run_helper_with_fake_docker(self, *args, reload_fail_service=None, extra_env=None):
        bin_dir, log, fail_service = self.fake_docker_bin(reload_fail_service=reload_fail_service)
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["FAKE_DOCKER_LOG"] = str(log)
        env["FAKE_DOCKER_RELOAD_FAIL_SERVICE"] = fail_service
        # Model an installed stack without depending on ignored operator env
        # files. An absent env makes the tier lookup fail under shell errexit.
        env_file = pathlib.Path(self.tmp.name) / "stack.env"
        scratch = pathlib.Path(self.tmp.name) / "transactions"
        scratch.mkdir(mode=0o700, exist_ok=True)
        self.whitelist.touch(mode=0o600, exist_ok=True)
        self.whitelist.chmod(0o600)
        env_file.write_text(
            f"SPARTAN_TIER=L4\nSPARTAN_PRIVATE_WHITELIST_PATH={self.whitelist}\n"
            f"SPARTAN_WHITELIST_TRANSACTION_DIR={scratch}\n", encoding="utf-8")
        env["FAKE_DOCKER_POLICY_TARGET"] = "/policy/whitelist.private.txt"
        env["FAKE_DOCKER_MOUNTS"] = json.dumps([
            {"Type": "bind", "Source": str(self.whitelist.parent), "Destination": "/policy", "RW": False}])
        env["SPARTAN_ENV_FILE"] = str(env_file)
        env["SPARTAN_TIER"] = "L4"
        env["SPARTAN_PRIVATE_COMPOSE"] = str(pathlib.Path(self.tmp.name) / "absent.yml")
        env["SPARTAN_WHITELIST_FILE"] = str(self.whitelist)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return result, log.read_text() if log.exists() else ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        policy_dir = pathlib.Path(self.tmp.name) / "policy"
        policy_dir.mkdir(mode=0o700)
        self.whitelist = policy_dir / "whitelist.private.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        return self.whitelist.read_text().splitlines()

    def line_domains(self):
        return [line.split("#", 1)[0].strip() for line in self.lines()]

    def test_adds_permanent_domain(self):
        result = self.run_helper("Example.COM")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Added permanent: example.com", result.stdout)
        self.assertIn("example.com", self.lines())

    def test_busy_lock_fails_without_changing_policy(self):
        original = "existing.example.com\n"
        self.whitelist.write_text(original)
        env = os.environ.copy()
        env["SPARTAN_WHITELIST_FILE"] = str(self.whitelist)
        with pathlib.Path(f"{self.whitelist}.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen(
                ["bash", str(SCRIPT), "--no-reload", "amzn.eu"],
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=35)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Whitelist is busy", stderr)
        self.assertNotIn("Added permanent", stdout)
        self.assertEqual(self.whitelist.read_text(), original)

    def test_reload_does_not_hold_policy_lock(self):
        result, calls = self.run_helper_with_fake_docker("amzn.eu", extra_env={
            "SPARTAN_WHITELIST_TRANSACTION_DIR": str(pathlib.Path(self.tmp.name) / "transactions")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lock-held", calls)
        self.assertIn("Reload signal delivered: proxy", result.stdout)
        self.assertIn("Reload signal delivered: dns", result.stdout)

    def test_adds_temporary_domains_for_supported_units(self):
        cases = [("15m", NOW + 900), ("6h", NOW + 21_600), ("15d", NOW + 1_296_000)]
        for ttl, expected in cases:
            with self.subTest(ttl=ttl):
                self.whitelist.unlink(missing_ok=True)
                result = self.run_helper(ttl, f"{ttl}.example.com")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Added temporary", result.stdout)
                self.assertIn(f"sg-expires-at={expected} sg-ttl={ttl}", self.whitelist.read_text())

    def test_rejects_invalid_duration_like_first_argument(self):
        for ttl in ["0d", "15x", "d15", "1.5h", "-1h", "366d", "999999999999999999d"]:
            with self.subTest(ttl=ttl):
                result = self.run_helper(ttl, "example.com")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid duration", result.stderr)

    def test_supports_comma_separated_domains(self):
        result = self.run_helper("15d", "a.example.com,b.example.com", "https://c.example.com/path")
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.whitelist.read_text()
        self.assertIn("a.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)
        self.assertIn("b.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)
        self.assertIn("c.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)

    def test_comma_separated_batch_rewrites_whitelist_once(self):
        self.whitelist.write_text("# Existing whitelist.\n")
        bin_dir, log = self.fake_mv_bin()

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["FAKE_MV_LOG"] = str(log)
        env["SPARTAN_WHITELIST_FILE"] = str(self.whitelist)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--no-reload",
                "a.example.com,b.example.com,c.example.com",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ["mv"])
        self.assertEqual(
            self.line_domains()[-3:],
            ["a.example.com", "b.example.com", "c.example.com"],
        )

    def test_failed_batch_replacement_does_not_report_success(self):
        original = "# Existing whitelist.\nexisting.example.com\n"
        self.whitelist.write_text(original)
        bin_dir, log = self.fake_mv_bin(fail=True)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["FAKE_MV_LOG"] = str(log)
        env["SPARTAN_WHITELIST_FILE"] = str(self.whitelist)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--no-reload",
                "a.example.com,b.example.com",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(log.read_text().splitlines(), ["mv"])
        self.assertEqual(self.whitelist.read_text(), original)
        self.assertNotIn("Added permanent", result.stdout)

    def test_invalid_policy_limits_fail_before_first_write(self):
        cases = [
            ("SPARTAN_WHITELIST_MAX_BYTES", "0"),
            ("SPARTAN_WHITELIST_MAX_BYTES", "many"),
            ("SPARTAN_WHITELIST_MAX_ENTRIES", "-1"),
            ("SPARTAN_WHITELIST_MAX_ENTRIES", "999999999"),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                result = self.run_helper(
                    "example.com",
                    extra_env={name: value},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)
                self.assertFalse(self.whitelist.exists())
                self.assertFalse(pathlib.Path(f"{self.whitelist}.lock").exists())

    def test_candidate_over_byte_limit_preserves_private_policy(self):
        original = "a.example\n"
        self.whitelist.write_text(original)
        result = self.run_helper(
            "bbbb.example",
            extra_env={"SPARTAN_WHITELIST_MAX_BYTES": "20"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("byte limit", result.stderr)
        self.assertEqual(self.whitelist.read_text(), original)
        self.assertNotIn("Added permanent", result.stdout)

    def test_candidate_over_entry_limit_preserves_private_policy(self):
        original = "a.example\n"
        self.whitelist.write_text(original)
        public = pathlib.Path(self.tmp.name) / "public.txt"
        public.write_text("")
        result = self.run_helper(
            "b.example",
            extra_env={
                "SPARTAN_WHITELIST_MAX_ENTRIES": "1",
                "SPARTAN_WHITELIST_PUBLIC_FILE": str(public),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("entry limit", result.stderr)
        self.assertEqual(self.whitelist.read_text(), original)
        self.assertNotIn("Added permanent", result.stdout)

    def test_updates_temporary_entry(self):
        self.run_helper("15m", "example.com")
        result = self.run_helper("6h", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated temporary: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com # sg-expires-at=1700021600 sg-ttl=6h"), 1)
        self.assertNotIn("sg-ttl=15m", self.whitelist.read_text())

    def test_promotes_temporary_to_permanent(self):
        self.run_helper("15m", "example.com")
        result = self.run_helper("example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Promoted to permanent: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com"), 1)
        self.assertNotIn("sg-expires-at", self.whitelist.read_text())

    def test_permanent_is_not_degraded_to_temporary(self):
        self.run_helper("example.com")
        result = self.run_helper("15m", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already permanent: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com"), 1)
        self.assertNotIn("sg-expires-at", self.whitelist.read_text())

    def test_upsert_collapses_duplicate_domain_lines(self):
        self.whitelist.write_text("example.com # sg-expires-at=1700000900 sg-ttl=15m\nexample.com # sg-expires-at=1700021600 sg-ttl=6h\n")
        result = self.run_helper("15d", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        matching = [
            line
            for domain, line in zip(self.line_domains(), self.lines())
            if domain == "example.com"
        ]
        self.assertEqual(matching, ["example.com # sg-expires-at=1701296000 sg-ttl=15d"])

    def test_rejects_invalid_ipv4_and_ipv6_explicitly(self):
        self.whitelist.write_text("[2001:db8::1]\n")
        result = self.run_helper("1.2.3.999", "[2001:db8::1]", "valid.example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping invalid hostname: 1.2.3.999", result.stderr)
        self.assertIn("IPv6 addresses are not supported", result.stderr)
        self.assertIn("valid.example.com", self.lines())
        self.assertNotIn("1.2.3.999", self.whitelist.read_text())
        self.assertEqual(self.lines().count("[2001:db8::1]"), 1)
    def test_repairs_malformed_expiry_marker(self):
        self.whitelist.write_text("example.com # sg-expires-at=garbage\n")
        result = self.run_helper("15m", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated temporary: example.com", result.stdout)
        self.assertEqual(self.lines(), ["example.com # sg-expires-at=1700000900 sg-ttl=15m"])


    def test_concurrent_updates_do_not_lose_domains(self):
        processes = [
            subprocess.Popen(
                ["bash", str(SCRIPT), "--no-restart", f"host-{index}.example.com"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "SPARTAN_WHITELIST_FILE": str(self.whitelist),
                    "SPARTAN_WHITELIST_NOW": str(NOW),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(40)
        ]
        results = [process.communicate() + (process.returncode,) for process in processes]
        self.assertTrue(all(returncode == 0 for _, _, returncode in results), results)
        content = self.whitelist.read_text().splitlines()
        for index in range(40):
            self.assertIn(f"host-{index}.example.com", content)

    def test_runtime_update_signals_proxy_and_dns_without_recreate(self):
        result, calls = self.run_helper_with_fake_docker(
            "a.example.com,b.example.com,c.example.com"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Hot-reloading running services: proxy dns", result.stdout)
        self.assertIn("exec -T proxy sh -c kill -USR1 1", calls)
        self.assertIn("exec -T dns sh -c kill -USR1 1", calls)
        self.assertEqual(calls.count("exec -T proxy sh -c kill -USR1 1"), 1)
        self.assertEqual(calls.count("exec -T dns sh -c kill -USR1 1"), 1)
        self.assertIn("Reload signal delivered: proxy", result.stdout)
        self.assertIn("Reload signal delivered: dns", result.stdout)
        self.assertNotIn("kill -s USR1", calls)
        self.assertNotIn("--force-recreate", calls)

    def test_runtime_update_failure_does_not_recreate_by_default(self):
        result, calls = self.run_helper_with_fake_docker("example.com", reload_fail_service="proxy")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Reload failed for proxy; existing runtime policy remains active.", result.stderr)
        self.assertIn("exec -T proxy sh -c kill -USR1 1", calls)
        self.assertIn("exec -T dns sh -c kill -USR1 1", calls)
        self.assertNotIn("--force-recreate", calls)

    def test_runtime_update_can_recreate_only_failed_reload_service_when_requested(self):
        result, calls = self.run_helper_with_fake_docker(
            "--recreate-on-reload-failure",
            "example.com",
            reload_fail_service="proxy",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Reload failed for proxy; will recreate it as requested.", result.stderr)
        self.assertIn("Recreating fallback services: proxy", result.stdout)
        self.assertIn("exec -T proxy sh -c kill -USR1 1", calls)
        self.assertIn("exec -T dns sh -c kill -USR1 1", calls)
        self.assertIn("up -d --no-build --force-recreate --no-deps proxy", calls)
        self.assertNotIn("up -d --no-build --force-recreate --no-deps dns", calls)

    def test_runtime_update_can_recreate_failed_reload_service_from_env(self):
        result, calls = self.run_helper_with_fake_docker(
            "example.com",
            reload_fail_service="dns",
            extra_env={"SPARTAN_WHITELIST_RELOAD_FALLBACK": "recreate"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Recreating fallback services: dns", result.stdout)
        self.assertIn("up -d --no-build --force-recreate --no-deps dns", calls)
        self.assertNotIn("up -d --no-build --force-recreate --no-deps proxy", calls)

    def test_no_reload_alias_validates_active_mounts_without_signals(self):
        for flag in ("--no-reload", "--no-restart"):
            with self.subTest(flag=flag):
                (pathlib.Path(self.tmp.name) / "docker.log").unlink(missing_ok=True)
                result, calls = self.run_helper_with_fake_docker(flag, "example.com")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("example.com", self.whitelist.read_text())
                self.assertIn("ps --status running -q proxy", calls)
                self.assertIn("ps --status running -q dns", calls)
                self.assertEqual(calls.count("inspect --format"), 2)
                self.assertNotIn("kill -USR1", calls)
                self.assertIn("scheduled adoption", result.stdout)

    def test_separate_offline_override_still_skips_docker(self):
        offline = pathlib.Path(self.tmp.name) / "offline.txt"
        result, calls = self.run_helper_with_fake_docker("--no-reload", "example.com",
            extra_env={"SPARTAN_WHITELIST_FILE": str(offline)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("example.com", offline.read_text())
        self.assertEqual(self.whitelist.read_text(), "")
        self.assertEqual(calls, "")

    def test_invalid_reload_fallback_env_fails_before_file_write(self):
        result, calls = self.run_helper_with_fake_docker(
            "example.com",
            extra_env={"SPARTAN_WHITELIST_RELOAD_FALLBACK": "restart"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid SPARTAN_WHITELIST_RELOAD_FALLBACK", result.stderr)
        self.assertEqual(self.whitelist.read_text(), "")
        self.assertEqual(calls, "")

    def transaction_env(self):
        scratch = pathlib.Path(self.tmp.name) / "scratch"
        scratch.mkdir(mode=0o700)
        self.whitelist.write_text("baseline.example\n")
        self.whitelist.chmod(0o600)
        env_file = pathlib.Path(self.tmp.name) / "selected.env"
        env_file.write_text(f"SPARTAN_PRIVATE_WHITELIST_PATH={self.whitelist}\n"
                            f"SPARTAN_WHITELIST_TRANSACTION_DIR={scratch}\n")
        return scratch, {"SPARTAN_ENV_FILE": str(env_file)}

    def test_selected_env_stages_outside_policy_and_process_scratch_wins(self):
        scratch, env = self.transaction_env()
        alternate = pathlib.Path(self.tmp.name) / "alternate"
        alternate.mkdir(mode=0o700)
        env.update({"SPARTAN_WHITELIST_FILE": "",
                    "SPARTAN_WHITELIST_TRANSACTION_DIR": str(alternate)})
        bin_dir, candidate_log = self.fake_mv_bin()
        fake_mv = bin_dir / "mv"
        fake_mv.write_text(fake_mv.read_text().replace("#!/bin/sh\n", "#!/bin/sh\n"
            'test "$(stat -c %a "$1")" = 600 || exit 8\n'
            'printf "%s\\n" "$1" > "$FAKE_CANDIDATE_PATH"\n'))
        python = bin_dir / "python3"
        python.write_text("#!/usr/bin/python3\nimport os, sys\n"
            "if sys.argv[1] == '-c': os.execv('/usr/bin/python3', ['python3', *sys.argv[1:]])\n"
            "original = os.replace\n"
            "def publish(source, target):\n"
            "    assert os.stat(source).st_mode & 0o777 == 0o600\n"
            "    with open(os.environ['FAKE_CANDIDATE_PATH'], 'w') as receipt: receipt.write(source)\n"
            "    original(source, target)\n"
            "os.replace = publish\nsys.argv = sys.argv[1:]\nexec(sys.stdin.read())\n")
        python.chmod(0o755)
        env.update({"PATH": f"{bin_dir}:{self.tmp.name}/bin:{os.environ['PATH']}",
                    "FAKE_MV_LOG": str(candidate_log),
                    "FAKE_CANDIDATE_PATH": str(alternate / "observed")})
        inode = self.whitelist.stat().st_ino
        result, calls = self.run_helper_with_fake_docker("--no-reload", "added.example", extra_env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(inode, self.whitelist.stat().st_ino)
        self.assertEqual(self.whitelist.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(scratch.iterdir()), [])
        candidate = pathlib.Path((alternate / "observed").read_text().strip())
        self.assertEqual(candidate.parent, alternate)
        self.assertTrue(candidate.name.startswith("whitelist.private.txt.tmp."))
        self.assertFalse(candidate.exists())
        self.assertEqual((alternate / "whitelist.private.txt.lock").stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.whitelist.parent.iterdir()), [self.whitelist])
        self.assertIn("scheduled adoption", result.stdout)
        self.assertNotIn("kill -USR1", calls)

    def test_active_no_reload_uses_selected_env_transaction_lock(self):
        scratch, env = self.transaction_env()
        lock_path = scratch / "whitelist.private.txt.lock"
        lock_path.touch(mode=0o600)
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_helper("added.example", extra_env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Whitelist is busy", result.stderr)
        self.assertEqual(self.whitelist.read_text(), "baseline.example\n")

    def test_active_without_scratch_rejects_alias_before_write(self):
        _, env = self.transaction_env()
        env["SPARTAN_WHITELIST_TRANSACTION_DIR"] = ""
        alias = pathlib.Path(self.tmp.name) / "alias.txt"
        alias.symlink_to(self.whitelist)
        result = self.run_helper("added.example", whitelist=alias, extra_env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRANSACTION_DIR", result.stderr)
        self.assertEqual(self.whitelist.read_text(), "baseline.example\n")

    def test_transaction_metadata_fails_before_policy_write(self):
        scratch, env = self.transaction_env()
        for invalid in ("missing", "symlink", "mode", "file-mode"):
            with self.subTest(invalid=invalid):
                selected = scratch
                if invalid == "missing":
                    selected = scratch / "absent"
                elif invalid == "symlink":
                    selected = scratch / "link"
                    selected.symlink_to(scratch, target_is_directory=True)
                elif invalid == "mode":
                    scratch.chmod(0o755)
                else:
                    self.whitelist.chmod(0o644)
                result = self.run_helper("added.example", extra_env={**env,
                    "SPARTAN_WHITELIST_TRANSACTION_DIR": str(selected)})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("transaction", result.stderr.lower())
                self.assertEqual(self.whitelist.read_text(), "baseline.example\n")
                scratch.chmod(0o700)

    def test_live_mount_mismatch_fails_before_write_or_signal(self):
        mounts_cases = ([], [{"Type": "bind", "Source": str(self.whitelist),
                             "Destination": "/policy/whitelist.private.txt", "RW": False}],
                       [{"Type": "bind", "Source": self.tmp.name,
                         "Destination": "/unused", "RW": False}],
                       [{"Type": "bind", "Source": str(self.whitelist.parent),
                         "Destination": "/policy", "RW": False},
                        {"Type": "bind", "Source": str(self.whitelist),
                         "Destination": "/policy/whitelist.private.txt", "RW": False}])
        cases = [{"FAKE_DOCKER_MOUNTS": json.dumps(mounts)} for mounts in mounts_cases]
        cases.append({"FAKE_DOCKER_NO_CONSUMER": "1"})
        for flags in ((), ("--no-reload",)):
            for env in cases:
                with self.subTest(flags=flags, env=env):
                    self.whitelist.write_text("baseline.example\n")
                    result, calls = self.run_helper_with_fake_docker(*flags, "added.example", extra_env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("running" if "FAKE_DOCKER_NO_CONSUMER" in env else "directory bind", result.stderr)
                    self.assertEqual(self.whitelist.read_text(), "baseline.example\n")
                    self.assertNotIn("kill -USR1", calls)
                    self.assertNotIn("--force-recreate", calls)


class WhitelistRuntimeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.public = self.root / "public.txt"
        self.private = self.root / "private.txt"
        self.combined = self.root / "combined.txt"
        self.dns_artifact_dir = self.root / "dns-artifacts"
        self.dns_artifact_dir.mkdir()
        self.dns_domains = self.dns_artifact_dir / "domains.txt"
        self.dns_servers = self.dns_artifact_dir / "servers.conf"
        self.dns_conf = self.dns_artifact_dir / "dnsmasq.conf"
        self.public.write_text("public.example.com\n")
        self.private.write_text(
            "future.example.com # sg-expires-at=1700000060 sg-ttl=1m\n"
            "expired.example.com # sg-expires-at=1699999999 sg-ttl=1m\n"
            "invalid-empty.example.com # sg-expires-at=\n"
            "invalid-text.example.com # sg-expires-at=tomorrow\n"
            "invalid-trailing.example.com # sg-expires-at=1700000060oops\n"
            "permanent.example.com\n"
            "https://port.example.com:443/path\n"
            "1.2.3.999\n"
            "[2001:db8::1]\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_entrypoint_generate(self, entrypoint, extra_env):
        env = os.environ.copy()
        env.update(extra_env)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        env["SPARTAN_WHITELIST_ENTRYPOINT_TEST"] = "generate"
        return subprocess.run(
            ["sh", str(entrypoint)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_entrypoint_mode(self, entrypoint, mode, extra_env=None):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        env["SPARTAN_WHITELIST_ENTRYPOINT_TEST"] = mode
        return subprocess.run(
            ["sh", str(entrypoint)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def runtime_paths(self, entrypoint):
        if entrypoint == PROXY_ENTRYPOINT:
            return {
                "SPARTAN_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_WHITELIST_PRIVATE": str(self.private),
                "SPARTAN_WHITELIST_COMBINED": str(self.combined),
            }
        return {
            "SPARTAN_DNS_WHITELIST_PUBLIC": str(self.public),
            "SPARTAN_DNS_WHITELIST_PRIVATE": str(self.private),
            "SPARTAN_WHITELIST_TEST_ARTIFACT_DIR": str(self.dns_artifact_dir),
        }

    def seed_runtime_artifacts(self, entrypoint):
        if entrypoint == PROXY_ENTRYPOINT:
            self.combined.write_text("old-proxy-policy\n")
            return
        self.dns_domains.write_text("old.example\n")
        self.dns_servers.write_text("server=/old.example/192.0.2.53\n")

    def assert_runtime_artifacts_restored(self, entrypoint):
        if entrypoint == PROXY_ENTRYPOINT:
            self.assertEqual(self.combined.read_text(), "old-proxy-policy\n")
            self.assertFalse(pathlib.Path(f"{self.combined}.rollback").exists())
            return
        self.assertEqual(self.dns_domains.read_text(), "old.example\n")
        self.assertEqual(
            self.dns_servers.read_text(),
            "server=/old.example/192.0.2.53\n",
        )
        self.assertFalse(pathlib.Path(f"{self.dns_domains}.rollback").exists())
        self.assertFalse(pathlib.Path(f"{self.dns_servers}.rollback").exists())

    def failed_mv_bin(self):
        bin_dir = self.root / "failed-mv-bin"
        bin_dir.mkdir(exist_ok=True)
        fake_mv = bin_dir / "mv"
        fake_mv.write_text("#!/bin/sh\nexit 9\n")
        fake_mv.chmod(0o755)
        return bin_dir

    def test_proxy_generation_excludes_expired_domains(self):
        result = self.run_entrypoint_generate(
            PROXY_ENTRYPOINT,
            {
                "SPARTAN_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_WHITELIST_PRIVATE": str(self.private),
                "SPARTAN_WHITELIST_COMBINED": str(self.combined),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(r"public\.example\.com", result.stdout)
        self.assertIn(r"future\.example\.com", result.stdout)
        self.assertIn(r"permanent\.example\.com", result.stdout)
        self.assertIn(r"port\.example\.com", result.stdout)
        self.assertNotIn(r"expired\.example\.com", result.stdout)
        self.assertNotIn("invalid-empty.example.com", result.stdout)
        self.assertNotIn("invalid-text.example.com", result.stdout)
        self.assertNotIn("invalid-trailing.example.com", result.stdout)
        self.assertNotIn("1.2.3.999", result.stdout)
        self.assertNotIn("2001", result.stdout)

    def test_proxy_generation_emits_anchored_escaped_filters(self):
        self.public.write_text("example.com\napi.example.com\n")
        result = self.run_entrypoint_generate(
            PROXY_ENTRYPOINT,
            {
                "SPARTAN_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_WHITELIST_PRIVATE": str(self.private),
                "SPARTAN_WHITELIST_COMBINED": str(self.combined),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        filters = self.combined.read_text().splitlines()
        example_filter = next(line for line in filters if "example\\.com" in line and "api\\." not in line)
        translated = example_filter.replace(r"\(", "(").replace(r"\)", ")").replace(r"\?", "?")
        pattern = re.compile(translated)
        for allowed in ["example.com", "sub.example.com", "https://deep.sub.example.com/path"]:
            self.assertRegex(allowed, pattern)
        for denied in ["notexample.com", "exampleXcom", "example.com.attacker.tld"]:
            self.assertNotRegex(denied, pattern)

    def test_dns_generation_excludes_expired_domains(self):
        result = self.run_entrypoint_generate(
            DNS_ENTRYPOINT,
            {
                "SPARTAN_DNS_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_DNS_WHITELIST_PRIVATE": str(self.private),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        domains = result.stdout.splitlines()
        self.assertIn("public.example.com", domains)
        self.assertIn("future.example.com", domains)
        self.assertIn("permanent.example.com", domains)
        self.assertIn("port.example.com", domains)
        self.assertNotIn("expired.example.com", domains)
        self.assertNotIn("invalid-empty.example.com", result.stdout)
        self.assertNotIn("invalid-text.example.com", result.stdout)
        self.assertNotIn("invalid-trailing.example.com", result.stdout)
        self.assertNotIn("1.2.3.999", result.stdout)
        self.assertNotIn("2001", result.stdout)

    def test_policy_limits_are_validated_before_generation(self):
        cases = [
            ("SPARTAN_WHITELIST_MAX_BYTES", "0"),
            ("SPARTAN_WHITELIST_MAX_BYTES", "many"),
            ("SPARTAN_WHITELIST_MAX_ENTRIES", "-1"),
            ("SPARTAN_WHITELIST_MAX_ENTRIES", "999999999"),
        ]
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            for name, value in cases:
                with self.subTest(entrypoint=entrypoint, name=name, value=value):
                    env = self.runtime_paths(entrypoint)
                    env.update(
                        {
                            name: value,
                            "SPARTAN_WHITELIST_REFRESH_TIME": "02:00",
                            "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                        }
                    )
                    result = self.run_entrypoint_mode(entrypoint, "validate", env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)

    def test_oversized_input_preserves_previous_runtime_artifacts(self):
        self.public.write_text("a" * 33 + "\n")
        self.private.write_text("")
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            with self.subTest(entrypoint=entrypoint):
                self.seed_runtime_artifacts(entrypoint)
                env = self.runtime_paths(entrypoint)
                env.update(
                    {
                        "SPARTAN_WHITELIST_MAX_BYTES": "32",
                        "SPARTAN_WHITELIST_MAX_ENTRIES": "10",
                    }
                )
                result = self.run_entrypoint_generate(entrypoint, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("byte limit", result.stderr)
                self.assert_runtime_artifacts_restored(entrypoint)

    def test_too_many_normalized_entries_preserve_previous_runtime_artifacts(self):
        self.public.write_text("a.example\nb.example\nc.example\n")
        self.private.write_text("")
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            with self.subTest(entrypoint=entrypoint):
                self.seed_runtime_artifacts(entrypoint)
                env = self.runtime_paths(entrypoint)
                env.update(
                    {
                        "SPARTAN_WHITELIST_MAX_BYTES": "1024",
                        "SPARTAN_WHITELIST_MAX_ENTRIES": "2",
                    }
                )
                result = self.run_entrypoint_generate(entrypoint, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("entry limit", result.stderr)
                self.assert_runtime_artifacts_restored(entrypoint)

    def test_dns_second_install_failure_restores_complete_previous_set(self):
        self.public.write_text("new.example\n")
        self.private.write_text("")
        self.seed_runtime_artifacts(DNS_ENTRYPOINT)
        child = subprocess.Popen(["sleep", "30"])
        try:
            env = self.runtime_paths(DNS_ENTRYPOINT)
            env.update(
                {
                    "SPARTAN_WHITELIST_REFRESH_TIME": "02:00",
                    "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                    "SPARTAN_WHITELIST_REFRESH_NOW": "02:00",
                    "SPARTAN_WHITELIST_TEST_CHILD_PID": str(child.pid),
                    "SPARTAN_WHITELIST_TEST_FAIL_INSTALL_AT": "2",
                }
            )
            result = self.run_entrypoint_mode(DNS_ENTRYPOINT, "scheduled-refresh", env)
            self.assertEqual(result.returncode, 143)
            self.assertIn("install failed", result.stderr)
            self.assertIn("Policy freshness lost", result.stderr)
            child.wait(timeout=5)
            self.assert_runtime_artifacts_restored(DNS_ENTRYPOINT)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_refresh_time_is_validated_before_service_start(self):
        invalid_times = ["2:00", "24:00", "00:60", "02:0", "tomorrow"]
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            for refresh_time in invalid_times:
                with self.subTest(entrypoint=entrypoint, refresh_time=refresh_time):
                    result = self.run_entrypoint_mode(
                        entrypoint,
                        "validate",
                        {
                            "SPARTAN_WHITELIST_REFRESH_TIME": refresh_time,
                            "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid SPARTAN_WHITELIST_REFRESH_TIME", result.stderr)

    def test_refresh_time_accepts_boundary_values(self):
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            for refresh_time in ("00:00", "23:59"):
                with self.subTest(entrypoint=entrypoint, refresh_time=refresh_time):
                    result = self.run_entrypoint_mode(
                        entrypoint,
                        "validate",
                        {
                            "SPARTAN_WHITELIST_REFRESH_TIME": refresh_time,
                            "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                        },
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_refresh_timezone_rejects_traversal_and_missing_files(self):
        invalid_timezones = [
            "../UTC",
            "/usr/share/zoneinfo/UTC",
            "Etc/../../etc/passwd",
            "Spartan/DefinitelyMissing",
        ]
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            for timezone in invalid_timezones:
                with self.subTest(entrypoint=entrypoint, timezone=timezone):
                    result = self.run_entrypoint_mode(
                        entrypoint,
                        "validate",
                        {
                            "SPARTAN_WHITELIST_REFRESH_TIME": "02:00",
                            "SPARTAN_WHITELIST_REFRESH_TZ": timezone,
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid SPARTAN_WHITELIST_REFRESH_TZ", result.stderr)

    def test_expired_scheduled_refresh_failure_is_fail_closed(self):
        self.private.write_text(
            "expired.example.com # sg-expires-at=1699999999 sg-ttl=1m\n"
        )
        original = "stale-expired-runtime-policy\n"
        self.combined.write_text(original)
        failed_mv_bin = self.failed_mv_bin()

        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            with self.subTest(entrypoint=entrypoint):
                env = self.runtime_paths(entrypoint)
                env.update(
                    {
                        "PATH": f"{failed_mv_bin}:{os.environ['PATH']}",
                        "SPARTAN_WHITELIST_REFRESH_TIME": "02:00",
                        "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                        "SPARTAN_WHITELIST_REFRESH_NOW": "02:00",
                    }
                )
                result = self.run_entrypoint_mode(entrypoint, "scheduled-refresh", env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Whitelist reload failed", result.stderr)
                self.assertIn("Policy freshness lost", result.stderr)

        self.assertEqual(self.combined.read_text(), original)

    def test_child_signal_failure_terminates_supervisor_and_child(self):
        for entrypoint in (PROXY_ENTRYPOINT, DNS_ENTRYPOINT):
            with self.subTest(entrypoint=entrypoint):
                self.seed_runtime_artifacts(entrypoint)
                child = subprocess.Popen(["sleep", "30"])
                try:
                    env = self.runtime_paths(entrypoint)
                    env.update(
                        {
                            "SPARTAN_WHITELIST_REFRESH_TIME": "02:00",
                            "SPARTAN_WHITELIST_REFRESH_TZ": "UTC",
                            "SPARTAN_WHITELIST_REFRESH_NOW": "02:00",
                            "SPARTAN_WHITELIST_TEST_CHILD_PID": str(child.pid),
                            "SPARTAN_WHITELIST_TEST_SIGNAL_FAILURE": "1",
                        }
                    )
                    result = self.run_entrypoint_mode(entrypoint, "scheduled-refresh", env)
                    self.assertEqual(result.returncode, 143)
                    self.assertIn("reload signaling failed", result.stderr)
                    self.assertIn("Policy freshness lost", result.stderr)
                    child.wait(timeout=5)
                    self.assertIsNotNone(child.returncode)
                    self.assert_runtime_artifacts_restored(entrypoint)
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)

    def test_doctor_validates_effective_refresh_configuration(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text()
        self.assertIn("validate_whitelist_refresh_config private/env/local.env", source)
        self.assertIn("validate_whitelist_refresh_config .env", source)
        self.assertIn('validate_whitelist_refresh_config ""', source)
        self.assertIn("refresh_time=${SPARTAN_WHITELIST_REFRESH_TIME:-}", source)
        self.assertIn("refresh_timezone=${SPARTAN_WHITELIST_REFRESH_TZ:-}", source)

    def test_container_images_install_timezone_data(self):
        proxy_dockerfile = (ROOT / "infra" / "outbound-proxy" / "Dockerfile").read_text()
        dns_dockerfile = (ROOT / "infra" / "dns" / "Dockerfile").read_text()
        self.assertIn("tzdata", proxy_dockerfile)
        self.assertIn("tzdata", dns_dockerfile)


if __name__ == "__main__":
    unittest.main()
