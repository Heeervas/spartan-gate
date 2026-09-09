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
