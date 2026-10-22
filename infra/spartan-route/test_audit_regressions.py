"""Synthetic handler regressions for the reviewed Route audit; no live credentials."""
import asyncio
import json
import threading
import tempfile
import sqlite3
import time
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch

import test_app as fixtures
import test_codex as upstream
from spartan_route import app as routeapp
from spartan_route.codex import CodexAuth, CodexRouter, CodexSlot
from spartan_route.telemetry import TelemetryStore


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.before_return = None

    async def post(self, url, **kwargs):
        self.calls.append(kwargs)
        if self.before_return:
            self.before_return()
        return self.response


class Sink:
    fail_prepare = False

    def __init__(self, **kwargs):
        self.status = kwargs.get('status')
        self.chunks = []

    async def prepare(self, request):
        if self.fail_prepare:
            raise ConnectionResetError('synthetic downstream failure')

    async def write(self, chunk):
        self.chunks.append(chunk)

    async def write_eof(self):
        pass


class Request:
    def __init__(self, app, body):
        self.app, self.body, self.headers = app, body, {}

    async def json(self):
        return self.body


class AuditRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.response = upstream.FakeResponse()
        self.store = TelemetryStore(':memory:')
        self.app = routeapp.create_app(fixtures.test_config(), self.store)
        self.state = self.app[routeapp.STATE_KEY]
        self.session = Session(self.response)
        self.router = CodexRouter(self.session, self.store)
        self.env = patch.dict('os.environ', {'SPARTAN_ROUTE_RECOVERY_WAIT_MS': '0'})
        self.env.start()
        self.slot = CodexSlot(0, token='synthetic', auth=CodexAuth('synthetic', account_id='account-a'))
        self.router.slots = [self.slot]
        self.state.codex, self.state.session = self.router, self.session

    async def asyncTearDown(self):
        await self.router.cleanup()
        self.store.close()
        self.env.stop()

    async def call(self, events, *, native=False, stream=False, model='codex/gpt-5.5'):
        self.response = upstream.FakeResponse(events=events)
        self.response.headers = {'Content-Type': 'text/event-stream'}
        self.session.response = self.response
        body = {'model': model, 'stream': stream}
        body.update({'input': 'synthetic'} if native else {'messages': [{'role': 'user', 'content': 'synthetic'}]})
        with patch.object(routeapp.web, 'StreamResponse', Sink):
            result = await (routeapp.responses if native else routeapp.chat_completions)(Request(self.app, body))
        self.assertTrue(self.response.released)
        self.assertEqual(self.slot.in_flight, 0)
        entry = self.state.queue.queue.get_nowait()
        self.state.queue.queue.task_done()
        wire = b''.join(result.chunks).decode() if stream else result.body.decode()
        return result, wire, entry

    async def test_chat_eof_never_publishes_partial_tools(self):
        events = [('response.output_item.added', {'type': 'response.output_item.added', 'item': {
            'type': 'function_call', 'id': 'item', 'call_id': 'call', 'name': 'tool', 'arguments': ''}}),
            ('response.function_call_arguments.delta', {'type': 'response.function_call_arguments.delta', 'item_id': 'item', 'delta': '{"x":'})]
        for stream in (False, True):
            with self.subTest(stream=stream):
                result, wire, entry = await self.call(events, stream=stream)
                self.assertNotIn('"tool_calls"', wire)
                self.assertIn('codex_incomplete_response', wire)
                self.assertIsNotNone(entry.error)
                if not stream:
                    self.assertEqual(result.status, 502)

    async def test_native_eof_emits_failure_and_failed_first_is_classified(self):
        cases = [([('response.output_text.delta', {'type': 'response.output_text.delta', 'delta': 'partial'})], 'codex_incomplete_response'),
                 ([('response.failed', {'type': 'response.failed', 'response': {'status': 'failed', 'error': {'code': 'server_error'}}})], 'server_error')]
        for events, code in cases:
            with self.subTest(code=code):
                _result, wire, entry = await self.call(events, native=True, stream=True)
                self.assertIn('response.failed', wire)
                self.assertIn(code, wire)
                self.assertIsNotNone(entry.error)

    async def test_old_outcomes_cannot_mutate_new_auth(self):
        for native in (False, True):
            for status in (401, 429, 500):
                with self.subTest(native=native, status=status):
                    old = CodexAuth('old', account_id='account-a')
                    new = CodexAuth('new', account_id='account-b')
                    self.slot.auth, self.slot.auth_fingerprint = old, 'old'
                    self.slot.path = '/synthetic/auth.json'
                    self.slot.transient_until = self.slot.rate_limited_until = 0
                    self.slot.failed_auth_fingerprint = None
                    self.session.response = upstream.FakeResponse(status=status, text='{"error":{"code":"invalid_token"}}')
                    def rotate():
                        self.slot.auth, self.slot.auth_fingerprint = new, 'new'
                    self.session.before_return = rotate
                    body = {'model': 'codex/gpt-5.5', 'input': 'x'} if native else {'model': 'codex/gpt-5.5', 'messages': []}
                    await (routeapp.responses if native else routeapp.chat_completions)(Request(self.app, body))
                    self.assertIs(self.slot.auth, new)
                    self.assertIsNone(self.slot.failed_auth_fingerprint)
                    self.assertEqual(self.slot.rate_limited_until, 0)
                    self.assertEqual(self.slot.transient_until, 0)
                    self.assertEqual(self.slot.in_flight, 0)

    async def test_provider_prepare_failure_releases_before_first_iteration(self):
        for native in (False, True):
            with self.subTest(native=native):
                with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic'}), patch.object(Sink, 'fail_prepare', True):
                    with self.assertRaises(ConnectionResetError):
                        await self.call([], native=native, stream=True, model='openai/gpt-test')
                self.assertTrue(self.response.released)

    async def test_missing_policy_rejects_dispatch_and_readiness(self):
        self.store._slot_policy = None
        for native in (False, True):
            body = {'model': 'codex/gpt-5.5', 'input': 'x'} if native else {'model': 'codex/gpt-5.5', 'messages': []}
            result = await (routeapp.responses if native else routeapp.chat_completions)(Request(self.app, body))
            self.assertEqual(result.status, 503)
            self.assertIn('slot_policy_unavailable', result.body.decode())
        self.assertEqual(self.session.calls, [])
        result = await routeapp.health(Request(self.app, {}))
        self.assertEqual(result.status, 503)
        self.assertFalse(json.loads(result.body)['policyReady'])

    async def test_rejected_policy_write_keeps_committed_disable_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'synthetic.db')
            store = TelemetryStore(path)
            try:
                store.set_slot_routing_enabled(0, False)
                store._conn.execute("CREATE TRIGGER reject_policy BEFORE UPDATE ON codex_slot_overrides BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
                store._conn.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    await asyncio.to_thread(store.set_slot_routing_enabled, 0, True)
                self.assertFalse(store.slot_routing_override(0)['routingEnabled'])
            finally:
                store.close()
            reopened = TelemetryStore(path)
            try:
                self.assertFalse(reopened.slot_routing_override(0)['routingEnabled'])
            finally:
                reopened.close()

    async def test_completed_with_invalid_tool_json_is_not_published(self):
        events = [('response.completed', {'response': {'status': 'completed', 'output': [
            {'type': 'function_call', 'id': 'item', 'call_id': 'call', 'name': 'tool', 'arguments': '{"x":'}]}})]
        for stream in (False, True):
            self.slot.transient_until = 0
            _result, wire, _entry = await self.call(events, stream=stream)
            self.assertNotIn('"tool_calls"', wire)
            self.assertIn('error', wire)

    async def test_successful_old_stream_cannot_install_new_account_lease(self):
        events = [('response.output_text.delta', {'delta': 'hello'}),
                  ('response.completed', {'response': {'status': 'completed'}})]
        self.session.response = upstream.FakeResponse(events=events)
        result = await self.router.execute_chat({'messages': []}, 'codex/gpt-5.5', True, 'session', 'key', 'id')
        self.slot.auth = CodexAuth('new', account_id='account-b')
        self.slot.auth_fingerprint = 'new'
        async for _chunk in result.stream:
            pass
        self.assertTrue(result.completed)
        self.assertEqual(self.router.leases, {})
        self.assertEqual(self.slot.in_flight, 0)

    async def test_sse_budget_handles_boundary_split_and_rejects_overflow(self):
        from spartan_route.sse import frames, SSEFrameTooLarge
        async def source(chunks):
            for chunk in chunks:
                yield chunk
        with patch('spartan_route.sse.MAX_FRAME_BYTES', 16):
            frames_ok = [item async for item in frames(source([b'data: 12345678', b'\n\n']))]
            self.assertEqual(frames_ok[0][1], '12345678')
            for chunks in ([b'data: 123456789\n\n'], [b'data: ', b'x' * 11]):
                with self.assertRaises(SSEFrameTooLarge):
                    _ = [item async for item in frames(source(chunks))]

    async def test_pi_observation_storage_wait_does_not_block_loop(self):
        from spartan_route.pi_usage import observe_chat
        session = str(uuid.uuid4())
        registered, _ = self.store.pi_usage.register(session, dict.fromkeys(
            ('inputTokens', 'cachedInputTokens', 'outputTokens', 'totalTokens', 'codexTokens', 'unknownQuotaTokens'), 0))
        headers = {'X-Spartan-Pi-Client': '1', 'X-Spartan-Pi-Session': session, 'X-Spartan-Pi-Origin': registered['originId']}
        entered, release = threading.Event(), threading.Event()
        def hold():
            with self.store._lock:
                entered.set()
                release.wait(1)
        thread = threading.Thread(target=hold)
        thread.start()
        entered.wait(1)
        async def execute():
            return routeapp.ExecutionResult(200, usage_seen=True, input_tokens=3)
        task = asyncio.create_task(observe_chat(self.state.pi_usage, headers, 'openai', execute))
        started = time.monotonic()
        try:
            await asyncio.sleep(.02)
            self.assertLess(time.monotonic() - started, .1)
            self.assertFalse(task.done())
        finally:
            release.set()
            thread.join()
        _result, finish = await task
        await finish()
        self.assertEqual(self.store.pi_usage.get(session)['observed']['totalTokens'], 3)

    async def test_policy_read_does_not_wait_for_telemetry_lock(self):
        self.store.set_slot_routing_enabled(0, False)
        entered, release = threading.Event(), threading.Event()
        def hold():
            with self.store._lock:
                entered.set()
                release.wait(1)
        thread = threading.Thread(target=hold)
        thread.start()
        entered.wait(1)
        try:
            beats = []
            asyncio.get_running_loop().call_later(.01, lambda: beats.append(True))
            started = time.monotonic()
            self.assertIsNone(await asyncio.wait_for(self.router.choose_slot(None, set(), set()), .1))
            self.assertLess(time.monotonic() - started, .1)
            await asyncio.sleep(.02)
            self.assertTrue(beats)
        finally:
            release.set()
            thread.join()


if __name__ == '__main__':
    unittest.main()
