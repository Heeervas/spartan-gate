"""Client-asserted grouping never grants replay, cache or usage authority."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route.correlation import parse_correlation
from spartan_route.pi_usage_store import COUNTERS
from spartan_route.app import STATE_KEY, ExecutionResult, create_app
from spartan_route.telemetry import TelemetryStore
from test_app import test_config

HEADER = "X-Spartan-Correlation"
DOCUMENT = {"version": 1, "client": "pi", "conversation": "native.session:1", "purpose": "agent"}


def headers(document=DOCUMENT):
    return CIMultiDict({"Authorization": "Bearer secret", HEADER: json.dumps(document)})


class CorrelationParserTests(unittest.TestCase):
    def test_absent_and_namespaced_hashes(self):
        self.assertIsNone(parse_correlation(CIMultiDict()))
        value = parse_correlation(headers({**DOCUMENT, "parent": "parent", "root": "root", "operation": "op"}))
        self.assertEqual(value["source"], "client_asserted")
        self.assertEqual(value["version"], 1)
        self.assertEqual(value["purpose"], "agent")
        self.assertNotEqual(value["conversation"], DOCUMENT["conversation"])
        other = parse_correlation(headers({**DOCUMENT, "client": "ultimate"}))
        self.assertNotEqual(value["conversation"], other["conversation"])
        for field in ("parent", "root", "operation"):
            self.assertNotEqual(value[field], field)

    def test_types_tokens_and_closed_fields(self):
        changes = [{"version": True}, {"version": 1.0}, {"version": 2}, {"client": "hermes"},
                   {"purpose": "unknown"}, {"conversation": ""}, {"conversation": "x" * 129},
                   {"conversation": "a/b"}, {"conversation": "é"}, {"parent": None},
                   {"operation": 1}, {"unknown": "private"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_correlation(headers({**DOCUMENT, **change}))
        for field in DOCUMENT:
            with self.subTest(missing=field), self.assertRaises(ValueError):
                parse_correlation(headers({key: value for key, value in DOCUMENT.items() if key != field}))

    def test_raw_limits_duplicates_and_escaped_ascii(self):
        raw = json.dumps(DOCUMENT)
        valid = headers()
        valid[HEADER] = raw + " " * (1024 - len(raw))
        self.assertIsNotNone(parse_correlation(valid))
        valid[HEADER] += " "
        with self.assertRaises(ValueError):
            parse_correlation(valid)
        for invalid in ('[]', 'null', '{', raw[:-1] + ',"version":1}',
                        raw.replace('"version"', '"ver\\u0073ion"')[:-1] + ',"version":1}', raw + "é"):
            with self.subTest(raw=invalid), self.assertRaises(ValueError):
                parse_correlation(CIMultiDict({HEADER: invalid}))
        duplicate = headers()
        duplicate.add(HEADER.lower(), raw)
        with self.assertRaises(ValueError):
            parse_correlation(duplicate)
        escaped = raw.replace('native', '\\u006eative')
        self.assertEqual(parse_correlation(CIMultiDict({HEADER: escaped})), parse_correlation(headers()))


class CorrelationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(":memory:")
        self.app = create_app(test_config(), self.store)
        self.app.on_startup.clear()
        self.state = self.app[STATE_KEY]
        self.state.queue.log = self.store.log_routing
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_invalid_denied_before_execution_registration_and_probe(self):
        invalid = headers({**DOCUMENT, "conversation": "private/path"})
        invalid["X-Spartan-Cache-Probe"] = "invalid"
        for path, body in (("/v1/responses", {"model": "codex/gpt-5.4", "input": "hello"}),
                           ("/v1/chat/completions", {"model": "codex/gpt-5.4", "messages": []})):
            with patch("spartan_route.app.observe_chat", new_callable=AsyncMock) as observe:
                response = await self.client.post(path, headers=invalid, json=body)
                self.assertEqual(response.status, 400)
                payload = await response.json()
                self.assertEqual(payload["error"]["code"], "invalid_correlation")
                self.assertNotIn("private", str(payload))
                observe.assert_not_awaited()
            unauthenticated = CIMultiDict(invalid)
            del unauthenticated["Authorization"]
            response = await self.client.post(path, headers=unauthenticated, json=body)
            self.assertEqual(response.status, 401)

    async def test_duplicate_header_http_and_fixed_errors(self):
        url = self.client.make_url('/v1/responses')
        reader, writer = await asyncio.open_connection(url.host, url.port)
        raw = json.dumps(DOCUMENT)
        wire = (f'POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer secret\r\n'
                f'{HEADER}: {raw}\r\nx-spartan-correlation: {raw}\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}')
        writer.write(wire.encode('ascii'))
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        self.assertTrue(response.startswith(b'HTTP/1.1 400'), response)
        self.assertEqual(json.loads(response.split(b'\r\n\r\n', 1)[1])["error"]["message"], 'Invalid correlation header')

    async def test_correlation_cannot_bypass_usage_origin_validation(self):
        session = str(uuid.uuid4())
        self.store.pi_usage.register(session, dict.fromkeys(COUNTERS, 0))
        marked = headers()
        marked.update({"X-Spartan-Pi-Client": "1", "X-Spartan-Pi-Session": session,
                       "X-Spartan-Pi-Origin": str(uuid.uuid4())})
        with patch("spartan_route.app.execute_chat", new_callable=AsyncMock) as execute:
            response = await self.client.post('/v1/chat/completions', headers=marked,
                json={"model": "openai/test", "messages": [{"role": "user", "content": "hello"}]})
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["error"]["code"], "pi_origin_invalid")
            execute.assert_not_awaited()

    async def test_signed_chain_evidence_remains_separate_from_correlation(self):
        with patch.dict(os.environ, SPARTAN_REASONING_BINDING_SECRET="41" * 32):
            response = await self.client.post('/api/pi/reasoning-binding', headers=headers(),
                json={"scope": "a" * 64, "sessionId": str(uuid.uuid4()), "realm": "codex_subscription"})
            self.assertEqual(response.status, 200)
            binding = (await response.json())["binding"]
            marked = headers()
            marked['X-Spartan-Pi-Client'] = '1'
            with patch("spartan_route.app.execute_responses", new_callable=AsyncMock,
                       return_value=ExecutionResult(status=200, body={"output": []})):
                response = await self.client.post('/v1/responses', headers=marked,
                    json={"model": "codex/gpt-5.4", "input": "hello", **binding})
                self.assertEqual(response.status, 200)
        row = self.store._conn.execute('SELECT * FROM routing_log ORDER BY id DESC LIMIT 1').fetchone()
        context = json.loads(row['context_info'])
        self.assertEqual(context['session_identity'], {"version": 1, "source": "signed_pi_chain"})
        self.assertEqual(context['correlation']['source'], 'client_asserted')
        self.assertEqual(row['session_id'], context['correlation']['conversation'])

    async def test_session_and_turn_aggregate_selected_labels(self):
        for purpose in ('agent', 'summary', 'agent'):
            with patch("spartan_route.app.execute_chat", new_callable=AsyncMock,
                       return_value=ExecutionResult(status=200, body={"output": []})):
                response = await self.client.post('/v1/chat/completions', headers=headers({**DOCUMENT, "purpose": purpose}),
                    json={"model": "openai/test", "metadata": {"turn_id": "synthetic-turn"},
                          "messages": [{"role": "user", "content": "hello"}]})
                self.assertEqual(response.status, 200)
        session = self.store.live_sessions()['sessions'][0]
        self.assertEqual(session['correlation']['purpose'], 'agent')
        self.assertEqual(session['purposes'], ['agent', 'summary'])
        self.assertEqual(session['turns'][0]['purposes'], ['agent', 'summary'])
        requests = self.store.turn_requests(session['turns'][0]['id'])['requests']
        self.assertEqual(requests[1]['context']['correlation']['purpose'], 'summary')

    async def test_shared_execution_turn_details_are_scoped_by_session(self):
        for conversation in ('first', 'second'):
            with patch("spartan_route.app.execute_chat", new_callable=AsyncMock,
                       return_value=ExecutionResult(status=200, body={"output": []})):
                response = await self.client.post('/v1/chat/completions', headers=headers({**DOCUMENT, "conversation": conversation}),
                    json={"model": "openai/test", "prompt_cache_key": "shared-execution-cache",
                          "messages": [{"role": "user", "content": "identical user content"}]})
                self.assertEqual(response.status, 200)
        sessions = self.store.live_sessions()['sessions']
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]['turns'][0]['id'], sessions[1]['turns'][0]['id'])
        path = '/api/routing/turns/' + sessions[0]['turns'][0]['id']
        for session in sessions:
            response = await self.client.get(path, params={'sessionId': session['id']}, headers={'Authorization': 'Bearer secret'})
            self.assertEqual(response.status, 200)
            requests = (await response.json())['requests']
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['sessionId'], session['id'])
        response = await self.client.get(path, headers={'Authorization': 'Bearer secret'})
        self.assertEqual(len((await response.json())['requests']), 2)

    async def test_invalid_session_filters_fail_closed(self):
        for params in ({'sessionId': ''}, {'sessionId': 'x' * 129}, {'sessionId': 'private/path'},
                       [('sessionId', 'first'), ('sessionId', 'second')]):
            response = await self.client.get('/api/routing/turns/turn', params=params,
                                             headers={'Authorization': 'Bearer secret'})
            self.assertEqual(response.status, 400)
            self.assertEqual((await response.json())['error']['code'], 'invalid_session_id')

    async def test_grouping_only_and_absent_behavior_on_both_apis(self):
        for path, executor, body in (
            ("/v1/responses", "execute_responses", {"model": "openai/test", "input": "hello", "prompt_cache_key": "original"}),
            ("/v1/chat/completions", "execute_chat", {"model": "openai/test", "messages": [{"role": "user", "content": "hello"}], "prompt_cache_key": "original"}),
        ):
            seen = []
            result = ExecutionResult(status=200, body={"output": []}, input_tokens=5)
            for supplied in (True, False):
                with patch("spartan_route.app." + executor, new_callable=AsyncMock, return_value=result) as execute:
                    response = await self.client.post(path, headers=headers() if supplied else {"Authorization": "Bearer secret"}, json=body)
                    self.assertEqual(response.status, 200, await response.text())
                    seen.append(execute.call_args)
                    entry = self.store._conn.execute("SELECT session_id, context_info FROM routing_log ORDER BY id DESC LIMIT 1").fetchone()
                    context = json.loads(entry["context_info"])
                    self.assertEqual(context["session_identity"]["source"], "legacy_fallback")
                    if supplied:
                        self.assertEqual(context["correlation"], parse_correlation(headers()))
                        self.assertEqual(entry["session_id"], context["correlation"]["conversation"])
                    else:
                        self.assertNotIn("correlation", context)
            self.assertEqual(seen[0].args[:6], seen[1].args[:6])
            self.assertEqual(seen[0].kwargs, seen[1].kwargs)
