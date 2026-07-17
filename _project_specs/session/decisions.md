# Decisions

## 2026-06-06

- Browserless-specific findings are deferred to a local gitignored plan.
- Camofox remains in scope.
- Codex account scheduling is an activation mechanism only; after fresh weekly
  telemetry exists for all relevant accounts, selection is driven by required
  weekly burn rate and safety constraints.
- The copied Coordinator system is adapted to Spartan Gate rather than keeping
  an incompatible generic CI workflow.

## 2026-06-08

- Prompt-cache repair for Codex should build on the existing weekday balancer
  and dashboard controls, not replace them.
- The missing pieces are a distinct durable session-cache lease, explicit
  `prompt_cache_key` propagation, cache-token telemetry, and account-level
  invalidation semantics.
- The current in-memory `claimSelectionLease()` / `releaseSelectionLease()`
  counters are request-scoped contention signals, not a usable long-lived cache
  lease model.

## 2026-06-11

- Codex usage analysis should anchor on two sources together: current quota
  state from `codex_usage_snapshots` and request/token/cache activity from
  `routing_log`. Request totals alone are not sufficient for the operator
  question.
- The new analysis UI should be a sibling dashboard route,
  `/dashboard-codex-analysis`, reusing the existing dashboard header-nav and
  bearer-token pattern.
- `codex_usage_snapshots` should remain the latest-known quota-state table. If
  quota trend lines are needed, they should use a separate append-only history
  table rather than changing the semantics of the current snapshot table.

## 2026-07-10

- `config/clawroute` remains the read-only, publishable routing baseline.
  Dashboard model changes are complete four-tier overrides in
  `spartan-route.db`, never writes to the mounted JSON directory.
- Runtime auto-routing overrides cover only `heartbeat`, `simple`, `moderate`,
  and `complex` primary models because fallback and frontier tiers are not
  executed by the current classifier.
- Config mutations use a whole-policy immutable snapshot, an async mutation
  lock, SQLite transactions, and ETag preconditions. Invalid persisted policy
  falls back wholesale to file defaults with an authenticated warning.
- Codex account failure styling is driven by backend slot-health precedence:
  current enabled-slot failures are red; cooldowns, cached history, and
  operator-disabled slots remain separate states.

## 2026-07-11

- Safari session authorization covers HTTP assets as well as WebSockets after
  one successful Basic Auth. Invalid WebSockets return `401` without
  `WWW-Authenticate`; they never fall through to another Basic challenge.
- Camofox and Hermes previews use separate secrets. All generated Hermes preview
  ports intentionally form one SSO/operator trust boundary. Rotation revokes
  the corresponding one-hour sessions.
- Caddy refuses startup unless both session secrets are exactly 64 lowercase
  hex. Edge and legacy cookies plus Basic `Authorization` are removed before
  proxying, and private multi-file upgrades use a validated transaction with
  rollback.
- Existing non-placeholder Caddy hashes are preserved when plaintext is absent.
  Install or migration flows must not invent plaintext or rotate credentials.
- A caddy-security/OIDC gateway is intentionally not added for this narrow fix:
  the referenced 2026 loops concern cookie/redirect or incompatible OIDC flows,
  adding another failure surface unrelated to noVNC Basic Auth.
