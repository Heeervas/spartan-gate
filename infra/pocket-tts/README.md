# Pocket TTS sidecar

Spartan Gate packages Pocket TTS 4.0.1 as an optional local English/Spanish
sidecar, pinned to an exact release wheel URL and SHA256 in `requirements.txt`.
Hermes uses its command-provider interface, so enabling this service does not
rebuild the Hermes image.

The runtime has no published port and joins only `pocket_tts_internal`, a
Docker-internal network shared with Hermes. It has no proxy, external network,
Hugging Face token, or arbitrary voice-upload endpoint. The one-shot prefetch
service is the only component allowed to download model files, through the
normal Spartan Gate proxy and DNS boundary.

## Wake on request and idle memory

The HTTP listener starts without loading Pocket/PyTorch models. The first valid
`/v1/tts` request starts one model worker, using the existing offline cache and
selected English/Spanish voices. Later requests reuse it. After 30 minutes with
no synthesis in progress, measured from the last completed synthesis, the worker
is terminated and reaped so its interpreter, models and native allocations return
to the OS. The small listener stays available; no Docker socket or wake-up daemon
is needed. Health checks, invalid requests and busy responses do not wake models
or extend their residency. Idle expiry is checked approximately every half second.

`POCKET_TTS_IDLE_SECONDS` is an operator-only positive finite number of seconds,
default `1800`. It is not a request parameter. Model selection/cache/profile and
network restrictions are unchanged. The first request after sleep includes cold
model loading, so it is slower. One 150-second worker I/O budget covers cold load,
request transfer, synthesis, optional tempo processing and reply, followed by at
most four seconds of worker-group termination escalation; the existing command client defaults to 180 seconds.
Concurrent synthesis requests still receive HTTP 429; no background queue, automatic
retry or provider fallback is added. Shutdown interrupts worker I/O and stops its
owned process group, including an active FFmpeg child. Docker init reaps adopted
children even if the model worker dies first.

`/health` now means **listener/configuration ready**, not models already loaded.
`languages`, `models` and `voices` advertise configured capabilities;
`resident_languages` is empty while asleep/loading, and populated after successful
synthesis. `speed` reports the captured operator setting, also used by the worker
and benchmark; it is not a per-request control. `last_worker_error` exposes a generic worker failure until the next
successful request. Cache/profile validation failures still return unhealthy/503.
A failed cold load or crashed/timed-out worker returns an explicit synthesis
error; only a subsequent independent request can start a fresh worker.

Process exit proves worker reclamation, not exact host savings: container memory
can include reclaimable model file cache. Measure current process-tree residency
and cgroup anonymous/file memory before/after, not peak RSS. Synthetic tests do
not establish native cold-start latency or savings. Deployment needs only a
Pocket image update/recreation after approval; Hermes and callers need no change.

## Voices and language selection

- English: `anna`, standard English model.
- Spanish default: `eponine`; optional predefined candidate: `lola`.
- Spanish model default: `spanish` (6 layers); optional candidate: `spanish_24l`.
- Heuristic automatic detection is the default. Prefix text with `[en]` or
  `[es]` to force the language for short/ambiguous sentences; the prefix is removed.

The integration uses public non-cloning weights. Operator-only
`POCKET_TTS_SPANISH_MODEL` accepts exactly `spanish` or `spanish_24l`;
`POCKET_TTS_SPANISH_VOICE` accepts exactly `eponine` or `lola`. Unknown values
fail closed before loading/downloading. Requests still select only language/text,
never a model, voice, file or URL. Custom cloning and arbitrary uploads are not exposed.

Every 4.0.1 model/voice combination has a distinct new cache identity and
manifest, including explicit models, voices, recommended int8 groups and the
release wheel URL/SHA256. Changing selection or using a 2.1 cache without matching
prefetch causes startup refusal, not fallback. The image build checks pip's
installed `direct_url.json` against the provenance used by the manifest; this is
wheel provenance, not a claimed Git commit. Bundled configs and predefined voices
refer to immutable Hugging Face revisions. A larger model is not a verified
quality improvement on a particular CPU. The selected model-specific predefined
embedding must exist upstream; constructing its pinned path is not proof of asset
availability. No Spanish-from-Spain accent is guaranteed by the voice name.

## Hermes controls versus Pocket controls

The current command client sends only text and language. Voice/model selection
belongs to the operator-side service profile and its matching prefetched cache,
not to an individual Hermes generation request. The current command does not
forward Hermes' generic `{speed}` or `{voice}` placeholders; setting `tts.speed`,
passing `voice_id`, or supplying speaking-style `instructions` does not implement
those controls for this Pocket integration. Pitch, gain, emotion, SSML and custom
voice cloning are not exposed either. The operator-only tempo setting below is
postprocessing, not native natural-language style conditioning or a forwarded
Hermes speed control. Text and punctuation may affect phrasing;
`[es]`/`[en]` explicitly choose language, not a different voice.

### Operator-only output tempo

`POCKET_TTS_SPEED` accepts finite numbers from **1.0 to 1.25**, default **1.0**.
The default returns identical audio bytes and never invokes FFmpeg. `1.15` applies
pitch-preserving FFmpeg `atempo`: audio is approximately 13% shorter (duration
÷1.15), not 15% shorter. It does not accelerate model generation; conversion adds
some request latency and may introduce subtle audible artifacts. Both configured
languages use the same captured setting. Model/voice selection, cache identity,
public int8 weights and the 1800-second idle default are unchanged.

The Pocket image supplies `/usr/bin/ffmpeg`; host/Hermes binaries are not used.
Enabled conversion accepts at most 20 MiB of mono PCM16 WAV and caps the output
during writing at 20 MiB plus a small muxer-packet margin. Two anonymous seekable
files fit the existing 64 MiB tmpfs and disappear when descriptors close, including
forced worker termination. The finalized WAV must have truthful lengths and
complete PCM frames; cap hits/truncation are errors, even if FFmpeg exits 0.
FFmpeg has at most 10 seconds and never more than the remaining shared 150-second
request budget. Missing FFmpeg, invalid configuration, conversion failure or
exhausted limits produce explicit errors, never silently unprocessed audio.

Build a uniquely tagged Pocket candidate and verify synthetic tone duration/pitch,
WAV headers and converter cleanup before native testing. Set the desired speed in
the private operator environment only after acceptance, verify effective Compose
configuration, then recreate **only Pocket**. No model download, cache relabel,
Hermes rebuild/restart or command-provider edit is needed. Roll back to `1.0` and
recreate only Pocket; retain the prior image for image-level rollback. Preserve
existing cache path, Spanish model/voice, resource limits and idle setting.

## Upgrade from 2.1 without replacing its cache

Keep the old image ID/tag, cache path and model/voice selection for rollback.
Build a uniquely tagged 4.0.1 candidate (set `POCKET_TTS_IMAGE` before building),
never over the 2.1 tag. Use a **new cache root** for prefetch and comparison;
never edit an old manifest, reuse its voice state or relink its `active` pointer
to make it pass validation. Tokenizers, weights and model-specific embeddings
changed. Prefetch checks the selected embedding before loading large weights,
then smoke-generates both languages and atomically activates only a complete
new cache. Asset availability and real compatibility still require native checks.

Preserve an operator's existing `spanish_24l`/Eponine selection across the upgrade;
the public 6-layer default is not a recommendation to downgrade. Compare fresh
4.0.1 Spanish24l samples against the retained 2.1 Spanish24l reference with the
same three prompts before promoting the candidate. Official bundled Spanish tiers
are 6 and 24 layers, not an intermediate tier; small English models are not Spanish
replacements. Default CPU int8 quantizes attention/FFN, not all modules. Neither
int4 nor broader quantization is enabled by this integration.

The package/API/schema/tiny quantized-forward screen does not establish native
voice compatibility, audio quality or memory. After offline generation/listener
acceptance and listening, change image and matching cache path together, preserving
model/voice and idle settings. Recreate **only Pocket** (`sg up -d --no-build
--no-deps pocket-tts`); an already configured Hermes provider needs no rebuild,
restart or reconfiguration. Validate the effective live resource limits first.

## Compare without enabling Hermes audio

The source supports comparison; builds/downloads, listening and activation are
separate operator actions. Nothing here requires changing Hermes configuration
or adding `pocket-tts` to persistent `COMPOSE_PROFILES`. Do not rebuild Hermes.
Keep at least 8 GiB free before prefetch and 5 GiB throughout; check available RAM.
Public 2-GiB/2-CPU service limits remain unchanged. Spanish24l plus English has
previously exceeded 2 GiB; use an explicitly approved isolated **4-GiB/2-CPU**
envelope for **prefetch, benchmark and listener checks**, only with measured host
headroom. Never silently raise production limits or use a cloud provider. Run
candidates sequentially, never concurrently on a shared host. A comparison override
does not configure live deployment; verify its effective limits separately.

After authorizing the sidecar build, run once:

```sh
source scripts/aliases.sh
sg build pocket-tts
```

Choose a private stable comparison location, **not the existing production
cache**. After resource approval, save this as `compose.resources.yml` there:

```yaml
services:
  pocket-tts:
    mem_limit: 4g
    cpus: 2.0
  pocket-tts-prefetch:
    mem_limit: 4g
    cpus: 2.0
```

In this subshell, replace the example path. Use the same explicit override for
any isolated listener lifecycle checks, not only benchmarking. Proxy/DNS must
already be healthy; `--no-deps` prevents starting neighboring services. Prefetch uses the
gated download network; comparison uses only the offline TTS network and a
read-only model mount. `compose run` does not accept `--no-build`: omit `--build`
and use the already-built local image with `--pull never`.

```sh
(
  export TTS_COMPARE_ROOT=/absolute/private/path/tts-comparison
  mkdir -p "$TTS_COMPARE_ROOT/output"
  export POCKET_TTS_SPANISH_MODEL=spanish_24l
  export POCKET_TTS_SPANISH_VOICE=eponine
  export POCKET_TTS_DATA_PATH="$TTS_COMPARE_ROOT/cache-v4.0.1-large-eponine"
  mkdir -p "$POCKET_TTS_DATA_PATH"
  sg -f "$TTS_COMPARE_ROOT/compose.resources.yml" config --quiet
  sg -f "$TTS_COMPARE_ROOT/compose.resources.yml" run --rm --no-deps --pull never \
    --user "$(id -u):$(id -g)" --env USER=operator --env LOGNAME=operator \
    pocket-tts-prefetch
  sg -f "$TTS_COMPARE_ROOT/compose.resources.yml" run --rm --no-deps --pull never \
    --user "$(id -u):$(id -g)" --env USER=operator --env LOGNAME=operator \
    --volume "$TTS_COMPARE_ROOT/output:/comparison:rw,z" \
    --entrypoint python pocket-tts /app/benchmark.py \
    --output-dir /comparison/v4.0.1-large-eponine
)
```

For additional listening comparisons, run sequentially with fresh version-labelled
cache/output names and these finite selections (do not replace the chosen24l reference):

| Model | Voice | Cache suffix / output name |
| --- | --- | --- |
| `spanish` | `eponine` | `standard-eponine` |
| `spanish` | `lola` | `standard-lola` |
| `spanish_24l` | `eponine` | `large-eponine` |
| `spanish_24l` | `lola` | `large-lola` |

A missing predefined embedding is an explicit failed candidate, not permission
to clone a voice or upload audio. Keep the production cache untouched. Each
output directory must be new, outside the model cache, with an existing writable
parent. On failure no sample directory/report is published. The CLI generates
three fixed Spanish WAVs and `report.json` through the service loader/synthesizer,
with offline Hugging Face mode enabled **before** loading. It never contacts Hermes.

Measurements: `load_seconds` is load time for both models. Per-sample
`generation_seconds` excludes loading; `rtf = generation_seconds / audio_seconds`
(below 1 means faster than real time). `peak_rss_bytes` is Linux process peak RSS,
**including Python, the English model and load-time peaks**, not cgroup/GPU memory
or total host usage. The first sample may include warmup. Compare identical
limits and listen for accent, rhythm, numbers and pronunciation before choosing.
Mocked tests do not establish real compatibility, quality, latency or memory.

## Activate

Only after selecting/listening to a candidate and authorizing activation, set
its model/voice and matching cache path in `private/env/local.env`. Defaults below
retain standard/Eponine. Keep 5 GiB free throughout. Prefetch refuses to start
below 8 GiB and removes staging on failure. Immutable cache blobs are hardlinked;
mutable metadata is copied without modifying the active cache during preparation.

```sh
source scripts/aliases.sh
sg build pocket-tts
sg run --rm pocket-tts-prefetch
```

After prefetch succeeds, preserve all existing profiles and append `pocket-tts`.
Example only (do not replace a private profile list wholesale):

```env
COMPOSE_PROFILES=camofox,pocket-tts
POCKET_TTS_DATA_PATH=/absolute/private/path/pocket-tts
POCKET_TTS_SPANISH_MODEL=spanish
POCKET_TTS_SPANISH_VOICE=eponine
```

Enable the Hermes command provider and recreate only affected containers:

```sh
scripts/configure-pocket-tts.py \
  --enable \
  --config /absolute/private/path/hermes/config.yaml
sg up -d --no-build pocket-tts
sg up -d --no-build --force-recreate hermes
```

The helper keeps the first `config.yaml.before-pocket-tts` backup and is idempotent.

## Validate and use occasional audio

```sh
sg ps pocket-tts hermes
printf '%s\n' '[es] Pocket TTS funciona dentro de Hermes.' | \
  sg exec -T hermes python3 /opt/hermes/bootstrap/pocket-tts-client.py \
  --input /dev/stdin --output /tmp/pocket-tts-output.wav
sg exec -T hermes python3 -c \
  'import wave; w=wave.open("/tmp/pocket-tts-output.wav"); print(w.getnframes()/w.getframerate())'
```

Use Hermes `text_to_speech` for full integration acceptance, including attachment
and playback in the selected profile/channel. A stopped/unhealthy/unreachable
service produces an explicit error, with no fallback to Edge or another provider.

Keep text as the normal reply and ask “respóndeme por audio” when wanted. Prefer
a short spoken summary, not reading code verbatim. Selecting a provider does not
itself turn on automatic audio. Do not enable all-replies voice mode or publish
new runtime guides during preparation. Voice-note auto-replies and Discord
voice-channel streaming are separate native/platform capabilities.

## Roll back

```sh
scripts/configure-pocket-tts.py \
  --disable \
  --config /absolute/private/path/hermes/config.yaml
```

Remove only `pocket-tts` from the existing profile list, then:

```sh
sg stop pocket-tts
sg up -d --no-build --force-recreate hermes
```

For an upgrade rollback, restore the retained previous **image and cache path**
together, preserving its model/voice and effective resource limits. For a
model/voice-only rollback, restore the previous model, voice and matching cache
path together; recreate only Pocket after operator approval. The Hermes
provider need not change. Never relink the production cache merely to compare.
Removing images/caches is a separate explicit cleanup step. Failed prefetch never
changes the `active` cache symlink.
