# Pocket TTS large Spanish model: download and activation

Status: applied locally — Spanish24l/Eponine is now selected for the main Hermes.
Subjective voice/accent quality and chat attachment/playback remain user checks.

## Download

After the operator reported whitelisting the required AWS CDN hostname, the same
gated prefetch completed successfully. The separate candidate cache contains the
pinned Pocket 2.1.0 `spanish_24l` model and predefined Eponine state, alongside
standard English/Anna. Built-in smoke synthesis returned nonempty audio for both
languages. Download did not itself select the model or authorize a limit change.

The original 2-GiB prefetch hit its memory ceiling but completed without an OOM
kill. This did not satisfy the 15% headroom gate. The operator then explicitly
requested switching to the large model and increasing memory if needed.

## Measured switch

- Only the standard Pocket sidecar was stopped before serial testing, avoiding
  holding both candidates in memory. Hermes and neighboring services were not
  restarted by this task; existing standard cache and image remain available.
- A 3-GiB trial exhausted memory during bilingual loading and was removed after
  exit. Fresh host headroom supported a 4-GiB trial, which passed: cgroup peak
  about 3.04 GiB (76% of limit), no OOM or memory-limit events.
- Three fixed Spanish samples took 2.65–4.63 seconds to generate, RTF about
  0.77–0.79. This is slower than the standard model's observed samples, but faster
  than real time on these short texts. It is not a universal performance claim.
- A private Pocket-only override raises memory from 2 to 4 GiB. CPU stays at 2;
  runtime UID10001, internal-only network, no published ports, read-only root/cache,
  dropped capabilities and offline loading are unchanged. Public defaults stay 2 GiB.
- Live service is healthy with the selected large cache/model. After native
  synthesis, live cgroup peak was about 2.85 GiB, with no OOM/limit events and
  about 2.22 GiB host memory available at the recorded acceptance observation.
- Actual Hermes `text_to_speech_tool` succeeded as UID1000 using Pocket: valid
  6.1665-second Ogg/Opus audio in about 6.12 seconds including packaging. No chat
  message was sent. Text stays default; ask for an audio reply when desired.

Only the selected model/cache fields and Pocket memory override changed. Exact
scoped rollback was verified locally, preserving unrelated configuration bytes.
One original private Compose beforeimage was added to the same restricted task
recovery folder, not a new backup directory or dated copies in the source root.
No Hermes config rewrite, rebuild, provider change, global memory/swap adjustment,
network-policy expansion or credential action was needed for this switch.

The earlier standard activation note remains historical evidence. General
comparison/activation procedure: `infra/pocket-tts/README.md`. Voice quality and
native channel playback still require listening; a larger model is not proof of
a better accent or a guarantee against pronunciation/content errors.
