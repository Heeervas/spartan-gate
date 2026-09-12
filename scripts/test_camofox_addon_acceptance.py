import json
import os
import pathlib
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "camofox-addon-acceptance.mjs"


class FakeCamofox:
    def __init__(self, expected_reachable, cleanup_status=200):
        self.expected_reachable = expected_reachable
        self.cleanup_status = cleanup_status
        self.errors = []
        self.created_user = None
        self.saw_page_world_probe = False
        self.saw_marker_probe = False
        self.cleaned = False
        self.closed_fallback = False


class CamofoxHandler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, _format, *_args):
        pass

    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self):
        if self.headers.get("Authorization") != "Bearer synthetic-test-key":
            raise AssertionError("missing acceptance bearer token")

    def do_POST(self):
        try:
            self._check_auth()
            body = self._json_body()
            path = urlsplit(self.path).path
            if path == "/tabs":
                user_id = body.get("userId")
                if not isinstance(user_id, str) or not user_id.startswith("spartan-addon-accept-"):
                    raise AssertionError("create must use a unique synthetic user")
                if body.get("url") != "https://example.com/" or body.get("nonDestructive") is not True:
                    raise AssertionError("create omitted the inert target or non-destructive flag")
                self.state.created_user = user_id
                self._send(200, {"tabId": "synthetic-tab"})
                return
            if path == "/tabs/synthetic-tab/evaluate":
                if body.get("userId") != self.state.created_user:
                    raise AssertionError("evaluate used a different synthetic user")
                expression = body.get("expression", "")
                if "data-spartan-refresh-addon" in expression:
                    self.state.saw_marker_probe = True
                    self._send(200, {"ok": True, "result": "loaded"})
                    return
                required = [
                    "document.createElement('script')",
                    "script.textContent",
                    "document.documentElement.getAttribute(attribute)",
                    "https://www.google-analytics.com/__spartan_refresh_probe__",
                    "https://www.googletagmanager.com/__spartan_refresh_probe__",
                    "https://www.facebook.com/tr/",
                ]
                if any(marker not in expression for marker in required):
                    raise AssertionError("evaluate did not transport the complete page-world probe")
                if "__spartan_refresh_probe__?" in expression or "facebook.com/tr/?" in expression:
                    raise AssertionError("inert tracker URLs must not contain event query parameters")
                self.state.saw_page_world_probe = True
                self._send(200, {"ok": True, "result": self.state.expected_reachable})
                return
            raise AssertionError(f"unexpected POST {path}")
        except Exception as error:  # pragma: no cover - asserted through client failure
            self.state.errors.append(str(error))
            self._send(500, {"error": "fake API rejected request"})

    def do_DELETE(self):
        try:
            self._check_auth()
            path = unquote(urlsplit(self.path).path)
            if path == "/tabs/synthetic-tab" and self.state.cleanup_status != 200:
                if self._json_body().get("userId") != self.state.created_user:
                    raise AssertionError("fallback close used a different synthetic user")
                self.state.closed_fallback = True
                self._send(200, {"ok": True})
                return
            expected = f"/sessions/{self.state.created_user}/storage_state"
            if path != expected:
                raise AssertionError(f"unexpected DELETE {path}")
            self.state.cleaned = True
            self._send(self.state.cleanup_status, {"ok": self.state.cleanup_status == 200})
        except Exception as error:  # pragma: no cover - asserted through client failure
            self.state.errors.append(str(error))
            self._send(500, {"error": "fake API rejected cleanup"})


class CamofoxAddonAcceptanceTests(unittest.TestCase):
    def run_acceptance(self, mode, reachable, custom_addon=False, expect_success=True, cleanup_status=200):
        state = FakeCamofox(reachable, cleanup_status)
        handler = type("BoundCamofoxHandler", (CamofoxHandler,), {"state": state})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                **os.environ,
                "CAMOFOX_ADDON_ACCEPTANCE_BASE": f"http://127.0.0.1:{server.server_port}",
                "CAMOFOX_ACCESS_KEY": "synthetic-test-key",
                "CAMOFOX_DISABLE_DEFAULT_ADDONS": mode,
                "CAMOFOX_ADDON_ACCEPTANCE_TIMEOUT_MS": "20000",
                "CAMOFOX_ADDONS": "/synthetic/addon" if custom_addon else "",
            }
            result = subprocess.run(
                ["node", str(SOURCE)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertNotIn("synthetic-test-key", result.stdout + result.stderr)
        self.assertTrue(state.saw_page_world_probe)
        self.assertEqual(state.saw_marker_probe, custom_addon)
        self.assertTrue(state.cleaned)
        self.assertEqual(state.errors, [])
        if cleanup_status != 200:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "", "must not report success before cleanup")
            self.assertIn("cleanup failed:", result.stderr)
            self.assertTrue(state.closed_fallback)
            return
        if not expect_success:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("protected mode must block every inert tracker probe", result.stderr)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["mode"], "analytics" if mode in {"true", "1"} else "protected")
        self.assertEqual(output["reachable"], reachable)

    def test_exact_true_and_one_are_analytics_mode(self):
        self.run_acceptance("true", [True, True, True], custom_addon=True)
        self.run_acceptance("1", [True, True, True])

    def test_other_values_remain_protected(self):
        self.run_acceptance("false", [False, False, False])
        self.run_acceptance("TRUE", [False, False, False])

    def test_reachable_tracker_fails_closed_and_still_cleans_up(self):
        self.run_acceptance("false", [True, True, True], expect_success=False)

    def test_cleanup_failure_does_not_report_success(self):
        self.run_acceptance("false", [False, False, False], cleanup_status=503)


if __name__ == "__main__":
    unittest.main()
