# Discord voice relay — applied wiring, 09/11/2026

Status: **applied** to the main Hermes gateway; real Discord voice acceptance is
pending the operator's channel test. This note is an operator archive entry,
not a guide published into Hermes.

## Applied

- Mounted only the existing `spartan-discord-voice-relay` plugin, read-only, and
  enabled that plugin in the main native configuration.
- Injected the existing relay credential through the private service env file,
  plus the fixed internal relay URL. No token rotation or credential disclosure.
- Recreated only Hermes with its existing image, without a build or pull.
  Rechecked the native live-work guard immediately before the recreation.
- Preserved a concurrent `voice.auto_tts` change and all other configuration;
  kept managed dependency validation, existing overlays and network isolation.
- Left Talk disabled and did not mount its lifecycle wrapper. Parakeet and
  Pocket remain the selected local STT/TTS providers.

## Verified

- All 12 focused relay fixtures pass, plus the installed discord.py synthetic
  seam check, including missing configuration and failed-relay denial paths.
- Full effective Compose comparison permits only the declared wiring additions.
- The recreated gateway is healthy. Its native control-socket plugin reload
  reports the relay in its loaded, non-errored activation summaries.
- Internal relay health succeeds with zero sessions; unauthenticated voice
  access returns 401. This uses the adapter's direct internal-client route;
  a generic environment-proxied HTTP probe is not the equivalent path.
- Hermes retains internal-only networking; the adapter mount is read-only.
  The existing relay token matches without printing it. All other running
  container identities are unchanged; Pocket remains healthy at its existing
  private memory limit.

## Remaining acceptance

The operator must retry `/voice join` from the intended Discord channel and
verify negotiation, speech recognition, replies aloud, interruption and leave.
No channel join, participant audio capture or actual playback was performed by
these checks. The existing prototype remains limited to one simultaneous
session, a ten-minute session lifetime and a fresh join after disconnect.
The separate webapp no-speech reporting issue is not fixed by this transport.
