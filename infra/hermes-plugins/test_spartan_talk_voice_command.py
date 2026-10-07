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
        adapter = NS(_voice_clients={1: NS(channel=channel)}, _voice_receivers={},
                     is_in_voice_channel=lambda guild: connected.value,
                     get_user_voice_channel=AsyncMock(return_value=channel))

        class Runner:
            async def _handle_voice_channel_join(self, event):
                calls.append('native_join')
                connected.value = True
                return 'native join'

            async def _handle_voice_command(self, event):
                return 'native command'

            async def _handle_voice_channel_leave(self, event):
                return 'native leave'

            def _get_guild_id(self, event): return 1
            def _delivery_adapter_for(self, source): return adapter

        bridge = {'guild_id': 1, 'adapter': adapter, 'voice_client': NS(channel=channel)}
        talk_module = NS(resolve_voice_bridge=lambda guild_id=None: bridge,
                         TalkDiscordError=RuntimeError, _SESSION_LOCK=threading.Lock(), _SESSION={})
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
        self.adapter = NS(_voice_clients={1: NS(channel=self.channel)}, _voice_receivers={},
                          is_in_voice_channel=lambda guild: self.connected,
                          get_user_voice_channel=AsyncMock(return_value=self.channel))
        self.delivery_adapter = self.adapter
        self.adapter_sources = []
        self.connected = False
        self.native_success = True
        self.bridge = {'guild_id': 1, 'adapter': self.adapter, 'voice_client': NS(channel=self.channel)}
        self.module = NS(resolve_voice_bridge=lambda guild_id=None: self.bridge,
                         TalkDiscordError=RuntimeError, _SESSION_LOCK=threading.Lock(), _SESSION={})
        env = {'talk_discord': self.module, 'calls': self.calls}
        exec('def handler(arg):\n calls.append(arg)\n return "Starting Talk; use `talk leave`."', env)
        self.handler = env['handler']
        owner = self
        class Runner:
            async def _handle_voice_channel_join(self, event):
                owner.calls.append('native_join')
                owner.connected = owner.native_success
                return 'native result'
            async def _handle_voice_command(self, event):
                owner.calls.append('native_command')
                return 'native command result'
            async def _handle_voice_channel_leave(self, event):
                owner.calls.append('native_leave')
                owner.connected = False
                return 'left'
            def _get_guild_id(self, event): return 1
            def _delivery_adapter_for(self, source):
                owner.adapter_sources.append(source)
                return owner.delivery_adapter
        self.cls = Runner
        plugin.install(Runner, lambda name: self.handler)
        self.runner = Runner()
        self.argument = ''
        self.event = NS(source=NS(platform=NS(value='discord'), user_id='fixture'),
                        get_command_args=lambda: self.argument)

    async def test_codec_is_loaded_before_native_receiver_creation(self):
        self.module.ensure_discord_codec = lambda: self.calls.append('codec')
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls[:2], ['codec', 'native_join'])

    async def test_unavailable_codec_refuses_before_join(self):
        def unavailable():
            raise RuntimeError('codec unavailable')
        self.module.ensure_discord_codec = unavailable
        result = await self.runner._handle_voice_channel_join(self.event)
        self.assertIn('Opus', result)
        self.assertNotIn('native_join', self.calls)

    async def test_join_uses_registered_handler_after_native_connection(self):
        result = await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join', 'join'])
        self.assertIn('/voice leave', result)
        self.assertEqual(self.adapter_sources, [self.event.source])

    async def test_missing_source_adapter_does_not_start_talk(self):
        self.delivery_adapter = None
        self.assertEqual(await self.runner._handle_voice_channel_join(self.event), 'native result')
        self.assertEqual(self.calls, ['native_join'])

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

    async def test_wrong_channel_does_not_start_talk(self):
        self.adapter._voice_clients[1].channel = NS(id=999)
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join'])

    async def test_missing_profile_bridge_does_not_start_talk(self):
        self.adapter._voice_clients.clear()
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join'])

    async def test_bare_voice_starts_talk(self):
        await self.runner._handle_voice_command(self.event)
        self.assertEqual(self.calls, ['native_join', 'join'])

    async def test_native_text_voice_modes_remain_available(self):
        self.argument = 'off'
        await self.runner._handle_voice_command(self.event)
        self.assertEqual(self.calls, ['native_command'])

    async def test_voice_controls_cannot_touch_another_profile_in_the_same_guild(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=1, task=task, spartan_adapter=NS())
        try:
            for argument in ['pause', 'resume', 'status']:
                self.argument = argument
                await self.runner._handle_voice_command(self.event)
            self.assertEqual(self.calls, ['native_command'])
            self.calls.clear()
            await self.runner._handle_voice_channel_leave(self.event)
            self.assertEqual(self.calls, ['native_leave'])
            self.assertFalse(task.done())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_missing_adapter_cannot_control_an_unbound_session(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=1, task=task)
        self.delivery_adapter = None
        try:
            for argument in ['pause', 'resume', 'status', '']:
                self.argument = argument
                await self.runner._handle_voice_command(self.event)
            self.assertEqual(self.calls, ['native_command'])
            self.calls.clear()
            await self.runner._handle_voice_channel_leave(self.event)
            self.assertEqual(self.calls, ['native_leave'])
            self.assertFalse(task.done())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_voice_controls_use_the_owned_session(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=1, task=task, spartan_adapter=self.adapter)
        try:
            for argument in ['pause', 'resume', 'status']:
                self.argument = argument
                await self.runner._handle_voice_command(self.event)
            self.assertEqual(self.calls, ['pause', 'resume', 'status'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_join_does_not_move_an_active_session(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=2, task=task, spartan_adapter=self.adapter)
        try:
            result = await self.runner._handle_voice_channel_join(self.event)
            self.assertIn('already active', result)
            self.assertEqual(self.calls, [])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_install_is_idempotent(self):
        plugin.install(self.cls, lambda name: self.handler)
        await self.runner._handle_voice_channel_join(self.event)
        self.assertEqual(self.calls, ['native_join', 'join'])

    async def test_leave_stops_owned_task_before_native_leave(self):
        task = asyncio.create_task(asyncio.sleep(60))
        self.module._SESSION.update(guild_id=1, task=task, spartan_adapter=self.adapter)
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
