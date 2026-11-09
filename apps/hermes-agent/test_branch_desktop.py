"""Desktop branch owner checks against exact native source, never an active profile.

The provider boundary is synthetic; SessionDB, branch mutation, admission and
connection authorization are native. Renderer checks are a separate native gate.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')


class DesktopSourceTests(unittest.TestCase):
    @unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE to pristine pinned Hermes')
    def test_native_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'TZ', 'HERMES_UPSTREAM_SOURCE', 'BRANCH_DESKTOP_ACK_RED')}
            env.update(HOME=tmp, HERMES_HOME=str(Path(tmp) / 'home'),
                       XDG_CONFIG_HOME=str(Path(tmp) / 'config'), XDG_DATA_HOME=str(Path(tmp) / 'data'),
                       XDG_CACHE_HOME=str(Path(tmp) / 'cache'), TMPDIR=tmp, PYTHONDONTWRITEBYTECODE='1')
            if os.environ.get('HERMES_NATIVE_TEST_DEPS'):
                env['PYTHONPATH'] = str(Path(os.environ['HERMES_NATIVE_TEST_DEPS']).resolve())
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--native', tmp],
                                    env=env, capture_output=True, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout + result.stderr)


if __name__ == '__main__' and '--native' in sys.argv:
    del DesktopSourceTests  # do not recursively run the wrapper in the native child
    import asyncio
    import socket
    from types import SimpleNamespace
    from unittest.mock import patch

    home = Path(sys.argv[-1])
    root = home / 'native'
    shutil.copytree(SOURCE, root, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    spec = importlib.util.spec_from_file_location('branch_patch', HERE / 'patches/patch_branch_navigation.py')
    patcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patcher)
    patcher.apply(root)
    if os.environ.get('BRANCH_DESKTOP_ACK_RED') == '1':
        for name in ('gateway/run_turn_runner.py', 'gateway/session_kanban.py'):
            shutil.copyfile(Path(SOURCE) / name, root / name)
    for name in ('spartan_branch_navigation.py', 'spartan_branch_gateway.py', 'spartan_branch_desktop.py'):
        if (HERE / name).exists():
            shutil.copyfile(HERE / name, root / name)
    sys.path.insert(0, str(root))
    socket.socket.connect = lambda *a: (_ for _ in ()).throw(AssertionError('network forbidden'))
    # Optional Git/tool inventory sees an unavailable executable; no real worker/tool
    # subprocess is needed by these in-process provider fixtures.
    subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(OSError('fixture forbids subprocess'))
    from gateway.session_contract import SessionRef
    import gateway.session_controls
    import hermes_state
    for module in (gateway.session_controls, hermes_state):
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
    from tests.gateway.test_session_local_reopen_on_turn import _ended_local_session
    import pytest
    import spartan_branch_navigation as nav

    class DesktopJourneys(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.tmp = tempfile.TemporaryDirectory(dir=home)
            self.folder = Path(self.tmp.name)
            self.mp = pytest.MonkeyPatch()
            self.mp.setenv('HERMES_HOME', str(self.folder))
            self.authority, self.connection, self.parent = await _ended_local_session(self.folder, self.mp)
            self.db = self.authority.db
            # The upstream ended-row helper intentionally has no adapter lookup. This journey
            # executes turns, so supply the runner's real native local adapter and loader signature.
            self.authority.runner._adapter_for_source = lambda source: self.authority.runner.adapters.get(source.platform)
            self.mp.setattr('gateway.run._load_gateway_config', lambda *a: {'platform_toolsets': {'cli': []}})
            self.schedule = self.authority._schedule
            self.authority._schedule = lambda ref: None  # tests explicitly enable native drains
            self.summary_rows = []
            def summarize(db, child, metadata, runtime=None):
                self.summary_rows.extend(nav.branch_messages(db, child, metadata))
                return 'Child-only result.'
            self.mp.setattr(nav, 'summarize_branch', summarize)
            self.mp.setattr('gateway.session_llm_oneshot._session_runtime', lambda *a: lambda: {'model': 'synthetic'})
            await self.connection.resume(self.parent, {})

        async def asyncTearDown(self):
            await self.connection.close()
            self.db.close()
            self.mp.undo()
            self.tmp.cleanup()

        async def branch(self, request='branch'):
            row = self.db.get_session(self.parent.session_id)
            result = await self.connection.mutate(self.parent, dict(session_id=self.parent.session_id,
                request_id=request, expected_revision=row['runtime_revision'],
                expected_generation=row['runtime_generation'], operation='branch', payload={}))
            return SessionRef(self.parent.profile_id, result['branched_session_id'])

        async def test_canonical_branch_records_actual_seed_and_retry_preserves_status(self):
            child = await self.branch()
            owner, metadata = nav.navigation_owner(self.db, child.session_id)
            self.assertEqual(metadata['parent'], self.parent.session_id)
            self.assertEqual(metadata['inherited'], [r['message_uid'] for r in self.db.get_messages(child.session_id)])
            nav.set_status(self.db, owner, metadata, 'completed')
            # A lost branch reply replays its native receipt; never resets a returned branch.
            result = await self.connection.mutate(self.parent, dict(session_id=self.parent.session_id,
                request_id='branch', expected_revision=0, expected_generation=0, operation='branch', payload={}))
            self.assertEqual(result['branched_session_id'], child.session_id)
            self.assertEqual(nav.navigation_owner(self.db, child.session_id)[1]['status'], 'completed')

        def enable_provider(self, *, fail=False):
            from gateway.run_turn_runner import TurnRunner
            from gateway.session_results import execution_result
            calls = []
            class Agent:
                tools = [{'type': 'function', 'function': {'name': 'terminal'}}]
                valid_tool_names = {'terminal'}
                _notification_reply_muted = False
                quiet_mode, verbose_logging = True, False
                def run_conversation(agent, text, **kwargs):
                    calls.append((text, list(agent.tools), set(agent.valid_tool_names)))
                    self.db.append_message(agent.session_id, 'user', text)
                    if fail:
                        return dict(final_response='Synthetic failure', completed=False, failed=True)
                    if nav.acknowledgment_only():
                        from agent.turn_tool_round import run_tool_round
                        import inspect
                        args = {key: None for key in inspect.signature(run_tool_round).parameters if key != 'agent'}
                        with self.assertRaisesRegex(RuntimeError, 'cannot execute tools'):
                            run_tool_round(agent, **args)
                    self.db.append_message(agent.session_id, 'assistant', 'Acknowledged.')
                    return dict(final_response='Acknowledged.', completed=True)
            agent = Agent()
            async def handle(event):
                # Native ingress, finite execution scope and TurnRunner own the path to this synthetic provider.
                agent.session_id = event.source.chat_id
                ctx = SimpleNamespace(session_key=self.authority.sessions[agent.session_id].route,
                    session_id=agent.session_id, source=event.source, message=event.text,
                    title_user_message=None, persist_user_display_kind=None, persist_user_display_metadata=None,
                    moa_config=None, inbound_message_id=event.message_id, mute_notification_reply=False,
                    reply_expected=True, stream_consumer_holder=[None])
                turn = TurnRunner(SimpleNamespace(), ctx)
                turn._native_image_run_message = lambda: event.text
                turn._approval_notify_sync = lambda *a: None
                result = await asyncio.to_thread(turn._run_conversation_with_approval, agent, [], None, None, None)
                execution_result.get()['result'] = result
                return result['final_response']
            self.authority.runner._handle_message = handle
            self.authority._schedule = self.schedule
            self.provider_agent, self.provider_calls = agent, calls

        async def test_authority_return_real_admission_execution_and_next_turn_tools(self):
            import spartan_branch_desktop as desktop
            from gateway.session_contract import Submission
            child = await self.branch()
            self.db.append_message(child.session_id, 'user', 'Added child work')
            self.db.append_message(self.parent.session_id, 'assistant', 'Parent advanced')
            sentinel = self.folder / 'effect.txt'
            sentinel.write_text('retained')
            child_before = self.db.get_messages(child.session_id)
            self.enable_provider()
            result = await desktop.authority_navigate(self.connection, child)
            self.assertEqual(result, dict(status='completed', parent_session_id=self.parent.session_id))
            self.assertEqual([r['content'] for r in self.summary_rows], ['Added child work'])
            self.assertEqual(self.provider_calls[0][1:], ([], set()))
            self.assertEqual(self.provider_agent.valid_tool_names, {'terminal'})
            self.assertEqual(self.db.get_messages(child.session_id), child_before)
            self.assertEqual(sentinel.read_text(), 'retained')
            from hermes_state_runtime import RuntimeStoreError
            with self.assertRaisesRegex(RuntimeStoreError, 'branch_navigation_refused'):
                await desktop.authority_navigate(self.connection, child)
            self.assertEqual(len(self.provider_calls), 1)
            receipt = await self.authority.submit(self.connection.actor, Submission('ordinary', self.parent,
                {'text': 'Ordinary parent tools restored'}, 'queue'))
            await self.authority.sessions[self.parent.session_id].task
            self.assertEqual(self.provider_calls[-1][2], {'terminal'})
            self.assertEqual(sum('Child-only result.' in str(r['content']) for r in self.db.get_messages(self.parent.session_id)), 1)

        async def test_cancel_no_import_and_metadata_summary_busy_owner_failures(self):
            import dataclasses
            import spartan_branch_desktop as desktop
            from gateway.session_contract import Submission
            from gateway.session_controls import AuthorityConnection
            from hermes_state_runtime import RuntimeStoreError
            child = await self.branch()
            self.db.append_message(child.session_id, 'user', 'Child effect')
            parent_before = self.db.get_messages(self.parent.session_id)
            with patch.object(nav, 'summarize_branch', side_effect=nav.NavigationError('summary failed')):
                with self.assertRaisesRegex(RuntimeStoreError, 'branch_navigation_refused'):
                    await desktop.authority_navigate(self.connection, child)
            self.assertEqual(nav.navigation_owner(self.db, child.session_id)[1]['status'], 'open')
            self.assertEqual(self.db.get_messages(self.parent.session_id), parent_before)
            # A queued parent is genuinely busy in the native ledger, not a mocked boolean.
            await self.authority.submit(self.connection.actor, Submission('busy', self.parent, {'text': 'queued'}, 'queue'))
            with self.assertRaisesRegex(RuntimeStoreError, 'session_busy'):
                await desktop.authority_navigate(self.connection, child)
            pending = (await self.authority.attach(self.connection.actor, self.parent)).pending[0]
            await self.authority.cancel_queued(self.connection.actor, self.parent, pending.admission_id)
            foreign = AuthorityConnection(self.authority, object(), {'user_id': 'other'})
            foreign.subscriptions[child.session_id] = 'not-a-grant'
            with self.assertRaisesRegex(RuntimeStoreError, 'permission_denied'):
                await desktop.authority_navigate(foreign, child, cancel=True)
            await foreign.close()
            with self.assertRaisesRegex(RuntimeStoreError, 'profile_mismatch'):
                await desktop.authority_navigate(self.connection, dataclasses.replace(child, profile_id='foreign'), cancel=True)
            result = await desktop.authority_navigate(self.connection, child, cancel=True)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual(self.db.get_messages(self.parent.session_id), parent_before)
            with self.assertRaisesRegex(RuntimeStoreError, 'branch_navigation_refused'):
                await desktop.authority_navigate(self.connection, self.parent, cancel=True)

        async def test_failed_ack_is_not_success_and_cannot_reimport(self):
            import spartan_branch_desktop as desktop
            from hermes_state_runtime import RuntimeStoreError
            child = await self.branch()
            self.db.append_message(child.session_id, 'user', 'Added work')
            self.enable_provider(fail=True)
            with self.assertRaisesRegex(RuntimeStoreError, 'branch_navigation_refused'):
                await desktop.authority_navigate(self.connection, child)
            self.assertEqual(nav.navigation_owner(self.db, child.session_id)[1]['status'], 'failed')
            with self.assertRaisesRegex(RuntimeStoreError, 'branch_navigation_refused'):
                await desktop.authority_navigate(self.connection, child)
            self.assertEqual(len(self.provider_calls), 1)

        async def test_native_deferred_rpc_and_closed_parameter_contract(self):
            child = await self.branch()
            replies = asyncio.Queue()
            self.connection.transport = SimpleNamespace(write_async=replies.put)
            refused = await self.connection.dispatch({'id': 'invalid', 'method': 'session.branch_cancel',
                'params': {'session_id': child.session_id, 'arbitrary': True}})
            self.assertIn('error', refused)
            reply = await self.connection.dispatch({'id': 'cancel', 'method': 'session.branch_cancel',
                'params': {'session_id': child.session_id, 'profile': 'default'}})
            self.assertIsNone(reply)  # the serial socket lane is released while work runs
            result = await asyncio.wait_for(replies.get(), 5)
            self.assertEqual(result['result'], dict(status='cancelled', parent_session_id=self.parent.session_id))

        async def test_native_managed_bootstrap_carries_ack_guard_and_restores_next_turn(self):
            import contextlib
            from gateway.session_policy import policy_for_source
            from gateway.session_managed_worker import _bootstrap, worker_turn_scope
            from gateway.session_kanban import run_worker_turns
            policy = policy_for_source(self.authority.runner, self.authority.sessions[self.parent.session_id].source)
            scope = {'execution_id': 'managed-ack', 'session_id': self.parent.session_id}
            with patch('gateway.session_managed_worker._worker_config', return_value=policy.config()), nav.acknowledgment_scope(True):
                frame = _bootstrap(self.authority, self.parent, {'payload': {'text': 'Handoff'}}, policy, scope)
            class Agent:
                tools, valid_tool_names = [{'name': 'terminal'}], {'terminal'}
                def run_conversation(agent, text, **kwargs):
                    return list(agent.tools), set(agent.valid_tool_names), nav.acknowledgment_only()
            agent = Agent()
            with patch('gateway.session_kanban._worker_workspace', return_value=contextlib.nullcontext(None)):
                self.assertEqual(run_worker_turns(agent, frame, []), ([], set(), True))
                self.assertEqual(agent.valid_tool_names, {'terminal'})
                import json
                request = json.loads(frame['policy']['request_json'])
                request['turn_v1']['spartan_branch_ack'] = False
                frame['policy']['request_json'] = json.dumps(request)
                self.assertEqual(run_worker_turns(agent, frame, [])[1:], ({'terminal'}, False))
                request['turn_v1']['spartan_branch_ack'] = 'true'
                frame['policy']['request_json'] = json.dumps(request)
                with self.assertRaisesRegex(ValueError, 'invalid_managed_worker_bootstrap'):
                    with worker_turn_scope(frame):
                        self.fail('Malformed trusted flag was accepted')

    class LegacyPersistence(unittest.TestCase):
        def setUp(self):
            import contextlib
            from tui_gateway import server
            from hermes_state import SessionDB
            self.server = server
            self.tmp = tempfile.TemporaryDirectory(dir=home)
            self.folder = Path(self.tmp.name)
            self.db = SessionDB(self.folder / 'state.db')
            self.db.create_session('parent', 'desktop')
            self.db.append_message('parent', 'user', 'Inherited context')
            self.patches = [patch.object(server, '_session_db', lambda session: contextlib.nullcontext(self.db))]
            for p in self.patches:
                p.start()

        def tearDown(self):
            for p in reversed(self.patches):
                p.stop()
            self.db.close()
            self.tmp.cleanup()

        def test_legacy_seed_commit_and_lazy_fallback_do_not_swallow_new_prompt(self):
            import threading
            s = self.server
            history = self.db.get_messages_as_conversation('parent')
            s._persist_branch(self.db, 'child', 'parent', 'Branch', history,
                source='desktop', cwd=str(self.folder), profile_name='default', model='synthetic')
            self.assertEqual(nav.navigation_owner(self.db, 'child')[1]['inherited'],
                [r['message_uid'] for r in self.db.get_messages('child')])
            self.db.create_session('lazy', 'desktop', model_config={'_branched_from': 'parent'})
            record = dict(session_key='lazy', parent_session_id='parent', seeded=True,
                          history=self.db.get_messages_as_conversation('parent'), history_lock=threading.Lock())
            s._persist_branch_seed(record)
            self.db.append_message('lazy', 'user', 'First new prompt')
            self.assertEqual([r['content'] for r in nav.branch_messages(self.db, 'lazy', nav.navigation_owner(self.db, 'lazy')[1])], ['First new prompt'])
            self.db.create_session('not-branch', 'desktop')
            ordinary = dict(session_key='not-branch', seeded=True, history=[{'role': 'user', 'content': 'seed'}], history_lock=threading.Lock())
            s._persist_branch_seed(ordinary)
            self.assertIsNone(self.db.get_session_model_config_value('not-branch', nav.NAV_KEY))

        def test_failed_lazy_seed_refuses_new_prompt_before_it_can_become_inherited(self):
            import threading
            s = self.server
            self.db.create_session('failed-seed', 'desktop')
            record = dict(session_key='failed-seed', parent_session_id='parent', seeded=True,
                history=[{'role': 'user', 'content': 'seed'}], history_lock=threading.Lock())
            with patch.object(s, '_ensure_session_db_row', return_value=True), \
                    patch.object(self.db, 'append_messages_batch', side_effect=RuntimeError('Synthetic seed failure')):
                response = s._persist_session_row_for_submit('first', record, 'First added work')
            self.assertIn('error', response)
            self.assertEqual(self.db.get_messages('failed-seed'), [])
            self.assertIsNone(self.db.get_session_model_config_value('failed-seed', nav.NAV_KEY))

        def dispatch_request(self, method, rid, params):
            """Exercise the real dispatcher and its bound response transport, not handler calls."""
            import threading
            s = self.server
            replies, done = [], threading.Event()
            def write(response):
                replies.append(response)
                done.set()
                return True
            with patch.object(type(s._stdio_transport), 'write', lambda transport, response: write(response)):
                response = s.dispatch({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
                self.assertIsNone(response, 'Long navigation must release the native serial RPC reader')
                self.assertTrue(done.wait(10), 'Native worker did not answer its bound transport')
            return replies[0]

        def test_legacy_live_branch_return_cancel_and_native_prompt_execution(self):
            import contextlib
            import threading
            import spartan_branch_desktop as desktop
            s, db = self.server, self.db
            mp = pytest.MonkeyPatch()
            calls = []
            class Agent:
                tools, valid_tool_names = [{'name': 'terminal'}], {'terminal'}
                _notification_reply_muted = False
                quiet_mode, verbose_logging = True, False
                def __init__(agent, key):
                    agent.session_id, agent._session_db = key, db
                def run_conversation(agent, text, conversation_history=None, **kwargs):
                    calls.append((text, list(agent.tools), set(agent.valid_tool_names)))
                    # Native prompt.submit already wrote its user row.
                    db.append_message(agent.session_id, 'assistant', 'Acknowledged.')
                    return dict(final_response='Acknowledged.', messages=db.get_messages_as_conversation(agent.session_id), completed=True)
            def record(sid, key, history):
                rec = s._deferred_session_record(key, cols=80, cwd=str(self.folder), history=history,
                    lease=None, source='desktop', profile_home=None)
                rec['agent'], rec['agent_ready'] = Agent(key), threading.Event()
                rec['agent_ready'].set()
                s._sessions[sid] = rec
                return rec['agent']
            def build(parent, sid, key, history, source):
                return record(sid, key, history)
            def invoke(rid, sid, session, text, **kwargs):
                # Synthetic provider assembly only: execute the native invocation seam on its actual worker input.
                agent = session['agent']
                st = s._TurnRun(agent=agent, one_turn_restore=None, terminal_callback=None, receipt_committed=True)
                st.history = list(session['history'])
                try:
                    s._invoke_agent(sid, session, st, text, text, None, [],
                        kwargs.get('display_kind'), kwargs.get('display_metadata'), text=text)
                    session['history'] = st.result['messages']
                    return True
                finally:
                    session['running'] = False
            try:
                mp.setattr(s, '_sessions', {})
                mp.setattr(s, '_get_db', lambda: db)
                mp.setattr(s, '_build_branch_agent', build)
                mp.setattr(s, '_session_info', lambda *a: {})
                mp.setattr(s, '_fallback_session_info', lambda *a: {})
                mp.setattr(s, '_run_prompt_submit', invoke)
                mp.setattr(s, '_load_interim_assistant_messages', lambda: False)
                mp.setattr(s, '_start_usage_ticker', lambda *a: (SimpleNamespace(set=lambda: None), SimpleNamespace(join=lambda: None)))
                mp.setattr(s, '_emit', lambda *a: None)
                mp.setattr(s, '_load_dashboard_process_isolation_config', lambda: {'enabled': False})
                mp.setattr(nav, 'summarize_branch', lambda db, child, metadata, runtime=None: 'Legacy child result.')
                record('parent-ui', 'parent', db.get_messages_as_conversation('parent'))
                branch = s._branch_live('branch', {}, s._sessions['parent-ui'])
                self.assertNotIn('error', branch, branch)
                child_sid = branch['result']['session_id']
                child_id = branch['result']['stored_session_id']
                db.append_message(child_id, 'user', 'New work')
                s._sessions[child_sid]['history'] = db.get_messages_as_conversation(child_id)
                child_before = db.get_messages(child_id)
                response = self.dispatch_request('session.branch_return', 'return', {'session_id': child_sid})
                self.assertNotIn('error', response, response)
                self.assertEqual(response['result']['parent_session_id'], 'parent')
                self.assertEqual(calls[0][1:], ([], set()))
                self.assertEqual(s._sessions['parent-ui']['agent'].valid_tool_names, {'terminal'})
                self.assertEqual(db.get_messages(child_id), child_before)
                repeated = desktop.legacy_navigate(s, False, 'again', {'session_id': child_sid})
                self.assertIn('not importing again', repeated['error']['message'])
                other = s._branch_live('cancel-branch', {}, s._sessions['parent-ui'])['result']
                count = len(db.get_messages('parent'))
                cancelled = self.dispatch_request('session.branch_cancel', 'cancel', {'session_id': other['session_id']})
                self.assertNotIn('error', cancelled, cancelled)
                self.assertEqual(len(db.get_messages('parent')), count)
            finally:
                mp.undo()

    unittest.main(argv=[sys.argv[0]], verbosity=2)
elif __name__ == '__main__':
    unittest.main()
