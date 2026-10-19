from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spartan_route.codex import CodexAuth, CodexCallResult, CodexRouter, CodexSlot
from spartan_route.telemetry import TelemetryStore
from spartan_route import app
from spartan_route.native_responses import execute_codex_responses

MODEL = 'codex/gpt-6-sol'


class FakeCatalog:
    def __init__(self, *, refresh=False):
        self.allowed = False
        self.refresh = refresh
        self.calls = 0

    def supports(self, slot, auth, model, effort=None):
        return self.allowed

    def eligibility(self, slot, auth, model, effort=None):
        return dict(eligible=self.allowed, reason='eligible' if self.allowed else 'model_absent',
                    catalog_generation=self.calls, catalog_age_seconds=10)

    def known_unavailable(self, model, effort=None):
        return not self.allowed

    async def recover(self):
        self.calls += 1
        self.allowed = self.refresh
        return dict(outcome='refreshed', duration_ms=2)


class ModelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TelemetryStore(':memory:')
        self.router = CodexRouter(None, self.store)
        self.router.slots = [CodexSlot(0, token='secret', auth=CodexAuth('secret', account_id='private-account'))]
        self.catalog = FakeCatalog()
        self.router.model_catalog = self.catalog

    async def asyncTearDown(self):
        self.store.close()

    async def test_no_model_selection_reports_model_gate(self):
        result = await self.router.execute_chat({'messages': []}, MODEL, False, None, None, 'request', 'max')
        self.assertEqual(result.error, 'codex_model_unavailable')
        error = result.body['error']
        self.assertEqual(error['selection_diagnostics']['operational_eligible'], 1)
        self.assertEqual(error['selection_diagnostics']['model_eligible'], 0)
        self.assertEqual(error['selection_diagnostics']['slots'][0]['reason'], 'model_absent')
        self.assertEqual(self.catalog.calls, 1)
        self.assertNotIn('private-account', str(error))
        self.assertNotIn('secret', str(error))

    async def test_refresh_reselects_same_model_and_effort(self):
        self.catalog.refresh = True
        self.router._single_chat_call = AsyncMock(return_value=CodexCallResult(200, body={'ok': True}))
        result = await self.router.execute_chat({'messages': []}, MODEL, False, None, None, 'request', 'max')
        self.assertEqual(result.status, 200)
        self.assertEqual(self.catalog.calls, 1)
        self.assertEqual(self.router._single_chat_call.await_args.args[1], MODEL)
        self.assertEqual(result.selection_diagnostics['recovery']['outcome'], 'refreshed')

    async def test_quota_and_exclusions_do_not_refresh(self):
        slot = self.router.slots[0]
        slot.rate_limited_until = time.time() + 100
        self.router._probe_rate_limit_recovery = AsyncMock(return_value=False)
        result = await self.router.execute_chat({'messages': []}, MODEL, False, None, None, 'request')
        self.assertEqual(result.status, 429)
        self.assertEqual(self.catalog.calls, 0)
        slot.rate_limited_until = 0
        for excluded, accounts in (({0}, set()), (set(), {slot.auth.account_key})):
            chosen = await self.router.choose_slot_with_catalog_recovery(None, excluded, accounts, MODEL)
            self.assertIsNone(chosen)
        self.assertEqual(self.catalog.calls, 0)

    async def test_native_no_selection_uses_same_recovery(self):
        result = await execute_codex_responses(self.router, {'input': 'hello'}, MODEL, False, None, None)
        self.assertEqual(result.error, 'codex_model_unavailable')
        self.assertEqual(self.catalog.calls, 1)

    async def test_admission_rechecks_unknown_and_unavailable(self):
        state = SimpleNamespace(config=SimpleNamespace(models={MODEL: object()}), codex_catalog=self.catalog, codex=self.router)
        result = await app.codex_model_admission_with_recovery(state, 'codex/unknown', None)
        self.assertEqual(result.status, 404)
        self.assertEqual(self.catalog.calls, 0)
        result = await app.codex_model_admission_with_recovery(state, MODEL, None)
        self.assertEqual(result.error, 'codex_model_unavailable')
        self.catalog.refresh = True
        result = await app.codex_model_admission_with_recovery(state, MODEL, None)
        self.assertIsNone(result)

    async def test_no_probe_recovery(self):
        state = SimpleNamespace(config=SimpleNamespace(models={MODEL: object()}), codex_catalog=self.catalog, codex=self.router)
        result = await app.codex_model_admission_with_recovery(state, MODEL, None, allow_recovery=False)
        self.assertEqual(result.error, 'codex_model_unavailable')
        self.assertEqual(self.catalog.calls, 0)


    async def test_disabled_transient_and_missing_auth_do_not_refresh(self):
        for gate in ('disabled', 'transient', 'auth'):
            with self.subTest(gate=gate):
                slot = self.router.slots[0]
                with patch.object(self.router, 'slot_routing_status', return_value={'routingEnabled': gate != 'disabled'}), \
                     patch.object(self.router, 'load_auth', new=AsyncMock(return_value=None if gate == 'auth' else slot.auth)):
                    slot.transient_until = time.time() + 100 if gate == 'transient' else 0
                    self.assertIsNone(await self.router.choose_slot_with_catalog_recovery(None, set(), set(), MODEL))
        self.assertEqual(self.catalog.calls, 0)

    async def test_both_api_admissions_recover_and_preserve_metadata(self):
        for native in (False, True):
            self.catalog.allowed = False
            self.catalog.refresh = True
            state = SimpleNamespace(config=SimpleNamespace(models={MODEL: object()}), codex_catalog=self.catalog,
                                    codex=self.router, native_cache=None, cache_experiments=None)
            route = SimpleNamespace(provider='codex', actual_model=MODEL)
            success = CodexCallResult(200, body={'ok': True})
            with patch.object(self.router, 'execute_chat', new=AsyncMock(return_value=success)) as chat, \
                 patch('spartan_route.app.execute_codex_responses', new=AsyncMock(return_value=success)) as responses:
                if native:
                    result = await app.execute_responses(state, {'input': 'hello'}, route, False, None, None)
                    responses.assert_awaited_once()
                else:
                    result = await app.execute_chat(state, {'messages': []}, route, False, None, None, 'req')
                    chat.assert_awaited_once()
            self.assertEqual(result.status, 200)
            self.assertEqual(result.selection_diagnostics['recovery']['outcome'], 'refreshed')
            target = app.ExecutionResult(200)
            app.copy_codex_telemetry(success, target)
            self.assertEqual(target.selection_diagnostics, result.selection_diagnostics)

    async def test_model_removed_during_admission_never_executes(self):
        for native in (False, True):
            self.catalog.allowed = False
            state = SimpleNamespace(config=SimpleNamespace(models={MODEL: object()}), codex_catalog=self.catalog,
                                    codex=self.router, native_cache=None, cache_experiments=None)
            route = SimpleNamespace(provider='codex', actual_model=MODEL)
            async def remove_model():
                state.config.models.clear()
                return {'outcome': 'refreshed', 'duration_ms': 1}
            with patch.object(self.catalog, 'recover', new=remove_model), \
                 patch.object(self.router, 'execute_chat', new=AsyncMock()) as chat, \
                 patch('spartan_route.app.execute_codex_responses', new=AsyncMock()) as responses:
                if native:
                    result = await app.execute_responses(state, {'input': 'hello'}, route, False, None, None)
                else:
                    result = await app.execute_chat(state, {'messages': []}, route, False, None, None, 'req')
                self.assertEqual(result.status, 404)
                chat.assert_not_awaited()
                responses.assert_not_awaited()
                self.assertEqual(result.selection_diagnostics['recovery']['outcome'], 'refreshed')

    async def test_native_committed_stream_never_replays_or_recovers(self):
        from test_codex import NativeResponsesTests, FakeResponse
        router = NativeResponsesTests.router([FakeResponse(events=[
            ('response.output_text.delta', {'type': 'response.output_text.delta', 'delta': 'hello'}),
            ('error', {'type': 'error', 'error': {'code': 'server_error'}}),
        ])])
        router.choose_slot = CodexRouter.choose_slot.__get__(router, CodexRouter)
        router.model_catalog = self.catalog
        self.catalog.allowed = True
        result = await execute_codex_responses(router, {'input': 'hello'}, MODEL, True, None, None)
        self.catalog.allowed = False
        async for _ in result.stream:
            pass
        self.assertEqual(len(router.session.calls), 1)
        self.assertEqual(self.catalog.calls, 0)

    async def test_native_recovery_is_once_per_request_across_retries(self):
        self.router.slots.append(CodexSlot(1, token='other', auth=CodexAuth('other', account_id='other')))
        self.catalog.refresh = True
        self.catalog.supports = lambda slot, auth, model, effort=None: self.catalog.allowed and slot.index == 0
        original = self.catalog.eligibility
        self.catalog.eligibility = lambda slot, auth, model, effort=None: {**original(slot, auth, model, effort),
            'eligible': self.catalog.supports(slot, auth, model, effort),
            'reason': 'eligible' if self.catalog.supports(slot, auth, model, effort) else 'model_absent'}
        with patch('spartan_route.native_responses.open_response', new=AsyncMock(return_value=CodexCallResult(
                500, body={'error': {'code': 'server_error'}}, error='server_error'))) as opened:
            result = await execute_codex_responses(self.router, {'input': 'hello'}, MODEL, False, None, None)
        self.assertEqual(self.catalog.calls, 1)
        self.assertEqual(opened.await_count, 1)
        self.assertEqual(result.error, 'codex_model_unavailable')
        self.assertEqual(len(result.native_cache['attempts']), 1)

    async def test_current_quota_overrides_older_model_diagnostic(self):
        detail = await self.router.model_selection_diagnostics(set(), set(), MODEL)
        self.router.slots[0].rate_limited_until = time.time() + 100
        result = await self.router._unavailable_result(detail)
        self.assertEqual(result.status, 429)
        self.assertEqual(result.selection_diagnostics['operational_eligible'], 1)

    async def test_selection_snapshot_reaches_routing_context(self):
        from test_app import AppCacheDiagnosticsTests
        fixture = AppCacheDiagnosticsTests()
        state = fixture.state()
        try:
            detail = await self.router.model_selection_diagnostics(set(), set(), MODEL)
            detail['recovery'] = {'outcome': 'timed_out', 'duration_ms': 20000}
            result = app.ExecutionResult(503, error='codex_model_unavailable', selection_diagnostics=detail)
            app.log_request(state, {'messages': [], 'tools': []}, fixture.route(), result,
                            time.monotonic(), 'request', 'session', 'turn', 'chat')
            context = json.loads(state.queue.entries[-1].context_info)
            self.assertEqual(context['selection_diagnostics'], detail)
        finally:
            state.telemetry.close()

    async def test_rotated_account_rechecks_identity_bound_disable(self):
        slot = self.router.slots[0]
        self.store.set_slot_routing_enabled(0, False, account_key=slot.auth.account_key)
        self.assertFalse(self.router.slot_routing_status(slot)['routingEnabled'])
        replacement = CodexAuth('replacement', account_id='replacement')
        async def load(current):
            current.auth = replacement
            return replacement
        self.catalog.allowed = True
        with patch.object(self.router, 'load_auth', new=load):
            chosen = await self.router.choose_slot_with_catalog_recovery(None, set(), set(), MODEL)
        self.assertIs(chosen[1], replacement)
        self.assertEqual(self.catalog.calls, 0)

    async def test_native_probe_keeps_fixed_account_denial_without_recovery(self):
        experiments = SimpleNamespace(choose=AsyncMock(return_value=(self.router.slots[0], self.router.slots[0].auth)), finish=Mock())
        result = await execute_codex_responses(self.router, {'input': 'hello'}, MODEL, False, None, None,
                                             experiments=experiments, probe=object())
        self.assertEqual(result.error, 'probe_account_unavailable')
        self.assertEqual(self.catalog.calls, 0)
        experiments.finish.assert_called_once()

    async def test_diagnostics_recheck_identity_after_other_auth_await(self):
        first = self.router.slots[0]
        second = CodexSlot(1, token='other', auth=CodexAuth('other', account_id='other'))
        self.router.slots.append(second)
        async def load(slot):
            if slot is second:
                first.auth = CodexAuth('replacement', account_id='replacement')
            return slot.auth
        with patch.object(self.router, 'load_auth', new=load):
            detail = await self.router.model_selection_diagnostics(set(), set(), MODEL)
        self.assertEqual(detail['operational_eligible'], 1)
        self.assertEqual(detail['slots'][0]['slot_index'], 1)


if __name__ == '__main__':
    unittest.main()
