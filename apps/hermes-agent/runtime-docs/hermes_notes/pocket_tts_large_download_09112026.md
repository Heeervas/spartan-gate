# Pocket TTS large Spanish model downloaded

Status: download applied; large-model activation not applied.

After the operator reported whitelisting the required AWS CDN hostname, the
same gated prefetch completed successfully. The separate candidate cache now
contains the pinned Pocket 2.1.0 `spanish_24l` model and predefined Eponine state,
alongside standard English/Anna. Built-in smoke synthesis returned nonempty
audio for both languages. This supersedes the earlier download-blocked status
in `pocket_tts_preparation_09112026.md`, not its successful standard activation.

The large candidate is not selected in Hermes. Prefetch reached the existing
2-GiB container memory ceiling and recorded memory-limit events, although it
completed without an OOM kill. It therefore did not meet the required 15%
headroom gate. Download/smoke success is not evidence of sustained bilingual
service fit, practical latency, subjective voice quality or channel delivery.

The existing standard/Eponine service stayed healthy with the same container
and image identities. No service restart, provider/config change, new allowlist
edit, direct-download fallback, limit increase or new recovery copy occurred.
The temporary prefetch container exited and was removed normally. Candidate
cache ownership remains with the existing Pocket task; the working cache was
not replaced. Benchmark/resource acceptance is required before a later switch.
