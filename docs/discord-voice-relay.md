# Discord voice relay proof of concept

Status: local protocol and bidirectional UDP tests pass. The local relay and
all-profile adapter are now enabled for an operator-run test, with one
simultaneous session. The real upstream voice WebSocket returned Discord Hello;
a real channel join, DAVE negotiation and listening/speaking are still unverified.
This is not completed Discord acceptance.

Hermes remains internal-network only. The optional `discord-voice-relay` service
has external connectivity for UDP and uses the existing HTTP proxy for upstream
voice WebSockets. It publishes no host ports and binds its HTTP/UDP listeners to
its internal address. No host firewall changes or Hermes network capability are
required. The current local Route runtime image supplies Python/aiohttp for the
prototype without another image build.

The `spartan-discord-voice-relay` Hermes plugin redirects only Discord media
WebSocket connections to the authenticated local relay. Other HTTP/WebSocket
traffic follows the existing client. Missing configuration fails closed. The
adapter currently covers `*.discord.media` endpoints; other endpoint families
must be reviewed before they can be supported.

The relay validates the upstream hostname, port and TLS certificate, rejects
redirects, and reads the actual UDP destination from the upstream Ready event.
The post-handshake destination check compares canonical URLs: an explicit TLS
port `:443` and its omitted default form are equivalent. Changes to the host,
port, scheme, path, query or credentials still fail before the downstream
WebSocket opens.
A client-supplied Ready event never authorizes forwarding. Non-public, multicast
and non-IPv4 destinations are rejected. The provider Ready event is rewritten
with an internal relay socket; the unmodified Discord UDP implementation then
performs IP discovery through that socket. The external socket stays connected
to the provider-supplied destination. The client source is pinned after a valid
74-byte discovery packet with the expected SSRC. Other source addresses/ports
are dropped. UDP payloads and binary DAVE signaling remain opaque.

Limits: one simultaneous session shared across profiles, ten-minute lifetime, 1 MiB WebSocket frames, 4096-byte
UDP packets, 256 packets/second per session, bounded datagram write buffering,
and five-second WebSocket writes. Sessions close when either signaling side
closes. Prototype resume is deliberately rejected: start a fresh voice join
after a disconnect. Full resume/reconnect semantics are outside this proof.

## Run the local tests

```sh
python3 -m unittest discover -s infra/discord-voice-relay -p 'test_*.py'
```

These require aiohttp. Tests exercise real loopback UDP and local WebSocket
connections; upstream Discord signaling is mocked. Only the UDP fixture relaxes
the public-IP check for its loopback echo server. No bot login or voice token is
needed and no real audio is recorded.

## Controlled live test preparation

Use `infra/compose/compose.discord-voice-relay.yml` as an explicit additional
Compose file through the normal `sg` helper. Supply a pinned Python/aiohttp image,
an unused internal IPv4 address, the actual internal subnet and an ignored
owner-only env file containing only `DISCORD_VOICE_RELAY_TOKEN`. The image must
have no incompatible entrypoint. The existing local Route image is suitable.
Do not put a credential in command arguments or rendered Compose output.

Mount the adapter directory read-only as a single Hermes plugin directory;
enable it only in the chosen test profile and arrange private environment
injection of the same relay token plus:

```dotenv
DISCORD_VOICE_RELAY_URL=http://discord-voice-relay:8791/voice
```

This adapter uses the installed discord.py `HTTPClient.ws_connect` seam and its
owned aiohttp session. It preserves existing voice encryption and audio handling.
An upstream library change requires the focused tests and a compatibility check.
It must load before the tested voice join. No new bot token or OpenAI credential
is required. Existing voice session credentials pass through the relay in memory;
never enable frame/debug logging or record those messages.

Before a live call, confirm endpoint filtering, authentication rejection,
health, internal-only Hermes networking, and profile plugin loading. Then join
the operator-selected channel, confirm real DAVE negotiation and both audio
directions, interrupt playback, leave the channel and confirm zero relay sessions.
Other channel participants must know a bot test is taking place. A failed join
or one-way audio is a failed proof, even if local tests pass.

Rollback: disable the adapter in the test profile, restore its prior private
configuration, restart that gateway as needed, and remove only the relay service
and mounts. Do not add a direct internet route to Hermes or silently fall back
to direct UDP. The existing Route Realtime service is independent of this relay.

## One-command Talk lifecycle

The optional `spartan-talk-voice-command` 0.2.1 plugin provides the Discord
lifecycle through `/voice` alone. Enable it alongside Talk and mount its own
plugin directory read-only. Bare `/voice` and `/voice join` join the caller's
channel and start Talk. `/voice leave` stops the owned Talk task before native
disconnection. `/voice status`, `/voice pause` and `/voice resume` address only
the session owned by that profile's adapter and server. Explicit native
`/voice on`, `off` and `tts` modes remain available; other platforms retain
their native voice command behavior.

Join the Discord voice channel yourself first, then send `/voice` to that bot.
No preceding `/talk` command is needed. The adapted Talk gateway handler directs
`/talk` to `/voice`, avoiding the terminal microphone path in Hermes' command
worker. The wrapper loads Talk's Opus codec before native receiver creation;
missing codecs refuse the join. A local deployment may bundle its compatible,
licensed codec in Talk's read-only plugin mount without rebuilding Hermes.

The bridge is bound to the command's receiving adapter and server using a
context-local scope inherited by the session task. A secondary profile never
borrows the default bot's connection. Missing, malformed or mismatched bridges
refuse startup. A live session cannot be moved by another join command.
Profiles share the provisioned Route voice credential's concurrency limit;
availability across profiles does not enable simultaneous calls.

This wrapper changes command ergonomics only: it does not supply host execution
attachments, grant operator authority, or bypass speaker verification. On a host
without the canonical tool attachment API, Talk retains its existing limited
legacy lane. Its registered handler reports that limitation. Full Hermes tool
parity requires the corresponding host capability; a command alias cannot add it.

The integration relies on the current GatewayRunner join/leave methods and Talk
session ownership state because Hermes has no voice lifecycle hook. Adapter
selection uses the native `_delivery_adapter_for(event.source)` resolver so it
follows the command's transport profile. Missing adapters and mismatched voice
bridges never start Talk. Revalidate
these surfaces when upgrading either component. Disable this plugin to restore
the separate native voice and Talk commands.
