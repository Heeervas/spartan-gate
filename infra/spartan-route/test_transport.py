import asyncio
import json
import unittest
from types import SimpleNamespace, MethodType
from unittest.mock import patch

from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError, web

from spartan_route.codex import CodexAuth, CodexRouter, parse_sse_response
from spartan_route.native_responses import execute_codex_responses
from spartan_route.transport import CodexTransport, TransportError, proxy_for_websocket, websocket_frame
import test_codex as fixtures
FakeResponse = fixtures.FakeResponse


def event(name, **fields):
    return {"type": name, **fields}


def success():
    return [
        event("response.created", response={"status": "in_progress"}),
        event("response.output_text.delta", delta="OK"),
        event("response.completed", response={"status": "completed", "output": [], "usage": {"input_tokens": 3000, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2000}}}),
    ]


class Socket:
    def __init__(self, events=None, *, send_error=False):
        self.messages = list(events or [])
        self.closed = False
        self.sent = []
        self.send_error = send_error
        self.receives = 0

    async def send_str(self, text):
        self.sent.append(json.loads(text))
        if self.send_error:
            raise ConnectionError("unknown delivery")

    async def receive(self):
        self.receives += 1
        if not self.messages:
            await asyncio.Event().wait()
        value = self.messages.pop(0)
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(value))

    async def close(self):
        self.closed = True


class Session:
    def __init__(self, sockets):
        self.sockets = list(sockets)
        self.connects = []
        self.posts = []

    async def ws_connect(self, url, **kwargs):
        self.connects.append((url, kwargs))
        value = self.sockets.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(events=[(item["type"], item) for item in success()])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    url = "https://chatgpt.com/backend-api/codex/responses"
    headers = {"Authorization": "Bearer synthetic-test-token", "session-id": "cache", "chatgpt-account-id": "account"}
    body = {"model": "gpt-6.1-sol", "stream": True, "store": False, "prompt_cache_key": "cache", "reasoning": {"effort": "medium"}, "input": "hello", "service_tier": "priority"}

    async def open(self, transport, **kwargs):
        return await transport.open(self.url, kwargs.get("body", self.body), kwargs.get("headers", self.headers), account=kwargs.get("account", "account"), scope=kwargs.get("scope", "signed-chain"), timeout=kwargs.get("timeout", 2))

    async def consume(self, response):
        try:
            return [item async for item in parse_sse_response(response)]
        finally:
            response.release()

    async def test_same_identity_reuses_and_terminal_does_not_wait_for_eof(self):
        socket = Socket(success() + success())
        transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
        try:
            first = await self.open(transport)
            self.assertEqual(len(await self.consume(first)), 3)
            self.assertEqual(socket.receives, 3)
            second = await self.open(transport)
            await self.consume(second)
            self.assertTrue(second.transport_details["connection_reused"])
            self.assertEqual(len(transport.session.connects), 1)
            self.assertEqual(socket.sent, [self.body | {"type": "response.create"}] * 2)
            self.assertEqual(len(transport.entries), 1)
            self.assertEqual(transport.entries[0].state, "idle")
        finally:
            await transport.close()
        self.assertEqual(transport.entries, [])

    async def test_accounts_chains_models_and_cache_keys_do_not_share_sockets(self):
        session = Session([Socket(success()) for _ in range(5)])
        transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
        try:
            variations = [{}, {"account": "other"}, {"scope": "other-chain"}, {"body": self.body | {"model": "gpt-6-astra"}}, {"body": self.body | {"prompt_cache_key": "other-cache"}}]
            for variation in variations:
                response = await self.open(transport, **variation)
                await self.consume(response)
                self.assertFalse(response.transport_details["connection_reused"])
            self.assertEqual(len(session.connects), 5)
        finally:
            await transport.close()

    async def test_proxy_is_explicit_and_tls_verification_enabled(self):
        transport = CodexTransport(Session([Socket(success())]), "auto", proxy="http://proxy:8888")
        try:
            await self.consume(await self.open(transport))
            url, options = transport.session.connects[0]
            self.assertTrue(url.startswith("wss://"))
            self.assertEqual(options["proxy"], "http://proxy:8888")
            self.assertIs(options["ssl"], True)
        finally:
            await transport.close()
        for proxy in ("", "https://proxy:8888", "http://user:password@proxy:8888", "http://proxy:bad", "http://proxy/path"):
            transport = CodexTransport(Session([]), "auto", proxy=proxy)
            result = await self.open(transport)
            self.assertEqual(result.status, 503)
            self.assertFalse(result.retry_allowed)
            self.assertFalse(transport.session.posts)
            self.assertFalse(transport.session.connects)

    async def test_unsupported_upgrade_falls_back_before_sending_only(self):
        failure = WSServerHandshakeError(None, (), status=426)
        session = Session([failure])
        transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
        try:
            for _ in range(2):
                response = await self.open(transport)
                self.assertEqual(response.transport_details["fallback_reason"], "unsupported")
                await self.consume(response)
            self.assertEqual(len(session.connects), 1)
            self.assertEqual(len(session.posts), 2)
        finally:
            await transport.close()

    async def test_send_failure_never_falls_back_or_rotates_native_accounts(self):
        for streaming in (False, True):
            session = Session([Socket(send_error=True), Socket(success())])
            router = fixtures.NativeResponsesTests.router([])
            router.transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
            try:
                result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", streaming, "session", "cache")
                self.assertEqual(result.status, 502)
                self.assertFalse(result.retry_allowed)
                self.assertEqual(len(session.connects), 1)
                self.assertEqual(session.posts, [])
                self.assertTrue(all(slot.in_flight == 0 for slot in router.slots))
            finally:
                await router.transport.close()

    async def test_precommit_disconnect_never_rotates_native_accounts(self):
        for streaming in (False, True):
            session = Session([Socket([success()[0], ConnectionError()]), Socket(success())])
            router = fixtures.NativeResponsesTests.router([])
            router.transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
            try:
                result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", streaming, "session", "cache")
                self.assertEqual(result.status, 502)
                self.assertFalse(result.retry_allowed)
                self.assertEqual(len(session.connects), 1)
                self.assertFalse(result.completed)
            finally:
                await router.transport.close()

    async def test_chat_collect_failure_cannot_retry_another_account(self):
        session = Session([Socket([success()[0], ConnectionError()]), Socket(success())])
        router = fixtures.NativeResponsesTests.router([])
        router._single_chat_call = MethodType(CodexRouter._single_chat_call, router)
        router.transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
        try:
            result = await CodexRouter.execute_chat(router, {"messages": [{"role": "user", "content": "hello"}]}, "codex/gpt-6.1-sol", False, "session", "cache", "request")
            self.assertFalse(result.retry_allowed)
            self.assertEqual(len(session.connects), 1)
        finally:
            await router.transport.close()

    async def test_sideband_events_do_not_commit_and_unknown_error_is_not_retryable(self):
        socket = Socket([event("codex.rate_limits", rate_limits={}), event("codex.response.metadata", headers={}), event("error", error={"code": "unknown"})])
        transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
        try:
            response = await self.open(transport)
            self.assertEqual(response.status, 502)
            self.assertFalse(response.retry_allowed)
            self.assertNotIn("first_event_ms", response.transport_details)
        finally:
            await transport.close()

    async def test_failed_terminal_and_cancelled_consumer_discard_socket(self):
        for terminal in ("failed", "incomplete", "cancelled"):
            socket = Socket([event("response." + terminal, response={"status": terminal})])
            transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
            response = await self.open(transport)
            await self.consume(response)
            await transport.close()
            self.assertTrue(socket.closed)
        socket = Socket(success())
        transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
        response = await self.open(transport)
        response.release()
        await transport.close()
        self.assertTrue(socket.closed)

    async def test_connecting_capacity_is_reserved_before_handshake(self):
        gate = asyncio.Event()
        class Slow(Session):
            async def ws_connect(self, *args, **kwargs):
                await gate.wait()
                return await super().ws_connect(*args, **kwargs)
        transport = CodexTransport(Slow([Socket(success())]), "auto", proxy="http://proxy:8888")
        with patch("spartan_route.transport.MAX_ACCOUNT_CONNECTIONS", 1):
            first = asyncio.create_task(self.open(transport))
            await asyncio.sleep(.01)
            other = await self.open(transport, scope="parallel")
            self.assertEqual(other.transport_details["fallback_reason"], "pool_busy")
            self.assertEqual(len(transport.entries), 1)
            gate.set()
            await self.consume(await first)
        await transport.close()

    async def test_credential_change_retires_old_generation(self):
        old, new = Socket(success()), Socket(success())
        transport = CodexTransport(Session([old, new]), "auto", proxy="http://proxy:8888")
        try:
            await self.consume(await self.open(transport))
            await self.consume(await self.open(transport, headers=self.headers | {"Authorization": "Bearer rotated"}))
            await asyncio.sleep(.01)
            self.assertTrue(old.closed)
            self.assertFalse(new.closed)
        finally:
            await transport.close()

    async def test_idle_expiration_without_new_requests(self):
        socket = Socket(success())
        # Idle sockets normally block in receive until ping, data, or closure.
        original = socket.receive
        async def receive():
            if socket.messages:
                return await original()
            await asyncio.Event().wait()
        socket.receive = receive
        transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
        with patch("spartan_route.transport.IDLE_SECONDS", .02):
            try:
                await self.consume(await self.open(transport))
                await asyncio.sleep(.06)
                self.assertTrue(socket.closed)
                self.assertEqual(transport.entries, [])
            finally:
                await transport.close()

    async def test_shutdown_during_handshake_never_sends(self):
        gate = asyncio.Event()
        socket = Socket(success())
        class Slow(Session):
            async def ws_connect(self, *args, **kwargs):
                await gate.wait()
                return socket
        transport = CodexTransport(Slow([]), "auto", proxy="http://proxy:8888")
        task = asyncio.create_task(self.open(transport))
        await asyncio.sleep(.01)
        await transport.close()
        gate.set()
        result = await task
        self.assertEqual(result.status, 503)
        self.assertTrue(socket.closed)
        self.assertEqual(socket.sent, [])

    async def test_committed_quota_error_preserves_cooldown_without_replay(self):
        for native in (True, False):
            socket = Socket(success()[:2] + [event("error", status=429, error={"code": "synthetic_quota", "resets_in_seconds": 60})])
            router = fixtures.NativeResponsesTests.router([])
            router._single_chat_call = MethodType(CodexRouter._single_chat_call, router)
            router.transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
            try:
                if native:
                    result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", True, "session", "cache")
                else:
                    result = await CodexRouter.execute_chat(router, {"messages": [{"role": "user", "content": "hello"}]}, "codex/gpt-6.1-sol", True, "session", "cache", "request")
                chunks = b"".join([chunk async for chunk in result.stream])
                self.assertIn(b"codex_unavailable", chunks)
                self.assertEqual(len(socket.sent), 1)
                self.assertTrue(any(slot.rate_limited_until > 0 for slot in router.slots))
                self.assertFalse(result.completed)
            finally:
                await router.transport.close()

    async def test_real_idle_socket_answers_peer_ping_and_reuses_after_heartbeats(self):
        pong = asyncio.Event()
        connections = []
        async def handler(request):
            ws = web.WebSocketResponse(autoping=False)
            await ws.prepare(request)
            connections.append(ws)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    for item in success():
                        await ws.send_json(item)
                    await ws.ping(b"idle-peer")
                elif message.type == WSMsgType.PING:
                    await ws.pong(message.data)
                elif message.type == WSMsgType.PONG and message.data == b"idle-peer":
                    pong.set()
            return ws
        app = web.Application()
        app.router.add_get("/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        local_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/"
        async with ClientSession() as client:
            class Local:
                async def ws_connect(self, _url, **kwargs):
                    return await client.ws_connect(local_url, heartbeat=.02, max_msg_size=kwargs["max_msg_size"])
            transport = CodexTransport(Local(), "websocket", proxy="http://proxy:8888")
            try:
                await self.consume(await self.open(transport))
                await asyncio.wait_for(pong.wait(), 1)
                await asyncio.sleep(.08)
                second = await self.open(transport)
                await self.consume(second)
                self.assertTrue(second.transport_details["connection_reused"])
                self.assertEqual(len(connections), 1)
            finally:
                await transport.close()
        await runner.cleanup()

    async def test_nonstream_material_rejection_classifies_without_replay(self):
        for native in (True, False):
            for status in (401, 429):
                socket = Socket(success()[:2] + [event("error", status=status, error={"code": "synthetic_rejection", "resets_in_seconds": 60})])
                router = fixtures.NativeResponsesTests.router([])
                router._single_chat_call = MethodType(CodexRouter._single_chat_call, router)
                router.transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
                try:
                    if native:
                        result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", False, "session", "cache")
                    else:
                        result = await CodexRouter.execute_chat(router, {"messages": [{"role": "user", "content": "hello"}]}, "codex/gpt-6.1-sol", False, "session", "cache", "request")
                    self.assertEqual(result.status, status)
                    self.assertFalse(result.retry_allowed)
                    self.assertEqual(len(socket.sent), 1)
                    if status == 429:
                        self.assertTrue(any(slot.rate_limited_until > 0 for slot in router.slots))
                    else:
                        self.assertTrue(any(slot.auth_unavailable_reason == "reauth_required" for slot in router.slots))
                finally:
                    await router.transport.close()

    async def test_definitive_quota_rejection_before_material_can_rotate(self):
        for native in (True, False):
            first = Socket([success()[0], event("error", status=429, error={"code": "usage_limit_reached", "resets_in_seconds": 60})])
            router = fixtures.NativeResponsesTests.router([])
            router._single_chat_call = MethodType(CodexRouter._single_chat_call, router)
            session = Session([first, Socket(success())])
            router.transport = CodexTransport(session, "auto", proxy="http://proxy:8888")
            try:
                if native:
                    result = await execute_codex_responses(router, {"input": "hello"}, "codex/gpt-6.1-sol", False, "session", "cache")
                else:
                    result = await CodexRouter.execute_chat(router, {"messages": [{"role": "user", "content": "hello"}]}, "codex/gpt-6.1-sol", False, "session", "cache", "request")
                self.assertEqual(result.status, 200)
                self.assertEqual(len(session.connects), 2)
                self.assertTrue(any(slot.rate_limited_until > 0 for slot in router.slots))
            finally:
                await router.transport.close()

    async def test_unstarted_chat_stream_cleanup_releases_socket_and_slot_once(self):
        socket = Socket(success())
        router = fixtures.NativeResponsesTests.router([])
        router._single_chat_call = MethodType(CodexRouter._single_chat_call, router)
        router.transport = CodexTransport(Session([socket]), "auto", proxy="http://proxy:8888")
        try:
            result = await CodexRouter.execute_chat(router, {"messages": [{"role": "user", "content": "hello"}]}, "codex/gpt-6.1-sol", True, "session", "cache", "request")
            await result.stream_cleanup()
            await result.stream_cleanup()
            await asyncio.sleep(.01)
            self.assertTrue(socket.closed)
            self.assertTrue(all(slot.in_flight == 0 for slot in router.slots))
        finally:
            await router.transport.close()


class SerializationTests(unittest.TestCase):
    def test_flat_subscription_frame_preserves_cache_and_controls(self):
        body = {"input": "hello", "model": "gpt-6.1-sol", "stream": True, "service_tier": "priority", "store": False, "include": ["reasoning.encrypted_content"], "reasoning": {"effort": "medium"}, "stream_options": {"include_usage": True}, "previous_response_id": "forbidden"}
        frame = json.loads(websocket_frame(body))
        self.assertEqual(frame, {k: v for k, v in body.items() if k != "previous_response_id"} | {"type": "response.create"})
        self.assertEqual(list(frame)[:4], ["type", "model", "stream", "service_tier"])

    def test_websocket_requires_https_proxy_even_if_no_proxy_allows_host(self):
        self.assertEqual(proxy_for_websocket({"HTTPS_PROXY": "http://proxy:8888", "NO_PROXY": "*"}), "http://proxy:8888")
        with self.assertRaises(ValueError):
            proxy_for_websocket({})
