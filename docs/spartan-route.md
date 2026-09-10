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

## Native Responses update surface

- Spartan Route owns native `/v1/responses` transport, subscription slot retry,
  SSE commit semantics, realm binding, and reasoning-token telemetry.
- The `spartan-cache-lineage` Hermes plugin owns profile/session chain identity,
  signed replay metadata, and safe reset when history comes from another
  profile or provider realm.
- Hermes configs own `api_mode: codex_responses`; automatic migration changes
  only explicit `codex/*` models because `clawroute/auto` may resolve outside
  the subscription realm.
- The shared private env owns `SPARTAN_REASONING_BINDING_SECRET`; Compose passes
  it only to Hermes and Spartan Route.
- The selected hermes-lcm revision must round-trip `codex_reasoning_items` and
  `codex_message_items`. This is an upstream plugin contract, not a Hermes image
  patch.

Kept in v1:

- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`,
  `/v1/images/generations`, and `/v1/images/edits`.
- Native Hermes `openai-api` provider discovery through `OPENAI_BASE_URL`.
- Profile `.env` route keys may reference `${OPENAI_API_KEY}` or legacy
  `${CUSTOM_1_API_KEY}`; those two route-key aliases delegate to the named
  container environment key. References to unrelated keys remain literal and
  are never expanded.
- `clawroute/auto`, `custom-1/clawroute/auto`, and `spartan-route/clawroute/auto` model aliases for Hermes compatibility.
- GPT-6 Astra is selectable as `codex/gpt-6-astra` through the existing
  Spartan Route `openai-api` provider in Hermes. Use `api_mode: codex_responses`
  for native Responses sessions. It supports tools and image input, with an
  advertised maximum context of 872,000 tokens from the Codex Subscription
  catalog (distinct from the public API context limit). Adding it does not
  change active Hermes profiles or auto-routing defaults.
- Selectable Codex GPT-5.6 catalog entries for manual Hermes sessions:
  `codex/gpt-5.6`, `codex/gpt-5.6-sol`, `codex/gpt-5.6-terra`, and
  `codex/gpt-5.6-luna`. The Codex provider profile still defaults to
  `codex/gpt-5.5` until these variants are promoted explicitly.
- `reasoning_effort=max` is accepted for GPT-5.6 variants and GPT-6 Astra and is sent to
  Codex exactly as `reasoning.effort=max`. Other Codex model families receive
  `400 codex_max_requires_gpt_5_6` before slot selection; the legacy error code
  is retained for compatibility.
- `reasoning_effort=ultra` is never silently downgraded to `max`. Chat
  Completions and Responses, streaming or non-streaming, return
  `400 codex_ultra_requires_app_server`, log the attempt, and do not select a
  Codex slot.
- `/v1/responses` is transported natively for Codex subscription and OpenAI
  API-key providers; it is never converted through Chat Completions. Requests
  use `store=false` and include `reasoning.encrypted_content` for manual opaque
  reasoning replay. Subscription traffic goes to the ChatGPT Codex Responses
  backend; API-key traffic goes to the configured `/v1/responses` endpoint.
- Signed reasoning affinity binds `HERMES_HOME + session_id` to exactly one
  provider realm. Sol/Terra model changes remain valid inside the subscription
  realm. Switching between subscription and API-key realms returns
  `409 reasoning_realm_mismatch`; start a new session or branch. Copying a
  session between profiles strips foreign opaque reasoning and server item IDs
  while preserving visible history. The same textual session ID in two profiles
  is safe because canonical `HERMES_HOME` is part of the signed chain identity.
  Background review with `provider: auto` inherits that runtime and realm.
- Codex subscription replay rejects unsigned encrypted reasoning. It also
+  rejects `store=true`, `previous_response_id`, native compaction items,
+  `context_management`, `reasoning.mode`, and non-function tools until Hermes
+  can preserve those contracts end to end. OpenAI API-key Responses remains a
+  separate explicit realm and native transport.
- Multiple Codex auth slots from `OPENAI_CODEX_AUTH_PATHS`. Configured auth
  files are read-only bootstrap inputs. Spartan Route stores rotated OAuth
  generations separately in `/app/data/codex-auth`, keyed by a SHA-256 source
  identity, and never rewrites a Codex CLI or editor profile. When the
  bootstrap identifies an account, managed state is eligible only for that
  same account; among same-account generations, the furthest valid expiry
  wins. This prevents a newer token for another account from shadowing the
  configured slot.
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
- Daily usage-cache reads use only the incremental
  `routing_daily_rollups` table. Ordinary `/api/codex/usage` reads and
  dashboard polls use bounded snapshot/window aggregate tables and never scan
  `routing_log`. Startup and retention rebuilds reconstruct the additive daily
  rollup from retained rows using SQLite `date(timestamp)`, so offset
  timestamps use the same UTC-day rule as incremental writes.
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
  invalidated, and the override survives service restart and normal OAuth token
  rotation. Durable account/path identity changes cause Spartan Route to ignore
  that stale disable and report an identity mismatch in the dashboard/API;
  missing current identity remains fail-closed and is reported as unverified.
- Persisted-first Codex usage polling uses one progressive,
  controller-owned single-flight refresh. Auth discovery runs with fan-out four
  and initially reports `refreshProgress.status: "discovering"` with
  `totalAccounts: null`; the remaining statuses are exactly `running`,
  `complete`, and `failed`. Completed accounts are persisted and published
  independently. Any state with `0 < completedAccounts < totalAccounts` has
  `source: "mixed"`; each slot independently reports
  `healthDataSource: "pending" | "live" | "persisted"`. Authenticated
  `POST /api/codex/usage/refresh` returns 202 immediately, `?force=true`
  remains the shielded compatibility wait path, and
  `POST /api/codex/usage/slots/{slot}` remains a focused live check whose
  response is merged into the complete current account projection. A focused
  check updates or prunes only the selected account's live windows; it preserves
  every other account's rings and derived analytics until a full-pool refresh
  can recompute global coverage. Persisted account/reset data is shown only when
  its exact account key belongs to a currently configured slot.
- Authenticated Codex dashboard/API responses include the configured Codex auth
  path for each slot so local operators can identify accounts. Public health
  responses keep only hashes and never expose raw slot paths.
- The Codex dashboard presents normalized quota units as the primary balance:
  x1 plans have 100 units and x5 plans have 500 units. The authenticated
  `PATCH /api/codex/balancer/accounts/{account_key}/capacity` endpoint accepts
  only `capacityMultiplier: 1 | 5`; x5 overrides persist by account key and x1
  removes the override. Ring geometry and warning colors still use the raw
  provider percentage: 90-100% used is red, 70-89.9% is amber, and lower usage
  is green.
- Expected weekly use per model is calibrated from current-window rows that
  have both a stable selected Codex account key and observed usage metadata.
  Missing-usage and unattributed rows are reported as excluded coverage rather
  than inferred into a slot. The estimate is local telemetry, not billing: it
  can diverge when an account is used outside Spartan Route or when retained
  logs do not cover the full provider window. The model/reasoning efficiency
  report uses each account's current weekly calibration to estimate x1
  percentage points per million tokens, and divides usage-seen tokens by
  distinct recorded session IDs for average M tokens per session. Its
  account-weighted differences are observational, not direct per-request quota
  measurements from the provider.
- For an offline, model-isolated observation report, run
  `python3 scripts/spartan-route-quota-report.py --model codex/gpt-6-astra --since <ISO-timestamp-with-timezone>`.
  It reads the private Route database in read-only mode (`--db` overrides the
  default), joins requests by stable account identity, and uses signed endpoint
  changes across contiguous model-only spans. Mixed models, missing usage,
  unattributed requests, retention gaps, quota resets and saturated readings
  are excluded. Small movements remain separately reported below resolution.
  Rates describe the observed cache/output mix; separate token-category prices
  remain unavailable. External account use and historical capacity changes can
  still contaminate the observations. Keep generated reports private.
- To import legacy ClawRoute x5 overrides, pass each currently active
  16-character account key shown by the Spartan Route Codex dashboard. The
  migration copies only the intersection of explicit current keys and legacy
  x5 keys, reports inactive mismatches, never infers identity from a slot, and
  is idempotent:

  ```sh
  python3 scripts/migrate_spartan_route_capacity.py \
    --active-account-key <current-account-key>
  ```

  The defaults read `~/.custom_claw/clawroute/data/clawroute.db` and update
  `~/.custom_claw/spartan-route/data/spartan-route.db`; use `--legacy-db` and
  `--target-db` for other local layouts.
  Usage windows are classified by declared duration: 604800 seconds is weekly
  and 18000 seconds is five-hour, regardless of primary/secondary position.
  Positional fallback is used only when duration is absent. A recognized
  weekly-only response is successful, removes the obsolete current five-hour
  snapshot while retaining history, and support resumes automatically if that
  window returns. Slots without usage telemetry still render as slot-only
  cards, and reset times include countdowns and timestamps.
- The routing dashboard presents sessions as a prompt-led activity feed with
  model, cache percentage, requested reasoning, errors, and actual tool-call
  counts visible before expansion. Model, reasoning, and literal prompt-text
  filters are applied before dashboard limits and retain complete matching
  turns. Session and turn summaries show estimated Codex quota units using the
  same current weekly calibration as the Codex model table; only usage-seen
  `codex/*` tokens contribute, and unavailable calibration is shown as unknown.
  These values are labelled `pool est.` because they are not isolated model prices.
  Tool names move into expanded details. Session, turn, request,
  account, slot, and cache identifiers remain available in disclosed
  diagnostics. Actual tool counts/names are recorded without arguments when
  observable. Native Responses accumulates function, custom and built-in call
  items across streamed events and final output, deduplicating by identity.
  Counts describe emitted calls, not confirmed execution. Completed responses
  can report zero; interrupted or malformed streams expose observed lower
  bounds (`N+ calls · partial`). Historical native zeros remain unknown and
  historical positive counts remain partial, without rewriting stored records.
  Session totals retain those coverage distinctions, including mixed old/new
  requests. Both subscription and API-key Responses use this capture logic.

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
- If every usable Codex slot reaches an explicit usage limit, Spartan Route
  returns ClawRoute-compatible `429 codex_429` immediately after trying the
  available pool. It includes the earliest `resets_at`, `resets_in_seconds`,
  slot diagnostics, and `Retry-After`; it does not hold or automatically replay
  the exhausted request.
- A later chat or image request arriving while every usable slot is quota
  cooled triggers one coalesced live usage refresh. Successful, structurally
  valid usage below every reported limit clears the matching account cooldown
  and the same request continues through that slot. Concurrent requests share
  the refresh, and new probes are throttled for five seconds. Failed, malformed,
  or still-full checks remain unavailable and return the aggregate pool error.
- An upstream auth rejection quarantines only the rejected credential
  fingerprint. Raw provider auth details are not returned as the final pool
  error; the slot becomes eligible again only after its auth source changes.
- An incomplete Codex response that contains tool calls is treated as retryable
  `502 codex_incomplete_response`; Spartan Route must not expose incomplete
  tool-call arguments to Hermes as a successful `finish_reason="length"` turn.
- Streaming tool-call deltas are buffered and emitted only after a terminal
  successful Codex event, so Hermes never receives partial tool arguments from
  an upstream stream that later becomes incomplete.
- Spartan Route does not implement Hermes gateway silence markers such as
  `[SILENT]` or `NO_REPLY`; those are delivery semantics above the router.
- Native Responses use version 2 cache observations in `context_info.native_cache`,
  captured after request normalization and account selection. Structured hashes
  include instructions, ordered tools, settings, and complete native input,
  including opaque reasoning and IDs. They describe local request compatibility,
  not provider token-prefix equality or proof of a retained cache entry.
- Comparisons are isolated by endpoint, realm, actual model, account or API
  credential scope, chain/session, and effective cache key. Only completed
  predecessors captured before dispatch are compared; failed or cancelled
  requests do not establish a completed baseline. Overlap affects both requests.
  Each bounded attempt records its status and a sanitized error code.
- Content hashes use an ephemeral process-local HMAC key. A restart starts a
  fresh comparison epoch. The ledger retains at most 512 scopes / 8 MiB and
  expires idle scopes after 30 minutes. Each observation processes at most
  8 MiB, 65,536 nodes and 64 nesting levels; the new persisted diagnostic
  object is capped at 8 KiB. Limits produce unknown observations. No additional
  raw prompts, schemas, keys, credentials or reasoning are persisted.
- Cache-read and cache-write coverage are explicit; missing counters are not
  known zeros. Native percentages use only rows with known cache readings,
  while total observed input remains available. Time to first output measures
  arrival of a meaningful provider delta, including buffered and collected SSE.
  Plain JSON responses without observable deltas have unknown first-output time.
- Corrected native aggregates live in a separate incremental table and appear
  as `nativeCache` in daily/session/turn metrics. Rebuild and retention use the
  same projection. Historical chat-derived classifications remain labelled
  legacy; they cannot be reconstructed as native comparisons. Legacy
  `cache_likely` / stall thresholds apply only to the older chat diagnostics.
  Existing usage totals remain available; the dashboard excludes missing
  native cache readings from its percentage and labels partial coverage.
- Authenticated `/api/routing/live` includes `cacheDiagnostics` with the
  current process epoch and start time. No cache optimization or extra provider
  request is enabled by collecting these observations.
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
- Native Responses preserve encrypted reasoning and full replay with
  `store=false`. The legacy Chat Completions assistant-reasoning switch does
  not describe native Responses behavior.

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
An expired bootstrap grant is refreshed for its intended account and replaces
conflicting managed state only after a successful refresh. A terminal rejection
of that intended grant never falls back to another managed account and requires
a new login; quota, network, lock, and local persistence failures remain
retryable and do not imply that the user should reauthenticate. A changed
bootstrap credential generation clears only its matching terminal quarantine.
Do not copy one refresh grant between slots: every slot needs an independent
local login. Treat backups of `/app/data/codex-auth` as credentials, not
ordinary telemetry.

Provider credentials, Codex tokens, raw auth data, and slot paths are not
returned by the Config API. Query-string bearer tokens remain unsupported.

## Cut From V1 On Purpose

- Authentic OpenAI Ultra execution is deferred. Hermes main currently exposes
  the selector and app-server scaffolding, but the upstream multi-agent
  integration is still under review. Spartan Route therefore fails closed
  instead of translating ordinary OpenAI-compatible requests into `max`.
  The reviewed implementation plan is `plans/ultra-bridge/plan.md`.
- Balance-loader scheduling, early activation, manual slot activation,
  cold-migration approval flow, cache-miss breaker, alerting, and the full
  analysis dashboard.
- Native Anthropic Messages format. Use OpenAI-compatible Chat Completions or
  OpenRouter-compatible models.
- Broad provider-specific translation. V1 passes through OpenAI-compatible
  providers and returns clean JSON for unsupported native providers.

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

The full-stack baseline mounts the local `spartan-cache-lineage` source read-only
at `/opt/data/plugins/spartan-cache-lineage`. The plugin remains opt-in in every
Hermes profile. For each explicit `codex/*` Responses profile, the hot migrator
enables it and transactionally creates a relative link from that profile plugin
directory to the single mounted source. `clawroute/auto` profiles are left
unchanged, and an existing non-managed plugin path fails before any config write.
Verify a migrated profile with:

```sh
sg exec hermes hermes -p vault plugins list --plain | rg spartan-cache-lineage
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
