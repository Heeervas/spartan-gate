# Spartan Route V1

`infra/spartan-route/` is a parallel router package for rebuilding ClawRoute
with a smaller reliability-first surface. It does not replace
`packages/clawroute/`; both can exist in the same checkout and both can run in
Compose while cutover is evaluated.

## Implementation Choice

Options considered:

- Trim the current TypeScript ClawRoute package. This keeps the strongest
  compatibility but leaves the service inside the same large policy graph that
  caused slow recovery and fragile failure modes.
- Restart from the original ClawRoute TypeScript repo. This restores the early
  simplicity but would require replaying Codex slots, prompt-cache affinity,
  image generation, dashboards, and telemetry anyway.
- Build a Python `aiohttp` proxy. This is the v1 choice: one runtime dependency,
  simple async streaming, direct SQLite telemetry, fewer background loops, and a
  smaller failure domain. The tradeoff is that v1 intentionally supports fewer
  native provider formats than ClawRoute.
- Build in Go. This would likely be faster and easy to ship as one binary, but
  it would slow iteration on Codex auth, dashboards, and telemetry while the
  behavior is still being rediscovered.

Winner: Python `aiohttp`, because the service is an OpenAI-compatible proxy and
the bottleneck is upstream latency/reliability, not local CPU. The v1 goal is to
make the route decision and failure handling obvious.

## V1 Surface

Relevant files:

- `infra/spartan-route/Dockerfile` and `requirements.txt`: Python 3.12 runtime
  with `aiohttp`.
- `infra/spartan-route/spartan_route/app.py`: HTTP server, auth, routing,
  OpenAI-compatible endpoints, provider passthrough, image endpoints, and
  dashboard APIs.
- `infra/spartan-route/spartan_route/codex.py`: Codex OAuth slots, retry and
  cooldown handling, prompt-cache key/affinity, SSE conversion, usage checks,
  and reset-credit fetch/normalization.
- `infra/spartan-route/spartan_route/telemetry.py`: isolated SQLite schema,
  daily cache rollups, live sessions, usage snapshots, and reset-credit tables.
- `infra/spartan-route/spartan_route/config.py`: reads existing
  `config/clawroute` provider profiles while defaulting to
  `/app/data/spartan-route.db`.
- `infra/spartan-route/web/dashboard.html`: session and turn dashboard.
- `infra/spartan-route/web/dashboard-codex.html`: Codex usage, slot state,
  persistent slot routing controls, daily usage-cache percentage, latency, active
  leases, reset countdowns, and mandatory reset-credit showcase.
- `infra/compose/compose.spartan-route.yml`: full-stack side-by-side overlay.
- `infra/compose/tiers/compose.spartan-route.yml`: tier-helper side-by-side
  overlay.
- `infra/spartan-route/test_*.py`: focused regression suite.

Kept in v1:

- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`,
  `/v1/images/generations`, and `/v1/images/edits`.
- `clawroute/auto` and `custom-1/clawroute/auto` model aliases for Hermes.
- Multiple Codex auth slots from `OPENAI_CODEX_AUTH_PATHS`.
- Prompt-cache keys based on explicit `prompt_cache_key`, `user`, or stable
  system/tool prefix shape.
- Per-key Codex cache leases, rate-limit cooldowns, transient 5xx cooldowns,
  and retry across slots/accounts.
- Codex image generation and no-mask image edits, with OpenAI image endpoint
  passthrough when `OPENAI_API_KEY` is available. The Codex-auth image fallback
  accepts OpenAI `gpt-image-2` sizes such as `1536x1024` and `1024x1536`,
  validates the documented request-size constraints, and returns Codex image
  result bytes unchanged even when upstream produces different pixel
  dimensions or aspect ratio. If dimensions can be detected, Spartan Route
  includes `actual_size` on the image item.
- Reset-credit telemetry and dashboard display. V1 only reads and persists
  reset-credit metadata; it never redeems credits.
- Daily usage-cache rows from `routing_log`.
- Today's dashboard latency from logged request rows. For new rows,
  `response_time_ms`/`responseTimeMs` means first upstream response time, while
  `total_response_time_ms`/`totalResponseTimeMs` means full request or stream
  duration through logging. Older rows without those split columns fall back to
  the legacy `response_time_ms` value and are marked as legacy latency. The
  dashboard uses split-only `firstResponseLatencyMs` when available and labels
  fallback-only days as legacy response time. Usage-cache percentage still uses
  only rows where upstream usage metadata was seen; request and latency
  counters can include rows without usage.
- Persistent Codex slot routing enable/disable controls. Disabled slots are
  skipped by chat and image routing, leases for that slot/account are
  invalidated, and the override survives service restart. If a stored override
  clearly belongs to a different slot identity, Spartan Route ignores that
  stale disable and reports an identity mismatch in the dashboard/API.
- Cached Codex usage polling for the dashboard. Normal `/api/codex/usage`
  calls reuse a short-lived in-process snapshot; `?force=true` or
  `POST /api/codex/usage/slots/{slot}` performs a live check.
- Authenticated Codex dashboard/API responses include the configured Codex auth
  path for each slot so local operators can identify accounts. Public health
  responses keep only hashes and never expose raw slot paths.
- The Codex dashboard groups usage windows and slot controls by account. Slots
  without live usage telemetry still render as slot-only cards, and reset times
  are shown as countdowns with exact timestamps available on hover.

With `CLAWROUTE_DEBUG=true`, Spartan Route logs a redacted summary of outbound
image passthrough payloads. The summary includes fields such as `model`, `size`,
and `quality`; prompts remain redacted unless `CLAWROUTE_LOG_CONTENT=true`.

Reliability contract:

- Codex Responses streams are converted to Chat Completions with explicit
  handling for text, tool calls, structured reasoning, raw/encrypted reasoning,
  incomplete responses, and terminal upstream errors.
- A successful upstream Codex response with no usable text, tool call, or
  reasoning payload is not returned to Hermes as `200` empty content. Spartan
  Route classifies it as retryable `502 codex_empty_response`, rotates to the
  next eligible slot/account, and only returns that error after slot retry is
  exhausted.
- A Codex stream that times out or fails while reading after response headers is
  a transport failure, not a successful empty turn. Streaming Chat Completions
  receive `event: error` with `codex_stream_timeout` or `codex_stream_error`
  followed by `[DONE]`; streaming Responses receive the same error and then
  `response.failed`. Non-streaming reads return retryable `502` with the same
  stable codes. Local stream read failures do not put the selected slot into the
  generic transient 5xx cooldown; terminal upstream server errors still do.
- `CODEX_REQUEST_TIMEOUT_MS` matches ClawRoute semantics: it bounds the wait
  for Codex response headers, not the total duration of the streamed response
  body.
- If all usable Codex slots are cooling after upstream server errors, Spartan
  Route waits up to `SPARTAN_ROUTE_RECOVERY_WAIT_MS` for a slot to recover
  before returning `503 codex_transient_unavailable` with sanitized retry
  metadata and `Retry-After`. The default wait is `180000` ms; set it to `0`
  to disable server-side waiting. `SPARTAN_ROUTE_RECOVERY_POLL_MS` controls the
  fallback poll interval and defaults to `1000` ms.
- If all usable Codex slots are cooling after explicit `usage_limit_reached`
  errors, Spartan Route applies the same bounded wait when the earliest reset
  is inside the recovery window. If recovery is too far away, it returns
  ClawRoute-compatible `429 codex_429` with earliest `resets_at`,
  `resets_in_seconds`, slot diagnostics, and `Retry-After`.
- An incomplete Codex response that contains tool calls is treated as retryable
  `502 codex_incomplete_response`; Spartan Route must not expose incomplete
  tool-call arguments to Hermes as a successful `finish_reason="length"` turn.
- Streaming tool-call deltas are buffered and emitted only after a terminal
  successful Codex event, so Hermes never receives partial tool arguments from
  an upstream stream that later becomes incomplete.
- Spartan Route does not implement Hermes gateway silence markers such as
  `[SILENT]` or `NO_REPLY`; those are delivery semantics above the router.
- Cache diagnostics in `context_info` are metadata-only by default: cache-key
  hash, cache key source, usage-event visibility, broad prefix-candidate and
  expected-hit flags, stricter cache-likely/low-cache flags, expectation reason,
  non-tool input token estimate, message/tool counts, request-shape hashes,
  tool-schema fingerprint, and same-session prefix comparison. Raw tool schemas,
  raw cache keys, and auth values are not added to these diagnostics.
- Low-cache warnings use the stricter `cache_likely` subset, not every broad
  prefix candidate. By default a request is cache-likely only when it has an
  upstream cache key, extends the previous same-session request as a prefix,
  has usage metadata and no request error, shares at least 50 prefix messages,
  and has at least 50000 non-tool input tokens. Tune those last two thresholds
  with `SPARTAN_ROUTE_CACHE_LIKELY_MIN_PREFIX_MESSAGES` and
  `SPARTAN_ROUTE_CACHE_LIKELY_MIN_NON_TOOL_TOKENS`.
- Large low-cache stream failures also update a diagnostic-only stall guard in
  `context_info.cache_stall_diagnostic`. It records consecutive stream-failure
  counts and stable error codes for the selected model/cache/slot/account/tool
  shape, but does not block requests in v1. Tune it with
  `SPARTAN_ROUTE_CACHE_STALL_GUARD_ENABLED`,
  `SPARTAN_ROUTE_CACHE_STALL_MIN_INPUT_TOKENS`, and
  `SPARTAN_ROUTE_CACHE_STALL_LOW_CACHE_RATIO`.
- When `CLAWROUTE_LOG_CONTENT=true`, routing rows also store sanitized request
  previews for the dashboard. Spartan Route prefers the latest user message
  over the system prompt, strips common Hermes metadata preambles, redacts
  secret-looking tokens, and records tool calls as function name plus argument
  keys/size rather than raw arguments.
- Prior assistant reasoning is not replayed into the next upstream Codex
  request by default, which keeps the stable prefix smaller and less volatile.
  Set `SPARTAN_ROUTE_REPLAY_ASSISTANT_REASONING=1` only as a rollback switch.

Cut from v1 on purpose:

- Balance-loader scheduling, early activation, manual slot activation, capacity
  multipliers, cold-migration approval flow, cache-miss breaker, alerting, and
  the analysis dashboard.
- Native Anthropic Messages format. Use OpenAI-compatible Chat Completions or
  OpenRouter-compatible models.
- Broad provider-specific translation. V1 passes through OpenAI-compatible
  providers and returns clean JSON for unsupported native providers.
- Quota-unit/session-cost estimates. Those are deferred to v2; v1 keeps raw
  usage-window percentages, cache metrics, and latency.

## Run

Tier overlay:

```sh
docker compose \
  -f infra/compose/tiers/compose.l1.yml \
  -f infra/compose/tiers/compose.spartan-route.yml \
  --env-file private/env/local.env \
  config --quiet
```

Full-stack side-by-side overlay:

```sh
source scripts/aliases.sh
sg route up -d
```

This uses the normal full-stack private Compose path plus
`infra/compose/compose.spartan-route.yml` and optional
`private/compose.spartan-route.local.yml` overrides. Hermes points at
`http://spartan-route:18790/v1` in this mode. Plain `sg up -d` remains the
normal ClawRoute path and does not load Spartan Route-only private overrides.

Spartan Route is exposed through Caddy on `SPARTAN_ROUTE_EDGE_PORT`, default
`18791`. For local private stacks that still reserve legacy Mission Control
port `18791`, set `SPARTAN_ROUTE_EDGE_PORT=18792`. Private HTTPS stacks should
use the Spartan Route URL printed by `sg-urls`; plain HTTP stacks use
`http://localhost:<SPARTAN_ROUTE_EDGE_PORT>/dashboard-codex`. The existing
ClawRoute service and `packages/clawroute/` remain untouched.

Dashboard and `/api/*` requests require the same bearer token as model
requests when `CLAWROUTE_TOKEN` is configured. Query-string `?token=...`
authentication is intentionally not accepted; enter the token in the dashboard
field or send `Authorization: Bearer <token>`.

## Validation

```sh
python3 -m py_compile infra/spartan-route/spartan_route/*.py infra/spartan-route/test_*.py
docker build -t spartan-route:test infra/spartan-route
docker run --rm -v "$PWD/infra/spartan-route:/tests:ro" -w /tests \
  spartan-route:test python3 -m unittest discover -s /tests -p 'test_*.py'
docker compose -f infra/compose/compose.yml -f infra/compose/compose.spartan-route.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml -f infra/compose/tiers/compose.spartan-route.yml config --quiet
```
