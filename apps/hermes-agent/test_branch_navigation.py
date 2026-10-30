"""Pinned-source native branch commands, persistence, queues and tool-denial checks.

No model, Discord or active HOME: run the native collaborators in a fresh process
with a synthetic agent/provider and Discord-shaped adapter. The terminal command
methods are AST-loaded to avoid importing the terminal UI's optional Rich stack.
"""
import ast
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).parent.resolve()
SOURCE = os.environ.get("HERMES_UPSTREAM_SOURCE")


def load_patch():
    spec = importlib.util.spec_from_file_location("branch_patch", HERE / "patches/patch_branch_navigation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchTests(unittest.TestCase):
    @unittest.skipUnless(SOURCE, "set HERMES_UPSTREAM_SOURCE to the pinned source")
    def test_preflight_idempotence_and_drift_before_any_write(self):
        patcher = load_patch()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in patcher.EDITS:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(SOURCE) / name, target)
            patcher.apply(root)
            first = {name: (root / name).read_bytes() for name in patcher.EDITS}
            patcher.apply(root)
            self.assertEqual(first, {name: (root / name).read_bytes() for name in patcher.EDITS})
            # A late target drifts while earlier files are pristine: preflight must write nothing.
            for name in patcher.EDITS:
                shutil.copyfile(Path(SOURCE) / name, root / name)
            last = root / "tools/mcp_tool_agent.py"
            last.write_text(last.read_text().replace('    db = getattr(agent, "_session_db", None)',
                                                      '    db = None'))
            before = {name: (root / name).read_bytes() for name in patcher.EDITS}
            with self.assertRaisesRegex(ValueError, "anchor changed"):
                patcher.apply(root)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in patcher.EDITS})

    @unittest.skipUnless(SOURCE, "set HERMES_UPSTREAM_SOURCE to the pinned source")
    def test_native_journeys(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home / "hermes"),
                   "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            # The native providers are never initialized; reject network even if a fixture regresses.
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--native", str(home)],
                                    env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout + result.stderr)


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--native':
    # Standalone fixture entry: native imports and test classes exist only in this
    # fresh synthetic process, never in the repository-wide unittest loader.
    home = Path(sys.argv[2])
    import asyncio
    import contextlib
    import dataclasses
    from datetime import datetime
    import json
    import socket
    from types import SimpleNamespace, ModuleType
    from unittest.mock import patch

    root = home / "native"
    shutil.copytree(SOURCE, root, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    load_patch().apply(root)
    if os.environ.get("BRANCH_TEST_TOOL_GUARD_RED") == "1":
        shutil.copyfile(Path(SOURCE) / "agent/turn_tool_round.py", root / "agent/turn_tool_round.py")
    for name in ("spartan_branch_navigation.py", "spartan_branch_gateway.py"):
        shutil.copyfile(HERE / name, root / name)
    sys.path.insert(0, str(root))
    # No host credentials/provider transport or accidental public traffic in these synthetic tests.
    socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError("network forbidden"))
    from hermes_state import SessionDB, AsyncSessionDB
    from gateway.config import Platform, GatewayConfig
    from gateway.session import SessionStore, AsyncSessionStore, SessionSource, build_session_key
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.slash_commands_session import GatewaySessionCommandsMixin
    from gateway.run_busy import GatewayBusySessionMixin
    from gateway.run_turn import GatewayTurnMixin
    from gateway.run_turn_runner import TurnRunner
    from agent.message_metadata import message_identity
    import spartan_branch_navigation as nav
    import spartan_branch_gateway as gate

    def db_at(path):
        return SessionDB(path)

    class Agent:
        """Synthetic provider boundary; native command/session/approval plumbing is real."""
        def __init__(self, db):
            self._session_db = db
            self.session_id = "parent"
            self.tools = [{"type": "function", "function": {"name": "terminal"}}]
            self.valid_tool_names = {"terminal"}
            self._last_flushed_db_idx = 0
            self._cached_system_prompt = "Synthetic system context"
            self._notification_reply_muted = False
            self.quiet_mode, self.verbose_logging = True, False
            self.calls = []

        def reset_session_state(self):
            pass

        def _flush_messages_to_session_db(self, messages, **kwargs):
            pass  # Test authored rows are already canonical, not a mock persistence result.

        def run_conversation(self, user_message, conversation_history=None, **kwargs):
            self.calls.append((user_message, list(self.tools), set(self.valid_tool_names)))
            self._session_db.append_message(self.session_id, "user", user_message,
                display_kind=kwargs.get("persist_user_display_kind"),
                display_metadata=kwargs.get("persist_user_display_metadata"))
            if getattr(self, "fail_ack", False):
                return {"final_response": "Synthetic provider failure", "completed": False, "failed": True,
                        "messages": self._session_db.get_messages_as_conversation(self.session_id)}
            self._session_db.append_message(self.session_id, "assistant", "Received the branch findings.")
            return {"final_response": "Received the branch findings.", "completed": True,
                    "messages": self._session_db.get_messages_as_conversation(self.session_id)}

    class Runner(GatewaySessionCommandsMixin, GatewayBusySessionMixin, GatewayTurnMixin):
        _BUSY_QUEUE_MAX_PENDING = 32
        _MAX_INTERRUPT_DEPTH = 10

        def __init__(self, folder):
            self.config = {}
            with patch.dict(os.environ, {"HERMES_HOME": str(folder)}):
                self.session_store = SessionStore(folder, GatewayConfig())
                self.session_store._db = self.session_store._db
            self.async_session_store = AsyncSessionStore(self.session_store)
            self._session_db = AsyncSessionDB(self.session_store._db)
            self.adapter = SimpleNamespace(send=self.send, create_handoff_thread=self.create_thread,
                handle_message=self.handle, _pending_messages={})
            self.notices, self.calls, self.pending = [], [], []
            self.busy = False
            self.counter = 0
            self.authorized = True

        def _get_proxy_url(self):
            return None

        def _session_key_for_source(self, source):
            return build_session_key(source)

        def _delivery_adapter_for(self, source):
            return self.adapter

        def _is_user_authorized_for_source(self, source, **kwargs):
            return self.authorized

        def _profile_scope_for_source(self, source):
            return contextlib.nullcontext()

        def _clear_session_boundary_security_state(self, key):
            pass

        def _evict_cached_agent(self, key):
            pass

        async def create_thread(self, parent, title):
            self.counter += 1
            return "child-" + str(self.counter)

        async def send(self, chat, text, **kwargs):
            self.notices.append((chat, text))
            return SimpleNamespace(success=True)

        async def handle(self, event):
            if self.busy:
                # Real native FIFO helper exercised below; the adapter accepts the event.
                self._queue_or_replace_pending_event(self._session_key_for_source(event.source), event)
                return
            await self.execute(event)

        def _queue_depth(self, key, **kwargs):
            return len(self.pending)

        def _enqueue_fifo(self, key, event, adapter):
            self.pending.append(event)
            event._gateway_accepted = True

        async def execute(self, event):
            resolved = await self._hmwa_resolve_session(event, event.source)
            if resolved is None:
                return
            source, entry, key = resolved
            await self._run_agent(event.text, "synthetic", [], source, entry.session_id,
                session_key=key, persist_user_display_kind="internal_notification",
                persist_user_display_metadata={nav.RETURN_KEY: event.metadata[nav.RETURN_KEY]})

        def _run_agent_display_settings(self, source):
            return SimpleNamespace(_native_slack_task_cards=False, needs_progress_queue=False,
                                   log_mode_enabled=False)

        def _run_agent_build_turn_context(self, disp, agent_type, **call):
            agent = Agent(self.session_store._db)
            agent.session_id = call["session_id"]
            agent.fail_ack = getattr(self, "fail_ack", False)
            ctx = SimpleNamespace(**{**call, "mute_notification_reply": False,
                "result_holder": [None], "stream_consumer_holder": [None],
                "streaming_tts_consumer_holder": [None]})
            turn = TurnRunner(self, ctx)
            turn._native_image_run_message = lambda: ctx.message
            turn._approval_notify_sync = lambda *a: None
            ctx.fixture_agent, ctx.fixture_call, ctx.fixture_turn = agent, call, turn
            return ctx, turn, self.adapter

        def _run_agent_start_turn_worker(self, ctx, run_sync):
            async def invoke():
                agent, turn = ctx.fixture_agent, ctx.fixture_turn
                result = turn._run_conversation_with_approval(agent, ctx.history, None, None, None)
                ctx.result_holder[0] = result
                self.calls.append((ctx.fixture_call, agent.calls, list(agent.tools)))
                return result
            return SimpleNamespace(executor_task=asyncio.create_task(invoke()))

        async def _run_agent_await_turn_worker(self, worker, *args):
            return await worker.executor_task

        async def _noop(self, *args, **kwargs):
            pass

        _run_agent_stream_consumer_task = _run_agent_track_agent = _noop
        _run_agent_monitor_for_interrupt = _run_agent_notify_long_running = _noop
        _run_agent_finalize_streaming_tts = _run_agent_mark_streamed_delivery = _noop
        _run_agent_cleanup_turn_tasks = _run_agent_deliver_first_response = _noop
        _refresh_agent_cache_message_count = _noop
        _run_agent_bind_turn_wiring = lambda *args: None
        _run_agent_start_streaming_tts = lambda *args: None
        _run_agent_evict_on_fallback = lambda *args: None
        _run_agent_schedule_bubble_cleanup = lambda *args: None
        _is_goal_continuation_event = lambda *args: False
        _reply_anchor_for_event = lambda *args: None
        _intake_adapter_for = lambda *args: None
        _pinned_channel_inputs = lambda self, key, prompt, source, **kwargs: (prompt, source)

        async def _prepare_profile_scoped_inbound_message_text(self, **kwargs):
            return kwargs["event"].text

        async def _run_agent_drain_pending(self, *args):
            return None, None

        async def drain(self, event, prior_session):
            ctx = SimpleNamespace(source=event.source, session_id=prior_session,
                session_key=self._session_key_for_source(event.source), run_generation=1,
                _interrupt_depth=0, history=[], _status_thread_metadata=None,
                context_prompt="synthetic", channel_prompt=None, result_holder=[None])
            return await self._run_agent_queued_followup(ctx, self.adapter, event.text, event,
                {"final_response": "Prior parent work"}, {"messages": []}, None)

    # Only heavyweight application assembly is replaced. Native turn-inner, FIFO recursion,
    # worker-completion hook and approval-scoped invocation all execute unchanged.
    shim = ModuleType("gateway.run")
    shim._wrap_current_message_with_observed_context = lambda msg, observed: msg
    shim._preserve_queued_followup_history_offset = lambda first, last: last
    sys.modules["gateway.run"] = shim
    agent_shim = ModuleType("run_agent")
    agent_shim.AIAgent = Agent
    sys.modules["run_agent"] = agent_shim

    def source(chat="parent", thread=None):
        return SessionSource(platform=Platform.DISCORD, chat_id=chat, user_id="operator",
            chat_type="thread" if thread else "group", thread_id=thread,
            parent_chat_id="channel" if thread else None)

    class NativeJourneys(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.tmp = tempfile.TemporaryDirectory(dir=home)
            self.runner = Runner(Path(self.tmp.name))
            self.db = self.runner.session_store._db
            self.parent = source()
            self.entry = await self.runner.async_session_store.get_or_create_session(self.parent)
            self.db.append_message(self.entry.session_id, "user", "Inherited parent secret context")
            self.summary_rows = []
            def summary(db, child, metadata, runtime=None):
                self.summary_rows.extend(nav.branch_messages(db, child, metadata))
                return "Branch verified a useful result."
            self.summary_patch = patch.object(nav, "summarize_branch", summary)
            self.summary_patch.start()

        async def asyncTearDown(self):
            await asyncio.sleep(0)
            self.summary_patch.stop()
            self.db.close()
            self.tmp.cleanup()

        async def branch(self, parent=None, fresh=False):
            event = MessageEvent(text="/start-fresh" if fresh else "/start-branch", source=parent or self.parent)
            reply = await self.runner._handle_branch_command(event)
            self.assertIn("child-", reply)
            child = source("child-" + str(self.runner.counter), "child-" + str(self.runner.counter))
            entry = await self.runner.async_session_store.get_or_create_session(child)
            return child, entry.session_id

        async def wait_dispatch(self):
            for _ in range(20):
                await asyncio.sleep(0.005)

        async def test_discord_in_place_branch_compatibility_without_navigation(self):
            # Native --here, DM and failed-thread fallback remain ordinary forks;
            # the aliases must not turn these existing journeys into refusals.
            for name, command, chat_type in (
                ("here", "/branch --here", "group"),
                ("dm", "/fork", "dm"),
                ("thread-failure", "/start-branch", "group"),
            ):
                with self.subTest(name=name):
                    parent = dataclasses.replace(source("compat-" + name), chat_type=chat_type)
                    original = await self.runner.async_session_store.get_or_create_session(parent)
                    self.db.append_message(original.session_id, "user", "Native fork history")
                    with patch.object(self.runner.adapter, "create_handoff_thread",
                                      side_effect=RuntimeError("synthetic thread creation failure")):
                        await self.runner._handle_branch_command(MessageEvent(text=command, source=parent))
                    child = await self.runner.async_session_store.get_or_create_session(parent)
                    self.assertNotEqual(child.session_id, original.session_id)
                    self.assertEqual([row["content"] for row in self.db.get_messages(child.session_id)],
                                     ["Native fork history"])
                    self.assertEqual(self.db.get_session_model_config_value(child.session_id, "_branched_from"),
                                     original.session_id)
                    self.assertIsNone(self.db.get_session_model_config_value(child.session_id, nav.NAV_KEY))
                    reply = await self.runner._handle_return_command(MessageEvent(text="/return", source=parent))
                    self.assertIn("no navigation parent", reply)

        async def test_discord_A_B_return_summary_ack_and_nested_cancel(self):
            child, child_id = await self.branch()
            self.db.append_message(child_id, "user", "Only branch work")
            self.db.append_message(self.entry.session_id, "assistant", "Parent advanced separately")
            nested, nested_id = await self.branch(child, fresh=True)
            self.assertEqual(self.db.get_messages(nested_id), [])
            self.assertEqual(nav.navigation_owner(self.db, nested_id)[1]["parent"], child_id)
            cancelled = await self.runner._handle_cancel_command(MessageEvent(text="/cancel", source=nested))
            self.assertIn("nothing imported", cancelled)
            await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            await self.wait_dispatch()
            self.assertEqual([row["content"] for row in self.summary_rows], ["Only branch work"])
            parent_rows = self.db.get_messages(self.entry.session_id)
            self.assertEqual(sum("Branch verified" in str(r["content"]) for r in parent_rows), 1)
            self.assertEqual(parent_rows[-1]["content"], "Received the branch findings.")
            self.assertEqual(self.runner.calls[0][1][0][1:], ([], set()))
            self.assertEqual(len(self.runner.calls[0][2]), 1)  # tools restored, not durably disabled
            self.assertEqual(self.runner.calls[0][0]["source"].chat_id, "parent")
            self.assertEqual([chat for chat, _ in self.runner.notices], [child.chat_id, child.chat_id])
            self.assertIn("queued", self.runner.notices[0][1])
            self.assertIn("completed", self.runner.notices[1][1])
            repeat = await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            self.assertIn("not importing again", repeat)
            self.assertEqual(len(self.runner.calls), 1)

        async def test_busy_fifo_compression_and_parent_replacement(self):
            child, child_id = await self.branch()
            self.db.append_message(child_id, "user", "branch result")
            self.runner.busy = True
            reply = await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            self.assertEqual(reply, "")
            await self.wait_dispatch()
            self.assertEqual(len(self.runner.pending), 1, repr(self.runner.notices))
            self.assertEqual(len(self.runner.calls), 0)
            queued = self.runner.pending.pop()
            # Real native compression lineage, not a claimed stale session id.
            parent_id = self.entry.session_id
            self.db.end_session(parent_id, "compression")
            self.db.create_session("compressed-parent", "discord", parent_session_id=parent_id)
            key = self.runner._session_key_for_source(self.parent)
            await self.runner.async_session_store.switch_session(key, "compressed-parent")
            self.runner.busy = False
            # Execute the actual native FIFO recursion, not the initial ingress guard.
            await self.runner.drain(queued, parent_id)
            self.assertEqual(self.runner.calls[0][0]["session_id"], "compressed-parent")
            self.assertEqual(self.runner.calls[0][1][0][1:], ([], set()))
            other, other_id = await self.branch()
            self.db.append_message(other_id, "user", "must not reach replacement")
            self.runner.busy = True
            await self.runner._handle_return_command(MessageEvent(text="/return", source=other))
            await self.wait_dispatch()
            queued = self.runner.pending.pop()
            self.db.create_session("replacement", "discord")
            await self.runner.async_session_store.switch_session(key, "replacement")
            await self.runner.drain(queued, "compressed-parent")
            self.assertEqual(self.db.get_messages("replacement"), [])
            self.assertEqual(len(self.runner.calls), 1)
            self.assertIn("rejected", self.runner.notices[-1][1])

        async def test_acknowledgment_failure_reports_its_own_outcome(self):
            child, child_id = await self.branch()
            self.db.append_message(child_id, "user", "branch result")
            self.runner.fail_ack = True
            await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            await self.wait_dispatch()
            self.assertEqual(nav.navigation_owner(self.db, child_id)[1]["status"], "failed")
            self.assertIn("failed", self.runner.notices[-1][1])
            repeat = await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            self.assertIn("not importing again", repeat)
            self.assertEqual(sum("Branch verified" in str(r["content"]) for r in self.db.get_messages(self.entry.session_id)), 1)

        async def test_nested_return_targets_immediate_parent(self):
            child, child_id = await self.branch()
            nested, nested_id = await self.branch(child, fresh=True)
            self.db.append_message(nested_id, "user", "Nested work")
            await self.runner._handle_return_command(MessageEvent(text="/return", source=nested))
            await self.wait_dispatch()
            self.assertEqual(self.runner.calls[0][0]["session_id"], child_id)
            self.assertEqual(len(self.db.get_messages(self.entry.session_id)), 1)
            self.assertEqual(nav.navigation_owner(self.db, child_id)[1]["parent"], self.entry.session_id)
            self.assertIn("completed", self.runner.notices[-1][1])

        async def test_summary_failure_and_child_compression_boundary(self):
            child, child_id = await self.branch()
            self.db.append_message(child_id, "user", "branch work before compression")
            original = self.db.get_messages_as_conversation(child_id)
            self.db.end_session(child_id, "compression")
            self.db.create_session("compressed-child", "discord", parent_session_id=child_id)
            self.db.append_messages_batch("compressed-child", original)
            self.db.append_message("compressed-child", "assistant", "branch work after compression")
            await self.runner.async_session_store.switch_session(self.runner._session_key_for_source(child), "compressed-child")
            owner, metadata = nav.navigation_owner(self.db, "compressed-child")
            self.assertEqual(owner, child_id)
            self.assertEqual([row["content"] for row in nav.branch_messages(self.db, "compressed-child", metadata)],
                             ["branch work before compression", "branch work after compression"])
            with patch.object(nav, "summarize_branch", side_effect=RuntimeError("synthetic summary outage")):
                reply = await self.runner._handle_return_command(MessageEvent(text="/return", source=child))
            self.assertIn("try /return again", reply)
            self.assertEqual(nav.navigation_owner(self.db, "compressed-child")[1]["status"], "open")
            self.assertEqual(len(self.db.get_messages(self.entry.session_id)), 1)
            self.db.append_message("compressed-child", "user", "branch remains usable")

    class TerminalAndTools(unittest.TestCase):
        def test_native_auxiliary_summary_input_and_failure(self):
            from agent import auxiliary_client
            with tempfile.TemporaryDirectory(dir=home) as tmp:
                db = db_at(Path(tmp)/"summary.db")
                db.create_session("parent", "cli")
                db.create_session("branch", "cli", parent_session_id="parent",
                                  model_config={"_branched_from": "parent"})
                db.append_message("branch", "user", "Inherited context")
                nav.record_navigation(db, "branch", "parent")
                db.append_message("branch", "tool", "Branch tool result", tool_name="terminal")
                answer = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                    message=SimpleNamespace(content="Only branch findings"))])
                with patch.object(auxiliary_client, "call_llm", return_value=answer) as call:
                    owner, metadata = nav.begin_return(db, "branch")
                self.assertEqual(metadata["summary"], "Only branch findings")
                request = call.call_args.kwargs
                self.assertEqual(request["tools"], [])
                self.assertEqual([m["content"] for m in json.loads(request["messages"][1]["content"])],
                                 ["Branch tool result"])
                nav.set_status(db, owner, metadata, "open")
                answer.choices[0].finish_reason = "length"
                with patch.object(auxiliary_client, "call_llm", return_value=answer):
                    with self.assertRaisesRegex(nav.NavigationError, "truncated"):
                        nav.begin_return(db, "branch")
                self.assertEqual(nav.navigation_owner(db, "branch")[1]["status"], "open")
                db.close()

        def test_native_terminal_branch_return_restore_and_fresh(self):
            # Load actual patched terminal command methods, not a reimplementation of switching.
            path = root / "hermes_cli/cli_commands_mixin.py"
            tree = ast.parse(path.read_text())
            wanted = {"_handle_branch_command", "_handle_resume_command", "_handle_start_fresh_command",
                      "_handle_return_command", "_handle_cancel_command"}
            methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in wanted]
            functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
                "_end_current_session", "_sync_agent_to_session", "_without_session_meta"}]
            shell = ModuleType("cli")
            shell._sync_process_session_id = lambda sid: None
            mixin = ModuleType("hermes_cli.cli_commands_mixin")
            output = []
            ns = {"__name__": mixin.__name__, "datetime": datetime, "os": os, "suppress": contextlib.suppress,
                "mint_session_id": lambda now: "child-" + str(len(output)), "_cp": lambda *args: output.extend(args),
                "_t": lambda key, **kwargs: key, "_gt": lambda key, **kwargs: key,
                "_tn": lambda key, count, **kwargs: key, "_command_arg": lambda cmd: cmd.partition(" ")[2],
                "extract_api_content_sidecar": lambda msg: msg.get("api_content"),
                "_BRANCH_COPY_KEYS": ("content", "tool_calls", "tool_call_id"), "message_identity": message_identity}
            exec(compile(ast.fix_missing_locations(ast.Module(body=functions + methods, type_ignores=[])), str(path), 'exec'), ns)
            mixin.__dict__.update(ns)
            with tempfile.TemporaryDirectory(dir=home) as tmp, patch.dict(sys.modules, {"cli": shell, mixin.__name__: mixin}), patch.object(nav, "summarize_branch", return_value="Branch verified a useful result."):
                db = db_at(Path(tmp)/"cli.db")
                db.create_session("parent", "cli")
                db.append_message("parent", "user", "Inherited terminal context")
                cli = SimpleNamespace(_agent_running=False, _session_db=db, session_id="parent",
                    conversation_history=db.get_messages_as_conversation("parent"), agent=Agent(db),
                    model="synthetic", max_turns=5, reasoning_config={})
                for name in wanted:
                    setattr(cli, name, ns[name].__get__(cli))
                cli._resolve_resume_target = lambda sid: (db.get_compression_tip(sid), db.get_session(sid))
                cli._restore_session_cwd = cli._restore_session_yolo = cli._restore_session_model = lambda meta: None
                cli._display_resumed_history = lambda: None
                cli._transfer_session_yolo = lambda *args: None
                cli._resolve_turn_agent_config = lambda msg: {"model": "synthetic", "runtime": {}}
                cli._handle_branch_command("/start-branch")
                child = cli.session_id
                db.append_message(child, "user", "Terminal branch result")
                cli.conversation_history = db.get_messages_as_conversation(child)
                cli._handle_return_command("/return")
                self.assertEqual(cli.session_id, "parent")
                self.assertIn("Received the branch findings.", output)
                self.assertEqual(nav.navigation_owner(db, child)[1]["status"], "completed")
                self.assertEqual(cli.agent.calls[0][1:], ([], set()))
                self.assertEqual(cli.agent.valid_tool_names, {"terminal"})
                cli._handle_start_fresh_command("/start-fresh")
                self.assertEqual(cli.conversation_history, [])
                fresh = cli.session_id
                cli._handle_cancel_command("/cancel")
                self.assertEqual(cli.session_id, "parent")
                self.assertEqual(nav.navigation_owner(db, fresh)[1]["status"], "cancelled")
                self.assertEqual(sum("Branch verified" in str(r["content"]) for r in db.get_messages("parent")), 1)
                db.close()

        def test_native_tool_restore_refresh_pin_and_dispatch_denial(self):
            from agent.conversation_loop import _restore_pinned_tools
            from agent.turn_context import _refresh_mcp_tools_between_turns
            from agent.turn_tool_round import run_tool_round
            from tools.mcp_tool_agent import persist_agent_tool_names
            with tempfile.TemporaryDirectory(dir=home) as tmp:
                db = db_at(Path(tmp)/"tools.db")
                db.create_session("parent", "cli")
                db.update_session_tool_names("parent", {"version": "synthetic", "tools": [{"name": "terminal"}]})
                pin = db.get_session("parent")["tool_names"]
                agent = Agent(db)
                with nav.acknowledgment_turn(agent):
                    _restore_pinned_tools(agent, {"tool_names": pin})
                    _refresh_mcp_tools_between_turns(agent)
                    persist_agent_tool_names(agent)
                    self.assertEqual(agent.tools, [])
                    self.assertEqual(db.get_session("parent")["tool_names"], pin)
                    args = {name: None for name in __import__('inspect').signature(run_tool_round).parameters if name != "agent"}
                    with patch("agent.turn_tool_round.validate_tool_calls", side_effect=AssertionError("tool dispatch path reached")):
                        with self.assertRaisesRegex(RuntimeError, "cannot execute tools"):
                            run_tool_round(agent, **args)
                self.assertEqual(agent.valid_tool_names, {"terminal"})
                self.assertEqual(len(agent.tools), 1)
                # A later ordinary native approval-scoped turn uses normal tools.
                result = agent.run_conversation("normal work")
                self.assertTrue(result["completed"])
                self.assertEqual(agent.calls[-1][2], {"terminal"})
                db.close()

    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(NativeJourneys),
                               unittest.defaultTestLoader.loadTestsFromTestCase(TerminalAndTools)])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())


if __name__ == '__main__':
    unittest.main()
