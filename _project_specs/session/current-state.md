# Current State

## Talk 0.21.1 and all-profile `/voice` compatibility (2026-10-07)

Updated the private adapted Talk snapshot from 0.17.2 to upstream 0.21.1
(`981711b8f44df78e2ad958174e931c1b584b8bdd`), preserving the Spartan Realtime
transport, sanitized diagnostics and scoped credentials. The previous source
and hash manifest are retained for rollback; no image was rebuilt.

The public `spartan-talk-voice-command` 0.2.1 wrapper uses Hermes' current
`_delivery_adapter_for` resolver. Bare Discord `/voice` starts the caller's
channel and Talk; `/voice leave` stops only the owned session before native
disconnection. Context-local bridge bindings survive into session tasks, so
secondary profiles never borrow the default bot's connection. Status and
microphone controls enforce adapter/server ownership; native explicit text-voice
modes and other platforms retain their behavior.

The adapted Talk configuration now derives the live multiplex API listener and
`/p/<profile>` namespace instead of using stale standalone URLs or the default
profile endpoint. Credentials remain profile-scoped; unavailable/malformed
listeners fail closed. Both plugins are enabled and linked in all 11 existing
profiles. The shared Route credential still permits one active call at a time.

Initial validation: the upstream candidate suite passed 2,760 tests with 61 skips and
five expected failures; final configuration/transport checks passed 168 tests
with two expected failures. The final wrapper passed 25 tests and provisioning
passed ten. Native Hermes loading plus inert `/voice` start/leave, context
inheritance, bridge selection and API routing passed for all 11 profiles, again
against the installed source. Installed source hashes match the retained
manifest. Only the default multiplex gateway process was gracefully reloaded;
all nine container identities were preserved. Final authenticated health and
readiness are `ok`, all 12 configured platform connections are active, and all
11 profile API capability endpoints return authenticated HTTP 200 with run
submission enabled. Real Discord audio remains pending operator acceptance.

Follow-up runtime errors exposed paths those inert checks had missed: Hermes
executes synchronous plugin commands in a worker, so `/talk` incorrectly chose
terminal audio and leaked `SystemExit(1)`; the new capability catalog warmer
lost profile credential context; and this image lacked Opus. The narrow repair
makes gateway-worker `/talk` return `/voice` guidance, contains terminal CLI
exits, copies context into catalog reads, and loads a locally bundled compatible
codec before the native voice join. No command framework or additional aliases
were added. Join a Discord channel yourself, then issue `/voice`; stop with
`/voice leave`.

Repair validation: 179 focused Talk tests passed (three skipped), and 27 wrapper
checks passed. Native Hermes worker dispatch and inert `/voice` lifecycle passed
for all 11 profiles. Actual DiscordAudio capture/handoff/cleanup and native
VoiceClient playback encoder creation passed offline with real Opus encode and
decode. The repaired gateway was gracefully reloaded and reached authenticated
health/readiness `ok`, with all 12 configured platform connections connected and
all 11 profile capability APIs authenticated. These checks do not establish a
live provider/audio conversation.

A subsequent organic Discord call failed during result return. The latest
Hermes API run is `completed` and its Talk run is `done`; the gateway remains
healthy. Talk logged announcement starvation, then Route closed the voice
session with `realtime_upstream_configuration_rejected`. Talk's `delivered`
marker records a wire send, not audible playback. Sanitized logs do not retain
the exact upstream rejected event, so no speculative transport patch was made.
Transcript-memory handoff also loses profile scope on its background thread.
Native TTS prerequisite checks pass for the default and affected profile;
a switch to native voice is pending the operator's mode preference.


## Hermes background-review cache lineage activated (2026-10-03)

The reviewed `spartan-cache-lineage` 0.4.0 plugin separates native Responses
foreground history from background-review runs using Hermes' existing
context-local provenance API. Reviews inherit a compatible retained foreground
cache lineage, including nonzero and delayed snapshots, without overwriting
foreground tracking. Actual rewrites still advance their own lane. Snapshot and
review state remains hash-only and bounded. No Hermes or Route code patch was
needed; LCM and request content were not modified.

Implementation and regression work used an isolated, initially clean worktree.
Three focused regressions failed before the fix. Final validation passed 77
plugin tests, ten Route lineage/native-binding contract tests, installed-Hermes
provenance integration, and the actual Route/Codex key and private-field
helpers. Independent adversarial plan and implementation reviews found no
remaining blocker. No paid inference probes ran.

The clean combined transport branch was fast-forwarded to the implementation.
Hermes alone was force-recreated with its existing image. The committed plugin
source matches the live mount; non-root discovery loads version 0.4.0 and its
request middleware/response hook. API health, proxy and Route connectivity pass.
Hermes remains read-only on internal networks; all eight neighboring container
IDs were preserved. Cold startup took approximately three minutes before the
health endpoint became ready.

Startup also logged an LCM plugin load-timeout warning with ignored late
registration callbacks. Cache-plugin discovery succeeds, but those checks do
not establish LCM activation in every already-running profile. Keep that
separate runtime question visible; provider cache savings also require organic
traffic rather than inference from key stability alone.

## Route transport and stable signed dashboard sessions activated (2026-10-03)

Combined the isolated Hermes session fix with the handed-off Route transport
work on `codex/spartan-route-transport`. Validated native chain bindings now
group dashboard requests across cache-key changes without altering execution
identity, provider keys, headers or account selection. Conversation diagnostics
identify changed keys/instructions independently of cache residency; legacy
turn IDs and historical rows remain unchanged.

Route and the separate voice relay run the tested shared image
`sha256:a9c1e3fe8e9771112364870e8b7fdd767f9d5036fc23c22a5604ab47a8fcd9e2`.
The retained rollback is `spartan-gate-spartan-route:transport-rollback`.
Only those two services were recreated; all seven neighboring container IDs,
images and start times are unchanged. Route remains internal and both target
root filesystems are read-only. Two guarded builds preserved more than 5.62 GB
free, with less than 0.1 GB net task overhead. Only one candidate and one rollback
Route image remain; no Hermes, Browserless or ClawRoute image was built.

Live acceptance used 18 of the authorized maximum 20 Sol medium/low submissions,
including the four original baseline requests and five incomplete WebSocket
requests while diagnosing an upstream timing sideband. The failure reproduced
in a regression: `responsesapi.websocket_timing` arrived after text and before
completion and was incorrectly rejected. Its exact name now uses bounded
sideband handling, matching upstream Codex. Unknown and overflowing events still
fail closed without replay. The private helper now requires terminal completion
and captures stream errors, so partial text cannot count as acceptance.

All four final WS probes completed with exactly `OK`, full usage accounting,
five output tokens and zero reasoning tokens. Medium first/warm requests each
read 10,240 cached tokens; the warm request reused the socket. First-event time
fell from 795 to 380 ms, but first output varied from 2,854 to 3,626 ms. The signed
continuation with changed key/instructions/history remained one session, reported
`continued`, flagged instructions/input changes and correctly recorded zero cache
reads; first output was 2,522 ms. Low first output was 3,838 ms with 10,240 cached
tokens. Tuned HTTP ranged 2,214–18,438 ms; the slow outlier waited 17,124 ms for
headers, and one identical medium replay reported a real zero cache read despite
unchanged account and request fingerprints. These small samples do not establish
an end-to-end speedup. Optimized HTTP remains active; WS/auto remains opt-in.

Validation: 375 Route tests pass in the final image, 12 voice-relay tests pass
against that image, and both dashboard checks pass. Earlier relevant gates passed
59 Hermes plugin tests and 369 script tests with two optional skips. Public and
active private Compose render. Independent combined and sideband reviews found
no blocking issue. Doctor still stops on pre-existing public-path findings.
Private receipts and the durable paid-budget ledger remain retained. The combined
branch is for user testing; no base-branch merge is authorized.

## Rebuilt Hermes activated; plugin test gate has residual failures (2026-10-02)

At the operator's explicit activation request, started only Hermes with
`sg up -d --no-deps hermes`, using rebuilt image
`sha256:2fc42f4a7fe399f4c9f48d0bced0ca5f94f35889419ae5af798deea16e3922e9`.
The container is healthy without restarts; authenticated detailed gateway
health reports `ok`, state `running`, all 12 platform connections active and
all readiness checks passing. The initial healthcheck failed while native
startup and MCP initialization were still in progress; it recovered without
configuration changes or another restart. All eight neighboring service IDs,
images and start times remain unchanged and healthy. The root filesystem is
read-only, both Hermes networks are internal, direct external TCP is denied,
and Route/Camofox/SearXNG health plus proxy connectivity pass from Hermes.
Persistent bind sources and all 39 volumes remain present; about 7.04 GB is
free. The old image-pinned code overrides remain disabled.

Native pytest 9.1.1 and the profile-delegate prerequisite check now pass with
the actual `/opt/hermes/.venv/bin/python` (Python 3.14.7). Fresh installed
discovery loads profile-delegate 1.10.0 without error and registers six tools;
the live API's delegation toolset exposes those tools. Portable tests pass
228/228 with the existing plugin Python 3.13 venv when launched as a normal
subprocess (direct Docker exec made pytest a session leader and invalidated
one test's process-identity assumption).

The native integration partition reports 478 passed, 3 failed, 228 deselected.
Do not claim the plugin's release gate is green. Rechecking the three failures
with temporary test-home fixtures resolves the timeout/default case (the live
plugin default is 1800 seconds, while that test expects the import-time 1200).
Two failures remain: preflight expects `execution_overrides_not_allowed` but
its minimal mocked policy first fails closed with `profile_policy_required`;
the legacy deny-bootstrap test without a frozen approval envelope observes an
approved dangerous command from the native noninteractive guard. A separate
guard-only probe using the production schema-v2 frozen deny envelope correctly
refuses dangerous terminal and execute-code actions and permits the safe
command. No dangerous command was executed. Plugin source, configuration and
profiles were not edited to change these outcomes. No LLM/delegation or
outbound message smoke was invoked. Private activation receipts are under
`private/maintenance/hermes-rebuild-20261002/`.

## Hermes image rebuilt; runtime validation pending (2026-10-02)

At the operator's request, only Hermes was stopped and its container, image
tags and three unused intermediate images were removed. Persistent bind-mount
sources and all Docker volumes were preserved; the other eight running services
retain their container IDs and start times. The pinned upstream base remains
available. The replacement image was built successfully as
`spartan-gate-hermes:latest`, digest
`sha256:2fc42f4a7fe399f4c9f48d0bced0ca5f94f35889419ae5af798deea16e3922e9`.
Hermes remains stopped; no gateway was started or restarted.

The browser-policy and fast-mode private Compose overrides were renamed with
the suffix `.disabled-for-rebuild`; their original code bundles remain intact.
The Dockerfile carries the corresponding patches directly. Do not apply the
old image-pinned bundles to a rebuilt image.

Cleanup used Hermes's `disk-retention-maintenance` guidance: removed the host
npm download cache, cached editor extension downloads and an inactive extension
already marked obsolete. Hermes sessions, databases, checkpoints, profiles,
workspace content and plugin/runtime environments were untouched. Six obsolete
Codex release directories were retired after creating a hash-verified private
archive and restore manifest. The initial build began with 8.59 GB free,
satisfying the operator's explicit task-specific exception to the normal
greater-than-10-GB gate. A disk guard interrupted export twice before the
5-GB floor was breached. An inactive, officially downloadable VS Code Server
installation was then removed, and exact, immutable Codex voice/rg binary
duplicates were hardlinked while preserving every path and file hash.

The final export completed entirely from cache, with a measured minimum of
5.23 GB free. `docker builder prune -a -f` subsequently removed build cache
only, recovering about 1.85 GB according to filesystem availability; Docker's
reported cache total was 3.74 GB and must not be treated as physical recovery.
About 7.08 GB is now free. Docker reports a 6.97-GB image total, compared with
the retained 4.54-GB base. Including export/cache overhead, the measured peak
additional filesystem usage was about 4.24 GB. All figures use decimal GB.
Private receipts, logs and the restore archive are under the ignored
`private/maintenance/hermes-rebuild-20261002/` directory.

The build installed only pytest 9.1.1, iniconfig 2.3.0 and pluggy 1.6.0 for test
tooling. Native Python 3.14 pytest/PyYAML checks, dependency compatibility and
the immutable install-tree audit passed. Host patch validation passed 217
Python cases with 28 source/runtime-dependent skips, plus 18 JavaScript cases;
active private Compose renders. The eight neighboring containers retain their
original IDs, images and start times and remain healthy; all 39 Docker volumes
and the original persistent bind sources remain present. The profile-delegate
native prerequisite check and pending plugin tests still require runtime
validation before gateway activation.

## Hermes fast-mode forwarding activated (2026-10-02)

Completed the interrupted fast-mode implementation and activation. Hermes's
shared resolver now permits eligible explicit `codex/*` and `openai/*` models
only at the internal Spartan Route endpoint with `openai-api`. Static, normal,
auto/cold windows and upstream ultrafast eligibility remain upstream-owned.
Spartan Route preserves `service_tier` through Chat-to-Codex conversion, native
Responses and OpenAI passthrough; retries reuse the same tier-bearing body.

Rebuilt only Spartan Route and retained its previous image under
`spartan-gate-spartan-route:pre-fast-20261002`. Hermes uses the existing image
with an image-pinned, fingerprinted read-only fast-mode overlay alongside the
Camofox policy overlay. Both native init guards passed before reconciliation.
Both services are Docker healthy, read-only and on internal networks; all seven
neighboring service IDs, images and start times are unchanged. Authenticated
gateway health returns HTTP 200 with state `running` and all 12 platforms
connected. Detailed readiness is degraded only by the authorized low-disk
exception (about 469 MiB free); all other readiness checks pass.

A minimal live native Codex request with `service_tier: priority` completed
with HTTP 200, but the upstream reported `service_tier: default`. Forwarding is
verified; priority delivery for this account is not established. There is no
configured OpenAI API key, so OpenAI was verified with mocked passthrough tests.
Installed resolver and mocked CLI turn configuration pass for fast, normal,
auto and cold. The managed Camofox code guard still passes.

Validation: 338 Route tests passed in the rebuilt image, 245 Hermes Python
tests passed against pinned source, 18 JavaScript tests and 11 fast-overlay
tests passed. All public layers and the active private Compose stack render.
The broader script run passed its other cases but hit seven errors in existing
browser-overlay tests because the host is below their 5 GiB gate; those nine
browser-overlay tests pass with simulated sufficient disk space. Two optional
script tests were skipped. Doctor still reports pre-existing public-path
findings. No unrelated pending changes were committed or reverted.

## Managed Camofox policy restored (2026-10-01)

Completed the interrupted browser-policy activation using the existing Hermes
image and an image-pinned, fingerprinted read-only code overlay. Camofox is
mandatory across native profile secret scopes whenever Spartan Gate selects
that mode. Endpoint/authentication are managed before s6 reconciliation;
profile identities, sessions and browser persistence remain separate.

Live activation exposed a missing helper import: the native Hermes launcher
replaces `PYTHONPATH`, while the first implementation stored its helper only
under `bootstrap`. The helper now also resides in the protected project root;
the boot import probe uses the launcher project path and safe-path mode. A
regression reproduces the missing import before the canonical helper is added.
The future image explicitly sets the canonical helper to mode 0644 before its
immutable-install audit. Only Hermes was recreated; no image was rebuilt.

Acceptance passed: Docker is healthy, authenticated detailed gateway health
returns HTTP 200 with status `ok` and state `running`, and all configured
platform connections are active. Default and named-profile built-in browser
tools navigated an inert page with Firefox, kept stable/distinct identities,
and rejected simulated unavailable/unauthorized Camofox without Chromium
fallback. Probe tabs were closed; no model/channel messages were sent and no
browser credential stores were inspected. Original overlay source snapshots
remain available for rollback.

Validation passed 240 Hermes Python tests with pinned sources and no skips,
18 JavaScript tests, and 357 script tests with two optional skips (pristine
Camofox source and the host aiohttp dependency). Seven public and seven overlay
Compose shapes render. Isolated native s6 checks prove valid initialization
continues and invalid authentication exits 1 before reconciliation, supervised
services or the main command. Independent review found no remaining blocker.
Doctor still stops on pre-existing public-path findings. Keep the active
overlay until the next validated Hermes build includes the managed-policy
patch; see `docs/operations.md` for its image guard and rollback contract.

## Hermes upstream main refresh activated (2026-10-01)

The Hermes-only refresh is running image
`sha256:a9e1c04d5c94964effddd6447c07478258d93fcd166340abb8ca26fe5fd58682`,
built from upstream main `e8c97320ac8691d4de92af49f98459f9ef9ddb08`
(OCI digest `sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde`).
It reports Python 3.14.7 and Hermes `git.e8c9732` (2026.9.24). The container
is healthy, its root filesystem is read-only, its application workload runs as
UID 1000, and it is attached only to the Spartan internal and Pocket TTS
internal networks. The other eight running containers retain their pre-refresh
IDs, images, and start times. A direct TCP egress probe was denied, and the
proxy returned HTTP 403 for an unlisted domain.

The image uses 16 build-time patches. The install-tree audit covered 69,939
paths. Final patch tests passed 211 with six source-dependent skips; 18 Hermes
JavaScript, 59 infrastructure/plugin, 10 Talk provisioning, and 74 Alberto
Tools tests also passed. The final Talk compatibility suite passed 221 tests
with two expected failures. All supported Compose variants rendered. Existing
profile databases completed the native 31/FTS3 migration; the upstream empty
FTS migration bug received a narrow regression-tested patch. No database
backup or VACUUM was performed, per the operator's instruction. Synced plugin
versions were not changed.

After the final Hermes-only reload, Docker health and the gateway API health
pass. Talk modules import from their read-only mount; Toolaria's UID 1000 write
check passes without permission warnings. The other eight container IDs remain
unchanged. The authorized private credential migration resolved 24 channel
references in eleven mode-0600 profile env files and generated ten independent
named `API_SERVER_KEY` values. All ten configured Telegram/Discord adapters
report `connected` through authenticated detailed health. Each named API
returns HTTP 200 with its own key and HTTP 401 with the default key; no channel
placeholders remain. The future-profile helper now preflights dedicated channel
sources and generates a distinct API key for gateway mode; its seven focused
tests and Bash syntax check pass. No credential values were printed by the
migration or verification.

After acceptance, only the exact unused old Hermes image
`sha256:cc5f4d2e2e2983f2e17a4dd4126836a6601c803cb9af805d56d0aeefada63549`
and task-generated candidate image
`sha256:0001501fdc331c92079499a05381a6bc10f4b02f006d2ed6ebb5b0f882fb1431`
were deleted; no broad image prune ran. Hermes remains healthy and disk space
increased to about 6.9 GB. `scripts/doctor.sh` stopped on pre-existing public
path findings;
private permission findings on the backups directory and executable Caddy
entrypoint were not changed as part of this refresh.

## Camofox 1.15 active — readiness and recovery accepted (2026-09-12)

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

The accepted adapter claims temporary-addon installation once per launch
profile and withholds browser publication until a page-world UBO probe passes.
Launch attempts use one deadline, dispose late resources, and degrade on
unproven cleanup. VNC starts only after acceptance, validates exact browser/Xvfb
PID and start-time identities, and is reaped before session teardown.

Independent closure review found no P0/P1 production blocker. Cold start,
protected/analytics/malformed modes, open-WebSocket generation 1→2 recovery,
immediate post-recovery protection and production health pass. Only Camofox was
rebuilt/recreated; neighboring services were unchanged. Superseded Camofox
images were pruned after acceptance while immutable rebuild coordinates remain.
See `docs/camofox-refresh.md`.

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

The Hermes refresh is activated. Complete the planned 24-hour log review
without sending synthetic model or channel traffic.

## Residual Risks

- Publish the local hermes-lcm provider-replay commit upstream when GitHub
  authentication and a writable fork are available.
- Codex Sol capacity can currently return `server_is_overloaded`; the bounded
  pre-commit retry does not replay committed streams.
- The OpenAI API-key Responses branch is regression-tested but was not charged
  against a live direct API key in this subscription-first private runtime.
