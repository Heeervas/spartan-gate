import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GOGCLI_AMD64_SHA256 = "f9f4f407bb762959085b42007c4da538b3dafc5c76b53f3a30dc7ff7c6237fc1"
GOGCLI_ARM64_SHA256 = "3ba0ce1c268bc95cf84c60c622e1e4cbf3bc257ecd6e447bd1cfcdd3aa09430a"
DENO_AMD64_SHA256 = "9b2928c032fd9fa058245b0105ca6382007d8f047ae8c1f4b40ea22106f591ed"
DENO_ARM64_SHA256 = "a63a5a70d44d7a20d1f6f2894dcd14204f6bb350635d038f7c31989730b43b3a"
UBLOCK_ORIGIN_SHA256 = "40c315b0da7871868155ecfae7a50a58dfa0920aebd865e008214986f1b7c578"


class VerifiedImageDownloadTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def block(self, source, start_marker, end_marker):
        start = source.index(start_marker)
        end = source.index(end_marker, start)
        return source[start:end]

    def assert_download_verified_before_extract(self, block, extractor):
        self.assertLess(block.index("curl -fsSL"), block.index("sha256sum -c -"))
        self.assertLess(block.index("sha256sum -c -"), block.index(extractor))

    def test_security_boundary_dockerfiles_reject_mutable_and_streamed_extracts(self):
        dockerfiles = [
            ROOT / "apps" / "hermes-agent" / "Dockerfile",
            ROOT / "packages" / "clawroute" / "Dockerfile",
            *sorted((ROOT / "infra").glob("*/Dockerfile")),
        ]
        for dockerfile in dockerfiles:
            with self.subTest(dockerfile=dockerfile.relative_to(ROOT)):
                source = dockerfile.read_text(encoding="utf-8")
                self.assertNotIn("downloads/latest", source)
                normalized = source.replace(chr(92) + "\n", " ")
                self.assertNotRegex(
                    normalized,
                    r"\b(?:curl|wget)\b[^;\n]*\|\s*(?:tar|unzip)\b",
                )

    def test_gogcli_uses_architecture_specific_official_digests(self):
        source = self.read("apps/hermes-agent/Dockerfile")
        block = self.block(source, "# Install gogcli", "# Install Deno")
        self.assertIn('GOGCLI_VERSION="0.30.0"', block)
        self.assertIn(
            "https://github.com/openclaw/gogcli/releases/download/"
            "v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz",
            block,
        )
        self.assertRegex(
            block,
            rf'amd64\)\s+GOGCLI_SHA256="{GOGCLI_AMD64_SHA256}"\s+;;',
        )
        self.assertRegex(
            block,
            rf'arm64\)\s+GOGCLI_SHA256="{GOGCLI_ARM64_SHA256}"\s+;;',
        )
        self.assertRegex(block, r'curl\b[\s\S]*\s-o\s+"\$\{GOGCLI_ARCHIVE\}"')
        self.assert_download_verified_before_extract(block, "tar xzf")

    def test_deno_uses_architecture_specific_official_digests(self):
        source = self.read("apps/hermes-agent/Dockerfile")
        block = self.block(source, "# Install Deno", "# Spartan Gate keeps")
        self.assertIn('DENO_VERSION="2.2.3"', block)
        self.assertIn(
            "https://github.com/denoland/deno/releases/download/"
            "v${DENO_VERSION}/deno-${DENO_ARCH}.zip",
            block,
        )
        self.assertRegex(
            block,
            rf'amd64\)\s+DENO_ARCH="x86_64-unknown-linux-gnu";\s+'
            rf'DENO_SHA256="{DENO_AMD64_SHA256}"\s+;;',
        )
        self.assertRegex(
            block,
            rf'arm64\)\s+DENO_ARCH="aarch64-unknown-linux-gnu";\s+'
            rf'DENO_SHA256="{DENO_ARM64_SHA256}"\s+;;',
        )
        self.assert_download_verified_before_extract(block, "unzip")

    def test_camofox_pins_and_verifies_exact_ublock_origin_release(self):
        source = self.read("infra/camofox/Dockerfile")
        block = self.block(source, "# Prefetch the exact", "ENV CAMOFOX_CRASH_REPORT_ENABLED")
        self.assertIn('UBLOCK_ORIGIN_VERSION="1.72.2"', block)
        self.assertIn(
            "https://addons.mozilla.org/firefox/downloads/file/4888680/"
            "ublock_origin-${UBLOCK_ORIGIN_VERSION}.xpi",
            block,
        )
        self.assertIn(UBLOCK_ORIGIN_SHA256, block)
        self.assertIn("/root/.cache/camoufox/addons/UBO/manifest.json", block)
        self.assert_download_verified_before_extract(block, "unzip")


if __name__ == "__main__":
    unittest.main()
