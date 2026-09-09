"""Compose native Discord join/leave with the registered Talk command.

The host has no voice lifecycle hook. This narrow, opt-in wrapper preserves
native command authorization and calls the same Talk handler as /talk join.
It does not fabricate host execution attachments or tool/speaker authority.
"""
import asyncio
import inspect
import weakref


def install(runner_class, get_handler):
    original_join = runner_class._handle_voice_channel_join
    original_leave = runner_class._handle_voice_channel_leave
    if getattr(original_join, '_spartan_talk_voice', False):
        return
    locks = weakref.WeakKeyDictionary()

    def talk():
        handler = get_handler('talk')
        module = getattr(handler, '__globals__', {}).get('talk_discord')
        if not callable(handler) or module is None:
            raise RuntimeError('Talk command integration is unavailable')
        return handler, module

    async def invoke(handler, argument):
        result = handler(argument)
        return await result if inspect.isawaitable(result) else result

    async def join(self, event):
        platform = getattr(event.source.platform, 'value', event.source.platform)
        if str(platform).lower() != 'discord':
            return await original_join(self, event)
        async with locks.setdefault(self, asyncio.Lock()):
            try:
                handler, module = talk()
            except RuntimeError:
                return 'Talk is unavailable; voice was not started.'
            result = await original_join(self, event)
            guild = self._get_guild_id(event)
            adapter = self._adapter_for_source(event.source)
            if not guild or not adapter.is_in_voice_channel(guild):
                return result
            # The normal Talk command chooses the sole voice connection.
            # Refuse ambiguity instead of attaching a different server.
            try:
                bridge = module.resolve_voice_bridge()
                channel = await adapter.get_user_voice_channel(guild, event.source.user_id)
                if (bridge['guild_id'] != guild or bridge['adapter'] is not adapter
                        or channel is None
                        or bridge['voice_client'].channel.id != channel.id):
                    return 'Talk was not started: voice channel does not match this command.'
                started = await invoke(handler, 'join')
            except Exception:
                return 'Talk could not start. Check gateway logs; use /voice leave to disconnect.'
            return str(started).replace('`talk status`', '`/talk status`').replace(
                '`talk leave`', '`/voice leave`')

    async def leave(self, event):
        async with locks.setdefault(self, asyncio.Lock()):
            guild = self._get_guild_id(event)
            try:
                handler, module = talk()
                with module._SESSION_LOCK:
                    state = dict(module._SESSION)
                task = state.get('task')
                if guild and state.get('guild_id') == guild and task and not task.done():
                    await invoke(handler, 'leave')
                    # Finish restoring the borrowed receiver before native leave.
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
            except Exception:
                # Native disconnection must still be available on teardown error.
                pass
            return await original_leave(self, event)

    join._spartan_talk_voice = True
    runner_class._handle_voice_channel_join = join
    runner_class._handle_voice_channel_leave = leave


def register(ctx):
    from gateway.run import GatewayRunner
    from hermes_cli.plugins import get_plugin_command_handler
    install(GatewayRunner, get_plugin_command_handler)
