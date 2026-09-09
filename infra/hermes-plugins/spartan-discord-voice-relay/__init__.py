"""Narrow Discord voice WebSocket adapter; UDP follows the broker's Ready reply."""
import os
import re
from urllib.parse import urlsplit


def install(http_class):
    original = http_class.ws_connect
    if getattr(original, '_spartan_voice_relay', False):
        return

    async def ws_connect(self, url, *, compress=0):
        parsed = urlsplit(str(url))
        if not (parsed.scheme == 'wss' and (parsed.hostname or '').endswith('.discord.media')):
            return await original(self, url, compress=compress)
        relay = os.environ.get('DISCORD_VOICE_RELAY_URL', '')
        token = os.environ.get('DISCORD_VOICE_RELAY_TOKEN', '')
        if relay != 'http://discord-voice-relay:8791/voice' or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise RuntimeError('Discord voice relay configuration is missing; direct voice is disabled')
        session = getattr(self, '_HTTPClient__session')
        try:
            return await session.ws_connect(
                relay, proxy=None, headers={'Authorization':'Bearer '+token,
                                           'X-Discord-Voice-Endpoint':str(url)},
                compress=0, max_msg_size=1024*1024, autoclose=False, timeout=25)
        except Exception:
            raise RuntimeError('Discord voice relay connection failed; no direct fallback') from None

    ws_connect._spartan_voice_relay = True
    http_class.ws_connect = ws_connect


def register(ctx):
    from discord.http import HTTPClient
    install(HTTPClient)
