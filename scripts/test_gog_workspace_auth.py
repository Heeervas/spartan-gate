"""Focused Google login journeys; native package smoke tests remain separate."""
import argparse
import contextlib
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("google_auth", ROOT / "scripts/google_auth.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)
CLIENT = {"client_id": "synthetic.apps.googleusercontent.com", "client_secret": "CLIENT_SECRET"}
ACCOUNT = "person@example.com"
AUTH_URL = "https://accounts.google.com/o/oauth2/auth?redirect_uri=http%3A%2F%2Flocalhost%3A1%2F&state=SESSION"
CALLBACK = "http://localhost:1/?code=PRIVATE_CODE&state=SESSION"


class GoogleLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "hermes"
        self.home.mkdir()
        self.state = self.root / "hub"
        self.state.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.client = self.root / "client.json"
        self.client.write_text(json.dumps({"installed": CLIENT}))
        for name in ("gtm", "ga4"):
            (self.state / name).mkdir()
        (self.state / "gtm/client.json").write_text(json.dumps({"installed": CLIENT}))
        (self.state / "ga4/adc.json").write_text(json.dumps({"type": "authorized_user", **CLIENT, "refresh_token": "OLD"}))
        self.settings = {"mcpServers": {
            "gtm": {"enabled": True, "env": {
                "GTM_MCP_CREDENTIALS_FILE": "/state/gtm/client.json", "GTM_MCP_TOKEN_FILE": "/state/gtm/tokens.json"}},
            "analytics": {"enabled": True, "env": {"GOOGLE_APPLICATION_CREDENTIALS": "/state/ga4/adc.json"}},
            "unrelated": {"enabled": True, "command": "unchanged"},
        }}
        (self.state / "mcp_settings.json").write_text(json.dumps(self.settings))
        self.config = {"SPARTAN_GOGCLI_DATA_PATH": str(self.root / "store"),
                       "GOGCLI_ACCOUNT": ACCOUNT, "GOGCLI_CLIENT_SECRET_PATH": str(self.client),
                       "GOG_KEYRING_PASSWORD": "KEYRING_SECRET", "SPARTAN_MCPHUB_STATE_PATH": str(self.state)}
        self.gog = self.root / "gog"
        self.gog.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as log: log.write(json.dumps(args)+'\\n')
if 'login' in args:
 print(os.environ['AUTH_URL'],flush=True)
 callback=sys.stdin.readline()
 if not callback: raise SystemExit(130)
 if not callback.startswith('http://localhost:1/'): raise SystemExit(2)
elif 'export' in args:
 Path(args[args.index('--out')+1]).write_text(json.dumps(json.loads(os.environ['GRANT'])))
""")
        self.gog.chmod(0o755)
        self.grant = {"email": ACCOUNT, "client": "default", "refresh_token": "REFRESH_SECRET",
                      "services": auth.SERVICES.split(","), "scopes": list(dict.fromkeys(auth.EXTRA_SCOPES + auth.WORKSPACE_SCOPES))}
        self.env = {**os.environ, "CALLS": str(self.calls), "AUTH_URL": AUTH_URL, "GRANT": json.dumps(self.grant)}

    def invoke(self, check=False, refresh=None, mobile=False):
        args = argparse.Namespace(check=check, skip_workspace=False)
        def fresh(grant, client):
            grant.update(access_token="ACCESS_SECRET", expiry="2026-11-12T12:00:00Z")
            return grant
        with patch.object(auth, "runtime", return_value=(str(self.gog), self.home, self.env)), \
             patch.object(auth, "refresh_grant", side_effect=refresh or fresh), \
             patch.object(auth, "reload_backends") as reload, \
             patch("builtins.input", side_effect=["", "2" if mobile else "1"]), \
             patch.object(auth.getpass, "getpass", side_effect=[CALLBACK.removeprefix("http://"), CALLBACK] if mobile else [CALLBACK]) as callback_input, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            auth.run(self.config, args)
        self.callback_prompts = callback_input.call_count
        return output.getvalue(), reload

    def test_one_native_login_populates_workspace_gtm_and_adc_without_secret_arguments(self):
        original = (self.state / "mcp_settings.json").read_bytes()
        output, reload = self.invoke()
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        logins = [call for call in calls if "login" in call]
        self.assertEqual(len(logins), 1)
        self.assertEqual(logins[0][logins[0].index("--services") + 1], auth.SERVICES)
        scopes = logins[0][logins[0].index("--extra-scopes") + 1]
        self.assertIn("analytics.edit", scopes)
        self.assertIn("tagmanager.publish", scopes)
        workspace = json.loads((self.home / "google_token.json").read_text())
        adc = json.loads((self.state / "ga4/adc.json").read_text())
        gtm = json.loads((self.state / "gtm/tokens.json").read_text())
        self.assertEqual(workspace["token"], "ACCESS_SECRET")
        self.assertEqual(workspace["refresh_token"], adc["refresh_token"])
        self.assertEqual(gtm["refresh_token"], adc["refresh_token"])
        self.assertIsInstance(gtm["scope"], str)
        self.assertIn(auth.SCOPE + "tagmanager.publish", gtm["scope"].split())
        self.assertEqual(adc["type"], "authorized_user")
        self.assertEqual(adc["client_id"], CLIENT["client_id"])
        self.assertEqual((self.state / "mcp_settings.json").read_bytes(), original)
        self.assertEqual((self.state / "ga4/adc.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.state / "ga4/adc.json.sg-previous").read_text())["refresh_token"], "OLD")
        reload.assert_called_once_with(self.config, self.state, ["gtm", "analytics"])
        for secret in ("PRIVATE_CODE", "REFRESH_SECRET", "CLIENT_SECRET", "KEYRING_SECRET", "ACCESS_SECRET"):
            self.assertNotIn(secret, self.calls.read_text())
            self.assertNotIn(secret, output)

    def test_mobile_selector_accepts_callback_without_scheme_on_first_paste(self):
        output, _ = self.invoke(mobile=True)
        self.assertEqual(self.callback_prompts, 1)
        self.assertIn("Modo móvil", output)
        self.assertTrue((self.home / "google_token.json").exists())

    def test_mobile_shorthand_preserves_host_and_session_validation(self):
        shorthand = CALLBACK.removeprefix("http://")
        for invalid in (shorthand.replace("localhost", "other.example"), shorthand.replace("SESSION", "OTHER"), CALLBACK.replace("http:", "https:")):
            with self.subTest(callback=invalid), self.assertRaises(auth.AuthError):
                auth.validate_callback(invalid, AUTH_URL, mobile=True)
        with self.assertRaises(auth.AuthError):
            auth.validate_callback(shorthand, AUTH_URL)

    def test_native_cached_export_can_be_refreshed_before_publication(self):
        cached = {**self.grant, "access_token": "CACHED_ACCESS", "access_token_expires_at": "2026-11-12T10:00:00Z"}
        fresh = {"token": "FRESH_ACCESS", "expiry": "2026-11-12T12:00:00Z"}
        with patch.object(auth, "command", return_value=b"[runtime audit]\nSG_GOOGLE_AUTH_JSON:" + json.dumps(fresh).encode()) as run:
            result = auth.refresh_grant(cached, CLIENT)
        supplied = json.loads(run.call_args.kwargs["input_data"])
        self.assertNotIn("token", supplied)
        self.assertEqual(supplied["refresh_token"], "REFRESH_SECRET")
        adc = auth.native_credentials(result, CLIENT)
        self.assertEqual(adc["token"], "FRESH_ACCESS")
        self.assertEqual(adc["expiry"], fresh["expiry"])

    def test_check_reuses_grant_without_starting_consent(self):
        self.invoke(check=True)
        calls = self.calls.read_text()
        self.assertNotIn('"login"', calls)
        self.assertTrue((self.home / "google_token.json").exists())

    def test_missing_scope_and_invalid_grant_preserve_existing_credentials(self):
        original = (self.state / "ga4/adc.json").read_bytes()
        self.env["GRANT"] = json.dumps({**self.grant, "scopes": [auth.SCOPE + "analytics.readonly"]})
        with self.assertRaisesRegex(auth.AuthError, "Faltan permisos"):
            self.invoke(check=True)
        self.env["GRANT"] = json.dumps(self.grant)
        def revoked(grant, client):
            raise auth.AuthError("Google/Gog: invalid_grant")
        with self.assertRaisesRegex(auth.AuthError, "invalid_grant"):
            self.invoke(check=True, refresh=revoked)
        self.assertEqual((self.state / "ga4/adc.json").read_bytes(), original)
        self.assertFalse((self.home / "google_token.json").exists())

    def test_callback_can_be_corrected_without_a_second_native_login(self):
        with patch.object(auth.getpass, "getpass", side_effect=[CALLBACK.replace("http:", "https:"), CALLBACK.replace("SESSION", "OTHER"), CALLBACK]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            auth.manual_login([str(self.gog), "login", ACCOUNT], self.env)
        self.assertIn("HTTP/HTTPS", output.getvalue())
        self.assertIn("otra sesión", output.getvalue())
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_cancel_reaps_the_actual_waiting_child(self):
        pid_path = self.root / "pid"
        sleeper = self.root / "waiting"
        sleeper.write_text(f"#!/usr/bin/env python3\nimport os,time\nopen({str(pid_path)!r},'w').write(str(os.getpid()))\nprint({AUTH_URL!r},flush=True)\ntime.sleep(30)\n")
        sleeper.chmod(0o755)
        with patch.object(auth.getpass, "getpass", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                auth.manual_login([str(sleeper)], self.env)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)

    def test_preflight_and_failed_batch_write_preserve_native_files(self):
        first = self.root / "one.json"
        second = self.root / "two.json"
        first.write_text('{"old":true}')
        original_replace = auth.os.replace
        def fail_second(source, destination):
            if destination == second:
                raise OSError("synthetic full disk")
            return original_replace(source, destination)
        with patch.object(auth.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(auth.AuthError, "guardar"):
                auth.publish_files({first: {"new": True}, second: {"new": True}})
        self.assertEqual(first.read_text(), '{"old":true}')
        self.assertFalse(second.exists())
        self.assertFalse(list(self.root.glob(".sg-google-*")))

    def test_native_hub_reload_uses_dashboard_session_header(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, self.headers.get("x-auth-token")))
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                valid = self.path == "/api/auth/login" or self.headers.get("x-auth-token") == "SESSION_TOKEN"
                self.send_response(200 if valid else 401)
                self.end_headers()
                self.wfile.write(json.dumps({"success": valid, "token": "SESSION_TOKEN"}).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        (self.state / "bootstrap-password").write_text("SYNTHETIC_PASSWORD")
        config = {"GOOGLE_AUTH_MCPHUB_URL": f"http://127.0.0.1:{server.server_port}"}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                auth.reload_backends(config, self.state, ["gtm", "analytics"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(calls, [("/api/auth/login", None), ("/api/servers/gtm/reload", "SESSION_TOKEN"),
                                 ("/api/servers/analytics/reload", "SESSION_TOKEN")])

    def test_store_preflight_rejects_a_stale_env_path(self):
        store = self.root / "store"
        info = {"Mounts": [{"Destination": "/opt/data/.config/gogcli", "Source": str(store)}],
                "Config": {"Env": ["XDG_CONFIG_HOME=/opt/data/.config"]}}
        auth.verify_store_mapping(info, store)
        with self.assertRaisesRegex(auth.AuthError, "no coincide"):
            auth.verify_store_mapping(info, self.root / "old-store")

    def test_large_existing_hub_config_is_supported(self):
        self.settings["unrelated_metadata"] = "x" * 400000
        (self.state / "mcp_settings.json").write_text(json.dumps(self.settings))
        self.invoke(check=True)
        self.assertTrue((self.state / "gtm/tokens.json").exists())

    def test_wrapper_help_runs_without_private_configuration(self):
        result = subprocess.run([ROOT / "scripts/gog-workspace-auth.sh", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--skip-workspace", result.stdout)


if __name__ == "__main__":
    unittest.main()
