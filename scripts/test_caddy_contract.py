import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CADDY = ROOT / "infra" / "caddy" / "Caddyfile"
HTTPS_EXAMPLE = ROOT / "private.example" / "caddy" / "Caddyfile.https.example"
ADD_PORT = ROOT / "scripts" / "add-port.sh"
HERMES_EXTENSION_PROFILES = {
    "default": 8642,
    "coach": 8643,
    "work": 8644,
    "market": 8645,
    "dreamer": 8646,
}


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
    def assert_hermes_extension_api_contract(self, source: str) -> None:
        shared = block(source, "(hermes_extension_api)")
        edge = block(source, ":8642")

        self.assertIn('header Authorization "Bearer {$HERMES_EXTENSION_EDGE_KEY}"', shared)
        self.assertIn('header_up Authorization "Bearer {$HERMES_API_KEY}"', shared)
        self.assertIn("header_up -X-Hermes-Profile", shared)
        self.assertIn(
            'header_down Access-Control-Allow-Origin "{$HERMES_BROWSER_EXTENSION_ORIGIN}"',
            shared,
        )
        self.assertIn(
            'header_down Access-Control-Expose-Headers "X-Hermes-Session-Id, '
            'X-Hermes-Session-Key"',
            shared,
        )
        self.assertIn('header_down Vary "Origin"', shared)
        self.assertIn("header_down -Access-Control-Allow-Headers", shared)
        self.assertNotIn("header @extension_origin", shared)
        self.assertIn(
            'header Access-Control-Allow-Methods "GET, POST, PATCH, DELETE, OPTIONS"',
            shared,
        )
        self.assertIn(
            "Authorization, Content-Type, Idempotency-Key, X-Hermes-Profile, "
            "X-Hermes-Session-Id, X-Hermes-Session-Key",
            shared,
        )
        self.assertIn(
            'Access-Control-Expose-Headers "X-Hermes-Session-Id, '
            'X-Hermes-Session-Key"',
            shared,
        )
        self.assertIn("respond 403", shared)
        self.assertIn('respond "Unauthorized" 401', shared)
        self.assertIn("reverse_proxy http://hermes:{args[0]}", shared)
        self.assertNotIn("{header.", shared)
        self.assertNotIn("{query.", shared)
        self.assertNotIn("{re.", shared)
        self.assertIn("@ambiguous_agent_path", edge)
        self.assertIn(
            "vars_regexp unsafe_agent_uri {http.request.uri}",
            edge,
        )
        self.assertIn('respond "Invalid Hermes gateway path" 400', edge)
        self.assertLess(
            edge.index("handle @ambiguous_agent_path"),
            edge.index("handle_path /agents/default/*"),
        )


        for profile, port in HERMES_EXTENSION_PROFILES.items():
            with self.subTest(profile=profile):
                self.assertIn(f"handle_path /agents/{profile}/*", edge)
                self.assertIn(f"import hermes_extension_api {port}", edge)

        self.assertIn("@unknown_agent_path path /agents /agents/*", edge)
        self.assertIn("handle @unknown_agent_path", edge)
        self.assertIn('respond "Unknown Hermes gateway profile" 404', edge)
        self.assertIn(
            "import hermes_extension_api {$HERMES_API_PORT:8642}",
            edge,
        )
        self.assertLess(
            edge.index("handle @unknown_agent_path"),
            edge.rindex("\thandle {"),
        )

    def test_https_example_has_fail_closed_multi_profile_extension_edge(self):
        self.assert_hermes_extension_api_contract(
            HTTPS_EXAMPLE.read_text(encoding="utf-8")
        )

    def assert_browserless_contract(self, source: str) -> None:
        browserless = block(source, ":3005")
        self.assertIn("BROWSERLESS_EDGE_TOKEN", browserless)
        self.assertIn("sgEdgeToken", browserless)
        self.assertIn("header Connection *Upgrade*", browserless)
        self.assertIn("respond \"Unauthorized\" 401", browserless)
        self.assertIn("basic_auth", browserless)
        self.assertIn('header_up Authorization "Bearer {$BROWSERLESS_TOKEN}"', browserless)
        self.assertNotIn("basic_auth @not_websocket", browserless)

    def assert_camofox_session_contract(self, source: str, secure: bool) -> None:
        camofox = block(source, ":26080")
        self.assertIn("@camofox_session_authorized", camofox)
        self.assertIn("@camofox_websocket", camofox)
        self.assertIn("{cookie.spartan_edge_camofox}", camofox)
        self.assertIn("{$CADDY_CAMOFOX_EDGE_TOKEN}", camofox)
        self.assertIn("spartan_edge_camofox={$CADDY_CAMOFOX_EDGE_TOKEN}", camofox)
        self.assertIn("spartan_ws_edge=; Path=/; Max-Age=0", camofox)
        self.assertIn("Max-Age=3600; HttpOnly; SameSite=Strict", camofox)
        self.assertIn("@camofox_only_edge_cookies", camofox)
        self.assertIn("spartan_edge_[A-Za-z0-9_]+", camofox)
        self.assertEqual(camofox.count("request_header Cookie"), 4)
        self.assertEqual(camofox.count("request_header -Authorization"), 2)
        self.assertNotIn("header_up Cookie", camofox)
        self.assertIn("respond \"Unauthorized\" 401", camofox)
        self.assertIn("basic_auth", camofox)
        self.assertGreaterEqual(camofox.count('Cache-Control "no-store"'), 2)
        self.assertEqual("; Secure" in camofox, secure)
        self.assertLess(camofox.index("basic_auth"), camofox.rindex("spartan_edge_camofox="))

    def test_public_browserless_websocket_uses_edge_token_not_basic_auth_bypass(self):
        self.assert_browserless_contract(PUBLIC_CADDY.read_text(encoding="utf-8"))

    def test_https_example_matches_browserless_edge_contract(self):
        self.assert_browserless_contract(HTTPS_EXAMPLE.read_text(encoding="utf-8"))

    def test_public_camofox_uses_full_http_and_websocket_session(self):
        self.assert_camofox_session_contract(PUBLIC_CADDY.read_text(encoding="utf-8"), secure=False)

    def test_add_port_generates_safari_compatible_fail_closed_websocket_auth(self):
        source = ADD_PORT.read_text(encoding="utf-8")
        self.assertFalse(any(line.startswith("\\t") for line in source.splitlines()))
        self.assertIn("@spartan_app_session_authorized", source)
        self.assertIn("{cookie.spartan_edge_apps}", source)
        self.assertIn("{\\$CADDY_WS_EDGE_TOKEN}", source)
        self.assertIn("spartan_edge_apps={\\$CADDY_WS_EDGE_TOKEN}", source)
        self.assertIn("Max-Age=3600; HttpOnly; SameSite=Strict", source)
        self.assertIn("spartan_ws_edge=; Path=/; Max-Age=0", source)
        self.assertIn('respond "Unauthorized" 401', source)
        self.assertIn("HttpOnly; SameSite=Strict${cookie_secure}", source)
        self.assertEqual(source.count("request_header Cookie"), 4)
        self.assertEqual(source.count("request_header -Authorization"), 2)
        self.assertIn('cookie_secure="; Secure"', source)
        self.assertIn("uses legacy or incomplete Safari session authentication", source)
        self.assertIn("remove only the %s block", source)
        self.assertLess(source.index("\t\t\tbasic_auth"), source.index("spartan_edge_apps="))

    def test_https_camofox_session_uses_secure_cookie_token(self):
        self.assert_camofox_session_contract(HTTPS_EXAMPLE.read_text(encoding="utf-8"), secure=True)

    def test_edge_cookie_cleanup_covers_legacy_and_new_positions(self):
        leading = re.compile(
            r"^([ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*(;[ \t]*)?)+"
        )
        remaining = re.compile(
            r";[ \t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*"
        )
        cases = {
            "spartan_edge_camofox=x; app=1": "app=1",
            "app=1; spartan_ws_edge=x; other=2": "app=1; other=2",
            "app=1; spartan_edge_apps=x": "app=1",
            "app=1; spartan_edge_camofox=x; spartan_ws_edge=y; other=2": "app=1; other=2",
        }
        for incoming, expected in cases.items():
            with self.subTest(incoming=incoming):
                cleaned = leading.sub("", incoming)
                cleaned = remaining.sub("", cleaned)
                self.assertEqual(cleaned, expected)
        only_edge = re.compile(
            r"^[ \t]*((spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*[; \t]*)+$"
        )
        self.assertRegex("spartan_ws_edge=x; spartan_edge_apps=y", only_edge)
        self.assertNotRegex("app=1; spartan_edge_apps=y", only_edge)

    def test_router_edge_points_to_spartan_route(self):
        for path in (PUBLIC_CADDY, HTTPS_EXAMPLE):
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                router = block(source, ":18790")

                self.assertIn("reverse_proxy http://spartan-route:18790", router)
                self.assertIn('header_up Authorization "Bearer {$CLAWROUTE_TOKEN}"', router)
                self.assertNotIn("reverse_proxy http://clawroute:18790", router)

    def test_hermes_dashboard_pass_through_is_explicitly_documented(self):
        for path in (PUBLIC_CADDY, HTTPS_EXAMPLE):
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                dashboard = block(source, ":9119")

                self.assertIn("Hermes dashboard serves its own application auth, /api, and /ws", dashboard)
                self.assertIn("@dashboard_internal path /login /auth/* /api/* /ws/*", dashboard)
                self.assertIn("handle @dashboard_internal", dashboard)
                self.assertIn("reverse_proxy http://hermes:9119", dashboard)
                self.assertIn("basic_auth", dashboard)
                if path == HTTPS_EXAMPLE:
                    self.assertIn("header_up Host {host}", dashboard)
                    self.assertIn("header_up X-Forwarded-Proto https", dashboard)


if __name__ == "__main__":
    unittest.main()
