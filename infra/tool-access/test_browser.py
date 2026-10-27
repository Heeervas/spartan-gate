"""HTTP enforcement tests against recorded loopback fault backends, not a browser."""

import asyncio
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from browser import Browser, Tab, Workflow, register_routes, snapshot_response
from policy import AccessError
from server import boundary, create_app

TOKEN_A = "a" * 64
TOKEN_B = "b" * 64
TOKEN_SEARCH = "c" * 64


def client_binding(name, token, capabilities):
    return {"principal": name, "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
            "browserUser": name + "-scratch", "capabilities": capabilities}


def png(width=1, height=1):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


class BrowserHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.policy_path = Path(self.directory.name) / "policy.json"
        self.bindings = [client_binding("alpha", TOKEN_A, ["browser"]),
                         client_binding("beta", TOKEN_B, ["browser"]),
                         client_binding("search", TOKEN_SEARCH, ["search"])]
        self.write_policy()
        self.records = []
        self.generation = 1
        self.single_attempt = True
        self.tabs = {}
        self.next_tab = 0
        self.fault = None
        self.image = png()
        self.snapshot_text = "fixture"
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        backend = web.Application()
        backend.router.add_route("*", "/{path:.*}", self.backend)
        self.backend_server = TestServer(backend)
        await self.backend_server.start_server()
        self.addAsyncCleanup(self.backend_server.close)
        url = str(self.backend_server.make_url("/")).rstrip("/")
        self.app = create_app({"ALLOWED_HOSTS": "fixture.test", "SEARCH_URL": url,
                               "READER_URL": url, "CAMOFOX_URL": url,
                               "CAMOFOX_ACCESS_KEY": "synthetic-backend-master"},
                              policy_path=self.policy_path, install_browser=register_routes)
        self.http = TestClient(TestServer(self.app))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

    def write_policy(self):
        self.policy_path.write_text(json.dumps({"version": 1, "clients": self.bindings}))
        self.policy_path.chmod(0o600)

    async def backend(self, request):
        body = await request.json() if request.can_read_body else None
        self.records.append((request.method, request.path, dict(request.query), body,
                             request.headers.get("Authorization")))
        if request.path == "/health":
            return web.json_response({"ok": True, "browserGeneration": self.generation})
        if request.path == "/spartan/capabilities":
            return web.json_response({"nonDestructiveTabCreate": True,
                                      "singleAttemptBrowserClick": self.single_attempt})
        fault, self.fault = self.fault, None
        if fault == "disconnect":
            request.transport.close()
            return web.Response()
        if fault == "pause":
            self.entered.set()
            await self.release.wait()
        if fault == "missing":
            return web.json_response({"error": "synthetic-backend-master"}, status=404)
        if fault == "capacity":
            return web.json_response({"error": "capacity"}, status=429)
        denial_codes = {"proxy-denied": "proxy_forbidden", "unclassified-denial": "other"}
        if fault in denial_codes:
            return web.json_response({"code": denial_codes[fault], "error": "synthetic-backend-master"}, status=403)
        if fault == "generation":
            self.generation += 1
        if request.method == "POST" and request.path == "/tabs":
            self.next_tab += 1
            tab_id = "tab-" + str(self.next_tab)
            self.tabs[tab_id] = body
            return web.json_response({"tabId": tab_id, "url": "http://fixture.test/"})
        if request.method == "DELETE":
            self.tabs.pop(request.path.split("/")[-1], None)
            return web.json_response({"ok": True})
        if request.path.endswith("/snapshot"):
            return web.json_response({"url": "http://fixture.test/", "snapshot": self.snapshot_text,
                                      "refsCount": 1, "truncated": False,
                                      "structure": {"private": "omitted"}})
        if request.path.endswith("/screenshot"):
            return web.Response(body=self.image, content_type="image/png")
        return web.json_response({"ok": True, "url": "http://fixture.test/",
                                  "secret": "synthetic-backend-master"})

    async def request(self, method, path, *, workflow=None, token=TOKEN_A, data=None):
        headers = {"Host": "fixture.test", "Authorization": "Bearer " + token}
        if workflow:
            headers["X-Spartan-Workflow"] = workflow
        async with self.http.request(method, path, headers=headers, json=data) as response:
            body = await response.read()
            content = body if response.content_type == "image/png" else json.loads(body)
            self.assertNotIn(b"synthetic-backend-master", body)
            return response.status, content

    async def mint(self, token=TOKEN_A):
        status, body = await self.request("POST", "/v1/workflows", token=token)
        self.assertEqual(status, 200, body)
        self.assertRegex(body["workflowToken"], r"^[0-9a-f]{64}$")
        return body["workflowToken"]

    async def create(self, workflow):
        status, body = await self.request("POST", "/v1/browser/create", workflow=workflow,
                                          data={"url": "http://fixture.test/"})
        self.assertEqual(status, 200, body)
        return body["tabId"]

    async def error(self, method, path, code, **kwargs):
        status, body = await self.request(method, path, **kwargs)
        self.assertGreaterEqual(status, 400)
        self.assertEqual(body, {"error": {"code": code}})

    def mutations(self):
        return [record for record in self.records if record[0] != "GET"]

    async def test_core_lifecycle_flags_filtered_outputs_and_storage_preservation(self):
        workflow = await self.mint()
        tab = await self.create(workflow)
        created = self.mutations()[0]
        self.assertEqual(created[3]["userId"], "alpha-scratch")
        self.assertTrue(created[3]["nonDestructive"])
        self.assertRegex(created[3]["sessionKey"], r"^[0-9a-f]{64}$")
        for action, arguments in (("navigate", {"url": "http://fixture.test/next"}),
                                  ("click", {"ref": "e1"}),
                                  ("type", {"selector": "input", "text": "synthetic"}),
                                  ("scroll", {"direction": "down", "amount": 50})):
            status, result = await self.request("POST", "/v1/browser/" + action,
                                               workflow=workflow, data={"tabId": tab, **arguments})
            self.assertEqual(status, 200, result)
            self.assertTrue(result["ok"])
        click = next(record for record in self.records if record[1].endswith("/click"))
        typed = next(record for record in self.records if record[1].endswith("/type"))
        self.assertIs(click[3]["singleAttempt"], True)
        self.assertEqual({key: typed[3][key] for key in ("mode", "submit", "pressEnter")},
                         {"mode": "fill", "submit": False, "pressEnter": False})
        status, snapshot = await self.request("POST", "/v1/browser/snapshot",
                                              workflow=workflow, data={"tabId": tab})
        self.assertEqual(status, 200)
        self.assertEqual(set(snapshot), {"url", "snapshot", "refsCount", "truncated",
                                         "hasMore", "nextOffset"})
        status, image = await self.request("POST", "/v1/browser/screenshot",
                                           workflow=workflow, data={"tabId": tab})
        self.assertEqual((status, image), (200, self.image))
        shot = next(record for record in self.records if record[1].endswith("/screenshot"))
        self.assertEqual(shot[2], {"userId": "alpha-scratch", "fullPage": "false"})
        status, listing = await self.request("GET", "/v1/browser/tabs", workflow=workflow)
        self.assertEqual((status, listing), (200, {"tabs": [{"tabId": tab}]}))
        status, result = await self.request("DELETE", "/v1/workflows", workflow=workflow)
        self.assertEqual((status, result), (200, {"ok": True}))
        self.assertEqual([r[1] for r in self.records if r[0] == "DELETE"], ["/tabs/" + tab])
        self.assertFalse(any(r[1] == "/tabs" and r[0] == "GET" for r in self.records))
        self.assertTrue(all(r[4] == "Bearer synthetic-backend-master" for r in self.records))

    async def test_cross_workflow_all_operations_and_cross_client_cannot_observe_ids(self):
        owner, other = await self.mint(), await self.mint()
        tab = await self.create(owner)
        status, listing = await self.request("GET", "/v1/browser/tabs", workflow=other)
        self.assertEqual((status, listing), (200, {"tabs": []}))
        before = len(self.records)
        for action, args in (("snapshot", {}), ("navigate", {"url": "http://fixture.test/"}),
                             ("click", {"ref": "e1"}), ("type", {"ref": "e1", "text": "x"}),
                             ("scroll", {}), ("screenshot", {}), ("close", {})):
            for observed in (tab, "guessed-tab"):
                await self.error("POST", "/v1/browser/" + action, "forbidden",
                                 workflow=other, data={"tabId": observed, **args})
        await self.error("POST", "/v1/browser/snapshot", "stale_workflow",
                         workflow=owner, token=TOKEN_B, data={"tabId": tab})
        self.assertEqual(len(self.records), before)

    async def test_capability_revocation_binding_change_and_identity_injection_zero_action(self):
        await self.error("POST", "/v1/workflows", "forbidden", token=TOKEN_SEARCH)
        self.assertEqual(self.records, [])
        workflow = await self.mint()
        before = len(self.records)
        await self.error("POST", "/v1/browser/create", "invalid_request", workflow=workflow,
                         data={"userId": "other", "sessionKey": "other"})
        self.bindings[0]["browserUser"] = "changed-scratch"
        self.write_policy()
        await self.error("GET", "/v1/browser/tabs", "stale_workflow", workflow=workflow)
        self.bindings = self.bindings[1:]
        self.write_policy()
        await self.error("GET", "/v1/browser/tabs", "unauthorized", workflow=workflow)
        self.assertEqual(len(self.records), before)

    async def test_missing_click_capability_prevents_workflow_and_mutations(self):
        self.single_attempt = False
        await self.error("POST", "/v1/workflows", "unsupported_backend")
        self.assertEqual(self.mutations(), [])
        self.single_attempt = True
        workflow = await self.mint()
        tab = await self.create(workflow)
        self.single_attempt = False
        await self.error("POST", "/v1/browser/click", "unsupported_backend",
                         workflow=workflow, data={"tabId": tab, "ref": "e1"})
        self.assertEqual(len(self.mutations()), 1)

    async def test_tab_client_global_capacity_reject_without_eviction(self):
        workflow = await self.mint()
        tabs = [await self.create(workflow) for _ in range(4)]
        before = len(self.records)
        await self.error("POST", "/v1/browser/create", "capacity", workflow=workflow)
        self.assertEqual(len(self.records), before)
        self.assertEqual(set(self.tabs), set(tabs))
        for _ in range(7):
            await self.mint()
        await self.error("POST", "/v1/workflows", "capacity")
        # Distinct configured clients exercise the actual global admission ceiling.
        for index in range(7):
            token = format(index + 10, "064x")
            self.bindings.append(client_binding("extra" + str(index), token, ["browser"]))
            self.write_policy()
            for _ in range(8):
                await self.mint(token)
        await self.error("POST", "/v1/workflows", "capacity", token=TOKEN_B)
        self.assertEqual(len(self.mutations()), 4)

    async def test_generation_change_and_missing_tab_retire_without_replay_cleanup_exact_id(self):
        workflow = await self.mint()
        tab = await self.create(workflow)
        self.fault = "generation"
        await self.error("POST", "/v1/browser/click", "stale_workflow", workflow=workflow,
                         data={"tabId": tab, "ref": "e1"})
        before = len(self.records)
        await self.error("POST", "/v1/browser/click", "stale_workflow", workflow=workflow,
                         data={"tabId": tab, "ref": "e1"})
        self.assertEqual(len(self.records), before)
        self.assertEqual(sum(r[1].endswith("/click") for r in self.records), 1)
        self.assertEqual((await self.request("POST", "/v1/browser/close", workflow=workflow,
                                            data={"tabId": tab}))[0], 200)
        fresh = await self.mint()
        tab = await self.create(fresh)
        self.fault = "missing"
        await self.error("POST", "/v1/browser/snapshot", "stale_tab", workflow=fresh,
                         data={"tabId": tab})
        before = len(self.records)
        await self.error("POST", "/v1/browser/navigate", "stale_tab", workflow=fresh,
                         data={"tabId": tab, "url": "http://fixture.test/"})
        self.assertEqual(len(self.records), before)

    async def test_unknown_create_and_click_outcomes_never_replay_or_promise_undo(self):
        workflow = await self.mint()
        self.fault = "disconnect"
        await self.error("POST", "/v1/browser/create", "remote_outcome_unknown", workflow=workflow)
        before = len(self.records)
        await self.error("POST", "/v1/browser/create", "stale_workflow", workflow=workflow)
        self.assertEqual(len(self.records), before)
        self.assertEqual(sum(r[0] == "POST" and r[1] == "/tabs" for r in self.records), 1)
        fresh = await self.mint()
        tab = await self.create(fresh)
        self.fault = "disconnect"
        await self.error("POST", "/v1/browser/click", "remote_outcome_unknown", workflow=fresh,
                         data={"tabId": tab, "ref": "e1"})
        await self.error("POST", "/v1/browser/click", "stale_tab", workflow=fresh,
                         data={"tabId": tab, "ref": "e1"})
        self.assertEqual(sum(r[1].endswith("/click") for r in self.records), 1)
        self.assertFalse(any(r[0] == "DELETE" for r in self.records))

    async def test_explicit_proxy_denial_is_sanitized_without_replay_or_handle_revival(self):
        for fault, code in (("proxy-denied", "egress_denied"),
                            ("unclassified-denial", "remote_outcome_unknown")):
            workflow = await self.mint()
            self.fault = fault
            await self.error("POST", "/v1/browser/create", code, workflow=workflow,
                             data={"url": "https://www.wikipedia.org"})
            before = len(self.records)
            await self.error("GET", "/v1/browser/tabs", "stale_workflow", workflow=workflow)
            self.assertEqual(len(self.records), before, "retired workflow cannot dispatch or replay")
        self.assertEqual([record[1] for record in self.mutations()], ["/tabs", "/tabs"])

    async def test_actual_transport_timeout_retires_unknown_create_without_cleanup(self):
        workflow = await self.mint()
        self.app["transport"].timeout = 0.03
        self.fault = "pause"
        try:
            await self.error("POST", "/v1/browser/create", "remote_outcome_unknown", workflow=workflow)
        finally:
            self.release.set()
        before = len(self.records)
        await self.error("POST", "/v1/browser/create", "stale_workflow", workflow=workflow)
        self.assertEqual(len(self.records), before)
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.mutations()[0][1], "/tabs")

    async def test_backend_capacity_is_not_unknown_and_text_response_is_byte_bounded(self):
        workflow = await self.mint()
        self.fault = "capacity"
        await self.error("POST", "/v1/browser/create", "capacity", workflow=workflow)
        tab = await self.create(workflow)
        self.snapshot_text = "x" * (128 * 1024)
        await self.error("POST", "/v1/browser/snapshot", "upstream_error", workflow=workflow,
                         data={"tabId": tab})
        self.snapshot_text = "fixture"
        self.assertEqual((await self.request("POST", "/v1/browser/snapshot", workflow=workflow,
                                             data={"tabId": tab}))[0], 200)

    async def test_outer_boundary_timeout_reports_unknown_only_after_mutation_started(self):
        async def expires(request):
            request["browser_mutation_started"] = request.match_info["started"] == "yes"
            raise TimeoutError

        app = web.Application(middlewares=[boundary])
        app["allowed_hosts"] = {"fixture.test"}
        app.router.add_post("/{started}", expires)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        for started, code in (("yes", "remote_outcome_unknown"), ("no", "deadline")):
            async with client.post("/" + started, headers={"Host": "fixture.test"}) as response:
                self.assertEqual(await response.json(), {"error": {"code": code}})
                self.assertEqual(response.status, 502 if started == "yes" else 504)

    async def test_cancelled_dispatched_mutation_retires_tab_and_propagates_cancellation(self):
        browser = Browser(self.app)
        tab = Tab()
        workflow = Workflow("alpha", "synthetic-binding", "alpha-scratch", "synthetic-group", 1,
                            tabs={"tab-cancelled": tab})
        self.fault = "pause"
        pending = asyncio.create_task(browser.execute(
            workflow, "click", {"ref": "e1", "singleAttempt": True}, "tab-cancelled", tab))
        await asyncio.wait_for(self.entered.wait(), 2)
        pending.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            self.release.set()
        self.assertTrue(tab.retired)
        self.assertEqual([record[1] for record in self.mutations()], ["/tabs/tab-cancelled/click"])

    async def test_workflow_busy_immediate_rejection(self):
        workflow = await self.mint()
        tab = await self.create(workflow)
        self.fault = "pause"
        pending = asyncio.create_task(self.request("POST", "/v1/browser/click", workflow=workflow,
                                                   data={"tabId": tab, "ref": "e1"}))
        await asyncio.wait_for(self.entered.wait(), 2)
        try:
            await self.error("POST", "/v1/browser/scroll", "workflow_busy", workflow=workflow,
                             data={"tabId": tab})
        finally:
            self.release.set()
            self.assertEqual((await pending)[0], 200)
        self.assertFalse(any(r[1].endswith("/scroll") for r in self.records))

    async def test_screenshot_signature_dimensions_actual_bytes_and_cleanup_failure(self):
        workflow = await self.mint()
        tab = await self.create(workflow)
        for invalid in (b"not-png", png(4097, 1), png(4096, 4096), png() + b"x" * (2 * 1024 * 1024)):
            self.image = invalid
            await self.error("POST", "/v1/browser/screenshot", "upstream_error", workflow=workflow,
                             data={"tabId": tab})
        self.fault = "disconnect"
        await self.error("DELETE", "/v1/workflows", "remote_outcome_unknown", workflow=workflow)
        self.assertEqual([r[1] for r in self.records if r[0] == "DELETE"], ["/tabs/" + tab])
        status, result = await self.request("DELETE", "/v1/workflows", workflow=workflow)
        self.assertEqual((status, result), (200, {"ok": True}))
        self.assertFalse(any("/sessions" in r[1] for r in self.records))


class SnapshotProtocolTests(unittest.TestCase):
    def test_short_and_empty_native_shapes_omit_pagination(self):
        for text in ("", "short snapshot"):
            data = {"url": "about:blank", "snapshot": text, "refsCount": 0,
                    "truncated": False}
            self.assertEqual(snapshot_response(data),
                             {**data, "hasMore": False, "nextOffset": None})

    def test_pagination_types_and_consistency_fail_closed(self):
        base = {"url": "about:blank", "snapshot": "snapshot", "refsCount": 0,
                "truncated": True, "hasMore": True, "nextOffset": 74800}
        self.assertEqual(snapshot_response(base), base)
        last = {**base, "hasMore": False, "nextOffset": None}
        self.assertEqual(snapshot_response(last), last)
        invalid = [{**base, "hasMore": 1}, {**base, "nextOffset": True},
                   {**base, "nextOffset": "74800"}, {**base, "nextOffset": 0},
                   {**base, "nextOffset": None}, {**last, "nextOffset": 74800},
                   {**base, "truncated": False}, {**last, "truncated": 0},
                   {**last, "refsCount": True}, {**last, "snapshot": None},
                   {key: value for key, value in base.items() if key != "hasMore"},
                   {key: value for key, value in last.items() if key != "nextOffset"}]
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(AccessError) as caught:
                    snapshot_response(data)
                self.assertEqual(caught.exception.code, "upstream_error")


if __name__ == "__main__":
    unittest.main()
