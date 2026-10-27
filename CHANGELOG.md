# Changelog

All notable changes to Spartan Gate are documented here.

## Unreleased

- Add optional per-account weekly and five-hour consumed-percent caps beside
  the Codex quota rings. Pause new routing at either configured limit and resume
  only after fresh provider readings confirm all capped windows are below their
  limits. Keep manual disables and model discovery separate, persist settings
  atomically, preserve dashboard drafts, and enforce caps on Chat, Responses,
  images and new Realtime admissions. Pi account status distinguishes quota
  pauses. Provider delays and in-flight/external use can overshoot these guards;
  this is not exact spend enforcement. Source verification does not activate it.

- Document explicit reuse of an already activated container Pi state by another
  Hermes owner through one symlink, without package copies or recreation. Preserve
  the owner's Hermes context and make shared configuration/session scope explicit.

- Accept schema-shaped neutral managed-search defaults without widening the
  configured SearXNG-only boundary; an empty optional query list no longer
  shadows a usable single query. Expose confirmed browser proxy-policy rejection
  as sanitized `egress_denied`, preserving conservative handle retirement and
  no replay. Active whitelist and timeouts remain unchanged; source verification
  is not installation or browser-domain authorization.

- Align Hermes startup health grace with native cold-start readiness: allow
  75 seconds before counting startup failures, retaining all dependency checks
  and existing per-request, command, interval and retry limits. Guarded activation
  uses a finite 90-second healthy-observation budget with separate native-profile
  acceptance. Existing containers require a separately approved recreation;
  source readiness is not deployment. Operator notes distinguish verified
  current-state reconciliation from missing original execution/timing evidence.

- Discover an existing managed-tools configuration in Pi's native agent directory
  when no explicit selector is set. Preserve strict explicit-selection guards,
  project only action-active browser arguments, and retain safe search error
  metadata instead of rendering failed calls as empty successful searches.

- Repair proxy/DNS supervised reloads so a handled `USR1` does not terminate the
  supervisor, and route scheduled regeneration through the same owner. Add
  fail-closed active-policy publication preflight, a shared protected transaction
  directory and no-copy atomic replacement for policy-only directory binds.
  Existing file-bind installations need a separately approved migration; source
  readiness does not activate services or make a legacy Hermes inspection mount
  a current enforcement view.

- Add opt-in shared managed-tool access for native host Pi and scoped HTTP
  clients: a dedicated loopback Caddy edge, fixed bounded SearXNG/Reader
  transports and server-issued browser workflow/tab authority. Reuse the native
  Pi search provider; keep credentials outside model history, preserve actual
  resume/fork identity and return bounded native PNG images. Add public-safe
  setup/rollback and isolated native/container acceptance without installing
  active clients or changing Hermes/Route connections. Deployment is pending.
  Installer path checks admit same-owner group-writable directories only beneath
  a verified owner-only `0700` enclosure, not exposed or world-writable paths.
- Bound Reader's entire fetch/redirect chain with one deadline, interrupt owned
  outbound sockets and account for residual DNS in four fixed workers. Add an
  opt-in single-attempt Camofox click mode without changing existing defaults.
  Keep profiles, storage and administrative authority separate.
- Seal Route's Python fixture environment/auth paths and non-loopback DNS/TCP
  before test discovery; isolate native Caddy fixture HOME/XDG state. Dedicated
  tool-edge errors no longer log private query URLs. Historical unsealed test
  side effects are disclosed in the shared-tool review; passing tests do not
  certify production preservation or authorize cleanup/activation.

- Complete the Hermes COMMON profile recipe beyond plugin enrollment: preserve
  native memory/USER learning, reviews and weekly curator behavior, explicit LCM
  settings, supported GPT-6 Luna helper assignments and distinct native/profile
  delegation limits. Profile delegation permits eight concurrent calls at depth
  two, without expanding async capacity or outgoing authorization. Keep recovered
  profiles unserved until separately approved; never clone DEFAULT credentials.

- Restrict Hermes runtime documentation to three curated public-safe guides in
  `apps/hermes-agent/runtime-guides`, mounted read-only at the existing
  `/opt/data/brain/runtime_docs` path. Move Camofox/package guidance and promote
  the sanitized profile recipe with the eight-plugin BASIC baseline, retaining
  `local/private/profile-creation.md` for continuity. Remove the example nested
  private-doc bind; keep operator notes as an unserved host-only archive.
  Existing private mounts require separately authorized migration/recreation.

- Restore Codex subscription pool failover for standard Responses replay. Keep
  authenticated opaque receipts and exact ciphertext/tool history; treat the last
  issuer as a preference after operational/model/effort gates, not an account pin.
  Accept valid mixed-issuer histories and stamp new receipts for the actual attempt.
  Preserve legacy raw safeguards and existing retry/ambiguous-send boundaries.
  Cross-account completion does not prove lossless hidden-reasoning reuse. Source
  verification does not activate this policy.

- Add optional, bounded client-asserted conversation correlation for Pi and
  Ultimate without changing cache keys, replay authorization or account affinity.
  Keep related child sessions independent, expose purpose and provenance, scope
  lazy turn detail to its logical session, and rename passive cache checks to
  cache observations. Historical telemetry is unchanged; source verification
  does not install clients or activate the Route service.

- Add source-only Ultimate worktree setup with Node 22, locked npm dependencies
  and isolated minimal Python test dependencies, without copying private data or
  enabling automatic landing checks, pushes or deployments.

- Fix OpenAI SDK 7.19 `responses.stream()` accumulation on Codex streams by
  excluding only proprietary `response.metadata` sideband after raw telemetry
  observation. Preserve terminal errors/usage, tool and encrypted replay,
  create-iterator and native Pi/legacy behavior. Deployment remains a separate gate.

- Support the documented stateless Responses conversation subset without client
  signing, using server-owned opaque Codex replay receipts. The original conservative
  issuing-account pin is superseded by the advisory issuer policy above. Preserve
  signed legacy Hermes/Pi histories, shared-secret trust limits and explicit
  visible-history recovery for old-server rollback.
- Avoid binding RPCs for new Pi sessions while retaining lazy legacy migration.
  Validate saved legacy provenance before committing a local realm lock; failed
  wrong-realm migration leaves the original realm resumable. Retain append-failure
  poison state across extension reloads of the same native SessionManager, so
  phantom in-memory locks cannot authorize submission. Preserve native named IDs,
  tools, usage and copied zero-reasoning visible history without bulk rewrites.
  Source verification and review do not imply active installation or deployment.

- Add opt-in native required-skill selection for Hermes profiles, preserving
  catalog contents, existing controls and one-shot restrictions without rewriting
  quoted project text.

- Fix native Responses and usage tracking for named Pi subagent sessions by
  mapping native IDs to stable Route UUIDs without renaming persisted sessions.

- Add native Responses discovery and signed Pi session reasoning replay while
  preserving other model protocols and Route's realm isolation. Track native
  Pi usage across attempts, retries, and stream cleanup.

- Document the approved Hermes process-instruction correction and its graceful
  gateway refresh. Preserve profile controls, distinguish persisted-session
  prompts from fresh loading, and leave efficiency claims for actual task evidence.

- Restore workstation command gateway discovery after the legacy-service
  cleanup. Respect Compose project selection and reject unavailable or invalid
  network gateways before invoking the workstation bridge.

- Preserve application query strings through Caddy signed-session authentication.
  Match verifier endpoints using only the literal URL path, retaining service-key,
  signature, audience and expiry checks plus exact malformed-path rejection.

- Keep the optional native Hermes `/plan` policy patch out of normal image
  builds pending runtime acceptance. Its evaluated source supports planning
  across domain profiles, complete enabled support checks and native fallback,
  while preserving plan-only behavior and independent review readiness gates.
  Document the existing native command as the common planning alternative.

- Quote the edge verifier's tmpfs mount as one YAML scalar, retaining all mount
  restrictions and preventing Docker from treating options as relative mount
  paths. Regress the exact rendered mount across all edge-enabled layers.

- Pin Spartan Route to aiohttp 3.14.4 for current HTTP dependency security fixes;
  add regressions for initial-request/header timeouts and Secure shared cookies.
  Make Hermes read-only runtime-document mount tests independent of unpublished
  operator directories so they run in clean checkouts.

- Retire Browserless and the legacy ClawRoute application from supported source,
  installation shapes and operator helpers. L3 now uses internal Spartan Route;
  Camofox remains the managed browser. Preserve shared Route configuration names,
  existing private data and the Camofox Meet transcription capability; CDP-only
  workflows are no longer supported. Existing deployments need explicit migration.
- Require successful upstream terminal events before publishing Codex tool calls,
  fence delayed credential outcomes by attempt identity, close upstream resources
  before stream iteration, and isolate durable slot policy from telemetry locks.
  Bound SSE frames and move Pi database work off the event loop.
- Protect doctor diagnostics with exclusive private scratch and cleanup, bound
  Reader's inbound request lifetime, minimize proxy audit URLs, and reject partial
  or incompatible Hermes stage2 patches against pinned upstream source.
- Enforce edge session expiry with an isolated, bounded HMAC verifier and stock
  Caddy authentication; add transactional configuration preparation and signed
  session migration. Activation requires new verifier wiring and independent keys;
  no deployed stack or private browser data is changed by these source commits.
- Replace legacy CI targets with current-stack portable and pinned-source native
  checks; repair test isolation and add file-backed dashboard characterization.
  Comparative measurements establish output parity, not a claimed speed gain.

- Bake hash-pinned Qlty 0.659.0 and Desloppify 1.0 into Hermes for optional
  local analysis. Preserve Qlty's native CLI syntax with telemetry and upgrade
  checks off; process-locally expose pinned analyzers and isolate Desloppify's
  full-extra imports from mutable Hermes paths while retaining policy bootstrap,
  Hermes venv isolation and license notices. Rebuild/recreation and live
  nonroot profile acceptance remain required; no new native quality gate.

- Show explicitly partial Pi quota balances when some enabled sources lack
  windows or known capacity, preserving unknown full-pool totals. Keep tiny
  positive session estimates visible instead of rounding them to zero.

- Add Pi's minimal Route quota display with pool, account and exact lease views,
  persistent session consumption, calibrated unit estimates and bounded outage
  recovery. Preserve native compaction, resume and separate subagent sessions.

- Add durable Pi session origins and cumulative observed Chat Completions usage,
  with passive sanitized quota views, exact lease lookup and shared refresh
  admission. Preserve reported zero values and account for retries and streams.

- Add scoped TOP quality measurement for the Pi/Route usage implementation,
  preserving the existing Hermes checks and pinning the TypeScript analyzer.

- Preserve selective links to registered canonical skill packages during native
  bundled sync, including byte-identical bundles, while retaining containment
  guards for unrelated targets. Limit the shared skill bind mount to its
  dedicated export so host desktop skills stay outside the Hermes filesystem.

- Add a Pi extension that supplies an explicit session prompt-cache key for
  Spartan Route Chat Completions requests while preserving existing keys, with
  installation guidance and focused regression checks. Provider cache hits and
  savings remain dependent on actual upstream requests.

- Document opt-in writable host skill sharing for Hermes through
  `skills.external_dirs`, with explicit profile enrollment, preserved local
  precedence and creation destination, SELinux shared labeling and non-root
  runtime acceptance checks. Record verified default-only activation with an
  empty library, unchanged named profiles and local skills, and the narrowly
  scoped SELinux correction required by the Compose Mount API limitation.

- Fix repeated Spartan Route dashboard session scans when selecting multiple
  turns. Read selected-turn metadata and complete requests in one snapshot,
  scanning each selected session once, with a covering metadata index to avoid
  loading retained context payloads during selection and filtering. Publish
  sessions and summary independently, avoid immediate timeout replay, and reuse
  active background reads when returning to a tab. Deploy the reviewed
  correction with Route-only recreation and live dashboard acceptance.

- Redesign Spartan Route's Routing, Codex and Config dashboards with responsive
  navigation, quota percentage rings and live countdowns. Preserve expanded
  details, focus and unsaved configuration across refreshes; lazy-load request
  details, revalidate authenticated local assets, pause hidden-page polling and
  prevent stale reads or retired quota refreshes from overwriting newer state.
  Bound telemetry materialization to selected turns, batch account lookups,
  isolate heavy SQLite readers and update today's summary incrementally.
  Source and corrected runtime validation are complete. Record
  the applied private image-pin correction so the normal Route build command
  can select the current source.

- Preserve reviewed operator updates: guarded cleanup of obsolete host Codex
  builds, safe workstation mount preparation, explicit Hermes recreation
  confirmation, shared MASTER prompt loading, and accompanying operational
  guidance. Runtime activation remains subject to each feature's documented
  acceptance steps.

- Recover Codex model eligibility through bounded shared catalog refreshes and
  report model-specific selection diagnostics without bypassing account,
  reasoning, identity, or quota restrictions. Keep removed discovered models
  fail-closed. Save an adversarially reviewed Hermes delegation repair plan;
  Hermes implementation and production activation remain pending.

- Complete native profile provisioning guidance: authorized in-gateway creation,
  curated identity/skills, new-profile cache-lineage and Reader checks, scoped
  delegation, shared Discord routing and distinct plugin/route reloads.

- Add backed-up preparation for private Hermes 4 GiB RAM/6 GiB RAM-plus-swap
  limits and a 300-second LSP idle timeout. Document automatic edit diagnostics
  and a complete final Pyright check; operator activation and acceptance remain
  pending.

- Make bundled Linux and Onest font paths explicit in Camofox, preserving
  upstream Fontconfig license comments and rendering rules. Reject conflicting
  launcher font configuration on patch reapplication.


- Recognize the reviewed native Hermes launcher in workstation peer checks.
  Bound transfer staging by validated approved bytes and a configurable 1 GiB
  reserve; deny missing disk information. Release whitelist policy locks before
  Docker reloads and bound lock contention to 30 seconds. Document managed Route
  inheritance, workstation state ownership and scoped secret passthrough.


- Refresh Hermes profile creation guidance for native multiplex enrollment,
  parked manual profiles, shared API authentication and scoped reloads.
  Update the live runtime guide and distinguish standalone provisioning.

- Fix `sg-hermes env set` terminal prompting with separate input/output streams,
  report missing terminal access clearly, and remove its free-space gate.

- Add hidden `sg-hermes env set` prompts for deliberately shared tool values
  in the existing administrator-managed dotenv, with private backups and a
  choice to recreate only Hermes now or save for later.

- Keep Spartan Route variant names stable across context and base-model edits.
  Add editable custom model IDs, preserve existing names when loading earlier
  configurations, and reject removed custom aliases across restarts.

- Manage Spartan Route context variants live from Config using a base model
  and token window. Persist the complete list with transactional, revision-safe
  updates, preserve initial 282k compatibility, and reject inactive or removed
  variants before forwarding requests.

- Detect changed Codex bootstrap credentials on healthy cached slots, invalidate
  stale auth, leases, usage cache and catalog eligibility, and wake discovery
  even without a prior catalog entry. Preserve managed-state account binding
  and expiry selection, with fail-closed catalog source identity checks.

- Clarify that Camofox analytics mode disables only default UBO and retains
  custom add-ons, with paired Obsidian Web Clipper configuration.

- Connect native Hermes credential-vault tools to Camofox with authenticated
  secret transport, durable tab scope, exact-origin OTP checks and textual
  result redaction. Preserve native secure prompts, payment consent and the
  documented screenshot confidentiality limitation.

- Revalidate sealed Hermes image dependencies at native CLI and gateway startup
  before omitting PM's generation verdict, with fingerprinted overlay guards
  and preserved native package-manager diagnostics.

- Validate and deploy the optional Camofox-only Hermes image with recent runtime
  repairs and authenticated service acceptance. Record measured storage savings,
  dependency drift, and the upstream rebuild prerequisite after build-image cleanup.

- Add an explicit image-only Hermes startup mode that validates baked Python,
  measured extras and enabled plugin requirements before skipping automatic
  dependency sync and generation collection. Refuse selected generations and
  preserve existing dependency facts and stamps unchanged.

- Normalize injected Camofox-only build helpers to mode 0644 and repair the
  existing variant base's root patcher before the final installation audit.

- Enforce the optional Hermes Camofox-only image at startup and package-manager
  admission. Require managed Camofox, reject native Chromium dependency chains
  before provisioning, and preserve updates for supported tools.

- Preserve the image's measured Hermes optional features across the first
  managed dependency generation. Bind raw API delegation claims and durable
  delivery to a unique served owner without changing database schemas or
  persistence functions. Validate and repair only the TypeScript companion
  SDK, and keep intentional cron destinations out of lost-thread warnings.
- Add dry-run-first, backed-up profile/plugin remediation for scoped search,
  Talk transcript worker context, Toolaria ownership and safe pass-through,
  absent legacy plugins, and retired reset timers. Include guarded native PM
  metadata recovery; runtime activation remains a separate operator action.
- Accept native PM image fallback records without generation pointers during
  guarded feature recovery; reject malformed pointers and preserve unrelated
  package records without installing dependencies or accessing databases.

- Request the Google Ads OAuth scope in the Gog Workspace helper defaults and
  reject incomplete grants before Workspace reauthentication. Document scope
  override behavior and the separate Google Ads developer-token requirement.

- Scope Discord `/voice` lifecycle and controls to the receiving Hermes profile
  and server, preserve bridge bindings in session tasks, and prepare the Opus
  codec before native join. Document Talk 0.21.1 multiplex routing and the
  remaining upstream result-return rejection; live audio acceptance is pending.

- Add opt-in pinned Camofox-only Hermes build preparation that omits bundled
  Chromium from its creation layer, preserves the full toolbox and installs
  variant guards before resident PM sealing. Keep the full image as the default
  and document explicit image selection and peak-storage review.

- Isolate native Hermes background-review cache tracking from foreground
  history while preserving compatible shared provider cache keys and signed
  reasoning identity. Retain bounded hash-only foreground snapshots for late
  reviews, with stateless handling for reviews missing identifiers. Uses the
  existing Hermes origin API without a Hermes patch or rebuild.

- Added bounded, cache-isolated Codex WebSocket inference with explicit proxy
  enforcement, full-history replay and safe pre-delivery HTTP fallback. Tuned
  HTTP pooling, isolated background control traffic, fixed split UTF-8 SSE
  decoding, and recorded transport/first-output timing separately from HTTP
  header latency. Updated aiohttp to 3.14.3 and validated the separate voice
  relay against the shared image dependency. Preserve completion and cache
  accounting across recognized upstream WebSocket timing diagnostics.

- Group validated Hermes native Responses chains into stable dashboard sessions
  across cache-key changes, preserving provider keys, account affinity and real
  cache counters. Add bounded conversation diagnostics for changed keys and
  instructions without changing cache experiment comparisons.

- Checkpointed the deployed Hermes refresh, managed Camofox policy, fast-mode
  forwarding, Route cache affinity and catalog/context updates, operator
  helpers, and existing architecture drafts before transport optimization.
  The checkpoint preserves the documented residual plugin acceptance failures.

- Added selectable `-282kcontext` Spartan Route model variants for eligible
  text models. Hermes sees a 282,000-token context window while Route forwards
  the base model ID; unknown or ineligible variants return 404.

- Let quota-cooled native Codex Responses requests retry after a coalesced live
  usage check, and keep cooldowns in place when reported usage is missing,
  malformed, or still full.

- Deferred the Hermes Talk voice-command gateway patch until dispatch so
  plugin discovery cannot deadlock while the gateway module imports.

- Added GPT-6 Sol/Luna to Spartan Route and automatic, account-aware discovery of compatible Codex Subscription models. Model selection respects per-account availability, keeps existing auto tiers, and fails closed for unknown or unavailable models. Preserved the deployed credential-reload fix for reauthenticated quota-cooled slots in the new Route image.

- Activated immutable Camofox 1.15.0 and retired the duplicate fixed-proxy guard and OS/VNC apt layer. Adapted durable checkpoints to upstream scoped plugin contexts, fixed reserved health admission, and reused upstream's checksum-pinned portable yt-dlp under noexec temporary storage. Added an exact-hash once-per-profile add-on patch, page-world UBO admission, global launch deadline with late-resource cleanup, and identity-bound VNC teardown before recovery checkpointing. Independent review and cold-start/open-WebSocket recovery acceptance pass; superseded local Camofox images were pruned while rebuild coordinates remain documented.

- Activated the reviewed cache diagnostics with a Route-only rebuild/recreation. Live health, telemetry, dashboard authentication and v2 reporting pass; other running services are unchanged. Started the passive 24–48-hour baseline with probes and cohorts disabled.

- Added opt-in Astra cache probes with a server-enforced 12-attempt/100k-estimated-input budget, private epoch-bound authority, expiring session cohorts and fail-closed experiment reservations. Added baseline and session-clustered evaluation gates; quota calibration excludes whole experiment-touched intervals, including unresolved and failed retries. No experiment or prompt mutation is enabled by default.

- Corrected native cache diagnostics to observe final Responses payloads with account/realm isolation, bounded private hashes, completed-predecessor timing, cancellation-safe cleanup and explicit cache-read/write coverage. Added separate incremental v2 aggregates and labelled historical cache heuristics as legacy.

- Fixed native Responses tool telemetry across streamed items and final output, including custom and built-in tools. Routing sessions now distinguish complete counts, partial observations and unverified historical zeros. Added a read-only model-isolated quota report and labelled dashboard quota values as pool estimates.

- Fixed Discord voice joins using an explicit `:443` endpoint: the relay now compares canonical upstream URLs, preserving redirect and changed-destination rejection.

- Validated real Talk-to-Hermes API tool delegation after correcting named profile URLs. Documented the plugin's existing polling timeout after repeated default-profile timeouts passed with a bounded 20-second request budget. Credentials and the installed plugin remain unchanged.

- Documented profile-local Hermes Talk API routing and acceptance: named gateways must use their own API port while retaining existing credentials. Distinguishes effective gateway environment, actual API delegation and spoken Discord acceptance from connectivity-only checks.

- Preserve Realtime sessions across the provider's benign inactive-response cancellation, with a content-free counter and fail-closed regressions. Add an opt-in Hermes plugin that starts Talk with `/voice join` and stops the matching session with `/voice leave`, preserving existing tool authority.

- Enabled the local Discord voice relay prototype for all Hermes profiles with one shared session, preserving internal-only Hermes networking. Verified real upstream voice signaling; channel audio acceptance remains pending the operator test.

- Added an opt-in Discord voice relay proof of concept and a narrow Hermes adapter, with authenticated signaling, provider-derived UDP destinations, bounded opaque packet forwarding and local security/transport tests. Real Discord acceptance remains pending; the relay is not enabled by default.

- Recorded operator-authorized local Realtime activation and successful live synthetic acceptance through the installed Hermes Talk native transport. All profiles have Talk available; real Discord playback and delegation acceptance remain pending.

- Added an opt-in, fail-closed boot helper for enabling a reviewed Hermes Talk plugin across profiles, with preflight, idempotence, rollback and private Compose credential delivery. Supports container recreation without a Hermes rebuild; live Hermes/Discord acceptance remains pending.

- Passed real isolated Route acceptance for audio, inert tools, cancellation, truncation and next-turn recovery with no remaining reservations/tickets. Added disabled-by-default Compose settings, the HTTP/WS operator/Hermes contract, malformed/oversize/race regressions and a credential-free source-bundle check. Production and Discord activation remain pending.

- Added disabled-by-default subscription-only Realtime mint/WS handlers with independent voice authentication, pinned-account tickets, bounded duplex forwarding, redacted lifecycle diagnostics, private capability observations and shutdown cleanup. Added fake-provider regressions and a loopback-only real-Route acceptance harness; production activation remains separate.

- Added independently scoped Realtime client policy, strict voice-session validation and bounded one-use ticket/account reservation primitives. This slice is offline-tested and does not yet register live endpoints.

- Verified a real subscription OAuth Realtime session with synthetic PCM audio, inert function output and audio continuation. The probe now reports redacted completion reasons and gives the tool response a bounded 1,024-token allowance after an observed 128-token truncation. Quota/billing attribution remains unknown.

- Corrected the Realtime probe to explicitly reuse Route's HTTPS proxy for WSS and fail closed without it. The first authorized OAuth mint returned 200, but its WebSocket connection failed before audio/tools; recorded that partial evidence without claiming voice or subscription billing support.

- Added an offline-by-default, explicitly authorized single-slot Realtime feasibility probe with synthetic PCM/tool checks, existing Route OAuth custody, sanitized diagnostics and a reviewed implementation/runbook. Live subscription access remains unverified; this batch does not enable voice endpoints.

- Checkpointed existing Spartan Route GPT-6 Astra catalog/reasoning support and batched telemetry-retention deletes. The full Route regression suite passes; no production service was restarted.

- Checkpointed existing Camofox 1.14 adapter, addon protection, persistence and operator changes. Local browser/script suites and Compose rendering pass; the pristine upstream patch-chain fixture and live browser acceptance were not run, so this checkpoint is not deployment validation.

- Checkpointed existing Hermes main compatibility updates and runtime-overlay import precedence before Realtime work. Local Python and JavaScript patch suites pass; this checkpoint does not deploy or rebuild Hermes.

- Refreshed Hermes to upstream main `d1afa160` on immutable amd64 digest `sha256:77cb88a7...`, rebased numeric Stage2 and gateway supervision patches onto upstream ownership/TOCTOU hardening, made cache-lineage 0.3.0 fingerprint native Responses instructions and input separately from Chat history, retained legacy scalar YAML values in MCP subprocess environments through explicit string normalization, and restored non-root traversal of the newly created Node/Python bootstrap directory.

- Mounted versioned Hermes runtime guides read-only from Compose, including public package-install and Camofox capability guidance plus a separate private documentation overlay. Removed the image-baked guide and startup seeding path.

- Added optional `CODEX_HOME` selection to `sg-tmux codex` when an existing named local Codex home is supplied, while preserving exact tmux session names and argument forwarding.

- Added native Responses transport for Codex Subscription and OpenAI API-key
  providers, including encrypted reasoning replay, signed profile/session realm
  affinity, pre-commit-only slot retry, nullable reasoning-token telemetry,
  Hermes/LCM replay preservation, and transactional private config migration.
  Profile-local plugin discovery uses managed relative links to the single
  read-only user-plugin mount; migration enables it only for explicit Codex
  Responses profiles and rejects path collisions before writing config.

- Added an approval-gated, fixed-destination workstation terminal and bounded
  file-transfer path for Hermes, with a private Unix broker, exact SSH host-key
  pinning, scoped restart/revocation helpers, and fail-closed regression tests.
  Live one-time approvals now use the registered per-session gateway callback
  as the owner proof instead of depending on lossy platform context metadata
  or the scheduler's sticky process-global cron flag. Cron runs remain closed
  because they have no non-empty interactive session key or registered owner
  callback.

- Added a bounded, read-only host memory audit with 24-hour `sysstat`, PSI,
  Docker cgroup/OOM, and swap-contract diagnostics; exposed it through
  `sg-memory`/`sg-health` with readable current/24-hour summaries and a
  persistent per-user 04:20 systemd timer.

- Cleared settled Camofox request deadline timers, bounded per-user operation
  queues, and removed expired waiters without releasing lifecycle tracking for
  underlying browser work.

- Added fail-closed, operator-started SSH/application relays for a private
  Tailscale workstation and sanitized host-connectivity status reporting;
  expanded the authenticated private Hermes extension edge to literal profile
  routes without exposing dynamic upstream selection.

- Kept Camofox cookies profile-scoped while isolating Hermes tabs by durable
  session ID, forbade cross-session tab adoption, handled stale 404/410 tab
  generations explicitly, and stopped managed tab cleanup from deleting the
  shared persistent browser context.
- Added atomic, single-flight Camofox storage checkpoints; renewable operator
  leases; query-secret log redaction; earlier cgroup admission/eviction/hard
  thresholds; and a transactional, rollbackable legacy-login migration across
  all existing Hermes profiles. Added pinned Wrangler 4.45.0 helpers for a
  private API token or stdin-only assisted OAuth callback completion.

- Limited Camofox's internal recovery wait to two minutes; later durable
  escalation levels preserve their penalty but exit PID 1 non-zero so Docker
  restarts the container immediately under `restart: unless-stopped`.
- Kept stale Camofox readiness fail-closed during bounded lifecycle work without
  letting those busy samples consume the supervisor recovery threshold; owners
  that exceed their deadline still advance normal recovery.
- Prevented cached noVNC shells from skipping Caddy edge-session refresh by
  versioning live-profile URLs and marking authenticated noVNC responses
  `no-store`, while retaining fail-closed WebSocket authorization.
- Added a PID-1 Camofox supervisor with credentialed loopback deep readiness,
  cgroup/OOM monitoring, process-tree cleanup, and persistent capped automatic
  backoff without Docker socket access or manual breaker resets, including one
  revision-aware rollout attempt that preserves the accumulated penalty.
- Fenced timed-out Camofox tab creation, cleaned late pages/contaminated
  sessions, returned semantic 503 errors, removed the unbounded upstream probe,
  made Compose memory admission use its cgroup limit, and stopped the
  empty-group/session and orphan-page reapers from invalidating newly created
  tabs by holding explicit fences only while the underlying create work is
  pending.
- Made Camofox public health a passive generation-bound readiness cache, added
  browser-independent liveness, and latched three terminal real-navigation
  timeouts as a hard browser fault. Recovery is single-flight per child PID and
  browser generation and succeeds only after a different generation passes a
  deep probe; local capacity now sheds with 429 while internal timeouts and
  degraded browser state remain 503.
- Stopped sharing one global Camofox owner/session across supervised Hermes
  profiles, disabled global tab adoption across L1/L2/L4/install/examples, and
  retained Google Meet as an explicit default-profile single-owner exception.
- Serialized Camofox create lifecycles through a configurable FIFO after
  concurrent Camoufox navigations stalled despite successful parallel proxy
  requests. Waiters are bounded by `MAX_SESSIONS` and cancel before the outer
  handler deadline, so expired requests cannot run later in the background.
- Persist rotating Codex OAuth grants in a private Spartan-owned store with
  cross-process refresh coordination, atomic durability, terminal-grant
  quarantine, and safe lifecycle diagnostics without mutating source profiles.
- Made Codex balances load from persisted telemetry immediately while one
  bounded concurrent refresh runs in the background; promoted weekly remaining
  balance to the primary wheel, kept optional five-hour windows compatible,
  added mobile-first session activity diagnostics and actual tool telemetry,
  and added masked/fidelity-aware Codex image edits.
- Added fail-closed Spartan Route SQLite ownership preflight to normal startup,
  rebuild, doctor, and L4 install paths; added a stopped-state exact-file
  migration with verified backup and rollback, replacing recursive data-root
  ownership repair while keeping newly created SQLite files private, including
  WAL/shared-memory sidecars materialized during backup verification.
- Replaced Spartan Route's SQLite sort/group telemetry aggregation with
  streaming aggregation over the retained 50,000-row window, keeping daily
  cache and rollup rebuilds within the service's fixed 32 MiB temporary mount.
- Kept live Codex usage available when snapshot/reset persistence or cache
  reads fail, returning sanitized partial metadata instead of HTTP 500;
  telemetry-only APIs now return a structured 503, health remains ready but
  reports database degradation, and the Codex dashboard marks partial data.
- Made L4 Hermes health reflect the complete gate path by requiring its local
  API, Tinyproxy, and Spartan Route to remain reachable, with bounded retries
  that tolerate short dependency restarts while surfacing persistent internal
  network loss.
- Pinned GogCLI and Deno release artifacts to architecture-specific official
  SHA-256 values and verified each download before extraction; replaced
  Camofox's moving uBlock Origin addon URL with an exact AMO-signed release,
  digest verification, and manifest-version validation in camoufox-js's
  expected cache.
- Aligned memory/PID ceilings and the proven Hermes privilege contract across
  every layer and addon; made Spartan Route, legacy ClawRoute, and Caddy
  explicit non-root read-only services with narrowly writable mounts, added
  host-derived installer identities, and added Caddy config/listener readiness
  checks across public and private renders.
- Bounded public/private egress-policy fragments and normalized active entries
  before host or container replacement; made proxy and DNS generation
  candidate-first, made DNS install its full artifact set transactionally, and
  restored the complete prior policy on partial installation or child reload
  failure before exiting through the fail-closed restart path.
- Replaced Reader's single-threaded server with a fixed worker pool, bounded
  pending admission, immediate `503` shedding, and deadlock-free signal
  shutdown; rejected URL userinfo, redacted target paths/queries from all
  logs, validated runtime integers, and dropped the service to fixed UID
  `10001` with no capabilities, no-new-privileges, read-only root, and a PID
  ceiling.
- Validated whitelist refresh times and installed traversal-free timezones in
  doctor and both egress services before child startup, and made scheduled or
  signal-triggered regeneration/signaling failures terminate proxy/DNS so
  restart re-enters the fail-closed startup path instead of serving an expired
  runtime policy.
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
