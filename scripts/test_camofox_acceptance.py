import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CamofoxAcceptanceContractTests(unittest.TestCase):
    def test_acceptance_covers_required_lifecycle_and_cleanup(self):
        source = (ROOT / "scripts" / "camofox-acceptance.mjs").read_text()
        self.assertIn("CAMOFOX_ACCEPTANCE_CYCLES || 20", source)
        self.assertIn("CAMOFOX_ACCEPTANCE_CONCURRENCY || 5", source)
        self.assertIn("/snapshot?userId=", source)
        self.assertIn("/evaluate", source)
        self.assertIn("evaluationResult !== true", source)
        self.assertIn("method: 'DELETE'", source)
        self.assertIn("finally", source)
        self.assertIn("waitForBaseline", source)
        self.assertNotIn("/sessions/", source)
        self.assertNotIn("console.log(created", source)
        self.assertIn("endpointLabel(path)", source)
        self.assertIn("'/tabs/:tabId'", source)
        self.assertIn("path.split('?', 1)[0]", source)


if __name__ == "__main__":
    unittest.main()
