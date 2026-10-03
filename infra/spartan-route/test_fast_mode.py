"""Hermes fast tiers must survive both Codex request paths."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route.codex import build_codex_request_body, build_codex_responses_body


class FastModeTests(unittest.TestCase):
    def test_tiers_survive_both_builders_without_changing_cache_or_reasoning(self):
        for tier in ("priority", "ultrafast", "default", "auto"):
            for builder, history in ((build_codex_request_body, {"messages": []}),
                                     (build_codex_responses_body, {"input": []})):
                with self.subTest(tier=tier, builder=builder.__name__):
                    request = {**history, "service_tier": tier, "reasoning_effort": "max"}
                    body = builder(request, "gpt-6-astra", "stable-cache")
                    self.assertEqual(body["service_tier"], tier)
                    self.assertEqual(body["prompt_cache_key"], "stable-cache")
                    self.assertEqual(body["reasoning"]["effort"], "max")
                    self.assertFalse(body["store"])
                    self.assertEqual(request["service_tier"], tier)

    def test_normal_requests_do_not_acquire_a_tier(self):
        for builder in (build_codex_request_body, build_codex_responses_body):
            self.assertNotIn("service_tier", builder({}, "gpt-6-astra", None))


if __name__ == "__main__":
    unittest.main()
