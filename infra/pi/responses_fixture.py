"""Disposable HTTP Route/upstream fixture. Never loads operator configuration."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

from aiohttp import ClientSession, web

sys.path.insert(0, os.environ.get("ROUTE_FIXTURE_SOURCE", str(Path(__file__).resolve().parents[1] / "spartan-route")))
from test_app import test_config
from spartan_route.app import STATE_KEY, create_app
from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot, sse
from spartan_route.telemetry import TelemetryStore


def paired_tool_results(items):
    pending = set()
    for item in items:
        if item.get('type') == 'function_call':
            call_id = item.get('call_id')
            if not call_id or call_id in pending:
                return False
            pending.add(call_id)
        elif item.get('type') == 'function_call_output':
            call_id = item.get('call_id')
            if call_id not in pending:
                return False
            pending.remove(call_id)
    return True


class Fixture:
    def __init__(self):
        self.calls = []
        self.bindings = 0
        self.route_runner = None
        self.disabled = set()
        self.generation = 0
        self.late = False
        self.omit_output = False

    async def upstream(self, request):
        body = await request.json()
        account = request.headers["X-Fixture-Account"]
        self.calls.append({"body": body, "account": account})
        items = body["input"]
        opaque = [i["encrypted_content"] for i in items if i.get("type") == "reasoning"]
        if any(value != "provider-opaque-" + account for value in opaque):
            return web.json_response({"error": {"code": "fixture_wrong_ciphertext"}}, status=400)
        if not paired_tool_results(items):
            return web.json_response({"error": {"code": "fixture_wrong_tool_pair"}}, status=400)
        results = [i for i in items if i.get("type") == "function_call_output"]
        if results and results[-1]["output"] != "42":
            return web.json_response({"error": {"code": "fixture_wrong_tool_result"}}, status=400)
        reasoning = {"id": "rs_1", "type": "reasoning", "summary": [],
                     "encrypted_content": "provider-opaque-" + account}
        call = {"id": "fc_1", "type": "function_call", "call_id": "call_1",
                "name": "answer", "arguments": "{}", "status": "completed"}
        message = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": "answer=" + results[-1]["output"], "annotations": []}]} if results else call
        output = [reasoning, message]
        terminal = {"id": "resp_1", "object": "response", "status": "completed", "output": output,
                    "usage": {"input_tokens": 10, "output_tokens": 4,
                              "output_tokens_details": {"reasoning_tokens": 2}}}
        events = [{"type": "response.created", "response": {"id": "resp_1", "object": "response", "status": "in_progress"}},
                  {"type": "response.output_item.done", "output_index": 0,
                   "item": {k: v for k, v in reasoning.items() if not (self.late and k == "encrypted_content")}},
                  {"type": "response.output_item.added", "output_index": 1, "item": {**message, "arguments": ""}},
                  {"type": "response.output_item.done", "output_index": 1, "item": message},
                  {"type": "response.completed", "response": terminal}]
        if self.omit_output:
            terminal["output"] = []
        return web.Response(body=b"".join(sse(e, e["type"]) for e in events), content_type="text/event-stream")

    async def start_route(self):
        store = TelemetryStore(":memory:")
        app = create_app(test_config(), store)
        app.on_startup.clear()
        fixture = self

        @web.middleware
        async def count_binding(request, handler):
            if request.path == "/api/pi/reasoning-binding":
                fixture.bindings += 1
            return await handler(request)

        app.middlewares.append(count_binding)
        session = ClientSession()

        class Router(CodexRouter):
            async def load_auth(self, slot):
                return slot.auth

            async def open_inference(self, body, headers, auth, scope=None):
                return await session.post(fixture.upstream_url, json=body,
                                          headers={"X-Fixture-Account": auth.account_id})

        router = Router(session, store)
        router.slots = [CodexSlot(i, auth=CodexAuth("synthetic-token-" + str(self.generation), account_id=a))
                        for i, a in enumerate(("A", "B", "A"))]
        app[STATE_KEY].session = session
        app[STATE_KEY].queue.start()
        app[STATE_KEY].codex = self.router = router
        for slot in router.slots:
            await router.set_slot_routing_enabled(slot.index, slot.index not in self.disabled)
        self.route_runner = web.AppRunner(app)
        await self.route_runner.setup()
        site = web.TCPSite(self.route_runner, "127.0.0.1", self.route_port)
        await site.start()
        self.route_port = site._server.sockets[0].getsockname()[1]

    async def control(self, request):
        data = await request.json()
        self.disabled = set(data.get("disabled", self.disabled))
        self.late = data.get("late", self.late)
        self.omit_output = data.get("omit_output", self.omit_output)
        if data.get("rotate_secret"):
            os.environ["SPARTAN_REASONING_BINDING_SECRET"] = "42" * 32
        if data.get("restart"):
            await self.route_runner.cleanup()
            self.generation += 1
            await self.start_route()
        elif "disabled" in data:
            for slot in self.router.slots:
                await self.router.set_slot_routing_enabled(slot.index, slot.index not in self.disabled)
        return web.json_response({"calls": self.calls, "bindings": self.bindings})

    async def run(self):
        # Do not inherit provider credentials, endpoint overrides or private program inputs.
        os.environ.clear()
        os.environ["SPARTAN_REASONING_BINDING_SECRET"] = "41" * 32
        app = web.Application()
        app.router.add_post("/upstream", self.upstream)
        app.router.add_post("/control", self.control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.upstream_url = f"http://127.0.0.1:{port}/upstream"
        self.route_port = 0
        await self.start_route()
        print(json.dumps({"baseURL": f"http://127.0.0.1:{self.route_port}/v1",
                          "control": f"http://127.0.0.1:{port}/control"}), flush=True)
        try:
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            await self.route_runner.cleanup()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(Fixture().run())
