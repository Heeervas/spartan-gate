# Changelog

All notable changes to Spartan Gate are documented here.

## Unreleased
- Bounded Spartan Route telemetry admission and shutdown draining, kept model
  requests available under telemetry pressure or database write failure,
  exposed non-sensitive queue/retention health, and transactionally enforced
  30-day/50,000-row routing retention without pruning append-only Codex usage
  history or control state.
- Bounded every service and overlay with Docker's `local` logging driver,
  configurable 10 MiB x 3-segment defaults, and sent Hermes Python HTTP audit
  output to that bounded stderr stream by default while retaining explicit
  file output as an operator-managed option.
- Added preflighted private-state permission checks and repair, restrictive
  installer creation, fatal chmod handling, doctor enforcement, rollback
  snapshots, and service-specific modes: 0600 files, 0700 private/data roots,
  and Browserless 0770 group access without traversing credential-store
  contents.
- Closed Reader access to shared CGNAT/overlay and every other non-global
  destination at URL, DNS, redirect, and connection time while preserving its
  intentional direct-fetch exception for arbitrary public page text and
  documenting that exception's public exfiltration tradeoff.

- Made Camofox/noVNC and generated Hermes previews compatible with Safari by
  exchanging one successful Caddy Basic Auth for a one-hour HttpOnly session
  covering subsequent HTTP assets and WebSockets; invalid WebSockets still fail
  closed without another browser auth challenge.
- Split Camofox and Hermes-preview session secrets, added fail-closed 64-hex
  startup validation, legacy-cookie removal, transactional private migration,
  and upstream credential stripping.
- Added generated `CADDY_WS_EDGE_TOKEN` wiring and private preflight checks,
  replaced the active Browserless placeholder edge token, and kept existing
  Caddy hash-only installs from receiving a fabricated plaintext password.
- Made Spartan Route the normal L4/full-stack router, kept existing
  `CLAWROUTE_TOKEN`/`CLAWROUTE_EDGE_PORT` compatibility, and left ClawRoute as
  the legacy lower-tier addon/source package.
- Repaired the private HTTPS Caddy contract for the router and Browserless
  debugger edge so local/Tailscale ports are published by the active stack
  while Browserless WebSockets still require `BROWSERLESS_EDGE_TOKEN`.
- Updated the Hermes base image pin, archived two upstream-absorbed Hermes
  patchers, and mapped Camofox protected-route auth into upstream Hermes'
  `CAMOFOX_API_KEY` client variable.
- Pinned refreshed Caddy, SearXNG, and Camofox image references, and rebuilt the
  Camofox wrapper with package upgrades applied at image build time.
- Added an opt-in Hermes `spartan-reader` web extract backend for the internal
  Reader service while keeping fresh installs on direct Reader usage rather than
  native `web_extract` auto-configuration.
- Added bounded Spartan Route request recovery for temporary all-slot Codex
  cooldowns, returning `Retry-After` metadata when recovery cannot happen
  within the wait budget, and changed private whitelist updates to hot-reload
  running proxy/DNS services with recreate fallback.
- Changed Spartan Route Codex-auth image fallback to accept valid
  `gpt-image-2` non-square sizes, use the Codex built-in image-generation
  payload shape, pass Codex image result bytes through unchanged even when
  upstream returns different pixel dimensions or aspect ratio, and expose
  `actual_size` when dimensions can be detected.
- Matched Spartan Route Codex request timeout behavior to ClawRoute by applying
  `CODEX_REQUEST_TIMEOUT_MS` only to response-header wait, keeping local stream
  read failures out of generic 5xx slot cooldown, and returning retry metadata
  for all-transient Codex slot pools.
- Classified Spartan Route Codex stream read timeouts/errors as explicit
  retryable transport failures, including SSE failure events, transient slot
  cooldowns, stable telemetry codes, and diagnostic-only low-cache stall
  tracking.
- Clarified ClawRoute Codex-auth image fallback errors for unsupported
  non-square or malformed image sizes before any upstream image call.
- Added Spartan Route dashboard latency, recent-first session/turn trees,
  persistent Codex slot routing controls, cached usage polling, active-lease
  visibility, and bearer-only dashboard/API auth while deferring quota-unit
  estimates to v2.
- Split Spartan Route latency telemetry into first upstream response and total
  request duration, added authenticated Codex slot paths, restored
  threshold-colored usage rings, and improved dashboard request previews without
  exposing health-check slot paths.
- Reworked Spartan Route dashboards so routing request content opens in
  expandable request details, Codex usage and slot controls share account cards,
  reset windows show countdowns, and current first-response latency is not
  visually blended with legacy rows.
- Drain Codex streaming responses long enough to preserve terminal usage after
  early client disconnects, restoring request-level Spartan Route cache
  metrics for Hermes tool-loop traffic.
- Kept Hermes private runtime `.env` aligned with Compose `OPENAI_BASE_URL` so
  Spartan Route stays authoritative across recreates, and changed Spartan Route
  cache reporting to exclude log rows without upstream usage metadata.
- Improved Spartan Route Codex prompt-cache diagnostics by preserving streamed
  usage metadata, detecting expected low-cache hits, and keeping assistant
  reasoning replay disabled by default to stabilize prompt prefixes.
- Deferred dashboard-only Codex quota/capacity estimates to Spartan Route v2;
  the v1 dashboard keeps raw `/wham/usage` percentages, cache metrics, and
  latency visible without adding routing behavior.
- Kept Codex prompt-cache leases sticky across balance-loader de-scoring and
  transient 502 retries, while preserving hard-block invalidation, and fixed
  Hermes profile-path prompt cache key plus reasoning effort forwarding.
- Added an explicit OSS-readiness posture to the public docs and a root
  `llms.txt` entry map plus agent-readability plan for future LLM/code-agent
  orientation.
- Hardened Browserless debugger exposure by requiring a generated
  `BROWSERLESS_EDGE_TOKEN` on WebSocket upgrades while preserving browser
  Basic Auth for HTTP debugger pages and keeping Safari/Firefox login flows
  out of Basic Auth loops.
- Documented the intentional Hermes dashboard `/api/*` and `/ws/*`
  same-origin pass-through so dashboard internals keep working behind the
  localhost/private edge instead of being broken by Caddy Basic Auth prompts.
- Generate a real `CADDY_AUTH_HASH` from the installer-managed Caddy password
  for all tiers, including L0/L1, and add installer coverage for that contract.
- Removed ClawRoute query-string token authentication, protected stats and
  dashboard endpoints when `CLAWROUTE_TOKEN` is configured, and emit explicit
  SSE error events before closing failed upstream streams.
- Hardened Reader SSRF handling by validating the actual connection
  resolution for each request/redirect and redacting URL query strings from
  audit logs.
- Updated ClawRoute production dependencies, added production `npm audit` plus
  installer/Caddy contract checks to CI, and pinned the Tinyproxy base image by
  digest.
- Added root OSS governance files, issue/PR templates, Dependabot, CodeQL,
  Gitleaks, report-only Trivy image scanning, and example-env Compose
  validation for the public repository.
- Aligned public clone, branch, package, and ClawRoute metadata with
  `Heeervas/spartan-gate` on `master` and the `1.0.0` public baseline.
- Pinned SearXNG and Caddy runtime image references by digest for more
  reproducible Compose validation and startup.
- Return a clear 503 when named ClawRoute virtual models such as
  `custom-1/clawroute/auto` cannot route because no provider keys or profiles
  are available, instead of falling through to the OpenAI provider path.
- Added a stable layer/addon model for L0-L4 installs, including ClawRoute as
  an independent addon, stable `spartan-gate` Compose project naming, stable
  `private/data/current` install data roots, and free Hermes user-level
  Python/Node package installs for lower tiers.
- Added OpenAI-compatible `/v1/images/edits` support for ClawRoute image
  requests, including multipart references, Codex Auth translation, and
  OpenAI API-key passthrough.
- Run Hermes helper shell, doctor, profile, health, cache, Meet, and GOG
  workspace commands as the runtime Hermes UID/GID with stable Hermes
  environment vars.
- Added optional Hermes workload numeric UID/GID drops for normal host-owned
  data folders while keeping `/opt/hermes` root-owned, and moved Python
  `[proxy-audit]` lines to a configurable destination.
- Kept generic streaming Codex rate-limit errors request-local while preserving
  long slot cooldowns for explicit `usage_limit_reached` responses.
- Clear stale Codex slot cooldowns after real reset evidence, including
  epoch-millisecond `resets_at` values and successful live usage refreshes.
- Gate automatic Codex usage checks by durable weekly activation checkpoints
  in balancer mode, while keeping slot 0 continuously active.
- Add per-slot expected reset editing and explicit usage checks to the Codex
  dashboard.
- Keep usage polling from refreshing OAuth credentials, reload reauthenticated
  files correctly, and back off failed token refresh attempts.
- Stop presenting expired persisted quota windows as current usage, and return
  explicit errors when a manual usage check fails.
- Added the Coordinator agent system, project-specific CI, session state, and
  local-plan handling.
- Hardened expiring egress whitelists against regex bypasses, overflow,
  malformed metadata, invalid hosts, timezone drift, and concurrent writes.
- Upgraded the Hermes startup pipeline while preserving the upstream UID and
  applying profile configuration migrations before s6 reconciliation.
- Aligned Hermes Docker profile startup with upstream s6 supervision, moved the
  Browserless CDP broker under s6, and retired automatic API port shifting.
- Added Camofox profile, noVNC, add-on, Meet, and authenticated clipping
  support with preflight-safe operational helpers.
- Changed Codex account scheduling to activate accounts on staggered days, then
  optimize weekly spend by required burn rate before each reset.
- Corrected Responses streaming event shape, identity, usage accounting, and
  completion semantics.
- Added regression coverage for five-hour Codex safety, malformed whitelist
  recovery, URL ports, and Camofox tab-close locking.
