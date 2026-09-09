from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route.realtime import (
    MODEL, RealtimeConfig, RealtimeError, Reservations, VoiceClient, client_event, digest, validate_session,
)

TOKEN = 'fixture-voice-secret-' + 'a' * 32
OTHER = 'fixture-voice-secret-' + 'b' * 32
CLIENT = VoiceClient('hermes-talk', digest(TOKEN), 1)


def session():
    return {'type': 'realtime', 'model': MODEL, 'audio': {'output': {'voice': 'cedar'}}}


class PolicyTests(unittest.TestCase):
    def test_scope_and_missing_auth_fail_closed(self):
        cfg = RealtimeConfig(enabled=True, clients=(CLIENT,))
        self.assertEqual(cfg.client('Bearer ' + TOKEN), CLIENT)
        for value in ('', 'Bearer short', 'Bearer ' + OTHER, 'Basic ' + TOKEN):
            with self.assertRaises(RealtimeError):
                cfg.client(value)

    def test_config_explicit_enable_model_and_global_token_isolation(self):
        self.assertFalse(RealtimeConfig.from_env(None, {}).enabled)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clients.json'
            path.write_text(json.dumps({'clients': [dataclasses.asdict(CLIENT)]}))
            path.chmod(0o644)
            env = {'SPARTAN_ROUTE_REALTIME_ENABLED': 'true', 'SPARTAN_ROUTE_REALTIME_MODEL': MODEL,
                   'SPARTAN_ROUTE_REALTIME_CLIENTS_FILE': str(path)}
            self.assertTrue(RealtimeConfig.from_env(OTHER, env).enabled)
            with self.assertRaises(ValueError):
                RealtimeConfig.from_env(TOKEN, env)
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                RealtimeConfig.from_env(None, env)
        for flag in ('1', 'maybe', ''):
            with self.assertRaises(ValueError):
                RealtimeConfig.from_env(None, {'SPARTAN_ROUTE_REALTIME_ENABLED': flag})

    def test_policy_rejects_text_models_and_hosted_tools_in_every_surface(self):
        validate_session(session(), mint=True)
        forbidden = [{'tools': [{'type': 'web_search'}]}, {'prompt': {'id': 'hosted-prompt'}},
                     {'model': 'codex/gpt-6-astra'}, {'output_modalities': ['text']},
                     {'audio': {'input': {'transcription': {'model': 'unverified'}}}},
                     {'audio': {'output': {'voice': 'unverified'}}}]
        for value in forbidden:
            with self.subTest(value=value), self.assertRaises(RealtimeError):
                validate_session(value)
            with self.subTest(value=value), self.assertRaises(RealtimeError):
                client_event(json.dumps({'type': 'session.update', 'session': value}))
            with self.subTest(value=value), self.assertRaises(RealtimeError):
                client_event(json.dumps({'type': 'response.create', 'response': value}))

    def test_protocol_payloads_are_not_reconstructed(self):
        for kind in ('input_audio_buffer.append', 'input_audio_buffer.commit', 'input_audio_buffer.clear',
                     'response.cancel', 'conversation.item.create', 'conversation.item.delete', 'conversation.item.truncate'):
            value = {'type': kind, 'event_id': 'fixture-id', 'item_id': 'item', 'audio': 'AQACAA=='}
            self.assertEqual(client_event(json.dumps(value)), value)
        for raw in ('[]', 'null', '{"type":[]}', '{"type":"response.cancel","type":"response.create"}', '{"type":"response.create","response":{"max_output_tokens":1e999}}', '{"type":"response.create","response":{"max_output_tokens":NaN}}'):
            with self.assertRaises(RealtimeError):
                client_event(raw)


class TicketTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_redemption_expiry_model_and_release(self):
        pool = Reservations(RealtimeConfig(enabled=True, clients=(CLIENT,)))
        r = pool.reserve(CLIENT)
        pool.bind(r, object(), 'fixture-account')
        ticket, expiry = pool.issue(r, 'upstream-fixture', time.time() + 90)
        self.assertLessEqual(expiry, time.time() + 30)
        self.assertNotEqual(ticket, 'upstream-fixture')
        with self.assertRaises(RealtimeError):
            pool.redeem(ticket, 'text-model')
        self.assertIs(pool.redeem(ticket, MODEL), r)
        with self.assertRaises(RealtimeError):
            pool.redeem(ticket, MODEL)
        pool.release(r)
        pool.release(r)
        self.assertEqual(pool.entries, {})
        self.assertIsNone(r.upstream_secret)
        r = pool.reserve(CLIENT)
        ticket, _ = pool.issue(r, 'upstream', time.time() + 90)
        r.deadline = time.monotonic() - 1
        with self.assertRaises(RealtimeError):
            pool.redeem(ticket, MODEL)
        self.assertEqual(pool.entries, {})

    async def test_client_and_account_capacity_and_shutdown(self):
        other = VoiceClient('other', digest(OTHER), 2)
        pool = Reservations(RealtimeConfig(enabled=True, clients=(CLIENT, other), per_account=1))
        first = pool.reserve(CLIENT)
        pool.bind(first, object(), 'same-account')
        with self.assertRaises(RealtimeError):
            pool.reserve(CLIENT)
        second = pool.reserve(other)
        with self.assertRaises(RealtimeError):
            pool.bind(second, object(), 'same-account')
        pool.release(second)
        pool.release(first)
        pool.closing = True
        with self.assertRaises(RealtimeError):
            pool.reserve(CLIENT)

    async def test_bad_provider_expiry_never_creates_ticket(self):
        for expiry in (True, float('inf'), float('nan'), 10 ** 1000, 0):
            pool = Reservations(RealtimeConfig(enabled=True, clients=(CLIENT,)))
            r = pool.reserve(CLIENT)
            with self.assertRaises(RealtimeError):
                pool.issue(r, 'upstream', expiry)
            pool.release(r)
            self.assertEqual(pool.tickets, {})

    async def test_pending_ticket_timer_releases_reservation(self):
        pool = Reservations(RealtimeConfig(enabled=True, clients=(CLIENT,), ticket_seconds=1.01))
        r = pool.reserve(CLIENT)
        pool.issue(r, 'upstream', time.time() + 90)
        await asyncio.sleep(1.03)
        self.assertEqual(pool.entries, {})

from aiohttp import web, WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from spartan_route import realtime as rt
from spartan_route.codex import CodexAuth, CodexSlot
from unittest.mock import AsyncMock


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mints = []
        self.messages = []
        self.upstream_auth = []
        self.mint_status = 200
        self.mint_error = {'error': {'code': 'invalid_api_key', 'message': 'private-upstream-message'}}
        self.ws_status = 101
        async def mint(request):
            self.mints.append(await request.json())
            self.upstream_auth.append(request.headers.get('Authorization'))
            if self.mint_status != 200:
                return web.json_response(self.mint_error, status=self.mint_status)
            return web.json_response({'value': 'fixture-upstream-secret', 'expires_at': time.time() + 60})
        async def socket(request):
            self.upstream_auth.append(request.headers.get('Authorization'))
            if self.ws_status != 101:
                return web.Response(status=self.ws_status)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({'type': 'session.created', 'session': {'model': MODEL, 'id': 'provider-session'}})
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                self.messages.append(message.data)
                obj = json.loads(message.data)
                if obj.get('event_id') == 'provider-error':
                    await ws.send_json({'type': 'error', 'event_id': 'provider-error', 'error': self.mint_error['error']})
                elif obj.get('event_id') == 'disconnect':
                    await ws.close()
                else:
                    await ws.send_str(message.data)
            return ws
        app = web.Application()
        app.router.add_post('/mint', mint)
        app.router.add_get('/ws', socket)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.patches = [patch.object(rt, 'MINT_URL', str(self.upstream.make_url('/mint'))),
                        patch.object(rt, 'WS_URL', str(self.upstream.make_url('/ws')).replace('http:', 'ws:')),
                        patch.object(rt, 'checked_proxy', return_value={})]
        for p in self.patches:
            p.start()
        auth = CodexAuth('fixture-oauth-access', account_id='fixture-account')
        self.slot = CodexSlot(1, path='/fixture/auth.json', auth=auth)
        self.router = SimpleNamespace(slots=[self.slot], load_auth=AsyncMock(return_value=auth),
                                      slot_routing_status=lambda s: {'routingEnabled': True, 'requestedRoutingEnabled': True})
        cfg = RealtimeConfig(enabled=True, clients=(CLIENT, VoiceClient('other', digest(OTHER), 1)))
        @web.middleware
        async def middleware(request, handler):
            return await rt.voice_dispatch(request, handler)
        app = web.Application(middlewares=[middleware])
        self.service = rt.register(app, cfg)
        await self.service.start(self.router)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.service.shutdown()
        await self.client.close()
        await self.upstream.close()
        for p in reversed(self.patches):
            p.stop()

    async def mint(self, token=TOKEN, payload=None):
        return await self.client.post('/v1/realtime/client_secrets',
                                      headers={'Authorization': 'Bearer ' + token},
                                      json={'session': session()} if payload is None else payload)

    async def connect(self, ticket):
        ws = await self.client.ws_connect('/v1/realtime?model=' + MODEL,
                                          headers={'Authorization': 'Bearer ' + ticket})
        self.assertEqual((await ws.receive_json())['type'], 'session.created')
        return ws

    async def test_auth_denial_no_provider_or_oauth_and_private_capabilities(self):
        for path in ('/v1/realtime/client_secrets', '/api/realtime/capabilities'):
            response = await self.client.request('POST' if path.endswith('client_secrets') else 'GET', path)
            self.assertEqual(response.status, 401)
        self.assertEqual(self.mints, [])
        self.router.load_auth.assert_not_awaited()
        response = await self.client.get('/api/realtime/capabilities', headers={'Authorization': 'Bearer ' + TOKEN})
        value = await response.json()
        self.assertIsNone(value['observation'])
        self.assertNotIn('fixture-account', json.dumps(value))

    async def test_subscription_only_mint_proxy_order_and_ticket_reuse(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-never-be-used'}):
            response = await self.mint()
        self.assertEqual(response.status, 200)
        value = await response.json()
        self.assertNotEqual(value['value'], 'fixture-upstream-secret')
        ws = await self.connect(value['value'])
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.connect(value['value'])
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.upstream_auth, ['Bearer fixture-oauth-access', 'Bearer fixture-upstream-secret'])
        await ws.close()
        await asyncio.sleep(0.02)
        self.assertEqual(self.service.pool.entries, {})

    async def test_racing_ticket_redemptions_open_only_one_upstream(self):
        ticket = (await (await self.mint()).json())['value']
        outcomes = await asyncio.gather(self.connect(ticket), self.connect(ticket), return_exceptions=True)
        sockets = [result for result in outcomes if not isinstance(result, Exception)]
        errors = [result for result in outcomes if isinstance(result, Exception)]
        self.assertEqual(len(sockets), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].status, 401)
        self.assertEqual(len(self.upstream_auth), 2)  # one mint, one WS
        await sockets[0].close()

    async def test_audio_tools_cancellation_raw_order_and_ids(self):
        value = await (await self.mint()).json()
        ws = await self.connect(value['value'])
        events = [
            {'type': 'input_audio_buffer.append', 'audio': 'AQACAA==', 'event_id': 'audio'},
            {'type': 'input_audio_buffer.commit', 'event_id': 'commit'},
            {'type': 'conversation.item.create', 'item': {'type': 'function_call_output', 'call_id': 'function-id', 'output': '{"ok":true}'}, 'event_id': 'tool-output'},
            {'type': 'response.create', 'event_id': 'continue'},
            {'type': 'response.cancel', 'response_id': 'response-id', 'event_id': 'cancel'},
            {'type': 'conversation.item.truncate', 'item_id': 'audio-id', 'content_index': 0, 'audio_end_ms': 20},
            {'type': 'input_audio_buffer.clear'},
        ]
        raw = [json.dumps(e, separators=(', ', ': ')) for e in events]
        for text in raw:
            await ws.send_str(text)
            self.assertEqual((await ws.receive()).data, text)
        self.assertEqual(self.messages, raw)
        await ws.close()

    async def test_update_cannot_enable_provider_tools_and_error_has_no_content(self):
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await ws.send_json({'type': 'session.update', 'session': {'tools': [{'type': 'mcp', 'server_url': 'https://private.invalid'}]}})
        value = await ws.receive_json()
        self.assertEqual(value['type'], 'error')
        self.assertNotIn('private.invalid', json.dumps(value))
        self.assertEqual(self.messages, [])
        await ws.close()

    async def test_mint_error_and_websocket_error_release_reservations(self):
        for status in (400, 401, 403, 429, 500):
            self.mint_status = status
            response = await self.mint()
            value = await response.json()
            self.assertNotEqual(response.status, 200)
            self.assertNotIn('private-upstream-message', json.dumps(value))
            self.assertEqual(self.service.pool.entries, {})
        self.mint_status = 200
        self.ws_status = 403
        ticket = (await (await self.mint()).json())['value']
        with self.assertRaises(WSServerHandshakeError) as exc:
            await self.connect(ticket)
        self.assertEqual(exc.exception.status, 403)
        self.assertEqual(self.service.pool.entries, {})

    async def test_account_identity_change_between_mint_and_ws_denied(self):
        ticket = (await (await self.mint()).json())['value']
        self.slot.auth = CodexAuth('different-auth', account_id='different-account')
        with self.assertRaises(WSServerHandshakeError) as exc:
            await self.connect(ticket)
        self.assertEqual(exc.exception.status, 409)
        self.assertEqual(len(self.upstream_auth), 1)
        self.assertEqual(self.service.pool.entries, {})

    async def test_slots_in_cooldown_denied_before_auth(self):
        self.slot.rate_limited_until = time.time() + 60
        self.assertEqual((await self.mint()).status, 429)
        self.router.load_auth.assert_not_awaited()
        self.assertEqual(self.mints, [])

    async def test_two_clients_have_distinct_tickets_and_observations(self):
        first = (await (await self.mint()).json())['value']
        second = (await (await self.mint(OTHER)).json())['value']
        self.assertNotEqual(first, second)
        a, b = await self.connect(first), await self.connect(second)
        await a.send_json({'type': 'response.cancel', 'event_id': 'client-a'})
        self.assertEqual((await a.receive_json())['event_id'], 'client-a')
        await b.send_json({'type': 'response.cancel', 'event_id': 'client-b'})
        self.assertEqual((await b.receive_json())['event_id'], 'client-b')
        await a.close()
        await b.close()

    async def test_binary_frame_and_shutdown_close_connections(self):
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await ws.send_bytes(b'invalid-binary-audio')
        self.assertEqual((await ws.receive_json())['error']['code'], 'realtime_invalid_frame')
        await ws.close()
        await asyncio.sleep(0.02)
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await self.service.shutdown()
        self.assertEqual(self.service.pool.entries, {})
        self.assertEqual(self.service.pool.tickets, {})
        self.assertIn((await ws.receive()).type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        await ws.close()

    async def test_provider_error_is_redacted_and_forwarded_once(self):
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await ws.send_json({'type': 'response.create', 'event_id': 'provider-error'})
        value = await ws.receive_json()
        self.assertEqual(value['event_id'], 'provider-error')
        self.assertNotIn('private-upstream-message', json.dumps(value))
        self.assertIn((await ws.receive()).type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        await ws.close()

    async def test_late_cancel_is_a_noop_and_next_turn_survives(self):
        self.mint_error = {'error': {'type': 'invalid_request_error',
                                    'code': 'response_cancel_not_active',
                                    'message': 'private-upstream-message'}}
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await ws.send_json({'type': 'response.cancel', 'event_id': 'provider-error'})
        await ws.send_json({'type': 'response.create', 'event_id': 'next-turn'})
        value = await asyncio.wait_for(ws.receive_json(), 2)
        self.assertEqual(value, {'type': 'response.create', 'event_id': 'next-turn'})
        reservation = next(iter(self.service.pool.entries.values()))
        self.assertEqual(reservation.metrics['inactive_cancel_count'], 1)
        await ws.close()

    async def test_cancel_exception_does_not_weaken_other_provider_errors(self):
        for error in ({'type': 'server_error', 'code': 'response_cancel_not_active'},
                      {'type': 'invalid_request_error', 'code': 'unknown'},
                      {'type': 'invalid_request_error', 'code': ['response_cancel_not_active']}):
            with self.subTest(error=error):
                self.mint_error = {'error': error}
                ticket = (await (await self.mint()).json())['value']
                ws = await self.connect(ticket)
                await ws.send_json({'type': 'response.cancel', 'event_id': 'provider-error'})
                self.assertEqual((await ws.receive_json())['type'], 'error')
                self.assertIn((await ws.receive()).type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
                await ws.close()
                await asyncio.sleep(0.02)

    async def test_oversized_frame_and_http_body_are_bounded(self):
        response = await self.mint(payload={'session': {**session(), 'instructions': 'x' * (self.service.config.mint_bytes + 1)}})
        self.assertEqual(response.status, 413)
        self.assertEqual(self.mints, [])
        ticket = (await (await self.mint()).json())['value']
        ws = await self.connect(ticket)
        await ws.send_str('x' * (self.service.config.frame_bytes + 1))
        # aiohttp may close its reader at 1009 before the owner can send JSON.
        message = await ws.receive()
        self.assertIn(message.type, {WSMsgType.TEXT, WSMsgType.CLOSE, WSMsgType.CLOSED})
        if message.type == WSMsgType.TEXT:
            self.assertEqual(json.loads(message.data)['error']['code'], 'realtime_limit_exceeded')
        await ws.close()

    async def test_slow_writer_has_bounded_lifecycle(self):
        cfg = dataclasses.replace(self.service.config, send_seconds=0.01)
        self.service.config = cfg
        class Slow:
            close_code = 1000
            async def send_str(self, data):
                await asyncio.Event().wait()
        class Source:
            close_code = 1000
            async def receive(self):
                return SimpleNamespace(type=WSMsgType.TEXT, data='{"type":"response.cancel"}')
            async def send_str(self, data):
                pass
        class Never(Slow):
            async def receive(self):
                await asyncio.Event().wait()
        r = self.service.pool.reserve(CLIENT)
        r.downstream, r.upstream = Source(), Never()
        with self.assertRaises(TimeoutError):
            await self.service.relay(r)
        self.service.pool.release(r)

class AppBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_auth_precedes_legacy_unconfigured_token_bypass(self):
        from spartan_route.app import create_app, STATE_KEY
        from spartan_route.config import load_config
        from spartan_route.telemetry import TelemetryStore
        with patch.dict(os.environ, {}, clear=True):
            config = dataclasses.replace(load_config(), token=None, db_path=':memory:')
        voice = RealtimeConfig(enabled=True, clients=(CLIENT,))
        with patch.object(rt.RealtimeConfig, 'from_env', return_value=voice):
            app = create_app(config, TelemetryStore(':memory:'))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            self.assertIs(app[rt.RT_KEY].codex, app[STATE_KEY].codex)
            self.assertEqual((await client.post('/v1/realtime/client_secrets', json={'session': session()})).status, 401)
            response = await client.get('/v1/models')
            data = await response.json()
            model = next(x for x in data['data'] if x['id'] == MODEL)
            self.assertNotIn('observation', model)
            self.assertNotIn('fixture-account', json.dumps(data))
            response = await client.get('/v1/models/' + MODEL)
            self.assertEqual(response.status, 200)
        finally:
            await client.close()
