# Pocket TTS Spanish preparation

Status: source preparation applied; runtime activation and real listening pending.

Pocket remains an optional offline sidecar. Prepared operator-only selection of
standard Spanish or the larger 24-layer model, with Eponine/Lola predefined voice
candidates and cache identities bound to the selected model/voice. Existing
default caches remain compatible. No custom voice cloning, arbitrary uploads,
cloud fallback or general outbound access was added.

The comparison CLI generates three Spanish WAVs and JSON load/generation timing,
RTF and process peak RSS through the service's actual loader. Unit tests use
synthetic audio; they do not establish native asset compatibility, accent,
quality, latency or memory. Do not describe a candidate as Spanish-from-Spain
without listening. Large-model fit within unchanged resource limits is unproved.

No image build, weight download, Hermes configuration write, service restart or
provider activation was performed for this preparation. The comparison recipe,
separate candidate caches, activation and rollback are documented in
`infra/pocket-tts/README.md`; environment keys are in `docs/env.md`.

After separate activation approval, validate native `text_to_speech` and actual
audio delivery in the intended profile/channel. Keep text as default; on-demand
“respóndeme por audio” should request a short spoken summary. Do not silently
turn on automatic all-replies audio. This archive note is not a mounted Hermes
guide and does not itself publish instructions to the running agent.
