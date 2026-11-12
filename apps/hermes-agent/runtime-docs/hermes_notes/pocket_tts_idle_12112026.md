# Pocket TTS idle residency — prepared, not activated

Pocket TTS source now keeps its HTTP listener alive without loading the language
models. A valid audio request starts a dedicated model worker. Later requests
reuse it, and 30 minutes after the last completed synthesis the idle worker exits
and is reaped. Health checks, rejected requests and busy responses do not keep it
warm. No active synthesis is evicted by the idle timer.

Existing text/language selection, voices, offline cache, provider command and
internal-only networking remain unchanged. No Hermes rebuild or client changes
are required. The first request after sleeping includes model load latency; a
failed or timed-out worker returns an error with no automatic retry or fallback.

This note describes prepared source, not a deployed optimisation. Synthetic
process/HTTP tests establish lifecycle behaviour, not actual Pocket/PyTorch RAM
savings, cold-load latency or channel delivery. Build/recreate only Pocket TTS
after operator approval and measure current process/container memory before and
after a real wake/idle cycle. Preserve the prior image for rollback. Do not change
other background services or enable automatic audio replies.

Operator details: `infra/pocket-tts/README.md`.
