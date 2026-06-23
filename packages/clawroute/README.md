# ClawRoute

ClawRoute is the internal OpenAI-compatible LLM router used by Spartan Gate. Hermes calls ClawRoute at `/v1`, and ClawRoute selects the configured upstream provider or model route.

## Development

```sh
npm ci
npm test
npm run build
```

## Runtime

In the Spartan Gate Compose stack, ClawRoute listens on `0.0.0.0:18790` inside the internal Docker network. Hermes authenticates with `CLAWROUTE_TOKEN`.

Provider keys are read from environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `DEEPSEEK_API_KEY`
- `OPENROUTER_API_KEY`

Codex subscription routing can be enabled from a private override by mounting auth files and setting `OPENAI_CODEX_AUTH_PATH` or `OPENAI_CODEX_AUTH_PATHS`.

## Image API

ClawRoute exposes OpenAI-compatible image endpoints for `gpt-image-2`:

- `POST /v1/images/generations`
- `POST /v1/images/edits`

Use `model: "gpt-image-2"` or `model: "openai/gpt-image-2"`. Quality is a
separate request field, for example `quality: "medium"`. ClawRoute does not
support quality-suffixed model IDs such as `gpt-image-2-medium`.

Image edits accept OpenAI-style multipart requests:

```sh
curl -X POST "$CLAWROUTE_BASE_URL/v1/images/edits" \
  -H "Authorization: Bearer $CLAWROUTE_TOKEN" \
  -F "model=gpt-image-2" \
  -F "prompt=Change the red square to blue" \
  -F "image=@red.png" \
  -F "size=1024x1024" \
  -F "quality=medium" \
  -F "response_format=b64_json"
```

Multiple references can be sent as repeated `image` fields or as `image[]`.
ClawRoute normalizes them to upstream `image[]` when using `OPENAI_API_KEY`.
When no OpenAI API key is configured, ClawRoute translates the same request to
the Codex Auth image flow and returns the same OpenAI-compatible
`data[0].b64_json` response shape.

`response_format=b64_json` is accepted for compatibility. `input_fidelity` is
not forwarded for `gpt-image-2`; the upstream model processes image inputs at
high fidelity automatically. Mask edits are forwarded on the OpenAI API-key
path; Codex Auth mask edits return a clear unsupported error until that backend
path is proven.
