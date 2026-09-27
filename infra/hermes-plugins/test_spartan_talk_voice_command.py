import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('voice_commands', Path(__file__).parent / 'spartan-talk-voice-command/__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class Registration(unittest.IsolatedAsyncioTestCase):
    async def test_register_defers_gateway_import_until_dispatch(self):
        callbacks = []
        ctx = NS(register_hook=lambda name, callback: callbacks.append((name, callback)))
        original_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == 'gateway.run':
                raise AssertionError('plugin discovery must not import gateway.run')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded_import):
            plugin.register(ctx)

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(callbacks[0][0], 'pre_gateway_dispatch')

        calls = []
        connected = NS(value=False)
        channel = NS(id=123)
        adapter = NS(is_in_voice_channel=lambda guild: connected.value,
                     get_user_voice_channel=AsyncMock(return_value=channel))

        class Runner:
            async def _handle_voice_channel_join(self, event):
                calls.append('native_join')
                connected.value = True
                return 'native join'

            async def _handle_voice_channel_leave(self, event):
                return 'native leave'

            def _get_guild_id(self, event): return 1
            def _adapter_for_source(self, source): return adapter

        bridge = {'guild_id': 1, 'adapter': adapter, 'voice_client': NS(channel=channel)}
        talk_module = NS(resolve_voice_bridge=lambda: bridge)
        handler_globals = {'talk_discord': talk_module, 'calls': calls}
        exec('def handler(arg):\n calls.append(arg)\n return "Talk started"', handler_globals)
        gateway = Runner()
        event = NS(source=NS(platform=NS(value='discord'), user_id='fixture'))
        hermes_cli = ModuleType('hermes_cli')
        plugins = ModuleType('hermes_cli.plugins')
        plugins.get_plugin_command_handler = lambda name: handler_globals['handler']
        with patch.dict(sys.modules, {'hermes_cli': hermes_cli, 'hermes_cli.plugins': plugins}):
            callback = callbacks[0][1]
            self.assertIsNone(callback(event=event, gateway=gateway, session_store=None))
            installed = Runner._handle_voice_channel_join
            self.assertTrue(installed._spartan_talk_voice)
            self.assertIsNone(callback(event=event, gateway=gateway, session_store=None))
            self.assertIs(Runner._handle_voice_channel_join, installed)
            self.assertEqual(await gateway._handle_voice_channel_join(event), 'Talk started')
            self.assertEqual(calls, ['native_join', 'join'])


class Commands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.channel = NS(id=123)
        self.adapter = NS(is_in_voice_channel=lambda guild: self.connected,
                          get_user_voice_channel=AsyncMock(return_value=self.channel))
        self.connected = False
        self.native_success = True
        self.bridge = {'guild_id': 1, 'adapter': self.adapter, 'voice_client': NS(channel=self.channel)}
        self.module = NS(resolve_voice_bridge=lambda: self.bridge, _SESSION_LOCK=threading.Lock(), _SESSION={})
        env = {'talk_discord': self.module, 'calls': self.calls}
        exec('def handler(arg):\n calls.append(arg)\n return "Starting Talk; use `talk leave`."', env)
        self.handler = env['handler']
        owner = self
        class Runner:
            async def _handle_voice_channel_join(self, event):
                owner.calls.append('native_join')
                owner.connected = owner.native_success
                return 'native result'
            async def _handle_voice_channel_leave(self, event):
                owner.calls.append('native_leave')
                owner.connected = False
                return 'left'
            def _get_guild_id(self, event): return 1
            def _adapter_for_source(self, source): return owner.adapter
        self.cls = Runner
        plugin.install(Runner, lambda name: self.handler)
        self.runner = Runner()
        self.event = NS(source=NS(platform=NS(value='discord'), user_id='fixture'))

    async def test_join_uses_registered_handler_after_native_connection(self):
        result = await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join', 'join'])
        self.assertIn('/voice leave', result)

    async def test_failed_join_does_not_start_talk(self):
        self.native_success = False
        self.assertEqual(await self.runner._handle_voice_channel_join(self.event), 'native result')
        self.assertEqual(self.calls, ['native_join'])

    async def test_missing_plugin_does_not_join(self):
        self.handler = None
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, [])

    async def test_other_platform_is_unchanged(self):
        self.event.source.platform.value = 'telegram'
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join'])

    async def test_wrong_guild_or_channel_does_not_start_talk(self):
        for key, value in [('guild_id', 2), ('voice_client', NS(channel=NS(id=999)))]:
            before = self.bridge[key]
            self.bridge[key] = value
            self.calls.clear()
            await self.runner._handle_voice_channel_join(self.event)
            self.assertEqual(self.calls, ['native_join'])
            self.bridge[key] = before

    async def test_install_is_idempotent(self):
        plugin.install(self.cls, lambda name: self.handler)
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join', 'join'])

    async def test_leave_stops_owned_task_before_native_leave(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=1, task=task)
        original = self.handler
        def handler(arg):
            if arg == 'leave': task.cancel()
            return original(arg)
        handler.__globals__['talk_discord'] = self.module
        self.handler = handler
        await self.runner._handle_voice_channel_leave(self.event)
        self.assertTrue(task.done())
        self.assertEqual(self.calls, ['leave', 'native_leave'])

    async def test_leave_does_not_stop_another_guild(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=2, task=task)
        try:
            await self.runner._handle_voice_channel_leave(self.event)
            self.assertFalse(task.done())
            self.assertEqual(self.calls, ['native_leave'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_native_leave_works_without_talk(self):
        self.handler = None
        await self.runner._handle_voice_channel_leave(self.event)
        self.assertEqual(self.calls, ['native_leave'])
