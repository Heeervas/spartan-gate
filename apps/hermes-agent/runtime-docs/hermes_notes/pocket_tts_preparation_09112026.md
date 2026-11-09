# Pocket TTS Spanish activation

Status: applied locally — standard Spanish/Eponine active in the main Hermes
home. Larger-model and subjective listening/channel playback acceptance pending.

Pocket runs as an offline internal-only sidecar with no published ports, a
read-only cache, the existing 2-GiB/2-CPU ceilings and no cloud fallback. Text
remains default. The native Hermes command provider points to the baked Pocket
client; native TTS config loading consumes the change without a Hermes restart.
Other settings, existing profiles and neighboring container identities were
preserved. No Hermes image rebuild or runtime-guide publication occurred.

## Actual native checks

- Three fixed Spanish comparison WAVs generated offline. Generation took
  1.36–2.07 seconds for 4.40–6.96 seconds of audio, RTF approximately 0.29–0.31.
- Comparison cgroup peak: about 1.52 GiB, below the 85%-of-2-GiB gate, no OOM
  events. The live service also remained within the gate. These are observed
  local samples, not a universal performance guarantee.
- Actual deployed `text_to_speech_tool` succeeded as Hermes's unprivileged user
  with Pocket explicitly selected: about 4.25 seconds of Ogg/Opus audio in
  1.98 seconds including packaging. Codec/duration were checked with ffprobe;
  this does not establish subjective accent/quality or chat delivery.
- No external chat message was sent. Ask Hermes “respóndeme por audio” to check
  delivery/playback in the intended channel. Request a short spoken summary,
  not reading code verbatim; do not enable automatic all-replies audio.

## Larger candidate and operational limits

The larger model was not activated: gated Hugging Face download returned an
explicit proxy filter denial for an additional public CDN hostname. No allowlist
expansion, direct-download bypass, token use or limit increase was performed.
Its voice assets, CPU memory/latency and quality remain unverified; it is not a
hardware failure or a silent fallback. The measured standard candidate is active.
Spanish-from-Spain accent is not guaranteed; Alberto retains listening acceptance.

The comparison recipe was corrected against real Compose: `run` has no
`--no-build`; omit `--build` and use the existing image with `--pull never`.
Fresh operator-owned comparison caches run prefetch as their owner, with explicit
USER/LOGNAME for UIDs absent from the image passwd database. Do not add DAC
capabilities or widen directory permissions to work around ownership.

One restricted task-local recovery holds only original env/configuration bytes
and exact helper-artifact metadata. It is not a system/session/profile/model
backup. The configurator's first-before-Pocket file has a fixed name and is
idempotent; no dated backup pile was created in the repository root.

Model/voice comparison, activation and rollback procedure:
`infra/pocket-tts/README.md`. Environment contract: `docs/env.md`.
