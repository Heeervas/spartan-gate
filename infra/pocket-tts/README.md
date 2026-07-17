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

- English: `anna`
- Spanish: `eponine`
- Heuristic automatic detection is the default; use a directive for short or
  ambiguous text.
- Prefix text with `[en]` or `[es]` to force a language for an ambiguous
  sentence. The prefix is removed before synthesis.

The integration uses Pocket TTS' public non-cloning weights. Custom voice
cloning and arbitrary voice URLs are intentionally not exposed.

## Activate

Keep at least 5 GiB free throughout. The prefetch refuses to start below 8 GiB
free and removes its staging directory on failure. When replacing an existing
cache version, immutable Hugging Face blobs are hardlinked into staging while
mutable metadata is copied, avoiding a second full copy without modifying the
active cache.

```sh
source scripts/aliases.sh
sg build pocket-tts
sg run --rm pocket-tts-prefetch
```

After prefetch succeeds, add `pocket-tts` to the existing browser profile in
`private/env/local.env`, for example:

```env
COMPOSE_PROFILES=camofox,pocket-tts
POCKET_TTS_DATA_PATH=/absolute/private/path/pocket-tts
```

Enable the Hermes command provider and recreate only the affected containers:

```sh
scripts/configure-pocket-tts.py \
  --enable \
  --config /absolute/private/path/hermes/config.yaml
sg up -d --no-build pocket-tts
sg up -d --no-build --force-recreate hermes
```

The configuration helper keeps the first pre-Pocket backup as
`config.yaml.before-pocket-tts` and is idempotent.

## Validate

```sh
sg ps pocket-tts hermes
printf '%s\n' '[es] Pocket TTS funciona dentro de Hermes.' | \
  sg exec -T hermes python3 /opt/hermes/bootstrap/pocket-tts-client.py \
  --input /dev/stdin \
  --output /tmp/pocket-tts-output.wav
sg exec -T hermes python3 -c \
  'import wave; w=wave.open("/tmp/pocket-tts-output.wav"); print(w.getnframes()/w.getframerate())'
```

Use the Hermes `text_to_speech` tool for the full integration check. A stopped,
unhealthy, or unreachable Pocket service returns an explicit TTS error. The
command provider does not fall through to Edge TTS or another provider.

## Roll back

```sh
scripts/configure-pocket-tts.py \
  --disable \
  --config /absolute/private/path/hermes/config.yaml
```

Remove `pocket-tts` from `COMPOSE_PROFILES`, then run:

```sh
sg stop pocket-tts
sg up -d --no-build --force-recreate hermes
```

Removing the Pocket image or versioned cache is a separate, explicit cleanup
step. A failed prefetch never changes the `active` cache symlink.
