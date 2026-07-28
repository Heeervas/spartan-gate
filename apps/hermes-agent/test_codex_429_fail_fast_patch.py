import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PATCH_PATH = ROOT / "patches" / "patch_codex_429_fail_fast.py"
SPEC = importlib.util.spec_from_file_location("patch_codex_429_fail_fast", PATCH_PATH)
PATCH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PATCH)


class Error:
    def __init__(self, status_code=None, body=None):
        self.status_code = status_code
        self.body = body


class Codex429PatchTests(unittest.TestCase):
    def setUp(self):
        self.classifier_source = '''def _extract_status_code(error):
    return error.status_code if isinstance(error.status_code, int) else None

def _extract_error_body(error):
    return error.body if isinstance(error.body, dict) else {}


def _extract_error_code(body: dict) -> str:
    return ""
'''
        self.loop_source = '''from agent.error_classifier import FailoverReason, classify_api_error

def run():
    failed = False

                if (
                    classified.reason == FailoverReason.billing
                ):
                    pass
                agent._recover_with_credential_pool()
                adaptive_rate_limit_backoff()
                agent._try_activate_fallback()
    return finalize_turn(
        _turn_exit_reason=_turn_exit_reason,
    )
'''
        self.finalizer_source = '''def finalize_turn(
    *,
    _turn_exit_reason,
    _pending_verification_response=None,
):
    result = {}
    if agent._tool_guardrail_halt_decision is not None:
        pass
    return result
'''

    def test_exact_signal_only(self):
        namespace = {}
        patched = PATCH.patch_classifier(self.classifier_source)
        exec(compile(patched, "error_classifier.py", "exec"), namespace)
        match = namespace["is_spartan_codex_capacity_error"]
        summary = namespace["spartan_codex_capacity_summary"]

        self.assertTrue(match(Error(429, {"error": {"code": "codex_429"}})))
        self.assertFalse(match(Error(429, {"error": "codex_429"})))
        self.assertFalse(match(Error(429, {"code": "codex_429"})))
        self.assertFalse(match(Error(429, {"error": {"error": {"code": "codex_429"}}})))
        self.assertFalse(match(Error(500, {"error": {"code": "codex_429"}})))
        self.assertFalse(match(Error("429", {"error": {"code": "codex_429"}})))
        self.assertEqual(
            summary(Error(429, {"error": {"code": "codex_429", "resets_in_seconds": 90}}), now=1000),
            "Codex capacity is exhausted. Retry in about 90 seconds.",
        )
        self.assertEqual(
            summary(Error(429, {"error": {"code": "codex_429", "resets_at": 1120}}), now=1000),
            "Codex capacity is exhausted. Retry in about 120 seconds.",
        )
        bounded = summary(
            Error(429, {"error": {"code": "codex_429", "resets_in_seconds": 10**20}})
        )
        self.assertEqual(bounded, "Codex capacity is exhausted. Retry this request later.")
        self.assertIsNone(summary(Error(429, {"error": {"code": "other"}})))

    def test_patch_is_atomic_and_idempotent_on_exact_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for filename, kind in (
                ("error_classifier.py", "classifier"),
                ("conversation_loop.py", "loop"),
                ("turn_finalizer.py", "finalizer"),
            ):
                path = root / filename
                source = {
                    "classifier": self.classifier_source,
                    "loop": self.loop_source,
                    "finalizer": self.finalizer_source,
                }[kind]
                path.write_text(source)
                paths[path] = kind

            self.assertTrue(PATCH.patch_files(paths))
            once = {path: path.read_bytes() for path in paths}
            self.assertFalse(PATCH.patch_files(paths))
            self.assertEqual(once, {path: path.read_bytes() for path in paths})

    def test_terminal_branch_precedes_all_recovery(self):
        patched = PATCH.patch_loop(self.loop_source)
        marker = patched.index(PATCH.MARKER)
        self.assertLess(marker, patched.index("_recover_with_credential_pool", marker))
        self.assertLess(marker, patched.index("adaptive_rate_limit_backoff", marker))
        self.assertLess(marker, patched.index("_try_activate_fallback", marker))
        self.assertIn('_turn_exit_reason = "spartan_codex_429"', patched)
        self.assertIn("failure_reason=_turn_failure_reason", patched)

    def test_anchor_drift_fails_without_writing_any_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            classifier = root / "error_classifier.py"
            loop = root / "conversation_loop.py"
            finalizer = root / "turn_finalizer.py"
            classifier.write_text(self.classifier_source)
            loop.write_text("drifted")
            finalizer.write_text(self.finalizer_source)
            before = classifier.read_bytes()

            with self.assertRaises(ValueError):
                PATCH.patch_files({classifier: "classifier", loop: "loop", finalizer: "finalizer"})
            self.assertEqual(classifier.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
