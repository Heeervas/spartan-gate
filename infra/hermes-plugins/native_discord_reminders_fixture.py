"""Sealed subprocess fixture: native registration/hooks/job store, never a ticker/send."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch
import weakref

SOURCE = Path(os.environ["HERMES_PROGRAM_SOURCE"])
EXPORTS = Path(os.environ["HERMES_REMINDER_SOURCE"])
PACKAGE = Path(__file__).parent / "discord-reminders"
ATTEMPTS = []


def denied(*args, **kwargs):
    ATTEMPTS.append(True)
    raise AssertionError("Network forbidden in reminder fixture")


def functions(file, names, scope):
    tree = ast.parse((SOURCE / file).read_text())
    nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(nodes) == len(names), names
    code = "from __future__ import annotations\n" + ast.unparse(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(code, file, "exec"), scope)
    return scope


def compare_inputs():
    paths = ("cron/jobs.py", "model_tools.py", "gateway/run_turn.py", "gateway/session_identity.py",
             "gateway/session_context.py", "agent/turn_context.py", "hermes_cli/plugins.py",
             "hermes_cli/plugins_dispatch.py", "hermes_cli/middleware.py", "tools/registry.py",
             "tools/cronjob_tools.py")
    for path in paths:
        exported = EXPORTS / path
        if not exported.is_file():
            exported = EXPORTS / Path(path).name
        assert hashlib.sha256((SOURCE / path).read_bytes()).digest() == hashlib.sha256(exported.read_bytes()).digest(), path


compare_inputs()
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.getaddrinfo = denied
with tempfile.TemporaryDirectory(prefix="reminder-native-") as temp:
    os.environ.update(HOME=temp, HERMES_HOME=temp, XDG_CONFIG_HOME=temp + "/config",
                      XDG_DATA_HOME=temp + "/data", XDG_CACHE_HOME=temp + "/cache", HERMES_GATEWAY_SESSION="1")
    sys.path.insert(0, str(SOURCE))
    from cron import jobs
    import model_tools
    from gateway import session_context as sc, session_identity as si
    from gateway.config import Platform
    from gateway.authz_mixin import GatewayAuthorizationMixin
    from gateway.session import is_shared_multi_user_session
    from hermes_cli import plugins
    from hermes_cli.config import load_config_readonly
    home = Path(temp)
    cfg = {"plugins": {"enabled": ["discord-reminders"], "entries": {"discord-reminders": {
        "enabled": True, "profile": "default", "requesters": ["100"]}}},
        "platform_toolsets": {"discord": ["cronjob", "discord-reminders"]}}
    def config():
        (home / "config.yaml").write_text(json.dumps(cfg))
    config()
    scripts = home / "scripts"
    scripts.mkdir(mode=0o700)
    (scripts / "discord-reminder.py").write_bytes((PACKAGE / "reminder_helper.py").read_bytes())
    (scripts / "discord-reminder.py").chmod(0o600)
    (scripts / ".discord-reminder-inputs").mkdir(mode=0o700)
    manager = plugins.PluginManager(scope_key=temp)
    manager._discovered = True  # Direct native registration, NOT full discovery acceptance.
    plugins._delivery_manager = lambda: manager
    spec = importlib.util.spec_from_file_location("reminders_native_fixture", PACKAGE / "__init__.py", submodule_search_locations=[str(PACKAGE)])
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)
    manifest = plugins.parse_manifest_file(PACKAGE / "plugin.yaml", PACKAGE, "user", "")
    assert manifest and plugins.gate_manifest(manifest, set(), {plugin.H.PLUGIN}).action == "load"
    assert not plugins.gate_manifest(manifest, {plugin.H.PLUGIN}, {plugin.H.PLUGIN}).enabled
    ctx = plugins.PluginContext(manifest, manager)
    plugin.register(ctx)
    namespace = {"Platform": Platform, "is_shared_multi_user_session": is_shared_multi_user_session,
                 "neutralize_untrusted_inline_text": lambda value: value}
    functions("gateway/run_inbound.py", {"_prefix_inbound_sender_context", "_prepend_inbound_reply_context",
                                        "strip_discord_triggering_note", "discord_triggering_note"}, namespace)
    functions("gateway/run_turn.py", {"_resolve_turn_toolsets", "_resolve_enabled_toolsets_for_source"}, namespace)
    inbound = ModuleType("gateway.run_inbound")
    inbound.strip_discord_triggering_note = namespace["strip_discord_triggering_note"]
    run = ModuleType("gateway.run")
    run._load_gateway_config = load_config_readonly
    scheduler = ModuleType("cron.scheduler")
    scheduler.CronSchedulerRegistrationError = RuntimeError
    scheduler._notify_provider_jobs_changed = lambda: None  # No scheduler/provider is started.
    scheduler.create_job_with_scheduler_registration = functions(
        "cron/scheduler.py", {"create_job_with_scheduler_registration"}, {})["create_job_with_scheduler_registration"]
    class Adapter:
        def toolsets_for_source(self, source):
            return None
    adapter = Adapter()
    class Gateway(GatewayAuthorizationMixin):
        _resolve_turn_toolsets = namespace["_resolve_turn_toolsets"]
        _resolve_enabled_toolsets_for_source = namespace["_resolve_enabled_toolsets_for_source"]
        _prefix_inbound_sender_context = namespace["_prefix_inbound_sender_context"]
        _prepend_inbound_reply_context = namespace["_prepend_inbound_reply_context"]
        def _is_user_authorized_for_source(self, source):
            return source.user_id == "100"  # Synthetic authorization boundary, not Discord roles.
    gateway = Gateway()
    gateway.config = NS(multiplex_profiles=True, group_sessions_per_user=True, thread_sessions_per_user=False)
    gateway.adapters = {Platform.DISCORD: adapter}
    gateway._profile_adapters = {}
    source = NS(platform=Platform.DISCORD, user_id="100", chat_id="200", thread_id="200", chat_type="thread", user_name="Fixture")
    routed = si.RoutingIdentity(transport_profile="default", runtime_profile="default", authorization_home=home,
                                runtime_home=home, transport=weakref.ref(adapter))
    setattr(source, si._IDENTITY_ATTR, routed)
    source._transport_adapter_ref = weakref.ref(adapter)
    event = NS(source=source, text="Remind me in 30 minutes to review deployment", message_id="300", internal=False)
    call = dict(task_id="task", session_id="session", turn_id="turn", tool_call_id="call")
    seen, deny = [], [False]
    def policy(tool_name, args, **kwargs):
        if tool_name == "cronjob_manage":
            seen.append((args["action"], kwargs["session_id"], kwargs["task_id"], kwargs["turn_id"]))
            if deny[0]:
                return {"action": "block", "message": "Synthetic native policy denied cron"}
    ctx.register_hook("pre_tool_call", policy)
    pre = functions("agent/turn_context.py", {"_collect_pre_llm_call_context"}, {"logger": __import__("logging").getLogger("fixture")})
    async def bind(message_id):
        event.message_id = message_id
        call["turn_id"] = "turn-" + message_id
        sc.set_session_vars(platform="discord", chat_id=source.chat_id, thread_id=source.thread_id,
                            user_id=source.user_id, session_id="session", profile="default")
        await manager.ainvoke_hook("pre_gateway_dispatch", event=event, gateway=gateway)
        text = gateway._prefix_inbound_sender_context(event, source, event.text)
        text = gateway._prepend_inbound_reply_context(event, source, text)
        agent = NS(session_id="session", model="synthetic", platform="discord", _user_id="100")
        pre["_collect_pre_llm_call_context"](agent, effective_task_id="task", turn_id=call["turn_id"],
            original_user_message=text, messages=[], conversation_history=[])
    def invoke(tool=plugin.CREATE, args=None, **overrides):
        args = args if args is not None else {"duration": "30m", "note": "Review deployment"}
        return json.loads(model_tools.handle_function_call(tool, args, **{**call, **overrides}))
    async def baseline_and_denials():
        await bind("300")
        first, again = invoke(), invoke()
        assert first.get("success") and again["reminder_id"] == first["reminder_id"], (first, again)
        assert again["due_at"] == first["due_at"] and len(jobs.list_jobs(include_disabled=True)) == 1
        for override in ({"turn_id": "foreign"}, {"task_id": "foreign"}, {"session_id": "foreign"}):
            assert not invoke(**override).get("success")
        os.environ["HERMES_GATEWAY_SESSION"] = "0"
        assert not invoke().get("success")  # Not the stale native schema check cache.
        os.environ["HERMES_GATEWAY_SESSION"] = "1"
        cfg["agent"] = {"disabled_toolsets": ["cronjob"]}
        config()
        assert not invoke().get("success")
        cfg["agent"] = {}
        config()
        await bind("301")
        deny[0] = True
        assert not invoke().get("success") and len(jobs.list_jobs(include_disabled=True)) == 1
        assert seen[-1] == ("create", "session", "task", "turn-301")
        deny[0] = False
    async def crash_and_retry():
        original = model_tools.handle_function_call
        def crash_after_create(name, args, **kwargs):
            value = original(name, args, **kwargs)
            if name == "cronjob_manage" and args["action"] == "create":
                os._exit(77)  # AFTER native commit, BEFORE plugin ID bind; no finally runs.
            return value
        pid = os.fork()
        if pid == 0:
            model_tools.handle_function_call = crash_after_create
            invoke()
            os._exit(78)
        deadline = time.monotonic() + 15
        while True:
            finished, status = os.waitpid(pid, os.WNOHANG)
            if finished:
                assert os.waitstatus_to_exitcode(status) == 77, status
                break
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                raise AssertionError("Native crash child did not finish")
            await asyncio.sleep(0.01)
        paused = [job for job in jobs.list_jobs(include_disabled=True) if job["state"] == "paused"]
        assert len(paused) == 1
        due = paused[0]["schedule"]["run_at"]
        contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = list(pool.map(lambda context: context.run(invoke), contexts))
        assert all(result.get("success") for result in retries), retries
        assert retries[0]["reminder_id"] == retries[1]["reminder_id"]
        assert retries[0]["due_at"] == retries[1]["due_at"] == due
        assert len(jobs.list_jobs(include_disabled=True)) == 2
        recovered = jobs.get_job(paused[0]["id"])
        assert recovered["enabled"] and recovered["no_agent"] and recovered["repeat"]["times"] == 1
        assert recovered["deliver"] == "discord:200:200" and recovered["schedule"]["kind"] == "once"
        return recovered, retries[0]["reminder_id"]
    async def cancel_boundary(job, reminder_id):
        source.chat_id = source.thread_id = "201"
        await bind("302")
        args = {"reminder_id": reminder_id}
        assert not invoke(plugin.CANCEL, args).get("success") and jobs.get_job(job["id"])["enabled"]
        source.chat_id = source.thread_id = "200"
        await bind("303")
        jobs.update_job(job["id"], {"last_delivery_error": "synthetic unknown send"})
        evidence = jobs._job_output_dir(job["id"]) / "unknown.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("synthetic uncertain-send receipt")
        revoked = invoke(plugin.CANCEL, args)
        assert revoked.get("success") and revoked["state"] == "revoked", revoked
        assert jobs.get_job(job["id"])["state"] == "paused"
        assert jobs.get_job(job["id"])["last_delivery_error"] == "synthetic unknown send"
        assert evidence.read_text() == "synthetic uncertain-send receipt"
    async def independent_turns():
        cfg["plugins"]["entries"]["discord-reminders"]["requesters"] = ["100", "101"]
        config()
        gateway._is_user_authorized_for_source = lambda origin: origin.user_id in {"100", "101"}
        barrier = threading.Barrier(2, timeout=10)
        async def turn(user):
            origin = copy(source)
            origin.user_id = user
            local_event = NS(source=origin, text="Explicit reminder for " + user, message_id="4" + user, internal=False)
            ids = dict(task_id="task-" + user, session_id="session-" + user, turn_id="turn-" + user, tool_call_id="call-" + user)
            sc.set_session_vars(platform="discord", chat_id="200", thread_id="200", user_id=user,
                                session_id=ids["session_id"], profile="default")
            await manager.ainvoke_hook("pre_gateway_dispatch", event=local_event, gateway=gateway)
            text = gateway._prefix_inbound_sender_context(local_event, origin, local_event.text)
            text = gateway._prepend_inbound_reply_context(local_event, origin, text)
            agent = NS(session_id=ids["session_id"], model="synthetic", platform="discord", _user_id=user)
            pre["_collect_pre_llm_call_context"](agent, effective_task_id=ids["task_id"], turn_id=ids["turn_id"],
                original_user_message=text, messages=[], conversation_history=[])
            def execute():
                barrier.wait()  # Both independently bound contexts coexist before either executes.
                args = {"duration": "30m", "note": "Task for " + user}
                foreign = "101" if user == "100" else "100"
                swapped = {**ids, "session_id": "session-" + foreign, "task_id": "task-" + foreign, "turn_id": "turn-" + foreign}
                refused = json.loads(model_tools.handle_function_call(plugin.CREATE, args, **swapped))
                assert not refused.get("success"), refused
                result = json.loads(model_tools.handle_function_call(plugin.CREATE, args, **ids))
                assert result.get("success"), result
                record = plugin.H.load(scripts / plugin.H.INPUTS / result["reminder_id"] / "request.json")
                assert record["owner"][2] == user and record["note"] == args["note"]
                # A later inherited turn cannot bind a second time to this consumed ingress.
                pre["_collect_pre_llm_call_context"](agent, effective_task_id=ids["task_id"], turn_id="later-" + user,
                    original_user_message=text, messages=[], conversation_history=[])
                reused = json.loads(model_tools.handle_function_call(plugin.CREATE, args, **{**ids, "turn_id": "later-" + user}))
                assert not reused.get("success"), reused
                return result["reminder_id"]
            return await asyncio.to_thread(execute)
        async def foreign_profile():
            origin = copy(source)
            setattr(origin, si._IDENTITY_ATTR, replace(routed, runtime_profile="secondary"))
            sc.set_session_vars(platform="discord", chat_id="200", thread_id="200", user_id="100", profile="secondary")
            await manager.ainvoke_hook("pre_gateway_dispatch", event=NS(source=origin, text="Foreign profile reminder",
                                        message_id="499", internal=False), gateway=gateway)
            await asyncio.sleep(0)  # Coexists with independently accepted default-profile turns.
            assert not invoke().get("success")
        before = len(jobs.list_jobs(include_disabled=True))
        results = await asyncio.gather(turn("100"), turn("101"), foreign_profile())
        results = results[:2]
        assert len(set(results)) == 2 and len(jobs.list_jobs(include_disabled=True)) == before + 2
    async def journey():
        await baseline_and_denials()
        job, reminder_id = await crash_and_retry()
        await cancel_boundary(job, reminder_id)
        await independent_turns()
        assert plugin.H.GRACE_SECONDS == jobs.ONESHOT_GRACE_SECONDS
        assert not ATTEMPTS
        print("PASS native registration/IDs/denials, crash-released lock, ONE recovered job/due, scoped cancel, unknown-send evidence and independent concurrent owners/turns")
    with patch.dict(sys.modules, {"gateway.run": run, "gateway.run_inbound": inbound, "cron.scheduler": scheduler}):
        asyncio.run(journey())
