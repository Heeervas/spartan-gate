# Changelog

All notable changes to Spartan Gate are documented here.

## Unreleased

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
  `[proxy-audit]` lines to `/opt/data/logs/proxy-audit.log` by default.
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
