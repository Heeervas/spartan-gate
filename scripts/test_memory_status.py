import importlib.util
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "memory-status.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("memory_status", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemoryClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def healthy_snapshot(self):
        return {
            "current": {
                "mem_available_bytes": 3 * 1024**3,
                "swap_total_bytes": 2 * 1024**3,
                "swap_used_bytes": 2 * 1024**3 - 1024,
                "memory_psi_full_avg60": 0.0,
            },
            "history": {
                "available": True,
                "sample_count": 12,
                "min_mem_available_bytes": 3 * 1024**3,
                "max_swap_out_bytes_per_second": 0,
                "critical_swap_out_low_memory_samples": 0,
            },
            "contract": {
                "active": True,
                "size_ok": True,
                "mode_ok": True,
                "swappiness_ok": True,
                "sysctl_persistent": True,
                "fstab_persistent": True,
            },
            "containers": [],
            "host_oom_events": 0,
        }

    def test_full_but_idle_swap_is_healthy(self):
        state, reasons = self.tool.classify_snapshot(self.healthy_snapshot())

        self.assertEqual(state, "healthy")
        self.assertEqual(reasons, [])

    def test_low_available_memory_has_warning_and_critical_boundaries(self):
        warning = self.healthy_snapshot()
        warning["history"]["min_mem_available_bytes"] = 1536 * 1024**2 - 1
        state, reasons = self.tool.classify_snapshot(warning)
        self.assertEqual(state, "warning")
        self.assertIn("low_memory_available", reasons)

        critical = self.healthy_snapshot()
        critical["current"]["mem_available_bytes"] = 768 * 1024**2 - 1
        state, reasons = self.tool.classify_snapshot(critical)
        self.assertEqual(state, "critical")
        self.assertIn("critical_memory_available", reasons)

    def test_swap_churn_warns_without_treating_occupancy_as_pressure(self):
        snapshot = self.healthy_snapshot()
        snapshot["history"]["max_swap_out_bytes_per_second"] = 1024**2

        state, reasons = self.tool.classify_snapshot(snapshot)

        self.assertEqual(state, "warning")
        self.assertIn("sustained_swap_out", reasons)

    def test_correlated_swap_churn_and_low_memory_is_critical(self):
        snapshot = self.healthy_snapshot()
        snapshot["history"]["critical_swap_out_low_memory_samples"] = 2

        state, reasons = self.tool.classify_snapshot(snapshot)

        self.assertEqual(state, "critical")
        self.assertIn("critical_swap_pressure", reasons)

    def test_psi_oom_health_and_container_ratio_are_classified(self):
        psi = self.healthy_snapshot()
        psi["current"]["memory_psi_full_avg60"] = 5.0
        self.assertEqual(self.tool.classify_snapshot(psi)[0], "critical")

        oom = self.healthy_snapshot()
        oom["host_oom_events"] = 1
        self.assertEqual(self.tool.classify_snapshot(oom)[0], "critical")

        warning = self.healthy_snapshot()
        warning["containers"] = [
            {
                "service": "hermes",
                "running": True,
                "health": "healthy",
                "memory_ratio": 0.8,
                "oom_killed": False,
                "oom_delta": 0,
                "restart_delta": 0,
            }
        ]
        self.assertEqual(self.tool.classify_snapshot(warning)[0], "warning")

        critical = self.healthy_snapshot()
        critical["containers"] = [
            {
                "service": "camofox",
                "running": True,
                "health": "unhealthy",
                "memory_ratio": 0.9,
                "oom_killed": False,
                "oom_delta": 0,
                "restart_delta": 0,
            }
        ]
        state, reasons = self.tool.classify_snapshot(critical)
        self.assertEqual(state, "critical")
        self.assertIn("container_unhealthy", reasons)
        self.assertIn("container_memory_critical", reasons)

    def test_contract_drift_and_missing_history_warn(self):
        snapshot = self.healthy_snapshot()
        snapshot["contract"]["swappiness_ok"] = False
        snapshot["history"]["available"] = False

        state, reasons = self.tool.classify_snapshot(snapshot)

        self.assertEqual(state, "warning")
        self.assertIn("memory_contract_drift", reasons)
        self.assertIn("history_unavailable", reasons)


class MemoryStatusFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def payload(self, **overrides):
        payload = {
            "version": 1,
            "observed_at": int(time.time()),
            "window_seconds": 86400,
            "state": "healthy",
            "reasons": [],
            "current": {
                "mem_available_bytes": 3 * 1024**3,
                "swap_total_bytes": 2 * 1024**3,
                "swap_used_bytes": 1024**3,
                "memory_psi_full_avg60": 0.0,
            },
            "history": {
                "available": True,
                "sample_count": 12,
                "avg_host_cpu_percent": 20.0,
                "peak_host_cpu_percent": 70.0,
                "avg_load1": 1.0,
                "peak_load1": 2.0,
                "min_mem_available_bytes": 2 * 1024**3,
                "avg_swap_used_percent": 50.0,
                "peak_swap_used_percent": 80.0,
                "max_swap_in_bytes_per_second": 1024,
                "max_swap_out_bytes_per_second": 2048,
                "peak_iowait_percent": 2.0,
                "critical_swap_out_low_memory_samples": 0,
            },
            "contract": {
                "active": True,
                "size_ok": True,
                "mode_ok": True,
                "swappiness_ok": True,
                "sysctl_persistent": True,
                "fstab_persistent": True,
            },
            "containers": [],
            "host_oom_events": 0,
        }
        payload.update(overrides)
        return payload

    def test_atomic_status_write_is_bounded_regular_and_world_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.json"

            self.tool.atomic_write_json(path, self.payload(), mode=0o644)

            metadata = path.stat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o644)
            self.assertLessEqual(metadata.st_size, self.tool.MAX_STATUS_BYTES)
            self.assertEqual(self.tool.load_status(path)["state"], "healthy")

    def test_invalid_oversized_future_and_stale_status_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(self.tool.StatusError):
                self.tool.load_status(path)

            path.write_text("x" * (self.tool.MAX_STATUS_BYTES + 1), encoding="utf-8")
            with self.assertRaises(self.tool.StatusError):
                self.tool.load_status(path)

            future = self.payload(observed_at=int(time.time()) + 61)
            self.tool.atomic_write_json(path, future, mode=0o644)
            with self.assertRaises(self.tool.StatusError):
                self.tool.status_age(self.tool.load_status(path), int(time.time()))

            stale = self.payload(observed_at=int(time.time()) - 129601)
            self.tool.atomic_write_json(path, stale, mode=0o644)
            line, healthy = self.tool.format_status(
                self.tool.load_status(path),
                now=int(time.time()),
                max_age=129600,
            )
            self.assertFalse(healthy)
            self.assertIn("STALE", line)
            self.assertIn("last updated 36h ago", line)

    def test_format_status_reports_healthy_and_warning_reasons(self):
        healthy_line, healthy = self.tool.format_status(
            self.payload(),
            now=int(time.time()),
            max_age=129600,
        )
        self.assertTrue(healthy)
        self.assertIn("Memory audit: HEALTHY", healthy_line)
        self.assertIn("Latest sample: 3.0 GiB RAM available", healthy_line)
        self.assertIn("swap using 1.0 GiB of 2.0 GiB", healthy_line)
        self.assertIn("Last 24h: RAM low point 2.0 GiB", healthy_line)
        self.assertIn("24h load: CPU 20.0% avg, 70.0% peak", healthy_line)

        warning = self.payload(
            state="warning",
            reasons=["sustained_swap_out"],
        )
        warning_line, healthy = self.tool.format_status(
            warning,
            now=int(time.time()),
            max_age=129600,
        )
        self.assertFalse(healthy)
        self.assertIn("Memory audit: WARNING", warning_line)
        self.assertIn("Why: swap-out activity occurred in the last 24h", warning_line)
        self.assertNotIn("sustained_swap_out", warning_line)

    def test_every_internal_reason_has_a_readable_label(self):
        self.assertEqual(set(self.tool._REASON_LABELS), self.tool.ALLOWED_REASONS)

    def test_critical_reasons_are_translated_and_collapsed(self):
        now = int(time.time())
        status = self.payload(
            observed_at=now - 125,
            state="critical",
            reasons=[
                "critical_memory_available",
                "critical_swap_pressure",
                "sustained_swap_out",
            ],
        )

        output, healthy = self.tool.format_status(
            status,
            now=now,
            max_age=129600,
        )

        self.assertFalse(healthy)
        self.assertIn("Memory audit: CRITICAL (updated 2m ago)", output)
        self.assertIn(
            "Why: low RAM and heavy swap-out happened together",
            output,
        )
        self.assertNotIn("critical_memory_available", output)
        self.assertNotIn("critical_swap_pressure", output)
        self.assertNotIn("sustained_swap_out", output)


class SysstatSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_summarizes_recent_sadf_samples_and_detects_correlated_pressure(self):
        now = 1_800_000_000
        samples = [
            {
                "epoch": now - 1200,
                "host_cpu_percent": 20.0,
                "iowait_percent": 1.0,
                "load1": 1.0,
                "mem_available_bytes": 2 * 1024**3,
                "swap_used_percent": 95.0,
                "swap_in_bytes_per_second": 0,
                "swap_out_bytes_per_second": 0,
            },
            {
                "epoch": now - 600,
                "host_cpu_percent": 80.0,
                "iowait_percent": 10.0,
                "load1": 3.0,
                "mem_available_bytes": 1024**3,
                "swap_used_percent": 96.0,
                "swap_in_bytes_per_second": 2 * 1024**2,
                "swap_out_bytes_per_second": 5 * 1024**2,
            },
            {
                "epoch": now,
                "host_cpu_percent": 40.0,
                "iowait_percent": 2.0,
                "load1": 2.0,
                "mem_available_bytes": 1024**3,
                "swap_used_percent": 97.0,
                "swap_in_bytes_per_second": 0,
                "swap_out_bytes_per_second": 5 * 1024**2,
            },
        ]

        summary = self.tool.summarize_history(samples)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["peak_host_cpu_percent"], 80.0)
        self.assertEqual(summary["min_mem_available_bytes"], 1024**3)
        self.assertEqual(
            summary["critical_swap_out_low_memory_samples"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
