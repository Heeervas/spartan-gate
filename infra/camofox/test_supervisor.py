import contextlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import signal
import stat
import sys
import tempfile
import threading
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("supervisor.py")


def load_supervisor():
    spec = importlib.util.spec_from_file_location("spartan_camofox_supervisor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self, wall=1_700_000_000.0):
        self.wall = wall
        self.steady = 0.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.steady

    def sleep(self, seconds):
        self.wall += seconds
        self.steady += seconds


class SupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.supervisor = load_supervisor()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_proc_stat(self, proc_root, pid, *, ppid=1, pgrp=None, state="S", start_time=99):
        pgrp = pid if pgrp is None else pgrp
        process = proc_root / str(pid)
        process.mkdir(parents=True, exist_ok=True)
        fields = [
            state,
            str(ppid),
            str(pgrp),
            str(pgrp),
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            str(start_time),
            "0",
            "0",
        ]
        (process / "stat").write_text(f"{pid} (fake worker) {' '.join(fields)}\n")

    def test_backoff_sequence_and_stable_reset(self):
        ledger = self.supervisor.Ledger.clean()
        delays = []
        for now in range(5):
            delays.append(ledger.record_recycle(now, (0, 30, 120, 600, 1800)))
        self.assertEqual(delays, [0, 30, 120, 600, 1800])
        self.assertEqual(ledger.record_recycle(5, (0, 30, 120, 600, 1800)), 1800)

        ledger.mark_healthy(10, stable_reset_seconds=1800)
        self.assertFalse(ledger.mark_healthy(1809, stable_reset_seconds=1800))
        self.assertTrue(ledger.mark_healthy(1810, stable_reset_seconds=1800))
        self.assertEqual(ledger.recycle_count, 0)
        self.assertEqual(ledger.next_allowed_at, 0)

    def test_corrupt_ledger_fails_closed_at_max_backoff_and_is_normalized(self):
        path = self.root / "ledger" / "ledger.json"
        path.parent.mkdir()
        path.write_text('{"recycle_count": 999999999999999999999999999999}')
        clock = FakeClock()

        ledger = self.supervisor.load_ledger(
            path,
            now=clock.time(),
            backoffs=(0, 30, 120, 600, 1800),
        )

        self.assertEqual(ledger.recycle_count, 4)
        self.assertEqual(ledger.next_allowed_at, clock.time() + 1800)
        normalized = json.loads(path.read_text())
        self.assertEqual(normalized["recycle_count"], 4)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(
            normalized["supervisor_revision"],
            self.supervisor.SUPERVISOR_REVISION,
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_new_supervisor_revision_gets_one_immediate_attempt(self):
        path = self.root / "ledger.json"
        old_revision = "a" * 64
        new_revision = "b" * 64
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "recycle_count": 4,
                    "next_allowed_at": 2500,
                    "healthy_since": None,
                    "supervisor_revision": old_revision,
                }
            )
        )

        ledger = self.supervisor.load_ledger(
            path,
            now=1000,
            backoffs=(0, 30, 120, 600, 1800),
            revision=new_revision,
        )

        self.assertEqual(ledger.recycle_count, 4)
        self.assertEqual(ledger.next_allowed_at, 0)
        self.assertEqual(ledger.supervisor_revision, new_revision)
        normalized = json.loads(path.read_text())
        self.assertEqual(normalized["supervisor_revision"], new_revision)

        self.assertEqual(ledger.record_recycle(1000, (0, 30, 120, 600, 1800)), 1800)
        self.supervisor.save_ledger(path, ledger)

        preserved = self.supervisor.load_ledger(
            path,
            now=1000,
            backoffs=(0, 30, 120, 600, 1800),
            revision=new_revision,
        )
        self.assertEqual(preserved.supervisor_revision, new_revision)
        self.assertEqual(preserved.recycle_count, 4)
        self.assertEqual(preserved.next_allowed_at, 2800)

    def test_valid_legacy_ledger_migrates_to_immediate_revision_attempt(self):
        path = self.root / "legacy-ledger.json"
        revision = "c" * 64
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "recycle_count": 4,
                    "next_allowed_at": 2500,
                    "healthy_since": None,
                }
            )
        )

        ledger = self.supervisor.load_ledger(
            path,
            now=1000,
            backoffs=(0, 30, 120, 600, 1800),
            revision=revision,
        )

        self.assertEqual(ledger.recycle_count, 4)
        self.assertEqual(ledger.next_allowed_at, 0)
        self.assertEqual(json.loads(path.read_text())["version"], 2)

    def test_invalid_revision_ledger_fails_closed(self):
        path = self.root / "invalid-revision-ledger.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "recycle_count": 0,
                    "next_allowed_at": 0,
                    "healthy_since": None,
                    "supervisor_revision": "invalid",
                }
            )
        )

        ledger = self.supervisor.load_ledger(
            path,
            now=1000,
            backoffs=(0, 30, 120, 600, 1800),
            revision="d" * 64,
        )

        self.assertEqual(ledger.recycle_count, 4)
        self.assertEqual(ledger.next_allowed_at, 2800)

    def test_future_and_negative_ledger_values_fail_closed(self):
        for payload in (
            {"version": 1, "recycle_count": -1, "next_allowed_at": 0, "healthy_since": None},
            {
                "version": 1,
                "recycle_count": 1,
                "next_allowed_at": 9e99,
                "healthy_since": None,
            },
        ):
            with self.subTest(payload=payload):
                path = self.root / f"ledger-{len(list(self.root.iterdir()))}.json"
                path.write_text(json.dumps(payload))
                ledger = self.supervisor.load_ledger(
                    path,
                    now=1000,
                    backoffs=(0, 30, 120, 600, 1800),
                )
                self.assertEqual(ledger.recycle_count, 4)
                self.assertEqual(ledger.next_allowed_at, 2800)

    def test_readiness_is_0600_fresh_and_bound_to_pid_start_time(self):
        proc_root = self.root / "proc"
        self.write_proc_stat(proc_root, 42, start_time=777)
        path = self.root / "run" / "readiness.json"
        self.supervisor.write_readiness(
            path,
            state="ready",
            updated_at=1000,
            child_pid=42,
            child_start_time=777,
            cgroup_status="ok",
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertTrue(
            self.supervisor.check_readiness(path, now=1010, max_age=45, proc_root=proc_root)
        )

        self.assertFalse(
            self.supervisor.check_readiness(path, now=1100, max_age=45, proc_root=proc_root)
        )
        self.write_proc_stat(proc_root, 42, start_time=778)
        self.assertFalse(
            self.supervisor.check_readiness(path, now=1010, max_age=45, proc_root=proc_root)
        )

    def test_readiness_rejects_future_timestamp_and_d_state_child(self):
        proc_root = self.root / "proc"
        self.write_proc_stat(proc_root, 43, state="D", start_time=888)
        path = self.root / "readiness.json"
        self.supervisor.write_readiness(
            path,
            state="ready",
            updated_at=1100,
            child_pid=43,
            child_start_time=888,
            cgroup_status="ok",
        )
        self.assertFalse(
            self.supervisor.check_readiness(path, now=1000, max_age=45, proc_root=proc_root)
        )
        self.supervisor.write_readiness(
            path,
            state="ready",
            updated_at=1000,
            child_pid=43,
            child_start_time=888,
            cgroup_status="ok",
        )
        self.assertFalse(
            self.supervisor.check_readiness(path, now=1001, max_age=45, proc_root=proc_root)
        )

    def test_cgroup_v2_uses_oom_baseline_then_detects_delta_and_memory_hard_limit(self):
        cgroup = self.root / "cgroup"
        cgroup.mkdir()
        (cgroup / "memory.current").write_text("500\n")
        (cgroup / "memory.max").write_text("1000\n")
        (cgroup / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 2\noom_kill 3\n")
        monitor = self.supervisor.CgroupMonitor(cgroup, soft_percent=90, hard_percent=98)

        first = monitor.sample()
        self.assertEqual(first.status, "ok")
        self.assertEqual(first.oom_delta, 0)
        self.assertFalse(first.hard_trigger)

        (cgroup / "memory.events").write_text("oom 3\noom_kill 4\n")
        second = monitor.sample()
        self.assertEqual(second.oom_delta, 1)
        self.assertTrue(second.hard_trigger)

        (cgroup / "memory.current").write_text("990\n")
        third = monitor.sample()
        self.assertEqual(third.percent, 99.0)
        self.assertFalse(third.hard_trigger)
        fourth = monitor.sample()
        self.assertFalse(fourth.hard_trigger)
        fifth = monitor.sample()
        self.assertTrue(fifth.hard_trigger)

        (cgroup / "memory.current").write_text("500\n")
        self.assertFalse(monitor.sample().hard_trigger)

    def test_cgroup_v2_absent_malformed_and_unlimited_are_unknown_not_zero(self):
        missing = self.supervisor.CgroupMonitor(
            self.root / "missing", soft_percent=90, hard_percent=98
        ).sample()
        self.assertEqual(missing.status, "unknown")
        self.assertIsNone(missing.percent)

        cgroup = self.root / "cgroup"
        cgroup.mkdir()
        (cgroup / "memory.current").write_text("not-a-number\n")
        (cgroup / "memory.max").write_text("max\n")
        (cgroup / "memory.events").write_text("oom_kill nope\n")
        malformed = self.supervisor.CgroupMonitor(
            cgroup, soft_percent=90, hard_percent=98
        ).sample()
        self.assertEqual(malformed.status, "unknown")
        self.assertIsNone(malformed.percent)
        self.assertFalse(malformed.hard_trigger)

        (cgroup / "memory.current").write_text("500\n")
        (cgroup / "memory.events").write_text("oom_kill 7\n")
        unlimited_monitor = self.supervisor.CgroupMonitor(
            cgroup, soft_percent=90, hard_percent=98
        )
        unlimited = unlimited_monitor.sample()
        self.assertEqual(unlimited.status, "unknown")
        self.assertIsNone(unlimited.percent)
        self.assertEqual(unlimited.oom_delta, 0)

    def test_internal_client_sends_dedicated_secret_and_global_bearer_over_loopback(self):
        secret_path = self.root / "run" / "internal.key"
        secret = self.supervisor.ensure_internal_secret(secret_path)
        self.assertGreaterEqual(len(secret), 32)
        self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)

        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                seen["path"] = handler.path
                seen["secret"] = handler.headers.get("X-Spartan-Camofox-Internal")
                seen["authorization"] = handler.headers.get("Authorization")
                handler.send_response(200)
                handler.end_headers()

            def log_message(self, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = self.supervisor.InternalClient(
                port=server.server_port,
                secret_file=secret_path,
                health_timeout=1,
                recovery_timeout=1,
                access_key="test-access-key",
            )
            self.assertTrue(client.deep_health())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(seen["path"], "/spartan/health/deep")
        self.assertEqual(seen["secret"], secret)
        self.assertEqual(seen["authorization"], "Bearer test-access-key")

    def test_policy_recovers_on_third_failure_or_immediate_hard_trigger(self):
        policy = self.supervisor.RecoveryPolicy(failure_threshold=3)
        self.assertEqual(policy.observe(healthy=False, hard_trigger=False), "unhealthy")
        self.assertEqual(policy.observe(healthy=False, hard_trigger=False), "unhealthy")
        self.assertEqual(policy.observe(healthy=False, hard_trigger=False), "recover")
        self.assertEqual(policy.failure_count, 3)
        policy.observe(healthy=True, hard_trigger=False)
        self.assertEqual(policy.failure_count, 0)
        self.assertEqual(policy.observe(healthy=True, hard_trigger=True), "recover")

    def test_stop_group_forwards_term_then_kill_and_reports_d_survivor(self):
        sent = []
        clock = FakeClock()
        survivor = self.supervisor.ProcessInfo(71, 1, 70, "D", 123)

        def send_signal(pgid, signum):
            sent.append((pgid, signum))

        def survivors():
            return [survivor]

        remaining = self.supervisor.stop_process_group(
            70,
            term_grace=12,
            kill_grace=1,
            survivor_probe=survivors,
            send_signal=send_signal,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(sent, [(70, signal.SIGTERM), (70, signal.SIGKILL)])
        self.assertEqual(remaining, [survivor])
        self.assertEqual(clock.steady, 13)

    def test_stop_group_does_not_kill_after_term_when_processes_exit(self):
        sent = []
        clock = FakeClock()
        calls = 0

        def survivors():
            nonlocal calls
            calls += 1
            if calls < 3:
                return [self.supervisor.ProcessInfo(72, 1, 72, "S", 124)]
            return []

        remaining = self.supervisor.stop_process_group(
            72,
            term_grace=12,
            kill_grace=1,
            survivor_probe=survivors,
            send_signal=lambda pgid, signum: sent.append((pgid, signum)),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(remaining, [])
        self.assertEqual(sent, [(72, signal.SIGTERM)])

    def test_event_logger_rejects_non_allowlisted_fields_and_never_emits_secret(self):
        stream = io.StringIO()
        logger = self.supervisor.EventLogger(stream=stream, now=lambda: 1000)
        logger.emit("probe_failed", failure_count=2, reason="deep_unhealthy")
        with self.assertRaises(ValueError):
            logger.emit("probe_failed", secret="must-not-appear")
        output = stream.getvalue()
        self.assertIn('"failure_count":2', output)
        self.assertNotIn("must-not-appear", output)
        self.assertNotIn("secret", output)

    def test_config_rejects_invalid_thresholds_and_secret_never_enters_child_argv(self):
        with self.assertRaises(ValueError):
            self.supervisor.Config.from_env(
                {
                    "SPARTAN_CAMOFOX_MEMORY_SOFT_PERCENT": "99",
                    "SPARTAN_CAMOFOX_MEMORY_HARD_PERCENT": "98",
                }
            )

        config = self.supervisor.Config.from_env(
            {
                "MAX_OLD_SPACE_SIZE": "256",
                "SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE": "/run/private/internal.key",
            }
        )
        command = config.child_command()
        self.assertEqual(command, ("node", "--max-old-space-size=256", "/app/server.js"))
        self.assertNotIn("internal.key", " ".join(command))
        self.assertNotIn("CAMOFOX_ACCESS_KEY", " ".join(command))

        with self.assertRaises(ValueError):
            self.supervisor.Config.from_env({"CAMOFOX_ACCESS_KEY": "bad\nheader"})


    def test_dockerfile_asserts_python_and_installs_supervisor_as_cmd(self):
        dockerfile = MODULE_PATH.with_name("Dockerfile").read_text()
        self.assertIn("RUN command -v python3 >/dev/null", dockerfile)
        self.assertIn("COPY spartan-health.mjs /app/spartan-health.mjs", dockerfile)
        self.assertIn("COPY --chmod=0755 supervisor.py /usr/local/bin/spartan-camofox-supervisor.py", dockerfile)
        self.assertIn(
            'CMD ["python3", "/usr/local/bin/spartan-camofox-supervisor.py", "run"]',
            dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
