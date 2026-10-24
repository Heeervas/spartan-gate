# Current State

## SDK accumulator follow-up — source candidate, review pending (2026-10-24)

The operator authorized a narrow repair for the confirmed SDK 7.19
`responses.stream()` failure below. A synthetic Route/upstream regression
reproduces its unsupported `response.metadata` accumulator error while collected
JSON and create-iterator controls succeed. The candidate excludes only that
proprietary Codex public-SSE event after raw telemetry observation; no SDK/Pi
patch, dependency upgrade or replay-policy change is required. Independent
source review and parent acceptance remain pending. No new deployment, active
Pi install or real inference is authorized by this source task. The prior live
acceptance and intermittent-incident limitations below remain historical facts.

## Standard Responses deployed; bounded live acceptance recorded (2026-10-24)

Parent approved the independently reviewed design for standard-client stateless
Responses conversations. The source candidate adds server-owned opaque Codex
replay receipts with conservative issuing-account scope, while retaining signed
legacy raw Hermes/Pi traffic. New Pi sessions avoid the binding RPC; old raw
histories use lazy cached bindings without rewriting sessions. Local realm locks,
zero-reasoning copied item-ID removal and append-failure retry safety are covered.
Independent source review found two local-state blockers in the first frozen
candidate: rejected legacy migration could persist the wrong realm, and extension
reload could trust a phantom lock after append failure. The revised source uses
provisional concurrency reservations with durable commit only after legacy
validation, plus process-local manager-owned poison state surviving extension
replacement. Correct-realm reopen and fresh-process disk recovery controls are
included. Independent revision-two source review passed and parent accepted the
frozen 27-file manifest `2cd8170ed5d3eb32f15025e549686c0c21b7014f5d75c0ffb2ec48d3588e70aa`.

Source tests use an unmodified OpenAI SDK, actual loopback Route/native selection
and synthetic HTTP upstream, with tool/encrypted continuation and fresh client/
Route process resume. Native Pi 1.3 and 1.4 and the unchanged Hermes lineage
consumer are exercised. Source commit `cc460db` is locally integrated; exact
reviewed bytes were verified before build. Only Route was rebuilt/recreated.
The healthy image's 26 module hashes match source; protected inference denies
unauthenticated requests. All nine neighboring container identities/images/start
times are unchanged. The four reviewed Pi files are installed with exact backups,
matching hashes, preserved settings and a fresh-process native loader check.

The unmodified SDK `responses.create({stream:true})` journey passed a streamed
inert function call, encrypted continuation and fresh-process resume. Native Pi
named new/resumed sessions and legacy raw named migration/resume also passed.
The finite live canary used eight of twelve physical upstream attempts including
one client-side failure, with usage recorded on all eight. SDK 7.19's higher-level
`responses.stream()` accumulator rejected an upstream `response.metadata` event;
this convenience API remains a concrete limitation, not an all-SDK compatibility
claim. No production source/SDK patch was made for it. See the tracked review
for exact image and receipt references. No push, session rewrite or database
migration/deletion occurred. Existing Pi processes require a full restart to
adopt changed helpers; no current parent was reloaded or terminated.

Receipt integrity assumes uncompromised holders of the existing root secret,
which is also held by Hermes; this is not hostile-tenant isolation. Account/secret
loss and old-server rollback have explicit visible-history recovery limits.
The separate intermittent incident remains undiagnosed.

## Hermes instruction debloat activated (2026-10-23)

Source bf6f202 adds opt-in native required-skill selection before prompt
composition, with policy-aware caching and unchanged native controls. The
reviewed private instruction candidate was checkpointed in Git before applying
MASTER, role SOUL/AGENTS, task-selected default references and the canonical
shared entry. Four profile configs gained only the selection-policy field.
Two Sol high review rounds plus affected re-review resolved a quoted-content
middleware defect before deployment; the middleware proposal was withdrawn.

The canonical suite passes 321 tests with one host-root-only skip; all nine
mandatory native suites pass 86 tests with zero skips. TOP retains existing
approved exceptions. The rebuilt image passed a no-network/nonroot/read-only
canary before Hermes alone was recreated. API returns 200 and Docker health is
healthy; installed bytes and four native profile loading checks pass. Other
service identities, required gates, profile controls and internal networking
are preserved. Exact scoped rollback files and previous image are retained.
Actual task latency/completion gains remain unmeasured. See the applied note
under apps/hermes-agent/runtime-docs/hermes_notes/instruction_debloat_23102026.md.

## Named Pi subagent binding correction (2026-10-23)

Source fix `adc8e6b` is installed and accepted. mjakl named child IDs are valid Pi
IDs but are not UUIDs; the first native adapter rejected them before binding.
A shared deterministic UUIDv8 adapter now supplies the same canonical identity
to Route signing and usage while preserving native IDs, session files and cache
keys. Independent review passed, all Pi suites pass, and TOP passes with the
previous exceptions unchanged.

The actual installed subagent tool reproduced the failure, then successfully
retried and resumed that same named session from the same parent process after
installation. Independent named and ephemeral children also completed using the
reported model. Both persisted named ledgers have complete observed usage.
Only three Pi helper modules changed; Route needed no restart. Existing parent
processes can retry the same child calls. See the follow-up in
`docs/reviews/pi-native-responses-20261023.md`. No push occurred.

## Pi native Responses activated (2026-10-23)

The requested Pi/Route fix is implemented in source commit `b4cc3ba`, installed
and live. Pi delegates supported models to its built-in Responses transport;
Route supplies authenticated, Pi-specific signed reasoning bindings and native
attempt accounting. Dynamic catalog metadata preserves model-specific efforts
and keeps mixed-realm auto routing on its supported protocol.

Independent review passed after fixing unsupported implicit `none` effort.
Route passed 483 Python and 41 JavaScript tests; Pi passed four Python tests,
all three JavaScript test files and ten focused native provider checks. TOP
assurance passed with the existing five exceptions and two independently
approved native-attempt limits expiring 2026-11-20; baseline is unchanged.

The healthy deployed image and installed provider/helpers match reviewed source.
A six-call real gateway canary passed streaming, a read-tool turn, continuation,
process restart and encrypted reasoning replay using one signed chain. All six
attempts have usage and none are incomplete. Only Route was recreated; all other
stack container identities remain unchanged. Previous image and exact installed
bytes are retained for rollback. Existing Pi processes need a restart. See
`docs/reviews/pi-native-responses-20261023.md`. No push occurred.

## Edge query-string regression repair (2026-10-22)

The operator authorized a security-reviewed fix and edge rebuild/activation.
Caddy auth subrequests preserve the application's query; the new verifier
previously included it in the audience comparison and returned 404 before the
app could respond. Match the literal path before the first `?`, without URL
decoding or path normalization. Service-key authentication, exact endpoints,
cookie signatures, audience separation and expiry remain unchanged.

Source commit `839ab6b` passed independent security/code review, seven verifier
and native Caddy tests, 26 Caddy/migration/port contracts, Compose validation and
TOP assurance with the five existing exceptions. Regression checks fail on the
original verifier and pass on the fix; exact repeated/encoded query content
reaches the native backend unchanged.

The operator-authorized scoped rebuild and activation are complete. The new
image preserves every deployed verifier base layer and runtime setting; its
baked source hash matches the reviewed source. Isolated image acceptance passed
before recreation. The reported app query now returns 200 via Basic bootstrap
and signed-cookie requests, matching the no-query content. Seven live rejection
cases return 401: missing credentials, spoofed verifier header, invalid,
duplicate, expired, future and wrong-audience cookies. All ten services are
healthy; the other nine identities, start times, configs and environment hashes
are unchanged. Stock Caddy validated successfully and needed no restart/rebuild.
The previous verifier image is retained for rollback. Private operational
receipts are under `private/maintenance/edge-query-20261022/`. No push occurred.

## Rebuild activated; bounded live checks passed (2026-10-22)

The operator executed the prepared build and ordered activation. Activation
stopped at the verifier because an unquoted YAML flow scalar split tmpfs options
into separate mounts. A focused regression reproduced the defect across seven
edge-enabled Compose shapes; the quoted scalar passed independent review.
Private input fingerprints were explicitly rebaselined for only the reviewed
Compose/test delta. Real Docker and kernel checks confirm one restricted tmpfs.

Recovery resumed the verifier, then Caddy and Hermes without another build or
recreating already-updated services. All ten containers are healthy. Hermes
initially exceeded its startup-health allowance but became healthy without retry
or restart; this remains a healthcheck timing caveat, not a hidden clean start.
Candidate image IDs match the isolated post-build receipts. Deployed Route uses
aiohttp 3.14.4. Both signed-session audiences passed bounded mint/verify and
missing/expired/future/wrong-audience/malformed/duplicate-cookie denial checks.
Hermes health/import/dependency admission and Route auth denial passed.

No persistent data, keys or volumes were deleted or reset. The pre-existing
watchdog-volume ownership warning was not repaired by deletion. Private recovery
receipts are under `private/maintenance/rebuild-ready-20261022/`.
Browser/vault/full-tool workflows, WebSocket success, interactive login/TLS trust
and live inference/telemetry remain outside these bounded acceptance checks.
The preparation-only descriptions below are historical checkpoints.

## Actual-stack rebuild preparation (2026-10-22)

The operator authorized completing private installation preparation, not image
builds or service activation. The earlier source closeout was subsequently
published to the actual default branch `master` at `2a338eb`; its original
no-push boundary below is historical, not the current publication record.

Private preparation now removes retired service references and baked-code
mounts, preserves persistent data and existing keys, wires the isolated edge
verifier, and migrates signed listeners with native validation against actual
TLS files. Candidate image tags retain old images for rollback. No service
was recreated. Private backups, selected-stack checks and executable runbook
are retained under `private/maintenance/rebuild-ready-20261022/`; do not publish
that directory. The frozen production-input check and independent Luna review
both PASS: the selected installation is ready for separately authorized builds.
Builds/canaries and live acceptance remain unexecuted.

Remote CI at `2a338eb` exposed a clean-checkout test dependency on an ignored
private directory. The fixture now retains read-only mount assertions without
requiring operator state. Subsequent clean-runner failures were corrected by
provisioning pinned native Caddy in both CI lanes and isolating test stack/UID
fixtures. All 492 script tests pass in a clean checkout and under runner UID
1001 (one existing skip); production ownership checks remain unchanged.
Route now pins aiohttp 3.14.4; dependency regressions
fail on 3.14.3 and pass on 3.14.4. Updated Route evidence: 476 Python and 41 JS;
mandatory native Hermes 75 and Camofox 53 pass without skips. TOP assurance
retains the existing bounded exceptions. No deployed-version claim is made.

Remote dependency alerts must be distinguished from current source matches:
retired manifest alerts and already-fixed version ranges are not a reason to
restore the deleted application. Final remote CI status is checked separately
from local and native validation. Reader's residual cancellation investigation
remains outside this preparation; it is not silently declared solved.

## Source accepted — audit remediation and legacy retirement (2026-10-22)

Authority: **complete-target for source implementation and validated local
commits**, authorized by the operator. Browserless and legacy ClawRoute may be
retired; private profiles/databases and surviving Spartan Route configuration
must be preserved. No deployment, builds, production mutation, data deletion or
push is authorized by this task.

Durable plan and recovery entry: `docs/reviews/2026-10-22-remediation-plan.md`.
Read its execution log, `.agents/validation.md` and Git status before resuming.
Starting candidate is `3b0262b`; inherited todo-list and three runtime notes stay
untouched. Source implementation and independent reviews are complete; local
commit closeout is recorded in the plan. F01–F15 are addressed in source, with
retired-shape obligations revalidated against surviving L0–L4 contracts. The
signed edge verifier and private override migration are NOT activated.

Consolidated local checks pass: Route 474 Python/41 JS (including benchmark),
Hermes 310 Python with one root-authority skip plus 75 mandatory native tests
without skips, Reader 26, Pi 4 Python/23 JS, Camofox 28 Python/53 JS, native
Caddy 5 and scripts 492 including final ports regression. Native Camofox repeats
its 53 JS tests; do not add overlapping counts. Retired Hermes JavaScript files
have no surviving suite: zero tests is not claimed as coverage. TOP assurance
passes with five pre-existing bounded exceptions; baseline is unchanged.

Sol/medium builders and Luna/high independent reviewers accepted their final
scopes, including the integration and security corrections. Remote GitHub CI,
container builds, private migration and live/browser acceptance remain separate
pending operations. Reader outbound DNS/fetch and executor-thread shutdown still
lack proven global cancellation guarantees. File-backed synthetic comparison
showed matching outputs, not a speed gain. No runtime data was deleted.

## Optional Hermes quality CLIs deployed (2026-10-21)

Qlty 0.659.0 and Desloppify 1.0 are baked into the rebuilt, healthy Hermes
container. Actual native Pi Builder/Reviewer handled implementation, independent
findings, correction and source re-review. Image and live runtime-user canaries
passed canonical commands, import isolation, Qlty analysis, Bandit detection and
review preparation. Only Hermes changed identity during this cutover; existing
profile controls, mounts, networks, limits and browser variant were preserved.

Desloppify's slow duplication coverage requires an optional analyzer not installed
here. Bounded `--skip-slow` scans retain Python security checks and report the
omission. See the runtime note `quality_tools_21102026.md` and
`docs/reviews/hermes-quality-cli-20261021.md`. No automatic quality gate, skill
enrollment, trusted subjective score import or publication was added.

## Spartan Route sessions regression corrected and deployed (2026-10-20)

After the dashboard deployment, the exact browser sessions query took 13.540
seconds, exceeding its five-second client deadline. Active logs had no errors,
and health remained responsive. Investigation confirmed repeated scans of each
selected session and a non-covering metadata scan over wide context rows.
The earlier synthetic benchmark did not represent this production cost.

The fix adds a covering selection/filter index, reads complete selected turns
once per session within one snapshot, avoids immediate timeout replay and
publishes sessions independently of summary reads. Real metadata projected into
an indexed RAM table completed the candidate query in 0.449 seconds; this is
mechanism validation, not a deployed latency guarantee. The final integrated
441 Python and 41 JavaScript tests passed, with independent review.

Source fixes are locally committed and deployed after the operator approved
a limited low-disk exception. An offline patch image reused the installed
runtime and changed only telemetry and dashboard JavaScript; only Route was
recreated, with rollback retained. The exact sessions query now returns in
0.35–0.50 seconds. Live Chrome acceptance passed sessions, summary, lazy
details, retained refresh state and mobile layout. Filter and authentication
checks passed; neighboring containers are unchanged and Route is healthy.
Final free space is about 848 MiB, so the normal 5 GB floor still needs
restoration before routine operations.

The reported inference slowdown has not been attributed to an inference-path
defect. A bounded before/after comparison
of 200 recent requests per period found median routing preparation at 36/32 ms,
while upstream first-output timing increased with a heavier request/output mix.
The measured delay increase occurs after dispatch; this is observational
evidence, not a controlled comparison or proof of provider fault.

## Spartan Route dashboard source completed (2026-10-20)

Reviewed existing operator changes were committed with user approval before
parallel GPT-6.1 Sol medium implementation and independent review. Routing,
Codex and Config now share a responsive layout, preserving percentage rings,
live counters, configuration drafts, expanded details and keyboard focus.
Refresh generations, serialized quota operations and guarded persistence/cache
recovery prevent retired requests from overwriting newer state. Assets use
authenticated private revalidation; request details load on demand.

Telemetry queries materialize selected complete turns, batch account metadata,
use separate read-only connections for heavy file-backed reads and incrementally
process new requests for today's summary. On a synthetic 60,000-request fixture,
five-run median live queries improved from 900.53 to 247.09 ms, filtered queries
from 607.47 to 158.30 ms and summaries after an append from 614.17 to 8.57 ms.
Live payload hashes matched the baseline. Cold summaries still scan the day;
retained-history selection and exact percentile work remain dataset dependent.
These measurements are comparative, not production latency guarantees.

The integrated suite passed 438 Python and 37 JavaScript tests. Chrome checked
all three screens at widths 320, 390, 768, 1024 and 1440, plus lazy detail loading,
preserved state, quota actions, countdowns and unsaved configuration. Independent
review regressions cover retired refresh writes/cache recovery, concurrent
controls and hidden-page polling. Repository doctor still fails on pre-existing
personal host paths in public files.

The requested redesign skill is installed and applied. Source work and tests
were authorized under a temporary low-disk exception; no image builds, runtime
restart or push occurred. Deployment and live acceptance remain pending until
adequate disk capacity is available. See `docs/spartan-route.md` and the runtime
note `route_dashboard_performance_20102026.md` for operator behavior.

## Native Hermes vault on Camofox deployed (2026-10-09)

Native vault tools now use the existing Camofox tab and session, with a dedicated
authenticated secret-evaluation route. The reviewed boundary denies tracing,
cross-session access, the operator login group and OTP origin mismatches;
timeouts return fixed errors and retire indeterminate tabs. Native backends,
secure prompts and payment consent remain in place. Text results are redacted;
the operator explicitly accepted native screenshot/vision confidentiality limits.
See `docs/camofox-vault.md` for the workflow and recovery contract.

Parallel Sol 6.1 medium implementation worktrees and independent adversarial
review preceded integration at `12c3d0e`. Hermes Python validation ran 318 tests
with one optional host-version skip, Hermes JavaScript passed 18, Camofox Python
passed 26, and Camofox JavaScript passed 46 with two optional skips. Separate
installed-Express and pinned-source patch-chain checks exercised the skipped
integration surfaces. Both Compose renders and whitespace checks passed.
The existing doctor failure on personal host paths in unrelated public files
remains outside this change.

The base, final Hermes and Camofox images were built sequentially. Final Hermes
is `sha256:7f2184310662b40d2e6d5ad66732e6bd91ca74c0ab8c611acc668bbdf5565cbd`;
Camofox is `sha256:d322a13f0c1ba21ff9f91f7b84732ce216726f601565bd63f918815202850a39`.
The real-image offline canary passed retained tooling and native/resident
Chromium denial. Live synthetic vault acceptance passed login, payment, address,
TOTP, exact-origin denial, text redaction and invalid-auth denial, using a
temporary encrypted vault and an exclusive scratch browser session.

Both services are healthy without restarts, and authenticated health/capability
GETs passed for every configured profile. The API needed approximately 176
seconds for cold startup; initial health failures preceded its listener.
All configured adapters subsequently connected with fresh gateway state and
no reported adapter error codes.
Network memberships and neighboring container identities are unchanged.
The dependency-admission code overlay was retired because its reviewed changes
are baked into the new Hermes image; persistent mounts and credentials remain.

Old images were removed with operator approval before rebuilding. Capacity
guards interrupted image operations; one cancelled Docker client did not stop
runtime extraction and free space briefly fell below the normal 5-GB floor.
Only precisely identified task build cache and generated temporary inputs were
removed to recover capacity. The operator subsequently authorized a temporary
1.5-GB floor during image work. Final free space exceeds 6 GB. The intermediate
Hermes base was removed after use; the verified upstream source bundle and
preparation manifest remain available for rebuilding. Private receipts retain
build, review and acceptance evidence; no browser/vault volumes were deleted.

## Managed proxies and image-only admission activated (2026-10-08)

Only Hermes was recreated with its existing immutable image. Six proxy/bypass
keys now use the native administrator-managed dotenv, without sharing the full
private Compose env or changing profile credentials. Native terminal filtering
preserved them across eleven profiles; default and work reached Google's public
API over HTTPS. All twenty-three configuration/env/PM-facts hashes and all
neighboring container identities remained unchanged.
Direct external TCP remained blocked and an unlisted proxy destination returned
403; no egress whitelist or network attachment was widened.

The read-only image-pinned adaptation freshly validates the baked interpreter,
features and enabled plugin dependency closure before omitting PM's writable
generation fingerprint verdict. It does not rewrite facts or claim full lock
reproducibility; raw PM checks and other tool diagnostics remain unchanged.
Failed admission exits 78 through the native CLI/gateway best-effort handlers.
The temporary overlay is L4-only and blocks Hermes builds or use in other tiers.

Astra medium reviewed the plan and implementation; its blockers were corrected.
Native Python 3.14 ran fifteen admission tests without skips. Native CLI/gateway
failure tests and real readonly-mount authority checks passed. The Hermes host
suite passed 306 tests with 35 optional/source-dependent skips using temporary
dotenv/packaging modules from the image. Fifty-six alias tests passed. Seven
browser-overlay fixture errors caused by the physical 5-GiB prerequisite passed
when that fixture prerequisite was simulated; production disk guards are intact.

Hermes completed cold startup with healthy API, zero restarts and twelve
connected adapters, including five Discord adapters. The reported Discord
nonresponse was confirmed as a mobile-app issue. Catalog-only remediation is
prepared and tested but was not applied; remote command approval is unchanged.
No upstream refresh, image build, session reset or browser-profile operation ran.
See `docs/operations.md` for overlay removal/rollback and `docs/patches.md` for
the dated upstream comparison. An extra manual PM-activation check was rejected
by automatic review; explicit read-only image validation passed instead.
`scripts/doctor.sh` remains blocked by pre-existing personal host paths in
public files; none of its reported files belongs to this implementation's diff.

## Camofox-only Hermes deployed and accepted (2026-10-07)

The reviewed Camofox-only image is deployed and healthy, including the recent
log-remediation, voice-scope and Gog patches. Runtime source revision is
`34dd7391a9615d9bed0864e0b07da83e4a699091`; final image ID is
`sha256:523dd6f40aa5445033155ebf0c491a5c686ad044dda94a56de3d045f533dec47`.
This supersedes the preparation-only status below. Backed-up, hash-verified
profile/plugin migrations and native PM image-fallback feature recovery were
applied. Persistent application databases were not manually modified or deleted.

The image's compressed content plus unpacked layers decreased from
6,973,663,861 to 6,326,309,967 bytes: 647,353,894 bytes (9.28%) saved. This is the
measured deployed-image difference, not a controlled Chromium-only comparison:
export used Zstandard level 15, and unlocked Python dependencies changed
fonttools 4.65 to 4.66, networkx 3.6.1 to 3.7, and pymdown-extensions 12.0.1 to
12.1. The APT inventory is unchanged, no Python distributions were removed,
and the retained PM toolbox differs only by removal of Chromium.

The opt-in image-dependencies-only startup mode validates the baked environment
before skipping automatic dependency sync and collection. It refuses selected
generations or unsatisfied plugin requirements. This prevents startup from
rebuilding an unnecessary persistent environment under constrained storage.
Managed Camofox remains required; native Chromium and Chromium HTML-to-PDF are
unsupported. The compiler, document/PDF, Python, Node and FFmpeg toolbox remains.

Validation passed 298 Hermes Python tests, 399 script tests (two optional skips),
and 18 JavaScript tests. Actual-image checks cover native and resident PM browser
rejection, startup denial, retained tools and pdflatex output. Authenticated
health and capability checks passed for every configured profile, and all
configured adapters connected. A synthetic Camofox navigation, snapshot and
screenshot passed. Existing mounts, network isolation, security options and
neighboring container identities were preserved. Private receipts retain the
inventories, backups and acceptance evidence.

The upstream source bundle and preparation manifest remain available for a
future rebuild; the transient upstream build image was removed during capacity
recovery and must be rebuilt before another downstream build. Existing Blender
MCP executable repair and live voice-provider/audio acceptance remain separate
follow-ups; this deployment does not establish that those issues are resolved.

## Hermes log repairs prepared; runtime activation pending (2026-10-07)

Prepared and independently reviewed by an Astra-high adversarial agent:
image feature inventory, raw API delegation ownership across the entire
claim/persist/settlement lifecycle, TypeScript companion SDK repair, and a
cron thread diagnostic that respects intentional destinations. Prepared
owner-preserving profile/plugin migration for scoped SearXNG, node-search,
Talk transcript worker context, Toolaria isolation and safe pass-through,
absent legacy plugin names, and dormant retired reset timers. OOM tuning and
automatic resets are excluded; explicit tool allowlists and cron routes stay
intact.

No live configuration, plugin source, PM facts, checkpoint lock, or service
was changed by this remediation. The dry-run migration covers 26 files;
native PM fallback preflight measured 25 image extras and installed nothing.
Hermes Python validation ran 265 tests with 28 skips; 27 focused script tests
passed. The broader script suite encountered seven errors in existing browser
overlay tests because their real disk-capacity guard rejected this host.
Compose rendering and whitespace validation passed.

Preparation was initially gated by AGENTS.md's 5 GB free-space requirement;
free space fell to approximately 300 MB without a runtime exception. A separate
integration session now coordinates the user-authorized definitive Camofox
image rebuild and activation. This branch performs no runtime operations.
Keep the diagnosed old checkpoint index lock intact until native-lock/no-writer
recovery can run; restore Blender through a persistent installation and repair
only the TypeScript SDK after the capacity gate. Core patches are wired into
the next image build, but no build was attempted. Existing-image activation
still needs guarded code overlays, plugin import checks, cold native launcher
selection, adapter readiness, and authenticated health acceptance.

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
