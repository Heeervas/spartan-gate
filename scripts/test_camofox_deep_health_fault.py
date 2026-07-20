import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CamofoxDeepHealthFaultContractTests(unittest.TestCase):
    def test_fault_injection_is_bounded_and_requires_automatic_recovery(self):
        source = (ROOT / "scripts" / "camofox-deep-health-fault-test.sh").read_text()
        self.assertIn('kill -STOP "$stopped_pid"', source)
        self.assertIn('kill -CONT "$stopped_pid"', source)
        self.assertIn("unhealthy_timeout", source)
        self.assertIn("recovery_timeout", source)
        self.assertIn('"$state" = unhealthy', source)
        self.assertIn('"$replacement_pid" = "$stopped_pid"', source)
        self.assertIn('"$after_restarts" = "$before_restarts"', source)
        self.assertNotIn("force-recreate", source)
        self.assertNotIn("docker restart", source)


if __name__ == "__main__":
    unittest.main()
