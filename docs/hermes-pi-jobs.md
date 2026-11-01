# Native Pi jobs from Hermes

Operator integration recipe; native migration settings/binds require deliberate
activation. Existing directory/file binds can expose source edits to future
launches before recreation; source preparation is not guaranteed runtime-inert. Hermes owns
intake, assignment permission, native process control and acceptance; Pi owns the
coding work inside the container. No daemon, queue, host bridge or new RPC client
is needed. Verify actual container Node compatibility, resources and lifecycle
separately; host Node 22 smoke checks are not a substitute. Instance-specific
activation and acceptance evidence belongs in the private operational receipt.

## Wiring and activation gates

Review `infra/pi/container/{pi,settings.json,route-config.example.json,
compose.example.yml,env.example}`. The Compose file is an **overlay proposal**,
not a standalone deployment. The controller merges only those exact binds into
the approved private topology and substitutes nonsecret installation paths.
Every new bind refuses missing host sources (`create_host_path: false`). Keep
existing writable owner state and `/opt/shared-skills` mounts unchanged; no new
network, broad project/home, auth/session store or Docker socket mounts. Mount
only the approved coding worktree already available within the boundary.

The package mount must contain the complete installed Pi 1.4.0 tree (dependencies,
assets and `dist/bundle/rpc-entry.js` included), not a host executable symlink.
The launcher execs **container** `node /opt/pi-package/dist/bundle/cli.js`.
The prepared migration selects **nicobailon/pi-subagents v0.84.0**, pinned to Git
`0398b9e8d6f03dc6054dbf13dfe851c7a20bff3a`, at `/opt/pi-subagents-nicobailon`.
Keep the complete checkout and its lockfile-installed production dependencies,
not an entry-file-only mount. It supports Pi >=1.2.0; its pinned Undici requires
Node >=22.19.0. The selected npm Pi 1.4 package supplies SDK peers to detached
runners; do not install a second physical Pi SDK in the extension checkout.
The launcher sets the explicit container Pi package root for child discovery.
Preserve the old pi-subagent-local checkout, named conversations and sessions for
rollback; no conversion of its session handles is supported.

Mount the entire selected installed Route extension root read-only at
`/opt/pi-route-extensions`. This candidate selects the coherent installed bundle,
not an implicit upgrade: provider entry SHA-256
`6733afc80e13c6834b41bb111fa98728d3825e1b783c1acd4f48dc281352d7a1`;
provider/cache match versioned source. Installed usage is older and has no
`correlationHeaders` import/call; its lack of `correlation.mjs` is coherent, not a
missing-file repair request. Preserve its matching helpers. Freeze/read back the
whole chosen bundle before activation; do not combine current source usage with
installed helpers. Explicit settings load only its provider/cache/usage entries
the small `subagents.ts` native entry, and the reviewed `pi-codex-fast` entry. It sets `PI_SUBAGENTS_TEMP_ROOT`
below the selected native agent directory **before** importing pinned upstream;
container package selection is `/opt/pi-subagents-nicobailon`, while host default
selection is its native local-share checkout. Do not load upstream a second time
through packages/extensions. No other package is enrolled, including the disabled
competing subagent. Preserve that native disable. Built-in extension enrollment
is also explicitly disabled in this initial configuration.

Only `builder.md`, `reviewer.md`, `general-purpose.md` are bound read-only, not the
whole canonical role directory. Their native discovery location is
`getAgentDir()/agents`. Skills are nine individually named canonical packages in
settings, not `/opt/shared-skills` as a discovery root; they remain **writable**.
Builder boundary safeguards are routed by the integrated `builder-engineering-rules`
base to `references/builder-boundary-safeguards.md`; no separate `-ext` enrollment.
Native user/project discovery and project settings can add resources: inspect the
actual trusted worktree and effective resources before use. These lists are not
an OS sandbox or a server-side model authorization policy.

After separate provisioning authority, the controller must discover the existing
owner's actual `HERMES_HOME` and workload UID, reconcile jobs/foreign state, back up
owned bytes/links outside discovery, then create these native resources as that UID:

```text
$HERMES_HOME/pi/agent/settings.json       reviewed materialized native settings
$HERMES_HOME/pi/agent/extensions/subagent/config.json reviewed native subagent config
$HERMES_HOME/pi/agent/agents/builder.md   -> /opt/pi-roles/builder.md
$HERMES_HOME/pi/agent/agents/reviewer.md  -> /opt/pi-roles/reviewer.md
$HERMES_HOME/pi/agent/agents/general-purpose.md -> /opt/pi-roles/general-purpose.md
$HERMES_HOME/pi/sessions/                private native sessions
$HERMES_HOME/pi/spartan-route/config.json private reviewed Route configuration
```

Do not overwrite existing settings/links or provision every Hermes profile. Keep
state directories private (0700) and config/credential files 0600, readable by the
actual workload UID. Launcher defaults `PI_CODING_AGENT_DIR`,
`PI_CODING_AGENT_SESSION_DIR`, `PI_SPARTAN_ROUTE_CONFIG` under the explicit
`HERMES_HOME`; deliberate nonempty overrides remain supported. Settings and links
are provisioning, not launcher side effects. No host auth or session mount.

### Reuse an already activated Pi state

When explicitly authorized to share Pi configuration, credentials and sessions,
another existing Hermes owner can use a single `$HERMES_HOME/pi` symlink to the
already activated Pi state directory. Verify the destination and workload UID,
refuse an occupied link path, and preserve the owner's actual `HERMES_HOME`.
This reuses the settings, roles, Route config and optional direct-HTTPS binding;
it does not copy packages or require container recreation, a rebuild or new mounts.
Run from the authorized repository with unique task session IDs; reopen only the
recorded session file, never an ambiguous `--continue`. This mode provides **no
profile isolation of Pi state**. To withdraw it, establish cessation of the owner's
jobs and remove only the owned symlink, not the shared destination or sessions.

Replace the example's `.invalid` endpoint with an **already-approved,
credential-free HTTPS** Route `/v1` URL reachable under unchanged proxy/DNS/TLS
rules. `http://spartan-route:18790/v1` is unsupported. The existing gateway parser
reads only `CADDY_AUTH_USER` and `CADDY_AUTH_PASSWORD` from the authorized env file
at `/run/pi-route/credentials.env`; it never sources it. No credentials in URLs,
CLI arguments or public files; do not copy laptop auth. Confirm credential scope,
catalog/model metadata and Sol/Luna permission separately. Initial parent settings select
`spartan-route/codex/gpt-6.1-sol-282kcontext`; child model scope permits the
operator-approved GPT-6 families, with exceptional-only Astra still governed by
instructions. Model and reasoning are chosen independently per child and encoded
as `model: "provider/id:high"`. Cached synthetic metadata in
source tests is not evidence of the live catalog, auth or cache hits.

### Optional Pi-only direct HTTPS origin

The native Pi CLI configures Undici's `EnvHttpProxyAgent` for global fetch. A
successful Python or Node `https` catalog probe does not prove that path works:
inherited `HTTPS_PROXY` can route native Pi through the proxy even when the
approved internal HTTPS origin is directly reachable. Do not unset proxies or
change Route, TLS, networks or profile-wide exclusions to work around this.

Only with explicit operator approval, provision the existing owner's nonsecret
`$HERMES_HOME/pi/spartan-route/direct-https-origin` file containing one approved
HTTPS DNS origin, for example `https://route.example.invalid:18790` (a trailing
`/` and one final newline are allowed). No credentials, path, query, fragment,
wildcard, IP address or other whitespace is accepted. The launcher validates it
against the selected `PI_SPARTAN_ROUTE_CONFIG` credential-free HTTPS `baseUrl`:
scheme, DNS hostname and effective port must match; the config may retain `/v1`.
Malformed, unreadable or mismatched inputs fail before Node with a sanitized error.

A matching opt-in appends only `hostname:port` to this Pi process's `NO_PROXY` and
`no_proxy`, retaining each existing list. If one case is unset, its baseline is
the other case's list so lowercase preference cannot discard inherited entries.
All HTTP(S) proxy variables, Pi defaults/explicit overrides and CLI arguments are
preserved. With no binding file, behavior is unchanged: no Python/config read and
no proxy-list modification. Python3 is already required by the existing gateway.

**Undici's native matching also includes subdomains**, even for a literal
`hostname:port` entry. This is not an exact-host security filter. Approval must
account for that suffix behavior within the unchanged internal-network/DNS/hosts
boundary; no wildcard or global bypass is introduced by this launcher. Validate
actual native SDK/global-fetch behavior separately without inference before the
next authorized job. Source fake-Node checks do not establish that live result.
Removal of the owned opt-in file restores inherited behavior for future launches;
running processes keep their existing environment. The controller owns any file-
bind inode readback/activation concern; this source repair performs no live edits.

New mounts require separately authorized Hermes **container recreation** through
the approved `sg` private selection, never an image rebuild. Coordinate all affected
gateways/jobs. Then verify workload UID, version, dependency/RPC imports, resources,
one provider registration, HTTPS/auth/catalog and unchanged neighbors before a
separately authorized live assignment. Stop if any gate fails; do not install,
upgrade or weaken networking to make the candidate run.

## Common baseline and preparation (option B)

`infra/pi/common-baseline.json` is the sole maintained, allowlisted **nonsecret**
behavior policy. `prepare_parity.py` merges it into the two selected native JSON
surfaces, preserving unrelated fields/packages and native resource exclusions.
The checked-in container `settings.json` and `subagent-config.json` are generated
examples, not separate policy owners. Host-only navigator/web/timing/plan/stop
resources and legitimate extra specialists stay local; parity does not enroll them
in Hermes. Shared roles explicitly opt into project/global context and lazy skill
metadata under the new engine; their no-further-delegation/tool boundaries remain.

Prepare each side from **its own** settings/config, never from the other side's
home. A bindings JSON supplies exactly `home`, `agentDir`, `routeRoot`,
`containerRoot`, `skillsRoot`, `fastRoot`, all absolute nonsecret paths. Container examples
use `/opt/data/pi/agent`, `/opt/pi-route-extensions`, `/opt/pi-container`, and
`/opt/shared-skills`, with `fastRoot: /opt/pi-codex-fast`; verify its actual OS home independently. Host roots resolve
to its selected native paths. The helper neither reads nor copies credentials,
auth, models, trust, instructions or sessions, and never activates candidates:

```sh
python3 infra/pi/prepare_parity.py --settings /selected/native/settings.json \
  --bindings /reviewed/local-bindings.json --output /outside-discovery/candidate
# Add --config /selected/native/extensions/subagent/config.json when present.
# Host migration only: --old-package '../../.local/share/pi-subagent-local'
# After preparation, the same inputs plus --check verify candidate drift.
```

Output must be vacant and outside the selected native agent directory. Parsing
and policy/input preflight complete before any output write; occupied destinations,
malformed/unknown policy, unresolved placeholders and explicit required-resource
disables refuse preparation. It is an offline preparation/check helper, not a
transactional live installer. Compare-before-write installation/rollback and
backups outside discovery belong to the controller after final review.

Both native config files matter. The extension reads
`<agent-dir>/extensions/subagent/config.json` in addition to Pi settings:

- Async and fresh context are default; depth-0 async is forced. Explicit child
  extension paths load a coherent Route provider/cache/usage bundle. Inherited
  providers alone do **not** bring SessionFence session hooks; never weaken that
  fence to make a child work. Only async local native children are certified here.
- Automatic workflow resumption, schedules, missions, upstream built-in agents,
  external/machine/pane/watchdog/agent-management/workflow-script features are off.
  Simple `tasks`/`chain` replaces workflow scripts. No automatic review campaign.
- Sessions/output/artifacts live below each native owner; temp runtime, run/result
  indexes and supervisor channels use its own `PI_SUBAGENTS_TEMP_ROOT`. Automatic
  role memory is not enabled. No writable state in shared program checkout or
  project-scoped defaults. Native overrides/per-call absolute outputs can still
  change this; review trusted project settings and each task's authority.
- Recursive legacy `.agents/**/*.md` discovery must prune skill/support trees with
  `subagents.agentExcludeDirs` (including symlink targets), not enroll every named
  Markdown document as an agent. Keep legitimate native agent discovery and skill
  discovery; new project support roots need the same preflight.

Focused source checks require the pinned upstream checkout, its production deps,
actual Pi 1.4, Node 22 and existing OpenSSL. The native test requires `PI_CODEX_FAST_SOURCE` pointing to the reviewed fast
program entry and uses disposable homes,
synthetic local HTTPS/credentials/catalog, three selected role fixtures and nine
common skill fixtures. It proves native loading, a detached child with its own
Route replay identity, retained run-ID continuation with ciphertext, unchanged
parent replay/history prefix, observed runner termination and missing-child-provider
refusal before HTTP. It captures completion notifications without triggering an
extra parent inference; live parent wake/TUI and real required-read traces are
separate acceptance, not claimed by this fixture.

```sh
python3 -m unittest discover -s infra/pi -p test_prepare_parity.py -v
PI_CODING_AGENT_DIR=/selected/pi-package node --test infra/pi/test_parity_runtime.mjs
# Optional explicit program-only coherent installed Route root:
# PI_PARITY_ROUTE_SOURCE=/selected/route-extension-root (never a profile/auth home)
npm run pi:quality
```

### Parent Fast / Ultrafast / off inheritance

Both parent and detached child explicitly load the reviewed existing fast entry.
Hermes needs only one additional narrow read-only fail-on-missing **program-file**
bind at `/opt/pi-codex-fast/extensions/codex-fast.ts`, not host settings/state.
Native Pi deduplicates the same canonical extension path if the host already
loads it through its local package registration. Host settings remain host-owned;
container defaults remain off unless its own native setting, `/codex-fast`,
`/codex-ultrafast` or `--fast`/`--ultrafast` selects otherwise.

The fast extension publishes its effective mode in the nonsecret
`PI_CODEX_FAST_PARENT_MODE` environment scalar after startup flags and live
commands. Nicobailon's normal detached spawn snapshots that environment;
`PI_SUBAGENT_PARENT_SESSION` identifies a child, which uses the snapshot instead
of a stale saved default. Invalid snapshots stay off. No upstream patch,
`fast:true` (unsupported for Spartan Route), parent history/credentials or copied
host settings are involved. Fast requests priority; eligible Ultrafast requests
ultrafast; off does not request priority. Existing exact model/API eligibility
and native controls remain unchanged, so unsupported tiers stay inactive.

Each new launch or retained revival takes the then-current parent mode. A later
toggle does not mutate an already running child. Host/container share behavior,
not a mode store; a container parent uses its own CLI/live mode. Isolated native
HTTPS acceptance exercises actual startup `--fast` over disk off, live Ultrafast
over disk off and live off over disk Fast → child priority/ultrafast/default.
It proves payload tier selection, not upstream entitlement, price or speed.
Set `PI_CODEX_FAST_SOURCE` to the reviewed program file for the native test.

### Cutover and rollback boundary

Preparation is **not activation**. Do not overwrite the old mounted checkout or
reload a parent still using it, especially while it owns work. After independent
review: freeze candidate and beforeimages; obtain an exact current host Pi child
and Hermes terminal/gateway/job inventory and operator-approved quiet window;
compare every owned target with its saved expected bytes; stop on drift. Install
only reviewed native settings/config/role/instruction deltas as the proper owner.
Canonical shared adapter edits have a separate assigned writer and must accompany
schema adoption, not precede it in old running sessions. Keep all disabled entries,
credentials/trust/session stores and foreign native fields unchanged.

Change only the owned private package bind to the **new** stable pinned checkout
and target. Existing container source/role mounts remain narrow read-only binds.
A new bind target requires separately approved Hermes recreation, **not a rebuild**;
it terminates same-container work, so the controller alone schedules it. Start
fresh host Pi processes, never hot-reload an old parent across schema changes.
Verify package/Route/roles/settings bytes, actual Node/UID/SDK imports, effective
resources, HTTPS/auth/catalog and neighbor identities. A bounded Sol/Luna live
smoke may be separately used within the remaining explicit budget; synthetic
acceptance is not live-provider or deployed-container proof.

Rollback also needs quiet/cessation evidence: compare installed owned bytes against
the accepted afterimages, restore only unchanged owned targets from beforeimages,
remove only newly created config files, restore the old package bind/registration,
then perform the separately authorized recreation/fresh-process restart. Preserve
both engines' sessions and all artifacts; never overwrite newer foreign settings
or use whole-home rollback. Do not attempt to resume an old logical session handle
through the new schema.

## Assignment and native lifecycle

Approve the complete coding tool/authority envelope first: outcome, repository,
allowed writes, source/candidate, enabled methods, checks, budgets, no-go actions,
independent review and acceptance owner. Pi's bash/edit/write and its local
subagents do **not** traverse Hermes per-command approvals. A prompt directory is
not a sandbox. Strict approval parity requires a different integration. Supply a
complete task packet, not an assumed parent transcript; reuse canonical
orchestration task patterns when available and enabled.

For the owner whose `HERMES_HOME` is already selected, use Hermes's native
`terminal` tool, not a workstation-terminal bridge or an untracked shell `&`:

```json
{
  "command": "pi --print --session-id task-123 --session-dir \"$HERMES_HOME/pi/sessions\" @task.md",
  "background": true,
  "notify": true
}
```

Run from the authorized worktree (`task.md` is its reviewed packet). Retain the
**actual returned `session_id`** (the Hermes process handle, not `task-123`), Pi
session ID/file, starting candidate and check evidence in the existing task owner.
`notify: true` requests one exit notification. Use the installed `process_manage`
contract with the real handle, for example:

```json
{"action":"poll","session_id":"<actual returned handle>"}
{"action":"log","session_id":"<actual returned handle>","offset":0,"limit":200}
{"action":"wait","session_id":"<actual returned handle>","timeout":300}
{"action":"kill","session_id":"<actual returned handle>"}
```

Page logs as needed; prefer completion notification/bounded wait over continuous
model polling. A wait timeout is not completion. On stop inspect the result and
confirm descendant cessation before handing off writes; a kill request alone is
not proof. Completed results are retrievable under the installed contract's
retention limits, not permanent evidence storage. Subagent-owned Hermes processes
are killed on that subagent's finish unless explicitly handed off with the native
`handoff` action; start this recipe from the owning Hermes conversation.

Inspect terminal status, actual exit code, raw output and expected artifacts.
Nonzero/abort/truncation/needs-input is not task acceptance. **Exit zero alone is
not success:** independently inspect the exact candidate/diff, allowed paths,
required checks and their raw results, then obtain required independent review.
Report incomplete gates honestly. No copied `--yolo`, `-q` or `--agent` flags.

## Continue, recover, remove

For needs-input, relay the decision, then explicitly reopen the recorded native
session, e.g. `pi --print --session /absolute/path/to/recorded-session.jsonl
@follow-up.md`, under a new native background handle. Do not repeat
`--session-id task-123` as a substitute for reopening or use ambiguous `--continue`.
Reconcile surviving processes, partial worktree effects, session contents and
checks before any recovery. Never automatically replay ambiguous external actions.
Print mode is not promised live steering; RPC is not added by this proposal.
Inside a newly accepted Pi runtime, one child uses
`subagent({agent:"builder",task:"<complete packet>",model:"spartan-route/codex/gpt-6.1-sol-282kcontext:medium",async:true,options:{context:"fresh"}})`.
Parallel independent children use `tasks:[{agent,task,cwd?,model?},...]`.
Dispatch `thinking` is ignored; reasoning belongs in the model suffix. Continue
eligible settled work with `{action:"resume",id:"<returned runId>",message:"..."}`;
save the new returned runId. Use `{action:"stop",id}` for stop and exact-ID status
for diagnosis. Stop acknowledgment is not process-terminal proof. The old local
`calls/prompt/initialContext/session/inactivityTimeout/timeout` contract is retired
only in newly cut-over processes; do not send new parameters to an old parent.

Ordinary disconnect, Hermes lifecycle cleanup, explicit stop and container death
are different. The installed terminal has `persist_on_release` only for explicitly
approved jobs intended to outlive conversation cleanup; it does not survive host
process/container death. This recipe does not enable it implicitly. Persisted Pi
sessions permit deliberate recovery, not exactly-once execution or automatic
restart. Container recreation terminates same-container processes.

For removal, first establish cessation of owned jobs. Controller restores only
owned unchanged-since-edit private binds/env and native settings/role links from
exact backups, preserving foreign entries; container recreation is again a
maintenance gate. Retain sessions/evidence until authorized retention cleanup.
Do not remove canonical skills, host Pi state, unrelated mounts or credentials.
