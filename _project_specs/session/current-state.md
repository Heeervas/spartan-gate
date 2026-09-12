# Current State

## Camofox 1.15 candidate — activation blocked (2026-09-12)

Built and trialled the independently reviewed immutable 1.15.0 wrapper after
disk headroom became available. Upstream owns fixed-proxy session preservation;
duplicate OS/VNC apt work is removed. Retained timeout-tab retirement, live
profile fences, durable checkpoints and operator leases. Runtime testing caught
and fixed the scoped-plugin checkpoint callback and health-priority admission;
upstream's pinned portable yt-dlp preserves the existing noexec `/tmp` policy.

The exact source patch chain, repeat application, 46 JS tests, 24 supervisor
tests and 3 Hermes isolation tests pass. All 345 script tests pass with one
optional aiohttp skip; all-tier Compose validation
pass; doctor still reports unrelated pre-existing public paths. Live acceptance
covers persistence, lease, forced checkpoint failure, 20 sequential/five
concurrent tabs, 390x844 viewport, authenticated noVNC, watchdog recovery and the
steady-state page-script tracker-protection matrix. Synthetic cookie/localStorage
state survived 630 seconds of continuous checks and a final checkpoint.

Final recovery exposed the activation blocker: generation 1→2 and deep health
succeeded, but the first ordinary page-script tracker probe allowed GA/GTM/Meta
before UBO became ready. Subsequent probes blocked them. Upstream window/add-on
startup is not an adequate filter-readiness signal. The candidate is not accepted
for deployment; the active service and private pins are restored to healthy 1.14.
No experimental browser-engine patch is included. Further work requires a
reviewed readiness fix or an upstream release with demonstrated parity.

Only Camofox was rebuilt/recreated; the eight neighboring containers and start
times are unchanged. At least 5 GB remained free throughout. The 1.14 rollback
image is retained. The 24-hour log review and seven-day retention window start
only after a future accepted activation. See `docs/camofox-refresh.md`.

## Astra cache diagnostics and gated experiments (2026-09-12)

Implemented and independently reviewed native payload diagnostics, explicit
cache-counter coverage, completed-predecessor timing, private fixed-budget
probes and expiring session cohorts. The evaluator requires comparable cache,
latency and conservative quota evidence, plus separate task/tool/replay review.
Experiment attempts are durably reserved so failed retries or lost logs cannot
improve normal quota calibration. See `docs/spartan-route-cache-experiments.md`.

Validation: all 300 Route tests, 10 quota-report tests, the dashboard cache test,
Compose rendering and diff checks pass. After the operator re-authorized the
Route rebuild under the disk exception, Route alone was rebuilt and recreated
on September 12. The built artifact passes import/schema checks; live telemetry
is healthy, authenticated routing serves v2 and unauthenticated access is 401.
All eight running services are healthy; the seven other containers, images and
start times are unchanged. A rollback image is retained.

The new baseline epoch began at 2026-09-12 08:40:20 UTC. Its first eligibility
review is September 13 at 08:40 UTC, with the 48-hour review on September 14.
The passive report is waiting for ordinary native traffic and elapsed time.
No probe or cohort is enabled and no synthetic model calls were sent.
A Route restart starts a new epoch and baseline window.

## Hermes Talk API delegation (2026-09-09)

The independently reviewed repair uses the installed Talk plugin's existing
Hermes API backend. Named gateways now target their own authenticated API;
credentials and manual gateway start choices are preserved. Repeated default
poll timeouts were resolved in live tests with the plugin's existing probe
setting at five seconds (twenty seconds for task requests).

Real tool checks read unpredictable fixture contents through the API in both
the default and a named profile. The named profile also passed the installed
plugin check after warming API readiness. Native stop requested cancellation
and the API confirmed a cancelled task. Operator-auth/approval regressions
passed (100 passed, 2 expected failures). These checks do not establish spoken
Discord delivery; operator acceptance remains pending. No new execution bridge,
plugin source edits, credential changes, image builds or services were needed.
See `docs/profiles.md` for the configuration and acceptance contract.

## Discord voice UDP proof (2026-09-09)

Implemented an opt-in signaling/UDP relay and narrow Discord client adapter.
Local tests cover destination rejection, authenticated signaling, actual
bidirectional loopback UDP, opaque binary signaling, and cleanup. Hermes
networking has not changed. The operator requested activation across all 11
profiles with one simultaneous session; the relay is now running and healthy
using the existing Route runtime image. Only Hermes was recreated for its new
mounts and credential injection. A real upstream WebSocket returned Discord
Hello and unauthenticated access returns 401. Operator-run channel join and
live bidirectional/DAVE proof remain pending.
See `docs/discord-voice-relay.md`.

## Hermes Talk activated (2026-09-09)

The operator authorized activation and a one-time exception to the local disk
headroom gate. Only Spartan Route was rebuilt; the stack was taken down and
recreated with `--no-build`, retaining the existing Hermes image. All eight
stack services are healthy. The boot hook enabled the reviewed Talk client
`477bf7c79fe212ae4b596374626c869e1d38e668` in all 11 existing profile configs.
Active gateways inherited the dedicated voice credential; existing per-profile
autostart choices were preserved. All profiles share one active voice session.

Live synthetic acceptance from inside Hermes passed through both the deployed
Route endpoint and the installed Talk native wire client: audio, inert tool
output/continuation, cancellation, truncation, next turn and clean close. The
installed-client check took 7.8 seconds, with first audio at 1.2 seconds and
cancellation completion at 160 ms. The initial 128-token audio continuation was
truncated; the successful checks used a bounded 512-token audio budget without
changing production limits. Billing remains unknown. Private redacted evidence
and rollback scripts are retained in the activation bundle.

At that activation stage, Discord playback/interruptions and real tool
delegation were untested; see the later API delegation results above. Startup
reported command-discovery/sync warnings involving other configured plugins;
this is recorded for the Hermes-side Discord follow-up. No Discord call or chat
message was initiated by this activation.

## Subscription Realtime Gateway (2026-09-09)

Spartan Route now has disabled-by-default subscription-only Realtime mint and
WebSocket endpoints with scoped client authentication, one-use tickets, pinned
account reservations and bounded bidirectional transport. The real isolated
acceptance passed synthetic audio, inert tools, cancellation, truncation and
next-turn recovery with zero remaining tickets/reservations. The verified model
is `gpt-realtime-2.1`, voice `cedar`, PCM16 mono 24 kHz. Billing/quota attribution
remains unknown; no API-key or alternate-account fallback exists.

See `docs/spartan-route-realtime.md` for the exact contract and reproducible
harness. The local production service is now enabled following explicit operator
authorization; synthetic installed-client acceptance passed as recorded above.
Real Discord acceptance remains pending. The existing Caddy listener is not a voice ingress. Full Route and script
suites pass; doctor stops on pre-existing workstation-plugin path-scan matches.

## Native Responses Update (2026-07-30)

Spartan Route now transports `/v1/responses` natively for Codex Subscription
and the explicit OpenAI API-key realm. Subscription replay uses `store=false`,
encrypted reasoning items, signed `HERMES_HOME + session_id` affinity,
pre-commit-only slot retry, and nullable reasoning-token telemetry. Hermes
profiles with explicit `codex/*` Responses models discover and enable the
`spartan-cache-lineage` 0.3.0 plugin; cross-profile reasoning is reset while
visible history remains. The active private vault path completed a real Terra
turn and LCM persisted one reasoning item with chain, realm, and MAC fields.
Unsigned replay returns 400 and a signed Subscription-to-API crossing returns
409 before provider egress.

`hermes-lcm` upstream main was inspected and lacks provider replay persistence.
The active/private clones are clean on local commit
`49aeda9990d05dde25a99beb388c7bca0bc0bd7f`, with an upstream-ready patch and
bundle stored in the private Responses backup. Publishing upstream remains
pending because this host has no GitHub CLI/SSH authentication or fork. Sol
also returned genuine `server_is_overloaded` responses during live checks;
Terra native and replay checks succeeded.

Date: 2026-07-19

## Latest Update

Spartan Route now classifies Codex balance windows by
`limit_window_seconds`, accepts weekly-only responses, suppresses obsolete
five-hour rows, and filters persisted balances to current account identities.
Usage refresh is progressive and single-flight through the nonblocking POST,
while polling reads incremental daily rollups instead of scanning
`routing_log`. The full suite passes 179/179. Only `spartan-route` was rebuilt
and recreated; neighboring services remained unchanged, healthy, and at restart
count 0. Runtime acceptance measured the initial authenticated GET at 0.0227 s,
subsequent polling at 0.0075 s maximum, and POST 202 at 0.021 s. Live refresh
returned weekly balances for all four accounts with no false `fiveHour` rows.
Slot 4 still reports the pre-existing `reauth_required` condition.

## Onest Update

Onest 1.000 is now installed at build time in the Hermes and Camofox images
and in the local Browserless wrapper, with checksum verification, Fontconfig
cache refresh, and an `fc-list` build assertion. Hermes and Camofox were
rebuilt and are healthy; each exposes all seven Onest TTFs at runtime. Hermes
rendered a PDF with Onest-Regular, and Camofox smoke/viewport screenshots pass.
The focused contract/installer suite passes 21/21 and the full scripts suite
passes 110/110, alongside Bash syntax, diff, and `sg config` checks.
Browserless completed a build before the local no-build hard rule was added,
but has no current local image and received no runtime validation. It will not
be rebuilt under that rule.

## Caddy Update

Caddy now exchanges one successful Basic Auth for a one-hour HttpOnly session
covering noVNC/Hermes-preview HTTP assets and WebSockets. Camofox and preview
listeners use separate 64-hex secrets; malformed startup values fail closed,
legacy/edge cookies and Basic Authorization are stripped upstream, and invalid
WebSockets return a non-challenging 401. The active private files were migrated
transactionally and only Caddy was recreated. Runtime checks pass for noVNC
module HTTP 200, invalid HTTP 401 with challenge, invalid WS 401 without
challenge, and valid WS 101.

## Previous Update

Spartan Route now has an authenticated `/dashboard-config` page for atomic,
database-backed overrides of the four active `clawroute/auto` primary tiers.
Overrides use ETag preconditions, serialized commit/publication, startup
revalidation, and whole-set fallback to read-only file defaults. The Codex
dashboard now marks current routing-enabled account failures with a red border,
badge, and reason while keeping cooldowns, cached history, and disabled slots
distinct. The full Spartan Route suite passes with 110 tests.

## Earlier Update

Camofox viewport work now uses the Spartan fail-closed route as the supported
contract and can relaunch the shared Camoufox browser with a launch-time window
size when `page.setViewportSize` leaves `innerWidth` at the display size. The
active acceptance path is Camofox-only: rebuild/recreate `camofox`, then run
`sg-camofox-viewport-check 390 844`. Hermes/Meet viewport patching is
intentionally out of scope because Hermes will not be rebuilt for this phase.

## Older Update

`sg-whitelist-domain` no longer uses `docker compose kill -s USR1` for normal
proxy/DNS hot reloads. The helper now sends an in-container `USR1` with
`docker compose exec -T`, keeps `--no-restart` as a compatibility alias for the
new `--no-reload`, and only recreates failed reload services when explicitly
requested.

## Active Task

No active implementation task after the native Responses commit.

## Residual Risks

- Publish the local hermes-lcm provider-replay commit upstream when GitHub
  authentication and a writable fork are available.
- Codex Sol capacity can currently return `server_is_overloaded`; the bounded
  pre-commit retry does not replay committed streams.
- The OpenAI API-key Responses branch is regression-tested but was not charged
  against a live direct API key in this subscription-first private runtime.
