# Spartan Route V1

`infra/spartan-route/` is the current L4 router package. It replaces ClawRoute
as the full-stack OpenAI-compatible router while keeping the existing
`CLAWROUTE_TOKEN`, `CLAWROUTE_EDGE_PORT`, model aliases, and `config/clawroute`
runtime profiles for compatibility. `packages/clawroute/` remains in the repo
for the legacy lower-tier addon and source-package work.

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
- `infra/compose/compose.yml`: full-stack baseline service and Hermes wiring.
- `infra/spartan-route/web/dashboard-config.html`: authenticated runtime model
  overrides for the active `clawroute/auto` classifier tiers.
- `infra/compose/compose.spartan-route.yml`: compatibility no-op overlay for
  older commands that still include it.
- `infra/compose/tiers/compose.spartan-route.yml`: tier-helper side-by-side
  overlay.
- `infra/spartan-route/test_*.py`: focused regression suite.

Kept in v1:

- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`,
  `/v1/images/generations`, and `/v1/images/edits`.
- Native Hermes `openai-api` provider discovery through `OPENAI_BASE_URL`.
- Profile `.env` route keys may reference `${OPENAI_API_KEY}` or legacy
  `${CUSTOM_1_API_KEY}`; those two route-key aliases delegate to the named
  container environment key. References to unrelated keys remain literal and
  are never expanded.
- `clawroute/auto`, `custom-1/clawroute/auto`, and `spartan-route/clawroute/auto` model aliases for Hermes compatibility.
- Selectable Codex GPT-5.6 catalog entries for manual Hermes sessions:
  `codex/gpt-5.6`, `codex/gpt-5.6-sol`, `codex/gpt-5.6-terra`, and
  `codex/gpt-5.6-luna`. The Codex provider profile still defaults to
  `codex/gpt-5.5` until these variants are promoted explicitly.
- `reasoning_effort=max` is accepted only for GPT-5.6 variants and is sent to
  Codex exactly as `reasoning.effort=max`. Other Codex model families receive
  `400 codex_max_requires_gpt_5_6` before slot selection.
- `reasoning_effort=ultra` is never silently downgraded to `max`. Chat
  Completions and Responses, streaming or non-streaming, return
  `400 codex_ultra_requires_app_server`, log the attempt, and do not select a
  Codex slot.
- Multiple Codex auth slots from `OPENAI_CODEX_AUTH_PATHS`. Configured auth
  files are read-only bootstrap inputs. Spartan Route stores rotated OAuth
  generations separately in `/app/data/codex-auth`, keyed by a SHA-256 source
  identity, and never rewrites a Codex CLI or editor profile.
- Prompt-cache keys based on explicit `prompt_cache_key`, `user`, or stable
  system/tool prefix shape.
- Per-key Codex cache leases, rate-limit cooldowns, transient 5xx cooldowns,
  and retry across slots/accounts.
- Codex image generation and image edits, including multipart PNG alpha masks
  translated to Responses `input_image_mask`. Edits set `action=edit` and
  forward validated `input_fidelity=low|high`; coordinates in prompt text are
  not a pixel boundary. OpenAI image endpoint passthrough remains active when
  `OPENAI_API_KEY` is available. The Codex-auth image fallback accepts OpenAI
  `gpt-image-2` sizes such as `1536x1024` and `1024x1536`, validates the
  documented request-size constraints, and returns Codex image result bytes
  unchanged even when upstream produces different pixel dimensions or aspect
  ratio. If dimensions can be detected, Spartan Route includes `actual_size`
  on the image item.
- Reset-credit telemetry and dashboard display. V1 only reads and persists
  reset-credit metadata; it never redeems credits.
- Bounded asynchronous routing telemetry: 1,024 pending rows by default,
  saturation drops telemetry rather than model requests, database failures do
  not stop the worker, and normal shutdown drains accepted entries for a
  bounded interval. Public health exposes counters and sanitized database
  read/write/maintenance state only, never request data or database paths.
- Codex usage endpoints keep a successfully fetched live payload at HTTP 200
  when snapshot/reset persistence or the daily cache read fails. Such payloads
  set `partial: true`, include sanitized `telemetryErrors`, and use an empty
  `cacheDaily` only when that derived data is unavailable. Telemetry-only
  endpoints return HTTP 503 with `telemetry_unavailable`; `/health` remains
  HTTP 200 with `degraded: true` while inference can still be served, and the
  Codex dashboard displays the partial-data state.
- `routing_log` is pruned to 30 days and at most 50,000 rows at startup and
  after bounded write batches. Pruning and derived `routing_daily_rollups`
  rebuilds share one immediate transaction; Codex snapshot history, current
  snapshots, reset credits, slot state, and routing overrides remain intact.
- Daily usage-cache rows and rollup rebuilds stream over the retained
  `routing_log` window and aggregate in bounded process memory. They do not ask
  SQLite to materialize temporary group/sort tables, so the service's fixed
  32 MiB `/tmp` mount does not scale with telemetry history.
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
- Persisted-first Codex usage polling for the dashboard. Normal
  `/api/codex/usage` calls return a fresh memory snapshot or the latest
  database snapshot immediately, then start one background live refresh.
  Accounts and their usage/reset-credit requests refresh concurrently with a
  bounded fan-out. `?force=true` waits for that shared refresh;
  `POST /api/codex/usage/slots/{slot}` performs a focused live check.
- Authenticated Codex dashboard/API responses include the configured Codex auth
  path for each slot so local operators can identify accounts. Public health
  responses keep only hashes and never expose raw slot paths.
- The Codex dashboard presents remaining weekly balance as the primary wheel.
  The five-hour wheel appears only when upstream returns a recognized primary
  window; a successful weekly-only response removes the obsolete current
  five-hour snapshot while retaining its history, and support resumes
  automatically if the window returns. Slots without usage telemetry still
  render as slot-only cards, and reset times include countdowns and timestamps.
- The routing dashboard presents sessions as a prompt-led activity feed with
  model, cache percentage, requested reasoning, errors, and actual tool-call
  activity visible before expansion. Session, turn, request, account, slot, and
  cache identifiers remain available in disclosed diagnostics. Actual tool
  counts/names are recorded without arguments when observable; older or
  unobservable rows report unknown rather than zero.

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
- Dashboard `usage cache` percentages are aggregate usage-visible rows for the
  selected day, session, or turn. Dashboard `likely cache` percentages are the
  mature cache-likely subset only. Individual expanded request rows show their
  own cached-token percentage, which can be much higher or lower than the
  aggregate.
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

## Dashboard Runtime Routing Overrides

`/dashboard-config` edits the primary model used by `clawroute/auto` for the
four tiers the current classifier emits: `heartbeat`, `simple`, `moderate`, and
`complex`. It does not expose the dormant frontier tiers or configured fallback
fields as active controls.

The page and its API use the normal dashboard bearer-token contract:

- `GET /api/config/routing` returns file defaults, runtime overrides, effective
  mappings, eligible models, non-secret readiness, limitations, and an `ETag`.
- `PUT /api/config/routing` requires the current `ETag` in `If-Match` and saves
  one complete four-tier mapping.
- `DELETE /api/config/routing` requires the same precondition and resets all
  four tiers to the file-loaded defaults.

Dashboard changes are stored in `routing_model_overrides` in
`spartan-route.db`; the read-only `config/clawroute` mount is never written.
The service validates the whole stored set at startup. An incomplete, unknown,
disabled, non-tool, or unsupported-provider target causes the entire override
to be ignored in favor of file defaults, with an authenticated dashboard
warning. Updates and resets are serialized and transactional, and stale browser
tabs receive `412 config_revision_conflict` instead of overwriting newer state.

The Codex dashboard also classifies each slot as healthy, failed, unavailable,
history, or disabled. Only a current failure on a routing-enabled slot makes the
account card red; the card also shows a `Failed` badge and text reason. Active
cooldowns are warnings, cached-only errors are history, and operator-disabled
slots remain neutral. Each slot also reports its safe OAuth lifecycle source,
expiry, last successful refresh, persistence health, and sanitized error code.

Rotating Codex grants are persisted atomically before use. The managed
`codex-auth` directory is private (`0700`) and its state/lock files are `0600`;
foreign-owned, symlinked, or non-regular objects fail closed. Refreshes are
coordinated across router processes so only one process consumes a generation.
Terminal grant failures require a new login, while quota, network, lock, and
local persistence failures remain retryable and do not imply that the user
should reauthenticate. After a manual login, the changed bootstrap fingerprint
automatically clears the terminal quarantine. Do not copy one refresh grant
between slots: every slot needs an independent local login. Treat backups of
`/app/data/codex-auth` as credentials, not ordinary telemetry.

Provider credentials, Codex tokens, raw auth data, and slot paths are not
returned by the Config API. Query-string bearer tokens remain unsupported.

## Cut From V1 On Purpose

- Authentic OpenAI Ultra execution is deferred. Hermes main currently exposes
  the selector and app-server scaffolding, but the upstream multi-agent
  integration is still under review. Spartan Route therefore fails closed
  instead of translating ordinary OpenAI-compatible requests into `max`.
  The reviewed implementation plan is `plans/ultra-bridge/plan.md`.
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

Full L4 stack:

```sh
source scripts/aliases.sh
sg up -d
```

`sg route up -d` remains a compatibility alias for the same full-stack Compose
path. Hermes points at `http://spartan-route:18790/v1`, and Caddy keeps the
router edge on `CLAWROUTE_EDGE_PORT` so existing local URLs continue to work.

Lower-tier side-by-side overlay:

```sh
docker compose \
  -f infra/compose/tiers/compose.l1.yml \
  -f infra/compose/tiers/compose.spartan-route.yml \
  --env-file private/env/local.env \
  config --quiet
```

The full-stack baseline mounts the local Hermes `spartan-cache-lineage` plugin
at `/opt/data/plugins/spartan-cache-lineage`. Hermes user plugins are opt-in,
so enable it once in the active Hermes data profile:

```sh
sg exec hermes hermes plugins enable spartan-cache-lineage
sg up -d --force-recreate hermes
sg exec hermes hermes plugins list --plain --no-bundled | rg spartan-cache-lineage
```

The plugin owns Spartan Route cache lineage metadata. It can preserve visible
`reasoning_effort` request fields, but it cannot observe the original
`reasoning_config` after Hermes has normalized provider profiles. The transport
patch therefore preserves raw `max`/`ultra` only for Spartan Route requests;
do not remove it until Hermes provides an equivalent pre-normalization hook.

Spartan Route is exposed through Caddy on `CLAWROUTE_EDGE_PORT`, default
`18790`, for compatibility with existing router URLs. Private HTTPS stacks
should use the scheme and hostname printed by `sg-urls`; if the private Caddy
file is HTTPS-only, plain `http://...:<port>` is expected to fail while HTTPS
through the certificate-covered private hostname reaches Caddy.
`SPARTAN_ROUTE_EDGE_PORT` is only for older side-by-side tier overlays.

Dashboard and `/api/*` requests require the same bearer token as model
requests when `CLAWROUTE_TOKEN` is configured. Query-string `?token=...`
authentication is intentionally not accepted; enter the token in the dashboard
field or send `Authorization: Bearer <token>`.

## Validation

```sh
python3 -m py_compile infra/spartan-route/spartan_route/*.py infra/spartan-route/test_*.py
python3 -m unittest infra/hermes-plugins/test_spartan_cache_lineage.py
docker build -t spartan-route:test infra/spartan-route
docker run --rm -v "$PWD/infra/spartan-route:/tests:ro" -w /tests \
  spartan-route:test python3 -m unittest discover -s /tests -p 'test_*.py'
docker compose -f infra/compose/compose.yml --env-file .env.example config --quiet
docker compose -f infra/compose/compose.yml -f infra/compose/compose.spartan-route.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml -f infra/compose/tiers/compose.spartan-route.yml config --quiet
```
