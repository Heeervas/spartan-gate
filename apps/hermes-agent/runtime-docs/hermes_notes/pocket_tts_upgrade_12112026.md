# Pocket TTS 4.0.1 — applied local upgrade and audio comparison

The local Pocket service is now running the verified 4.0.1 image with a separate
matching cache. After listening, the operator selected standard six-layer Spanish
(`spanish`) and Eponine, with default attention/FFN int8 quantization and a
1800-second idle timeout. Operator-only `POCKET_TTS_SPEED=1.15` applies
pitch-preserving FFmpeg time-stretching inside Pocket's disposable worker. Hermes and its command-provider
client were not rebuilt, restarted or reconfigured for this update. The former
2.1 image/cache and comparison recordings are retained for scoped rollback.

Native public-asset prefetch and offline generation passed for both standard and
24-layer Spanish, using separate caches and the same three comparison texts.
Six new WAVs and their reports are available in the operator's local comparison
output. The operator approved the six-layer voice and the 1.15 audition; this is
not a general pronunciation-quality certification. The 24-layer cache and old
recordings remain available as unloaded alternatives.

Actual application-path checks passed: cold/warm audio through the existing
Hermes client, worker reuse, isolated sleep/reap/wake, and a full deployed
30-minute idle observation on the speed-enabled image: the worker group was
released 1801.251 seconds after the last successful request. Only Docker init and
the listener remained, with listener RSS approximately 31 MiB (not total cgroup
memory). Health probes did not keep models resident; no OOM or restart occurred.
The same 8.24-second speech WAV became 7.174 seconds with approximately 0.115
seconds of conversion overhead. This shortens playback, not model inference.

The matching Hugging Face CDN hostname was renewed through the existing policy
helper with a 24-hour expiry. This is not permission for broader network access,
arbitrary runtime downloads, voice uploads, cloning or provider fallback. Offline
runtime continues to use the prefetched read-only cache.

Speed is operator-configured postprocessing, not native model prosody or a
request-level control. Natural-language speaking-style instructions remain
unsupported. Piper is not installed or selected. Operational details and cache
migration/rollback procedure are in `infra/pocket-tts/README.md`.
