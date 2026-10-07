import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

spec = importlib.util.spec_from_file_location(
    'voice_profile_bridge', Path(__file__).parent / 'spartan-talk-voice-command/__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class ProfileBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.default_bridge = object()
        self.module = NS(resolve_voice_bridge=lambda guild_id=None: self.default_bridge,
                         TalkDiscordError=RuntimeError)
        self.first_client, self.second_client = object(), object()
        self.first = NS(_voice_clients={1: self.first_client}, _voice_receivers={1: object()})
        self.second = NS(_voice_clients={1: self.second_client}, _voice_receivers={1: object()})

    async def test_secondary_profile_never_borrows_default_bridge(self):
        with plugin.voice_scope(self.module, self.first, 1):
            bridge = self.module.resolve_voice_bridge()
        self.assertIs(bridge['adapter'], self.first)
        self.assertIs(bridge['voice_client'], self.first_client)
        self.assertIs(self.module.resolve_voice_bridge(), self.default_bridge)

    async def test_task_retains_its_profile_after_command_scope_exits(self):
        ready = asyncio.Event()
        async def resolve_later():
            await ready.wait()
            return self.module.resolve_voice_bridge(1)
        with plugin.voice_scope(self.module, self.first, 1):
            first_task = asyncio.create_task(resolve_later())
        with plugin.voice_scope(self.module, self.second, 1):
            second_task = asyncio.create_task(resolve_later())
        ready.set()
        first, second = await asyncio.gather(first_task, second_task)
        self.assertIs(first['voice_client'], self.first_client)
        self.assertIs(second['voice_client'], self.second_client)

    async def test_missing_secondary_connection_fails_closed(self):
        self.first._voice_clients.clear()
        with plugin.voice_scope(self.module, self.first, 1):
            with self.assertRaisesRegex(RuntimeError, 'not in a voice channel'):
                self.module.resolve_voice_bridge()

    async def test_wrong_server_fails_closed(self):
        with plugin.voice_scope(self.module, self.first, 1):
            with self.assertRaisesRegex(RuntimeError, 'server'):
                self.module.resolve_voice_bridge(2)

    async def test_malformed_voice_state_fails_closed(self):
        self.first._voice_receivers = None
        with plugin.voice_scope(self.module, self.first, 1):
            with self.assertRaisesRegex(RuntimeError, 'internals'):
                self.module.resolve_voice_bridge()

    async def test_nested_scopes_restore_the_previous_binding(self):
        with plugin.voice_scope(self.module, self.first, 1):
            with plugin.voice_scope(self.module, self.second, 1):
                self.assertIs(self.module.resolve_voice_bridge()['adapter'], self.second)
            self.assertIs(self.module.resolve_voice_bridge()['adapter'], self.first)


class VoiceReplies(unittest.TestCase):
    def test_voice_receipts_keep_limitations_without_unsupported_core_command(self):
        result = plugin.voice_reply(
            'Limited legacy voice. Use `/talk core join` for full canonical Hermes parity; '
            'say `talk status` or `talk leave`.')
        self.assertIn('Limited legacy voice', result)
        self.assertIn('/voice status', result)
        self.assertIn('/voice leave', result)
        self.assertNotIn('core join', result)
        self.assertNotIn('/talk', result)
