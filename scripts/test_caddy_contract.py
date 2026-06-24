import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CADDY = ROOT / "infra" / "caddy" / "Caddyfile"
HTTPS_EXAMPLE = ROOT / "private.example" / "caddy" / "Caddyfile.https.example"


def block(source: str, address: str) -> str:
    marker = f"{address} {{"
    start = source.index(marker)
    depth = 0
    for pos in range(start, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
    raise AssertionError(f"block not closed: {address}")


class CaddyContractTests(unittest.TestCase):
    def assert_browserless_contract(self, source: str) -> None:
        browserless = block(source, ":3005")
        self.assertIn("BROWSERLESS_EDGE_TOKEN", browserless)
        self.assertIn("sgEdgeToken", browserless)
        self.assertIn("header Connection *Upgrade*", browserless)
        self.assertIn("respond \"Unauthorized\" 401", browserless)
        self.assertIn("basic_auth", browserless)
        self.assertIn('header_up Authorization "Bearer {$BROWSERLESS_TOKEN}"', browserless)
        self.assertNotIn("basic_auth @not_websocket", browserless)

    def test_public_browserless_websocket_uses_edge_token_not_basic_auth_bypass(self):
        self.assert_browserless_contract(PUBLIC_CADDY.read_text(encoding="utf-8"))

    def test_https_example_matches_browserless_edge_contract(self):
        self.assert_browserless_contract(HTTPS_EXAMPLE.read_text(encoding="utf-8"))

    def test_hermes_dashboard_pass_through_is_explicitly_documented(self):
        source = PUBLIC_CADDY.read_text(encoding="utf-8")
        dashboard = block(source, ":9119")

        self.assertIn("Hermes dashboard serves its own /api and /ws same-origin", dashboard)
        self.assertIn("@dashboard_internal path /api/* /ws/*", dashboard)
        self.assertIn("handle @dashboard_internal", dashboard)
        self.assertIn("reverse_proxy http://hermes:9119", dashboard)
        self.assertIn("basic_auth", dashboard)


if __name__ == "__main__":
    unittest.main()
