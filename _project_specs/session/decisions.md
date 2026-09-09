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


## 2026-07-30

- Codex Subscription is the primary Responses realm. Spartan Route forwards
  `/v1/responses` natively with `store=false`, encrypted reasoning replay, and
  `reasoning.context=all_turns`; Chat Completions conversion is not a fallback.
- Reasoning identity is HMAC-bound to canonical `HERMES_HOME + session_id` and
  one provider realm. Sol/Terra switches remain inside the subscription realm;
  subscription/API-key switches require a new session or branch.
- A profile switch with the same textual session ID keeps visible history but
  drops foreign opaque reasoning and server message IDs. Background review
  `provider: auto` inherits the parent runtime and realm.
- Hermes 0.19 cannot replay native Responses compaction or stateful response
  IDs, so those capabilities fail closed. hermes-lcm must preserve the two
  Codex replay fields; this remains an upstream plugin concern and is not added
  to the Hermes image.
- Automatic private migration sets `api_mode: codex_responses` only for
  explicit `codex/*` models. Auto aliases do not prove a subscription realm.


## 2026-09-09

- Realtime uses native aiohttp mint/ticket plus WebSocket relay in Spartan Route.
  Hermes retains playback, tool execution, permission checks and delegation.
- OAuth custody and refresh locks stay in the existing Codex manager. Realtime
  reserves a configured authorized account per session and never falls back to
  API keys or another account. A successful live voice/tool probe is required
  before implementation is considered supported; billing remains unknown.
- Voice credentials are independent of the legacy global text token; bearer-only
  tickets prove possession, not the identity of someone holding a stolen ticket.
  Public discovery exposes protocol capability, not private account observations.
- Realtime remains disabled in production pending credential provisioning and
  explicit activation. Isolated loopback staging may reuse the existing local
  credential in a voice-only test server without generating credentials. It
  does not change the production requirement for an independent voice key.

## 2026-09-09 — All-profile Talk activation preparation

- The operator requested Talk availability across Hermes profiles and will own
  the later Route-only rebuild and whole-stack recreation. Preparation must not
  restart the running stack or rebuild Hermes.
- Share a dedicated voice credential through private Compose env-file injection
  and existing s6 environment inheritance. Do not copy provider OAuth or use the
  global text token. The current single-client concurrency limit remains shared.
- Mount the reviewed Talk snapshot read-only and enable it with a numeric-user
  boot helper before profile gateway reconciliation. Apply to existing profiles
  at startup; newly created profiles receive it at their next container start.
- Rollback disables Talk plugin enablement and Route Realtime admission while
  preserving the Spartan transport setting. No direct-provider fallback is
  implied. Live Hermes and Discord acceptance remains a separate next step.
