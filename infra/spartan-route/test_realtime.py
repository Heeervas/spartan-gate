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
        for raw in ('[]', 'null', '{"type":"response.create","response":{"max_output_tokens":NaN}}'):
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
