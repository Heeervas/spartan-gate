# Pocket TTS sidecar

Spartan Gate runs Pocket TTS 2.1.0 as an optional local English/Spanish
sidecar. Hermes uses its command-provider interface, so enabling this service
does not rebuild the Hermes image.

The runtime has no published port and joins only `pocket_tts_internal`, a
Docker-internal network shared with Hermes. It has no proxy, external network,
Hugging Face token, or arbitrary voice-upload endpoint. The one-shot prefetch
service is the only component allowed to download model files, through the
normal Spartan Gate proxy and DNS boundary.

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

The default cache identity is unchanged. Each alternative model/voice combination
has a distinct identity and manifest; changing selection without matching
prefetch causes startup refusal, not fallback. A larger model is not a verified
quality improvement on a particular CPU. The selected model-specific predefined
embedding must exist upstream; constructing its pinned path is not proof of asset
availability. No Spanish-from-Spain accent is guaranteed by the voice name.

## Compare without enabling Hermes audio

The source supports comparison; builds/downloads, listening and activation are
separate operator actions. Nothing here requires changing Hermes configuration
or adding `pocket-tts` to persistent `COMPOSE_PROFILES`. Do not rebuild Hermes.
Keep at least 8 GiB free before prefetch and 5 GiB throughout; check available RAM.
Existing 2-GiB/2-CPU service limits remain unchanged. If the large model cannot
load within them, record the failure; do not silently raise limits or use a cloud
provider. Run candidates sequentially, never concurrently on a shared host.

After authorizing the sidecar build, run once:

```sh
source scripts/aliases.sh
sg build pocket-tts
```

Choose a private stable comparison location, **not the existing production
cache**. In this subshell, replace the example path. Proxy/DNS must already be
healthy; `--no-deps` prevents starting neighboring services. Prefetch uses the
gated download network; comparison uses only the offline TTS network and a
read-only model mount. `compose run` does not accept `--no-build`: omit `--build`
and use the already-built local image with `--pull never`.

```sh
(
  export TTS_COMPARE_ROOT=/absolute/private/path/tts-comparison
  mkdir -p "$TTS_COMPARE_ROOT/output"
  export POCKET_TTS_SPANISH_MODEL=spanish
  export POCKET_TTS_SPANISH_VOICE=eponine
  export POCKET_TTS_DATA_PATH="$TTS_COMPARE_ROOT/cache-standard-eponine"
  mkdir -p "$POCKET_TTS_DATA_PATH"
  sg run --rm --no-deps --pull never \
    --user "$(id -u):$(id -g)" --env USER=operator --env LOGNAME=operator \
    pocket-tts-prefetch
  sg run --rm --no-deps --pull never \
    --user "$(id -u):$(id -g)" --env USER=operator --env LOGNAME=operator \
    --volume "$TTS_COMPARE_ROOT/output:/comparison:rw,z" \
    --entrypoint python pocket-tts /app/benchmark.py \
    --output-dir /comparison/standard-eponine
)
```

Repeat sequentially with fresh candidate cache/output names and these selections:

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

For a model/voice-only rollback, restore the previous model, voice and matching
cache path together; recreate only Pocket after operator approval. The Hermes
provider need not change. Never relink the production cache merely to compare.
Removing images/caches is a separate explicit cleanup step. Failed prefetch never
changes the `active` cache symlink.
