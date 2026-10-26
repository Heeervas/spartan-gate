"""Synthetic boundary tests using explicitly supplied pinned Hermes program files.

No gateway, provider, profile or Discord client is started. See package README.
"""

import ast
import asyncio
import importlib.util
import itertools
import json
import logging
import os
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace as NS
import textwrap
import unittest
from unittest.mock import AsyncMock, patch


PACKAGE = Path(__file__).parent / "discord-deferred-thread-title"


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "deferred_title_fixture", PACKAGE / "__init__.py",
        submodule_search_locations=[str(PACKAGE)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def native_title_consumer():
    """Execute the exported consumer; replace only imported runtime boundaries."""
    source = Path(os.environ["HERMES_TITLE_SOURCE"])
    agent = ModuleType("agent")
    auxiliary = ModuleType("agent.auxiliary_client")
    auxiliary.call_llm = None
    compressor = ModuleType("agent.context_compressor")
    compressor.LEGACY_SUMMARY_PREFIX = "[Previous summary]"
    delegation = ModuleType("agent.delegation_context")
    delegation.is_dispatcher_owned_worker_context = lambda: False
    content = ModuleType("agent.message_content")
    content.flatten_message_text = lambda value: value
    modules = {
        "agent": agent, "agent.auxiliary_client": auxiliary,
        "agent.context_compressor": compressor,
        "agent.delegation_context": delegation, "agent.message_content": content,
    }
    spec = importlib.util.spec_from_file_location("agent.title_generator", source)
    consumer = importlib.util.module_from_spec(spec)
    modules[spec.name] = consumer
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(consumer)
    agent.title_generator = consumer
    consumer._title_config = lambda: {}
    return consumer, modules


def native_adapter_class():
    """Compile only the actual rename method, without importing the native gateway."""
    source = Path(os.environ["HERMES_DISCORD_ADAPTER_SOURCE"])
    with source.open() as stream:
        lines = itertools.dropwhile(
            lambda line: not line.startswith("    async def rename_thread("), stream,
        )
        signature = next(lines)
        body = itertools.takewhile(
            lambda line: not re.match(r"    (?:async )?def ", line), lines,
        )
        method = ast.parse(textwrap.dedent(signature + "".join(body))).body[0]
    namespace = {
        "Optional": __import__("typing").Optional, "re": re,
        "logger": logging.getLogger(__name__), "DISCORD_AVAILABLE": True,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return type("NativeAdapter", (), {"rename_thread": namespace["rename_thread"]})


def event(text, *, created=False):
    return NS(text=text, source=NS(
        platform="discord", chat_type="thread", chat_id="123", thread_id="123",
        user_id="fixture-user",
        auto_thread_created=created,
        auto_thread_initial_name="/model fixture" if created else None,
    ))


class SemanticTitle(unittest.IsolatedAsyncioTestCase):
    async def test_installed_json_consumer_receives_workload_and_language_policy(self):
        plugin = load_plugin()
        consumer, modules = native_title_consumer()
        calls = []

        def provider(**request):
            calls.append(request)
            # A provider boundary, not an LLM: respond to the requested policy.
            # Without workload instructions, native titling emits an unmarked title.
            prompt = request["messages"][0]["content"]
            title = "Plan distributed storage migration"
            if "Judge workload, not urgency" in prompt:
                title = "🔴 " + title
            return NS(choices=[NS(message=NS(content=json.dumps({"title": title})))])

        consumer.call_llm = provider
        with patch.dict(sys.modules, modules):
            plugin.install_global_title_policy()
            installed = consumer.generate_title
            plugin.install_global_title_policy()
            self.assertIs(consumer.generate_title, installed)
            for language in ("", "Spanish"):
                with self.subTest(language=language):
                    consumer._title_config = lambda: {"language": language}
                    state = plugin._STATE.__class__()
                    state.process(event("/model fixture", created=True))
                    first = event("Plan a multi-stage distributed storage migration")
                    self.assertTrue(state.process(first))
                    title = consumer.generate_title(first.text)
                    self.assertEqual(title, "🔴 Plan distributed storage migration")
                    request = calls[-1]
                    prompt = request["messages"][0]["content"]
                    self.assertIn("🔴 for very high complexity", prompt)
                    self.assertIn("🟢 for a simple task", prompt)
                    self.assertIn("Spanish" if language else "same language", prompt)
                    self.assertNotIn("__LANGUAGE_RULE__", prompt)
                    self.assertEqual(request["task"], "title_generation")
                    self.assertEqual(request["reasoning_config"], {"enabled": False})
                    schema = request["extra_body"]["response_format"]["json_schema"]
                    self.assertEqual(schema["schema"]["required"], ["title"])
            # The old helper supplied assistant text as the second positional
            # argument. Pinned Hermes now interprets that position as timeout.
            store = NS(
                get_or_create_session=AsyncMock(return_value=NS(session_id="fixture")),
                load_transcript=AsyncMock(return_value=[{
                    "role": "user", "content": "Plan distributed storage migration",
                }]),
            )
            generated = await plugin._generate_title_from_session(
                NS(async_session_store=store), event("/rename-thread").source,
            )
            self.assertEqual(generated, "🔴 Plan distributed storage migration")
            self.assertIsNone(calls[-1]["timeout"])


class DeferredDispatch(unittest.TestCase):
    def test_configuration_then_content_is_one_shot_and_expires(self):
        plugin = load_plugin()
        state_type = plugin._STATE.__class__
        now = [0]
        for command in ("/model fixture", "/reasoning high"):
            with self.subTest(command=command):
                state = state_type(ttl_seconds=10, clock=lambda: now[0])
                initial = event(command, created=True)
                self.assertFalse(state.process(initial))
                self.assertTrue(initial.source.auto_thread_created)
                self.assertEqual(initial.source.auto_thread_initial_name, "/model fixture")
                self.assertFalse(state.process(event("/reasoning high")))
                first = event("Plan distributed storage migration")
                self.assertTrue(state.process(first))
                self.assertTrue(first.source.auto_thread_created)
                self.assertEqual(first.source.auto_thread_initial_name, "/model fixture")
                self.assertFalse(state.process(event("More content")))
                state.process(event(command, created=True))
                now[0] += 11
                self.assertFalse(state.process(event("Too late")))


class NativeRename(unittest.IsolatedAsyncioTestCase):
    async def test_shared_kwargs_keep_native_human_name_and_permission_guards(self):
        plugin = load_plugin()
        adapter_type = native_adapter_class()
        thread = NS(name="/model fixture", archived=False, edit=AsyncMock())
        adapter = adapter_type()
        adapter._client = NS(get_channel=lambda channel_id: thread)
        adapter.name = "fixture"
        base = ModuleType("gateway.platforms.base")
        base.utf16_len = lambda text: len(text.encode("utf-16-le")) // 2
        base._prefix_within_utf16_limit = lambda text, limit: text[:limit]
        with patch.dict(sys.modules, {"gateway.platforms.base": base}):
            self.assertTrue(plugin.install_native_discord_rename_compatibility(adapter))
            self.assertTrue(await adapter.rename_thread(
                "123", "🔴 Storage migration plan", only_if_current_name="/model fixture",
                prefer_connector_created=True, parent_chat_id="456",
            ))
            thread.edit.assert_awaited_once_with(
                name="🔴 Storage migration plan", reason="Hermes semantic session title",
            )
            thread.edit.reset_mock()
            thread.name = "Human chosen name"
            self.assertFalse(await adapter.rename_thread(
                "123", "Replacement", only_if_current_name="/model fixture",
            ))
            thread.edit.assert_not_awaited()
            thread.name = "/model fixture"
            thread.edit.side_effect = PermissionError("Manage Threads denied")
            self.assertFalse(await adapter.rename_thread("123", "Replacement"))
            thread.edit.reset_mock()
            thread.name = "✅ Storage migration plan"
            thread.archived = True
            self.assertFalse(await adapter.rename_thread(
                "123", "Replacement", only_if_current_name="/model fixture",
                prefer_connector_created=True, parent_chat_id="456",
            ))
            thread.edit.assert_not_awaited()

    async def test_terminal_actions_archive_once_and_cancel_deferred_title(self):
        for action, marker in (("close", "✅"), ("drop", "🗑️")):
            with self.subTest(action=action):
                plugin = load_plugin()
                thread = NS(name="🔴 Storage migration plan", archived=False)
                thread.edit = AsyncMock(return_value=NS(
                    name=f"{marker} Storage migration plan", archived=True,
                ))
                adapter_type = native_adapter_class()
                adapter = adapter_type()
                adapter._client = NS(get_channel=lambda channel_id: thread)
                adapter.send = AsyncMock()
                gateway = NS(
                    adapters={"discord": adapter},
                    _is_user_authorized_for_source=lambda source: True,
                    _intake_adapter_for=lambda source: adapter,
                )
                hooks = {}
                ctx = NS(register_hook=lambda name, callback: hooks.update({name: callback}),
                         register_command=lambda *args, **kwargs: None)
                with patch.object(plugin, "install_global_title_policy"):
                    plugin.register(ctx)
                hook = hooks["pre_gateway_dispatch"]
                hook(event=event("/model fixture", created=True), gateway=gateway)
                with patch.object(plugin.asyncio, "get_running_loop") as loop:
                    tasks = []
                    loop.return_value.create_task.side_effect = tasks.append
                    result = hook(event=event(f"/rename-thread {action}"), gateway=gateway)
                self.assertEqual(result["action"], "skip")
                for task in tasks:
                    await task
                thread.edit.assert_awaited_once_with(
                    name=f"{marker} Storage migration plan", archived=True,
                    reason=f"Hermes manual thread {'completion' if action == 'close' else 'drop'}",
                )
                self.assertFalse(plugin._STATE.process(event("Later content")))
                adapter.send.assert_not_awaited()

    async def test_manual_rename_uses_authorized_receiving_bot_not_primary_map(self):
        plugin = load_plugin()
        hooks = {}
        ctx = NS(register_hook=lambda name, callback: hooks.update({name: callback}),
                 register_command=lambda *args, **kwargs: None)
        with patch.object(plugin, "install_global_title_policy"):
            plugin.register(ctx)
        primary = NS(rename_thread=AsyncMock(return_value=True), send=AsyncMock())
        receiving = NS(rename_thread=AsyncMock(return_value=True), send=AsyncMock())
        gateway = NS(
            adapters={"discord": primary},
            # An ambient authorization check must never admit this operation.
            _is_user_authorized=lambda source: True,
            _is_user_authorized_for_source=lambda source: True,
            _intake_adapter_for=lambda source: receiving,
        )
        hook = hooks["pre_gateway_dispatch"]
        with patch.object(plugin, "install_native_discord_rename_compatibility", return_value=True):
            request = event("/rename-thread Deployment review")
            request.source.profile = "secondary"
            result = hook(event=request, gateway=gateway)
            self.assertEqual(result["action"], "skip")
            await asyncio.sleep(0)
            receiving.rename_thread.assert_awaited_once_with("123", "🟡 Deployment review")
            receiving.send.assert_awaited_once()
            primary.rename_thread.assert_not_awaited()
            primary.send.assert_not_awaited()
            receiving.rename_thread.reset_mock()
            receiving.send.reset_mock()
            # Revocation, missing provenance, and missing owner all deny. No fallback.
            gateway._is_user_authorized_for_source = lambda source: False
            hook(event=request, gateway=gateway)
            await asyncio.sleep(0)
            receiving.rename_thread.assert_not_awaited()
            gateway._is_user_authorized_for_source = lambda source: True
            gateway._intake_adapter_for = lambda source: None
            hook(event=request, gateway=gateway)
            await asyncio.sleep(0)
            primary.rename_thread.assert_not_awaited()
            gateway._intake_adapter_for = lambda source: receiving
            request.source.user_id = None
            hook(event=request, gateway=gateway)
            await asyncio.sleep(0)
            receiving.rename_thread.assert_not_awaited()
            receiving.send.assert_not_awaited()

    async def test_unauthorized_dispatch_does_not_arm_or_rename(self):
        plugin = load_plugin()
        hooks = {}
        ctx = NS(register_hook=lambda name, callback: hooks.update({name: callback}),
                 register_command=lambda *args, **kwargs: None)
        with patch.object(plugin, "install_global_title_policy"):
            plugin.register(ctx)
        adapter = NS(rename_thread=AsyncMock())
        gateway = NS(
            adapters={"discord": adapter},
            _is_user_authorized_for_source=lambda source: False,
            _intake_adapter_for=lambda source: adapter,
        )
        hook = hooks["pre_gateway_dispatch"]
        hook(event=event("/model fixture", created=True), gateway=gateway)
        hook(event=event("/rename-thread close"), gateway=gateway)
        self.assertFalse(plugin._STATE.process(event("Content")))
        adapter.rename_thread.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
