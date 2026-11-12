# Pocket TTS 4.0.1 — applied local upgrade and audio comparison

The local Pocket service is now running the verified 4.0.1 image with a separate
matching cache. Spanish remains `spanish_24l`, Eponine, with default attention/FFN
int8 quantization and a 1800-second idle timeout. Hermes and its command-provider
client were not rebuilt, restarted or reconfigured for this update. The former
2.1 image/cache and comparison recordings are retained for scoped rollback.

Native public-asset prefetch and offline generation passed for both standard and
24-layer Spanish, using separate caches and the same three comparison texts.
Six new WAVs and their reports are available in the operator's local comparison
output. No subjective voice-quality or pronunciation acceptance is claimed.
The larger model is still selected; standard is a listening alternative only.

Actual application-path checks passed: cold/warm audio through the existing
Hermes client, worker reuse, isolated sleep/reap/wake, and a full deployed
30-minute idle observation. Warm container memory was approximately 3 GiB and
fell to approximately 47 MiB after expiry; health probes did not keep models
resident. No candidate OOM was observed. This update is not claimed to reduce
active-model memory or improve generation speed.

The matching Hugging Face CDN hostname was renewed through the existing policy
helper with a 24-hour expiry. This is not permission for broader network access,
arbitrary runtime downloads, voice uploads, cloning or provider fallback. Offline
runtime continues to use the prefetched read-only cache.

Neither native speed control nor natural-language speaking-style instructions
was added. Piper is not installed or selected. Operational details and cache
migration/rollback procedure are in `infra/pocket-tts/README.md`.
