"""Deletion-only transport tests; native loader acceptance is a separate fixture."""

import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

PATH = Path(__file__).resolve().parent / "spartan-browser-reset" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("reset_under_test", PATH)
RESET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESET)
SECRET = "SYNTHETIC_COOKIE_SECRET"
GOOD = {
    "ok": True,
    "site": "target.example.test",
    "cookies": "deleted",
    "persisted": "saved",
    "reload": "reloaded",
}


class Response:
    def __init__(self, data=GOOD, status=200, raw=None):
        self.status_code = status
        self.payload = json.dumps(data).encode() if raw is None else raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, size):
        for offset in range(0, len(self.payload), size):
            yield self.payload[offset : offset + size]


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            _sessions_lock=threading.Lock(),
            _session_key=lambda value: ("home", value),
            _sessions={
                ("home", "session"): {
                    "user_id": "user",
                    "session_key": "group",
                    "tab_id": "tab/quoted",
                }
            },
            get_camofox_url=lambda: "http://unreachable.invalid",
            _auth_headers=lambda: {"Authorization": "Bearer synthetic"},
        )
        self.own = SimpleNamespace(enabled=True, error=None, module=RESET)
        tools = SimpleNamespace(client=self.client, handle_navigate=lambda *_: None)
        self.official = SimpleNamespace(
            enabled=True,
            error=None,
            module=SimpleNamespace(camofox_tools=tools, camofox_client=self.client),
            tools_registered=["browser_navigate"],
        )
        registry = ModuleType("tools.registry")
        registry.registry = SimpleNamespace(
            get_entry={"browser_navigate": SimpleNamespace(handler=tools.handle_navigate)}.get
        )
        manager = SimpleNamespace(_plugins={RESET.NAME: self.own, "camofox": self.official})
        plugins = ModuleType("hermes_cli.plugins")
        plugins.get_plugin_manager = lambda: manager
        policy = ModuleType("spartan_browser_policy")
        policy.camofox_policy_enabled = lambda: True
        self.addCleanup(patch.stopall)
        patch.dict(
            sys.modules,
            {
                "hermes_cli.plugins": plugins,
                "spartan_browser_policy": policy,
                "tools.registry": registry,
            },
        ).start()
        self.http = patch.object(RESET.requests, "Session").start()
        self.transport = self.http.return_value.__enter__.return_value
        self.transport.request.return_value = Response()
        self.policy = policy

    def invoke(self, args=None, session_id="session"):
        result = json.loads(RESET.handle_reset({} if args is None else args, session_id=session_id))
        self.assertNotIn(SECRET, json.dumps(result))
        return result

    def test_fixed_schema_warns_before_call_and_never_accepts_target_or_cookies(self):
        self.assertEqual(
            RESET.SCHEMA["parameters"],
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        for text in (
            "logs you out",
            "shared parent domain",
            "Cannot read cookies",
            "restart",
            "repeat",
        ):
            self.assertIn(text, RESET.SCHEMA["description"])
        self.assertEqual(self.invoke({"url": SECRET})["code"], "invalid_arguments")
        self.http.assert_not_called()

    def test_real_transport_shape_identity_copy_uuid_no_redirect_proxy_or_retry(self):
        self.assertEqual(self.invoke(), GOOD)
        self.assertEqual(self.invoke(), GOOD)
        calls = self.transport.request.call_args_list
        self.assertEqual(len(calls), 2)
        bodies = []
        for args, kw in calls:
            self.assertEqual(
                args, ("POST", "http://unreachable.invalid/tabs/tab%2Fquoted/spartan/reset-site")
            )
            self.assertFalse(kw["allow_redirects"])
            self.assertTrue(kw["stream"])
            self.assertEqual(kw["timeout"], (5, 145))
            self.assertEqual(kw["headers"]["Authorization"], "Bearer synthetic")
            body = json.loads(kw["data"])
            self.assertEqual(set(body), {"userId", "sessionKey", "operationId"})
            self.assertEqual((body["userId"], body["sessionKey"]), ("user", "group"))
            self.assertEqual(UUID(body["operationId"]).version, 4)
            bodies.append(body)
        self.assertNotEqual(bodies[0]["operationId"], bodies[1]["operationId"])
        self.assertFalse(self.transport.trust_env)

    def test_opt_in_official_live_identity_and_backend_guards_precede_http(self):
        self.assertTrue(RESET._available())
        for loaded in (self.own, self.official):
            loaded.enabled = False
            self.assertEqual(self.invoke()["code"], "plugin_unavailable")
            loaded.enabled = True
        self.policy.camofox_policy_enabled = lambda: False
        self.assertEqual(self.invoke()["code"], "backend_unavailable")
        self.policy.camofox_policy_enabled = lambda: True
        self.client.get_camofox_url = lambda: ""
        self.assertEqual(self.invoke()["code"], "backend_unavailable")
        self.http.assert_not_called()

    def test_old_reset_handler_and_stale_official_binding_fail_closed(self):
        self.own.module = SimpleNamespace(handle_reset=lambda *_: None)
        self.assertEqual(self.invoke()["code"], "plugin_unavailable")
        self.own.module = RESET
        self.official.module.camofox_client = object()
        self.assertEqual(self.invoke()["code"], "binding_unavailable")
        self.http.assert_not_called()

    def test_missing_identity_never_creates_adopts_or_uses_default(self):
        for value in (None, "", " "):
            self.assertEqual(self.invoke(session_id=value)["code"], "session_required")
        self.assertEqual(self.invoke(session_id="unknown")["code"], "tab_required")
        session = self.client._sessions[("home", "session")]
        for key in ("user_id", "session_key", "tab_id"):
            original = session[key]
            session[key] = ""
            self.assertEqual(self.invoke()["code"], "tab_required")
            session[key] = original
        self.http.assert_not_called()

    def test_allowlisted_projection_keeps_only_fixed_residual_notice(self):
        data = {
            **GOOD,
            "ok": False,
            "cookies": "deleted_with_residual",
            "residual_note": RESET.RESIDUAL_NOTE,
            "cookie": SECRET,
            "deleted_count": 100,
        }
        self.transport.request.return_value = Response(data)
        result = self.invoke()
        self.assertEqual(set(result), set(GOOD) | {"residual_note"})
        self.assertEqual(result["residual_note"], RESET.RESIDUAL_NOTE)
        data["residual_note"] = SECRET
        self.transport.request.return_value = Response(data)
        self.assertEqual(self.invoke()["code"], "invalid_response")

    def test_route_replies_keep_server_codes_and_executed_outcomes(self):
        denied = {
            "ok": False,
            "site": None,
            "cookies": "failed",
            "persisted": "not_attempted",
            "reload": "not_attempted",
        }
        partial = {"ok": False, "cookies": "deleted", "persisted": "unknown"}
        cases = [
            (401, {**denied, "code": "unauthorized"}),
            (400, {**denied, "code": "invalid_request"}),
            (413, {**denied, "code": "invalid_request"}),
            (400, {**denied, "code": "invalid_site"}),
            (403, {**denied, "code": "scope_denied"}),
            (409, {**denied, "code": "operator_denied"}),
            (409, {**denied, "code": "trace_denied"}),
            (409, {**denied, "code": "busy"}),
            (503, {**denied, "code": "busy"}),
            (503, {**denied, "code": "persistence_unavailable"}),
            (500, {**denied, "code": "reset_failed"}),
            (200, {**GOOD, **partial, "reload": "not_attempted", "code": "checkpoint_unknown"}),
            (200, {**GOOD, "ok": False, "cookies": "partial", "code": "cookies_failed"}),
            (200, {**GOOD, "ok": False, "reload": "failed", "code": "reload_failed"}),
            (200, {**GOOD, "site": "under_score.example.test"}),
        ]
        for status, data in cases:
            self.transport.request.return_value = Response(data, status=status)
            self.assertEqual(self.invoke(), data)
        for site in ("bad\n" + SECRET, SECRET + "/path", "a" * 254, 7):
            self.transport.request.return_value = Response({**GOOD, "site": site})
            result = self.invoke()
            self.assertEqual(result, {**GOOD, "site": None})
            self.assertNotIn(str(site), json.dumps(result))

    def test_invalid_responses_and_transport_errors_are_bounded_sanitized_single_attempt(self):
        cases = [
            Response(raw=b"not JSON"),
            Response(status=302),
            Response(raw=b"x" * (RESET.MAX_RESPONSE_BYTES + 1)),
            Response({**GOOD, "cookies": SECRET}),
            Response({**GOOD, "code": SECRET}),
            Response(GOOD, status=403),
            Response({**GOOD, "ok": 1}),
            Response([]),
        ]
        for response in cases:
            self.transport.request.reset_mock()
            self.transport.request.return_value = response
            self.assertFalse(self.invoke()["ok"])
            self.transport.request.assert_called_once()
        self.transport.request.side_effect = RuntimeError(SECRET)
        self.assertEqual(self.invoke(), {"ok": False, "code": "transport_failed"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
