"""Compose native Discord join/leave with the registered Talk command.

The host has no voice lifecycle hook. This narrow, opt-in wrapper preserves
native command authorization and calls the same Talk handler as /talk join.
It does not fabricate host execution attachments or tool/speaker authority.
"""
import asyncio
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
import weakref


@contextmanager
def voice_scope(module, adapter, guild):
    """Pin Talk's borrowed bridge; spawned session tasks inherit this binding."""
    binding = getattr(module, '_spartan_voice_binding', None)
    if binding is None:
        binding = ContextVar('spartan_talk_voice_bridge', default=None)
        original = module.resolve_voice_bridge

        def resolve(guild_id=None):
            owner = binding.get()
            if owner is None:
                return original(guild_id)
            selected, selected_guild = owner
            if guild_id is not None and guild_id != selected_guild:
                raise module.TalkDiscordError('Voice belongs to a different server.')
            clients = getattr(selected, '_voice_clients', None)
            receivers = getattr(selected, '_voice_receivers', None)
            if not isinstance(clients, dict) or not isinstance(receivers, dict):
                raise module.TalkDiscordError('Discord voice internals are unavailable.')
            client = clients.get(selected_guild)
            if client is None:
                raise module.TalkDiscordError("I'm not in a voice channel on this profile.")
            return {'guild_id': selected_guild, 'adapter': selected,
                    'voice_client': client, 'receiver': receivers.get(selected_guild)}

        module.resolve_voice_bridge = resolve
        module._spartan_voice_binding = binding
    token = binding.set((adapter, guild))
    try:
        yield
    finally:
        binding.reset(token)


def session_state(module):
    with module._SESSION_LOCK:
        return dict(module._SESSION)


def active(state):
    task = state.get('task')
    return task is not None and not task.done()


def owns(state, adapter, guild):
    return bool(adapter is not None and guild and state.get('guild_id') == guild
                and state.get('spartan_adapter') is adapter)


def voice_reply(result):
    text = str(result).replace(
        'Use `/talk core join` for full canonical Hermes parity; ', '')
    return text.replace('/talk', '/voice').replace('`talk ', '`/voice ')


def install(runner_class, get_handler):
    original_join = runner_class._handle_voice_channel_join
    original_leave = runner_class._handle_voice_channel_leave
    original_command = runner_class._handle_voice_command
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
            guild = self._get_guild_id(event)
            adapter = self._delivery_adapter_for(event.source)
            state = session_state(module)
            if active(state):
                if owns(state, adapter, guild):
                    return voice_reply(await invoke(handler, 'status'))
                return 'Voice is already active in another channel; leave that session first.'
            try:
                ensure_codec = getattr(module, 'ensure_discord_codec', None)
                if callable(ensure_codec):
                    ensure_codec()
            except Exception:
                return 'Voice was not started: the Discord Opus codec is unavailable. Check gateway logs.'
            result = await original_join(self, event)
            if not guild or adapter is None or not adapter.is_in_voice_channel(guild):
                return result
            try:
                with voice_scope(module, adapter, guild):
                    bridge = module.resolve_voice_bridge(guild)
                    channel = await adapter.get_user_voice_channel(guild, event.source.user_id)
                    if (channel is None
                            or bridge['voice_client'].channel.id != channel.id):
                        return 'Voice was not started: channel does not match this command.'
                    started = await invoke(handler, 'join')
                    with module._SESSION_LOCK:
                        if active(module._SESSION) and module._SESSION.get('guild_id') == guild:
                            module._SESSION['spartan_adapter'] = adapter
            except Exception:
                return 'Voice could not start. Check gateway logs; use /voice leave to disconnect.'
            return voice_reply(started)

    async def leave(self, event):
        async with locks.setdefault(self, asyncio.Lock()):
            guild = self._get_guild_id(event)
            adapter = self._delivery_adapter_for(event.source)
            try:
                handler, module = talk()
                state = session_state(module)
                task = state.get('task')
                if owns(state, adapter, guild) and active(state):
                    await invoke(handler, 'leave')
                    # Finish restoring the borrowed receiver before native leave.
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.gather(task, return_exceptions=True)), 5)
            except Exception:
                # Native disconnection must still be available on teardown error.
                pass
            return await original_leave(self, event)

    async def command(self, event):
        platform = getattr(event.source.platform, 'value', event.source.platform)
        if str(platform).lower() != 'discord':
            return await original_command(self, event)
        argument = event.get_command_args().strip().lower()
        if argument in {'', 'join', 'channel'}:
            return await join(self, event)
        if argument == 'leave':
            return await leave(self, event)
        if argument in {'status', 'pause', 'resume'}:
            async with locks.setdefault(self, asyncio.Lock()):
                try:
                    handler, module = talk()
                    state = session_state(module)
                    adapter = self._delivery_adapter_for(event.source)
                    guild = self._get_guild_id(event)
                    if owns(state, adapter, guild) and active(state):
                        return voice_reply(await invoke(handler, argument))
                except RuntimeError:
                    pass
                if argument != 'status':
                    return 'No active voice session belongs to this profile and server.'
        return await original_command(self, event)

    join._spartan_talk_voice = True
    runner_class._handle_voice_channel_join = join
    runner_class._handle_voice_channel_leave = leave
    runner_class._handle_voice_command = command


def register(ctx):
    def install_before_dispatch(*, gateway, **_kwargs):
        runner_class = type(gateway)
        if not getattr(runner_class._handle_voice_channel_join, '_spartan_talk_voice', False):
            from hermes_cli.plugins import get_plugin_command_handler
            install(runner_class, get_plugin_command_handler)
        # None leaves the inbound event and authorization flow unchanged.

    # Discovery can run on a worker while the main thread imports gateway.run.
    # Importing that module here would deadlock against the discovery lock.
    ctx.register_hook('pre_gateway_dispatch', install_before_dispatch)
