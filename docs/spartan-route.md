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

### Dashboard performance and refresh behavior

The Routing, Codex and Config screens share a responsive operator layout and
local stylesheet. Codex rings show the raw percentage used; capacity-weighted
units remain beside each ring. Reset and expiry countdowns update locally.
Tables retain all columns on small screens in keyboard-accessible scroll regions.
No external fonts, image downloads or third-party scripts are required.

Dashboard JavaScript is served separately from the HTML. Routing request-detail
rendering (about 7.7 KB uncompressed) loads only when a turn is expanded. Total
JavaScript grew with refresh and focus safeguards; this change defers detail
code and avoids retransferring unchanged assets rather than reducing every
screen's initial script size. HTML and the allowlisted
`/dashboard-assets/` files use private HTTP revalidation, retaining the same
authentication requirement as the dashboard. Dashboard API responses use
`Cache-Control: no-store`; private telemetry is not a browser asset cache.

Routing and Codex check for updated data every 15 seconds while visible, pause
scheduled reads when hidden, and check again when visible. Refresh retains
expanded Routing sessions and turns. Unchanged data avoids replacing their DOM
and Codex account/table contents. Request-detail loads are coalesced, and
credential changes or superseded responses cannot publish into a newer view.
Config keeps its explicit save model, unsaved drafts and ETag conflict checks.

Use **Refresh** to read the current snapshot and **Refresh Live** on Codex to
start or join an upstream usage refresh. The page reports progress and preserves
last-good data on a temporary failure. A countdown changing does not mean a new
provider quota observation has arrived; check the snapshot/progress status.
Mutations are not automatically replayed after a network error.

The session query materializes only requests belonging to the selected turns,
preserving whole-turn metrics, literal filters and exact matching counts. It
selects turn metadata first, then scans each selected session once for those
turns; the two phases share one read snapshot. A covering index keeps selection
and filters on metadata and prompt-preview pages instead of full context payloads.
The first startup after this update creates that index; allow extra startup
time and disk space for its retained metadata and previews. Its selection still
examines retained history, so the retained dataset and active filters continue
to affect query time. Capacity multipliers are read in a batch. Today's summary processes newly appended requests incrementally; a day
change, retention rebuild or external database update invalidates that cache.
The first summary and exact latency percentiles still depend on the day's data.
`infra/spartan-route/benchmark_dashboard.py` provides an offline synthetic
query benchmark; it never opens the operator database. Its timings are
comparative evidence, not a production latency guarantee.

## Native Responses update surface

Codex inference supports `SPARTAN_ROUTE_CODEX_TRANSPORT=http|auto|websocket`.
The public default is `http`. `auto` uses account, credential generation,
model, chain and prompt-cache isolated WebSockets and falls back to HTTP
before request delivery when the backend rejects an upgrade or capacity is
unavailable. Set it in the private env and apply with
`sg up -d --no-deps --force-recreate spartan-route`. Invalid modes fail startup.
WebSockets require the explicit HTTPS HTTP proxy and verified TLS; missing or
malformed proxy configuration fails closed. Both native Responses and Chat
compatibility send full history, preserve cache keys, model, reasoning and tier,
and never use incremental `previous_response_id` replay. An ambiguous send or
disconnect cannot trigger account rotation or HTTP replay. Explicit rejection
before material output retains existing authentication/quota retry policy.

The pool admits at most 32 connections, four per account, counting connections
being opened or closed. It services idle peer pings and expires connections
after 240 seconds idle or 50 minutes age. Frames are limited to 4 MiB, outgoing
WebSocket requests to 2 MiB; `auto` sends larger requests over HTTP. Handshake,
send, first event, idle read and close have bounded deadlines. Only successful
terminal responses return a socket to the pool. Tool arguments still require
a successful terminal response before Chat compatibility releases them.
Recognized quota, response metadata and `responsesapi.websocket_timing`
diagnostics are bounded sideband events; their payloads are not forwarded or
persisted. The timing diagnostic follows the
[upstream Codex event handling](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs).
OAuth, catalog and quota requests use a separate HTTP connector so inference
does not compete with background refreshes. HTTP keeps pooled connections for
240 seconds, caches DNS for 300 seconds and uses compact UTF-8 JSON. Shared SSE
parsing preserves multibyte characters split across network chunks.

Routing context includes sanitized `transport` measurements: connection reuse,
connect/pool wait, first event, reasoning, text, tool and visible output times,
plus request-relative times including routing preparation. WebSocket event
latency is separate from the existing HTTP header-latency rollups; total request
latency remains comparable. No credentials, frames or prompt bodies are added
to transport telemetry.

Route and a separately configured Discord voice relay can share the same
immutable Route image. Keep their commands, networks, credentials and memory
limits separate. Validate the relay's tests against the candidate dependency
before updating its private image pin; recreate only those two services. Retain
one active image and one rollback, and remove obsolete Route images only after
checking all container references. Restore the retained image and `http` mode
in the private configuration for rollback.

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

### Dashboard session identity

Native Responses requests with a complete, successfully validated Hermes chain
binding use a hash of that chain as the dashboard session ID. Cache-key changes
therefore remain turns of the same conversation. Different profile/session
chains remain separate. `context_info.session_identity` records version 1 and
source `signed_hermes_chain` or `legacy_fallback`.

Unsigned clients and rejected bindings keep the existing cache-key/content
fallback. Execution identity, upstream cache keys, session headers and account
affinity are independent of the dashboard ID. Historical rows keep their
recorded session IDs; this change does not rewrite telemetry.
Turn IDs retain the existing request-derived identity; a cache-key change
within one user turn can still split its turn rows inside the same session.

A continued conversation can still have a cold provider cache. Hermes's native
key includes instructions and tool schemas, so an instruction change can change
the key while history and the signed chain remain intact. Dashboard grouping
does not establish cache residency or alter actual provider cache counters.

Kept in v1:

- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`,
  `/v1/images/generations`, and `/v1/images/edits`.
- Native Hermes `openai-api` provider discovery through `OPENAI_BASE_URL`.
- `/v1/models` lists context variants managed from `/dashboard-config`.
  Before the first save, eligible text models retain their automatic
  `-282kcontext` variants. After saving, the complete managed list is
  authoritative, including an empty list; newly discovered base models do not
  automatically receive variants. Hermes can select a variant to use its
  advertised context window. Spartan Route sends the base model ID upstream;
  the variant is context metadata, not a server-side token limit.
  `/v1/models/{id}` reports the same variant metadata. Variants whose base
  model disappears or no longer supports their window are unavailable.
- Profile `.env` route keys may reference `${OPENAI_API_KEY}` or legacy
  `${CUSTOM_1_API_KEY}`; those two route-key aliases delegate to the named
  container environment key. References to unrelated keys remain literal and
  are never expanded.
- `clawroute/auto`, `custom-1/clawroute/auto`, and `spartan-route/clawroute/auto` model aliases for Hermes compatibility.
- GPT-6 Astra, Sol, and Luna are selectable as `codex/gpt-6-astra`,
  `codex/gpt-6-sol`, and `codex/gpt-6-luna` through the existing Spartan Route
  `openai-api` provider in Hermes. Use `api_mode: codex_responses` for native
  Responses sessions. Before any observed metadata exists, bundled entries use
  an initial estimate of 872,000 tokens. Successful discovery replaces that
  estimate with the Codex Subscription catalog's `max_context_window`, distinct
  from public API limits.
  Active Hermes profiles and automatic tier defaults do not change.
- Spartan Route refreshes the visible Codex Subscription model catalog in the
  background at startup and hourly. It uses each enabled slot's existing OAuth
  credential, lists a model when at least one account offers it, and selects only
  offering accounts for its requests. Sol and Luna wait for a successful account
  catalog before routing. Hidden, malformed, and models requiring a
  newer validated Codex client protocol are excluded. The catalog is an observed
  Codex backend interface, not a public OpenAI API contract. A failed refresh
  keeps the last valid account catalog for at most 24 hours. Detection of a
  changed bootstrap credential generation revokes stale catalog eligibility
  and wakes discovery, even when that slot has no catalog entry. Absent or
  mismatched source identity cannot grant catalog eligibility. Unknown explicit
  Codex model IDs return `404 model_not_found`; a listed model without an eligible
  account returns `503 codex_model_unavailable`. Saved auto-tier overrides are
  revalidated after refresh and fall back to file defaults with a warning when
  their model is unavailable.
- Catalog requests use a baseline Codex client version of `0.159.0`, or the
  highest valid `client_version` from enabled slots' mounted `models_cache.json`
  files. This version applies to every account and never decreases during a
  process lifetime when another CLI overwrites a cache with an older version.
  Missing, malformed, oversized, and nonregular caches are ignored. A failed
  request with a newer hint retries once with the baseline. Cache model lists
  never grant account eligibility. The validated transport ceiling remains
  independent; skipped incompatible models and refresh failures are logged.
- Ordinary Codex context metadata follows successful catalog refreshes, using
  the smallest `max_context_window` among offering enabled accounts. Last
  observed values persist in SQLite and overlay already-listed models after
  restart or refresh failure; bundled estimates apply only before a value is
  observed. Metadata failures add no routing availability gate and cannot
  resurrect removed models or extend account eligibility. Generated
  variants retain their chosen window and become unavailable when
  their base model's supported maximum drops below that window. Both model
  endpoints expose consistent `max_context`, `context_length`, and
  `max_model_len` fields for ordinary and variant models.
- Compatible future model additions and context changes follow Codex cache
  updates automatically. New transport requirements or backend schema changes
  may still need maintenance; a permanently old CLI/cache cannot reveal newer
  catalog visibility requirements.
- Selectable Codex GPT-5.6 catalog entries for manual Hermes sessions:
  `codex/gpt-5.6`, `codex/gpt-5.6-sol`, `codex/gpt-5.6-terra`, and
  `codex/gpt-5.6-luna`. The Codex provider profile still defaults to
  `codex/gpt-5.5` until these variants are promoted explicitly.
- `reasoning_effort=max` is accepted for GPT-5.6 variants, GPT-6 Astra/Sol/Luna,
  and discovered models that advertise it, and is sent to Codex exactly as
  `reasoning.effort=max`. Other Codex model families receive
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
- Codex subscription Responses requests also send the final resolved cache key
  as the HTTP `session-id` header, for both native Responses and Chat
  compatibility. This follows upstream Codex's
  [cache-affinity contract](https://github.com/openai/codex/pull/44862).
  Existing lineage boundaries produce distinct effective keys; retries retain
  the same affinity key while using the selected account's credentials. Requests
  without a resolved key omit the header. Other provider transports do not
  receive this subscription header.
- Per-key Codex cache leases, rate-limit cooldowns, transient 5xx cooldowns,
  and retry across slots/accounts.
- Codex image generation and image edits, including multipart PNG alpha masks
  translated to Responses `input_image_mask`. The outer Codex model is selected
  by the Route service environment variable `CODEX_IMAGE_HOST_MODEL` (alias:
  `SPARTAN_ROUTE_CODEX_IMAGE_HOST_MODEL`); it is independent of the plugin image
  model. Set an available model with verified image-tool support when the bundled
  host default is retired. Edits set `action=edit` and
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
- A later chat, image, or native Responses request arriving while every usable
  slot is quota cooled triggers one coalesced live usage refresh. Successful,
  structurally valid usage below every reported limit clears the matching
  account cooldown and the same request continues through that slot. Concurrent
  requests share the refresh, and new probes are throttled for five seconds.
  Failed, malformed, or still-full checks remain unavailable and return the
  aggregate pool error. Usage checks read quota and reset-credit metadata only;
  they never redeem a banked reset.
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
- A separate `native_cache.conversation_trace` compares the last successful,
  non-overlapping observation in the same endpoint/realm/model/account/chain
  across effective cache keys. It reports `continued`, `no_predecessor` or
  `unknown`, plus nullable `cache_key_changed`, changed-component fingerprints
  and predecessor gap. The dashboard can show conversation continuation beside
  a changed key or changed instructions without treating it as a cache hit.
  Cross-key overlap, expiry and eviction invalidate this comparison; ambiguous
  completions never establish its next predecessor. Existing key-qualified
  comparisons, cache aggregates and experiment eligibility remain separate.
- Content hashes use an ephemeral process-local HMAC key. A restart starts a
  fresh comparison epoch. The ledger retains at most 512 scopes / 8 MiB and
  expires idle scopes after 30 minutes. Each observation processes at most
  8 MiB, 65,536 nodes and 64 nesting levels; the new persisted diagnostic
  object is capped at 8 KiB. Limits produce unknown observations. No additional
  raw prompts, schemas, keys, credentials or reasoning are persisted.
  Conversation entries share that byte budget, use spare capacity, and cannot
  displace key-qualified scopes. With no spare capacity their trace is unknown.
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
- Optional bounded probes and expiring cache cohorts use private file authority;
  they are disabled by default. See [the cache experiment runbook](spartan-route-cache-experiments.md)
  for baseline, budget, quota-isolation and review gates.
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

## Hermes fast mode

Hermes `/fast` works through the exact internal endpoint
`http://spartan-route:18790/v1` with the `openai-api` provider and explicit
`codex/*` or `openai/*` models eligible under Hermes's upstream model gate.
Automatic routing aliases, unrelated proxies, and unsupported models do not
receive fast parameters. The build-time patch changes the shared route gate
used by CLI and gateway requests.

Use `/fast fast` to request `service_tier: priority`, and `/fast normal` to
return to normal processing. `/fast auto` opens the upstream timed window on
each user turn; `/fast cold` opens it only for the first turn of a session.
`/fast ultrafast` keeps upstream's per-model eligibility restriction. Commands
are session scoped unless Hermes's `--global` option is supplied. No fast mode
is enabled by default.

Spartan Route preserves `service_tier` through Codex Chat Completions conversion,
native Codex Responses, and OpenAI passthrough. Reasoning and cache identity
remain intact. Account/model eligibility and the tier actually served belong
to the upstream provider; requesting a tier does not guarantee latency. Upstream
errors are returned without silently retrying at a normal tier.

Existing Hermes images can use the guarded overlay described in
[operations](operations.md#hermes-fast-mode-overlay) until the next image build.

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

## Dashboard Context Variants

The **Context window variants** section of `/dashboard-config` manages variants
with three editable fields: **Name (model ID)**, the identifier clients select;
**Base model**, an existing text model; and **Context window**, an integer token
count. Add, edit, or remove rows, then save the complete list.
These changes are independent of the auto-routing tier selections.

The name stays unchanged when the base model or window changes. For example,
`codex/gpt-6.1-sol-282kcontext` can advertise 412,000 tokens while still routing
to `codex/gpt-6.1-sol`. The name's text does not determine the window or upstream
model. This preserves Hermes sessions and profiles pinned to that identifier.
For Codex models in Hermes, keep the `codex/` prefix so Hermes can recognize its
Codex-specific modes. Multiple names may point to the same base model and window.

New rows receive a suggested name, which can be edited independently. Existing
names are retained, including those loaded from the earlier configuration
format. Only explicitly renaming or removing a row removes its client-facing
identifier; clients using that old name must then select another model.

- `GET /api/config/model-variants` returns the variants, available base models,
  whether a managed list has been saved, warnings, and an `ETag`.
- `PUT /api/config/model-variants` requires that `ETag` in `If-Match` and a
  complete list, for example:
  `{"variants":[{"id":"codex/my-sol","model":"codex/gpt-6.1-sol","contextWindow":412000}]}`.
  Legacy API clients may omit `id` to request the earlier generated-name behavior.
- `{"variants":[]}` explicitly removes all variants. The first save replaces
  the automatic 282k defaults with the managed list. New discoveries then
  appear only as base-model choices until a variant is explicitly added.

Windows must be integers from 1 to the smaller of the base model's supported
maximum and 2,000,000 tokens. Auto aliases, image/realtime models, nested
variants, duplicate names, reserved names, and IDs colliding with real models
are rejected. Names must be bounded model identifiers without whitespace,
control characters, query/fragment characters, or client wrapper prefixes such
as `custom-1/` and `spartan-route/`.
Unavailable saved rows remain visible and can be preserved or removed; they
become usable again if the base model returns with a compatible window.
Ordinary provider credential and Codex account eligibility checks still apply.

The authenticated API stores a versioned configuration in `spartan-route.db`
without writing to `config/clawroute`. Updates are transactional and use the
same serialization boundary as catalog refreshes. Missing `If-Match` returns
428; a stale revision returns `412 config_revision_conflict`. Failed writes
preserve the active list. An unreadable or malformed stored configuration
disables managed variants and produces an authenticated warning. In that
degraded state unknown explicit IDs are rejected, so previously managed custom
names cannot accidentally fall through to provider passthrough. Known base
models continue to work. Removed custom names are retained as rejection records
across restarts and can be reused by explicitly adding them again.

Version 1 configurations load with their existing generated IDs preserved;
saving writes version 2 with explicit names and removed-name records. Before
rolling back to an image that supports only version 1, retain and restore the
previous variant configuration document as well as the previous image.

Saved changes affect new Route requests immediately and survive restart.
Clients that cache model metadata, including Hermes, must refresh their model
catalog to observe the changed window. This does not change the base model's
context metadata or impose a server-side token limit.

## Codex Account Health And Credentials

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
retryable and do not imply that the user should reauthenticate. Spartan Route
automatically checks for changed bootstrap credential generations even on
healthy slots with cached auth. Detection invalidates stale cached auth, slot
and account leases, usage cache, and catalog eligibility, clears matching
terminal quarantine and stale quota cooldown, and wakes model discovery.
Unchanged grants keep cooldowns. Managed-state account binding and expiry
selection still apply; this does not guarantee revocation of in-flight calls.
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
at `/opt/data/plugins/spartan-cache-lineage`. Runtime activation remains explicit
per Hermes profile. Operator provisioning policy requires the link, enablement,
discovery/import and actual hook registration for every new profile; preserve
existing intentional exceptions. See the [profile completion checklist](profiles.md).
For each explicit `codex/*` Responses profile, the hot migrator
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

For native Codex Responses, plugin version 0.4.0 reads Hermes' existing
context-local background-review marker. Foreground and review requests keep
their shared logical provider cache key and signed reasoning identity, but
have independent history trackers. A review starts with the lineage of the
newest compatible foreground snapshot and keeps that lineage as the foreground
continues; review-only rewrites never advance foreground lineage. Compatibility
requires an exact sanitized history prefix, tool fingerprint and model.

The plugin retains at most eight foreground snapshots per scope, within the
shared key limit (512 by default) and an 8-MiB conservative Python allocation
budget. Repeated snapshots are deduplicated. Oversized history retains counter
and hash metadata only. After snapshot eviction, incompatible history, or a
plugin/process restart, reuse of an earlier provider cache is not guaranteed.
A review without task/turn identifiers is stateless and cannot update foreground
history. Older Hermes versions without the origin API retain legacy tracking.
Chat lineage rules, LCM compression, review tools, provider content and account
affinity remain unchanged; no Hermes patch or image rebuild is needed.

The existing plugin mount is read-only. After updating its source, load the
new version by recreating only Hermes using the current image:

```sh
sg up -d --no-deps --no-build --force-recreate hermes
```

This restarts Hermes processes and can interrupt active turns. Cache-key
stability is a local invariant, not proof of provider cache residency; judge
cache savings from actual provider usage counters.

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
