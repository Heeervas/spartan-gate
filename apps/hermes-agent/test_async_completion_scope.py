import asyncio
import contextlib
from contextvars import ContextVar
from enum import Enum
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('completion_patch', BASE / 'patches/patch_async_completion_scope.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
SOURCE = (BASE / 'fixtures/async_completion_scope.py').read_text()


class Platform(Enum):
    API_SERVER = 'api_server'


class CompletionScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.home = ContextVar('test_completion_home', default='default')
        self.calls = []
        self.stores = {}
        for name in ['default', 'builder', 'other']:
            db = sqlite3.connect(':memory:', check_same_thread=False)
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY); CREATE TABLE deliveries(sid TEXT REFERENCES sessions(id));')
            self.stores[name] = db
            self.addCleanup(db.close)
        self.stores['builder'].execute("INSERT INTO sessions VALUES ('synthetic-sid')")

        @contextlib.asynccontextmanager
        async def runtime_scope(home):
            token = self.home.set(str(home))
            try:
                yield
            finally:
                self.home.reset(token)

        def owned(config, profile, sid):
            return bool(self.stores[profile].execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone())

        async def persist(adapter, *, text, session_id, evt):
            self.calls.append(('persist', self.home.get()))
            if getattr(self, 'fail_persist', False):
                raise OSError('synthetic persistence failure')
            self.stores[self.home.get()].execute('INSERT INTO deliveries VALUES (?)', (session_id,))

        async def forbidden_wake(*args, **kwargs):
            raise AssertionError('delegation must not start a model turn')

        class WakeNotAccepted(Exception):
            pass

        modules = {}
        for name, attrs in {
            'gateway.run': {'_multiplex_profile_homes': lambda config: [(x, x) for x in self.stores], '_async_profile_runtime_scope': runtime_scope},
            'gateway.wake': {'session_owned_by_profile': owned, 'persist_delegation_delivery': persist, 'deliver_wake': forbidden_wake, 'WakeNotAccepted': WakeNotAccepted,
                             'adapter_supports_push': lambda adapter: False, 'admit_internal_event': forbidden_wake},
            'hermes_constants': {'get_hermes_home_override': self.home.get},
            'tui_gateway.launch_profile_policy': {'async_launch_profile_scope_if_multiplexed': lambda: runtime_scope('default')},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            modules[name] = module
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.evt = {'type': 'async_delegation', 'origin_session_id': 'synthetic-sid', 'platform': 'api_server', 'delegation_id': 'synthetic-delegation'}

    def runner(self, repaired=True):
        namespace = {'contextlib': contextlib, 'asyncio': asyncio, 'Optional': __import__('typing').Optional,
                     'logger': __import__('logging').getLogger('completion-test'), 'Platform': Platform}
        exec(PATCH.patch_text(SOURCE) if repaired else SOURCE, namespace)
        runner = namespace['Runner']()
        runner.config = SimpleNamespace(multiplex_profiles=True)
        runner._build_process_event_source = lambda evt: getattr(self, 'source', None)
        runner._resolve_profile_home_for_source = lambda source: source.profile
        runner._completion_delivery_identity = lambda evt: None
        runner._CompletionClaim = lambda: SimpleNamespace(claim_id='', delegation_id='', proceed=True)
        runner.adapters = {Platform.API_SERVER: SimpleNamespace(_ensure_session_db=lambda: object())}

        async def preflight(evt):
            self.assertTrue(await runner._completion_delivery_ready(evt))
            self.calls.append(('claim', self.home.get()))
            return SimpleNamespace(claim_id='synthetic-claim', delegation_id=evt['delegation_id'], proceed=True)

        runner._preflight_completion_delivery = preflight
        runner._settle_durable_claim = lambda operation, *args: self.calls.append((operation, self.home.get()))
        return runner

    async def test_native_bug_reproduces_then_whole_lifecycle_uses_owner(self):
        self.assertFalse(await self.runner(False)._deliver_completion_notification('synthetic-result', self.evt))
        self.assertEqual(self.calls, [('claim', 'default'), ('persist', 'default'), ('release', 'default')])
        self.calls.clear()
        self.assertTrue(await self.runner()._deliver_completion_notification('synthetic-result', self.evt))
        self.assertEqual(self.calls, [('claim', 'builder'), ('persist', 'builder'), ('complete', 'builder')])
        self.assertEqual(self.home.get(), 'default')
        self.assertEqual(self.stores['default'].execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    async def test_failed_persistence_releases_claim_in_same_owner(self):
        self.fail_persist = True
        self.assertFalse(await self.runner()._deliver_completion_notification('synthetic-result', self.evt))
        self.assertEqual(self.calls, [('claim', 'builder'), ('persist', 'builder'), ('release', 'builder')])
        self.assertEqual(self.home.get(), 'default')

    async def test_missing_or_ambiguous_owner_fails_before_claim(self):
        runner = self.runner()
        self.stores['builder'].execute('DELETE FROM sessions')
        with self.assertRaises(LookupError):
            await runner._deliver_completion_notification('synthetic-result', self.evt)
        for name in ['default', 'builder']:
            self.stores[name].execute("INSERT INTO sessions VALUES ('synthetic-sid')")
        with self.assertRaises(LookupError):
            await runner._deliver_completion_notification('synthetic-result', self.evt)
        self.assertEqual(self.calls, [])

    async def test_cached_source_cannot_disambiguate_client_chosen_session_id(self):
        self.stores['default'].execute("INSERT INTO sessions VALUES ('synthetic-sid')")
        for cached_profile in ['default', 'builder', 'other']:
            self.source = SimpleNamespace(profile=cached_profile)
            with self.assertRaises(LookupError):
                await self.runner()._deliver_completion_notification('synthetic-result', self.evt)
        self.assertEqual(self.calls, [])

    async def test_raw_api_delivery_ignores_ambient_cached_messaging_origin(self):
        self.source = SimpleNamespace(profile='default', platform='discord', chat_id='unrelated')
        self.assertTrue(await self.runner()._deliver_completion_notification('synthetic-result', self.evt))
        self.assertEqual(self.calls, [('claim', 'builder'), ('persist', 'builder'), ('complete', 'builder')])

    async def test_direct_api_persistence_is_scoped_and_rejects_mismatched_identity(self):
        runner = self.runner()
        self.assertTrue(await runner._self_post_api_server(None, 'synthetic-result', 'synthetic-sid', self.evt))
        self.assertEqual(self.calls, [('persist', 'builder')])
        self.calls.clear()
        self.assertFalse(await runner._self_post_api_server(None, 'synthetic-result', 'different-sid', self.evt))
        self.assertEqual(self.calls, [])

    async def test_patch_is_idempotent_and_rejects_partial_or_drifted_input(self):
        result = PATCH.patch_text(SOURCE)
        self.assertEqual(PATCH.patch_text(result), result)
        for source in [SOURCE.replace(PATCH.ANCHOR, '    def changed(self, evt):'), result.replace('scope = self._completion_event_scope(evt)', 'scope = None')]:
            with self.assertRaises(ValueError):
                PATCH.patch_text(source)


if __name__ == '__main__':
    unittest.main()
