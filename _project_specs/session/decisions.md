# Decisions

## 2026-10-22 — Audit remediation and source retirement

- The operator authorized implementation of the reviewed audit and retirement
  of Browserless and the legacy ClawRoute application, including source deletion.
  Preserve private browser profiles, credentials and databases; no data cleanup,
  deployment, build, push or publication is implied.
- Preserve L0–L4 entry points; L3 becomes L2 plus internal Spartan Route. Reject
  retired `clawroute` addon selection before writes with migration guidance.
  Keep shared `config/clawroute`, consumed `CLAWROUTE_*` keys and auto model aliases.
- Retire CDP-only workflows honestly; preserve existing Camofox transcription,
  managed browser isolation, vault behavior and numeric/root-owned supervision.
- Server-side edge expiry uses a bounded internal verifier and stock Caddy
  forward-auth, with separate audience keys and service authentication. Existing
  private overrides need explicit offline migration and separately approved
  activation; browser Max-Age alone is not an authorization deadline.
- Keep the immutable quality baseline and thresholds. Independent Sol/medium
  implementation and Luna/high review must precede acceptance of integrated
  source. Known executor/outbound DNS lifecycle limits remain explicit residuals,
  not claims of cancellation solved by moving work into threads.
- Durable execution owner: `docs/reviews/2026-10-22-remediation-plan.md`.

## 2026-10-09 — Native vault transport for Camofox

- Reuse Hermes vault backends, classifiers, fill scripts and secure prompts;
  adapt only Camofox transport, durable session selection and textual redaction.
  Require exact-origin OTP handle validation before resolving a code.
- Keep native screenshot/vision behavior at the operator's explicit request:
  exact-value text redaction does not protect secrets visible in image pixels.
- Build pinned upstream Hermes Camofox-only, downstream Hermes and Camofox
  sequentially after source-backed tests and independent review. The operator
  authorizes deleting current Hermes/Camofox images, the unused older Hermes
  image and necessary unused build cache; preserve volumes, profiles and data.
  Recovery after image removal is by recorded rebuild coordinates.
- Preserve prior changes in separate commits before goal activation. Parallel
  implementation uses isolated worktrees and GPT-6.1 Sol medium workers; finish
  in local commits with clean Git/worktrees and no push.

## 2026-10-08 — Native managed proxies and sealed-image admission

- Use Hermes' native administrator-managed scope for the six shared HTTP proxy
  and bypass keys. Never mount the full private Compose env as shared policy;
  other settings need individual scope decisions and credentials remain local
  to their profiles. Recreate only Hermes to apply the deployment change.
- In explicit image-only mode, freshly validate the executing baked interpreter,
  feature inventory and enabled plugin dependency closure. Omit only the PM
  generation verdict, retain tool diagnostics, and abort failed CLI/gateway
  admission with SystemExit so native best-effort handlers cannot swallow it.
  Never forge PM facts or represent this as full uv.lock reproducibility.
- Avoid rebuilding for a stale mutable generation fingerprint. Use a guarded,
  read-only, image-pinned L4 adaptation for the existing artifact; verify code
  fingerprints and remove the overlay before rebuilding or changing tiers.
- Retain existing patches unless an integrated upstream replacement preserves
  their complete boundary. Open proposals and partial middleware overlap do
  not establish that equivalence. Discord's mobile-app incident requires no
  gateway/catalog migration or remote command approval changes.

## 2026-10-03 — Route transport and Hermes dashboard sessions

- Use a fully validated signed Hermes chain for dashboard session identity
  only. Preserve execution identity, provider keys/headers, account affinity and
  existing turn IDs. Historical telemetry is not rewritten.
- Compare completed, non-overlapping conversation observations across keys in
  a bounded hash-only ledger that shares spare capacity with the existing
  key-qualified ledger. Changed instructions/key are diagnostics, not proof of
  provider cache residency; experiment comparisons remain key-qualified.
- Keep public and active private transport HTTP. Bounded Sol acceptance verified
  full-history WebSocket completion, real cache counters and socket reuse, but
  did not show a consistent first-token advantage. Reused WS first-event latency
  was 380 ms while first output was 3,626 ms; upstream processing still dominates.
  Keep auto/WebSocket opt-in and do not infer Astra performance from Sol probes.
- Ignore only recognized bounded WS sideband events, including upstream
  `responsesapi.websocket_timing`. Require terminal completion in acceptance;
  text output alone is insufficient. Unknown/overflowing events and ambiguous
  delivery fail without replay.
- Route and the separate voice relay share one tested image with unchanged
  network/process/credential boundaries. Retain one rollback image, enforce the
  5-GB disk floor and 2-GB overhead limit, and stop paid acceptance at 18 of the
  authorized maximum 20 actual submissions. No Astra or voice inference ran.
- Combine the session fix into `codex/spartan-route-transport` for user testing;
  do not merge into the base branch without the user's later instruction.

## 2026-10-02 — Hermes fast mode through Spartan Route

- Patch the shared Hermes eligibility gate because request middleware runs
  after this decision and cannot restore static/timed fast-mode resolution.
  Trust only the exact internal Route endpoint, `openai-api`, and explicit
  eligible Codex/OpenAI models; retain all other upstream restrictions.
- Preserve service tiers through Codex Chat conversion and keep cache/reasoning
  identity intact. Do not downgrade the requested tier locally during retries.
  The upstream may return a different served tier: the live priority probe
  completed successfully but reported `default`, so do not claim priority
  capacity or a speed improvement for this account.
- Activate with a guarded existing-image Hermes overlay, preserving Camofox,
  and rebuild only Spartan Route under the operator's task-specific low-disk
  exception. Keep the previous Route image and original private code snapshot
  for rollback. Remove the overlay before a future Hermes image build.

## 2026-10-01 — Managed Camofox selection and launcher imports

- Spartan Gate's selected Camofox mode overrides profile cloud, Browser Use and
  CDP selection through strict browser-only guards. Use native managed secret
  scopes for infrastructure endpoint/authentication while retaining per-profile
  identity, sessions and persistence.
- Validate before s6 profile reconciliation and stop native initialization on
  failure. Do not let malformed policy, unavailable Camofox or unauthorized
  requests activate another browser.
- The native Hermes launcher replaces `PYTHONPATH`. Install the shared helper
  in the protected project root and validate imports with that launcher path;
  a bootstrap-only test can pass while actual gateways fail.
- Activate on the existing image using the fingerprinted private overlay,
  preserve original snapshots and recreate only Hermes. Retain image-drift
  guards until the next validated image incorporates the patch.

## 2026-09-12 — Camofox 1.15 candidate and activation gate

- Retire upstream-owned fixed-proxy protection and duplicate OS/VNC apt work,
  while retaining immediate timeout-tab retirement and launch-profile fences.
- Share persistence callbacks through the core-owned holder required by 1.15's
  prototype-scoped plugin loader. Deep health must use its reserved queue even
  while another browser operation owns the lifecycle.
- Preserve noexec `/tmp`: reuse upstream's pinned portable yt-dlp installer,
  not the GHCR PyInstaller executable or a weaker runtime mount policy.
- Test UBO using ordinary page-script requests and DOM result transport. Direct
  automation evaluation has different interception behavior on Camoufox 152.
  Exact true/1 alone enables analytics; custom add-ons remain denied otherwise.
- Build/recreate only Camofox. Keep healthy 1.14 active while 152's first-page
  protection lacks a verified readiness gate; do not accept eventual UBO
  readiness as protection of the first page.
- Accept the exact-hash browser-init adapter only with a page-world protection
  gate before publication, one global launch deadline, late-resource disposal,
  and cleanup-failure degradation. Bind VNC to exact accepted process identities
  and tear it down before checkpointing. Activate only after independent review
  and immediate post-recovery acceptance; then prune superseded local Camofox
  images while retaining immutable rebuild coordinates.

## 2026-09-09 — Hermes Talk delegation

- Retain the installed Talk plugin's API execution, result watchers and existing
  authorization/stop handling. Missing optional host attachment is not a reason
  to build a second executor or weaken authority checks.
- Fix profile-local endpoint selection while preserving credentials; tune the
  existing bounded API timeout only where repeated live tests justify it.
- Require actual tool execution through the intended API and operator-confirmed
  spoken delivery. A provider probe or no-tools `talk check` alone is insufficient.

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


## 2026-09-12 — Cache baseline activation

- The operator re-authorized the Route-only rebuild under the disk exception.
  Reused cached base/dependency layers and recreated only Route, retaining its
  previous image for rollback. Other services were not rebuilt or recreated.
- Activated passive v2 diagnostics. The 24–48-hour baseline begins at the new
  process epoch; historical traffic does not satisfy that timing gate. No
  synthetic probe or production cohort is enabled by this deployment.

## 2026-09-10 — Cache measurement and experiments

- Observe the final native payload with bounded process-local hashes and keep
  missing cache counters distinct from zero. Historical chat heuristics are
  not a native baseline and are not backfilled.
- Probe only after a 24–48-hour v2 baseline identifies a recurring stable
  developer prefix under the same key. One fixed run permits at most twelve
  physical requests and 100,000 estimated input tokens, without retries.
- Production cohorts are explicit, session-stable, candidate-bound and expire
  within seven days. Preserve normal routing, full encrypted replay and tools;
  only add one verified developer-block marker. Stop treatment on failures.
- Exclude whole experimental quota intervals, including failed/unlogged
  attempts. Cache or quota uncertainty cannot justify expansion. Numerical
  gates supplement a separate task, tool-argument and replay quality review.

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

## 2026-09-09 — Operator-authorized activation

- The operator explicitly waived the disk gate for this activation only and
  requested execution of the prepared rebuild/recreate sequence. The normal
  guard remains in the saved activation script.
- Rebuilt only Route and recreated the stack with the existing Hermes image.
  Preserved gateway autostart policy while making Talk available to all profiles.
- Validated the installed client's native Route transport with synthetic audio
  and inert tools, including barge-in protocol and next-turn recovery. No Discord
  call, user-directed message or real tool execution formed part of the test.
