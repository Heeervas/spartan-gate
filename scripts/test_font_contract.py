import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONEST_RELEASE_URL = "https://github.com/simpals/onest/releases/download/1.000/onest-fonts.zip"
ONEST_SHA256 = "e44491d1f892716036307beddb4cad5adae52aac7fb70f8c274198c8aea3cb15"
ONEST_TTF_FILES = {
    "Onest-Black.ttf",
    "Onest-Bold.ttf",
    "Onest-ExtraBold.ttf",
    "Onest-Light.ttf",
    "Onest-Medium.ttf",
    "Onest-Regular.ttf",
    "Onest-Thin.ttf",
}


class FontImageContractTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def assert_onest_install(self, relative_path):
        source = self.read(relative_path)
        self.assertIn(ONEST_RELEASE_URL, source)
        self.assertIn(ONEST_SHA256, source)
        self.assertIn("/usr/local/share/fonts/onest", source)
        self.assertRegex(source, r"unzip\s+[^\n;]*\s+['\"]onest-fonts/fonts/ttf/\*\.ttf['\"]")
        self.assertEqual(
            set(re.findall(r"Onest-(?:Black|Bold|ExtraBold|Light|Medium|Regular|Thin)\.ttf", source)),
            ONEST_TTF_FILES,
        )
        self.assertRegex(
            source,
            r"find\s+/usr/local/share/fonts/onest\b[^\n;]*-name\s+['\"]?\\?\*\.ttf['\"]?"
            r"[^\n;]*\|\s*wc\s+-l[^\n;]*(?:-eq|=)\s*['\"]?7['\"]?",
        )
        self.assertIn("fc-cache -fv", source)
        self.assertRegex(source, r"fc-list\s*\|\s*grep\s+-i\s+['\"]?Onest['\"]?")

    def test_hermes_installs_complete_onest_release(self):
        self.assert_onest_install("apps/hermes-agent/Dockerfile")

    def test_camofox_installs_complete_onest_release(self):
        self.assert_onest_install("infra/camofox/Dockerfile")


if __name__ == "__main__":
    unittest.main()
