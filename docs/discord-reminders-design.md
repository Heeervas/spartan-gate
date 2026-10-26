# Native fixed-text Discord reminders — proposed source design

Status: **design only; implementation and provisioning pending review/approval**.
The existing manual rename owner is unchanged by this proposal. This is not a
recovery of the Alberto Tools pins bundle or of historical reminder jobs.

## Outcome and native constraints

Preserve an explicit Discord user's reminder task as fixed text, with **no model
at firing time**. Reuse Hermes `cronjob` persistence, one-shot scheduling,
execution claims, cancellation fences and delivery. Do not add a scheduler,
SQLite database, polling loop, cron service or independent delivery/retry lane.

The inspected installed program exposes:

- `tools/cronjob_tools.py`: `cronjob_manage` in toolset `cronjob`; a `no_agent`
  job requires a script. `in 30m` is once, unlike recurring `30m`.
- `cron/jobs.py`: create paused, read/resolve an exact job, resume, pause and
  remove; one-shot schedules retain their parsed due instant when resumed.
- `cron/scheduler.py`: `no_agent` short-circuits before agent construction and
  session DB initialization. Empty stdout is silent success. Native script
  errors generate native error alerts, not an LLM fallback.
- `cron/scheduler_script.py`: one validated script path, **no per-job argv or
  stdin payload**; the script must resolve inside the selected profile's
  `scripts/`. `workdir` becomes subprocess cwd; if missing, native execution
  falls back to the script directory. The helper must explicitly reject that
  fallback rather than read input there.
- Native delivery still applies its own wrapper/redaction, media-directive and
  silence parsing, adapter permissions and delivery receipts. Zero-agent does
  not mean bypassing those controls or guaranteeing byte-identical transport.

Source defaults are not fresh registration: `hermes-discord` includes the core
`cronjob_manage`; `cronjob` is configurable and not default-off.
`hermes_cli/tools_config.py::_get_platform_tools` resolves saved lists/composites
and subtracts `agent.disabled_toolsets` last, including composite exclusions.
Adapter overrides and availability checks can further restrict the actual turn.
The approved DEFAULT projection established rename enabled/not disabled; cron
inclusion/exclusion and fresh effective availability remain **UNKNOWN**.

## Proposed source and installed delta

One narrow package, `infra/hermes-plugins/discord-reminders/`:

1. `plugin.yaml` and `__init__.py`: explicit opt-in, fresh ingress/turn binding,
   create/cancel handling and native scheduler calls only.
2. `reminder_helper.py`: one trusted, independently executable, fixed helper;
   also imported by the plugin for the common data/format contract. No generated
   source, shell evaluation, subprocess creation, network, gateway startup or
   model imports in the helper.
3. `README.md` plus a named-plugin mount example and operator provisioning
   procedure; focused tests under the existing Hermes-plugin test directory.

Keep the original tool contract `discord_reminder_create(duration, note)`.
Propose one narrow companion, `discord_reminder_cancel(reminder_id)`, accepting
only this plugin's returned opaque receipt, not arbitrary native job names/IDs.
No pins, generic send, arbitrary schedule/script/path/recipient/model arguments,
list-all-jobs tool or automatic profile enrollment.

Estimated delta: 200–350 production Python lines across the plugin/helper,
150–250 focused test lines and 60–100 documentation/example lines. The driver is
request correlation and the safe native-job/input handoff, not a new framework.
Keep this estimate a bound for review, not a target; report growth before coding.

For **DEFAULT only**, a later operator operation would add:

- One named read-only plugin mount, only if absent; never the whole plugins tree.
- One reviewed helper copy at `$HERMES_HOME/scripts/discord-reminder.py`.
  It must be a real regular file, not an escaping symlink. A helper symlink into
  the plugin directory would violate native script-root validation.
- One previously vacant owner-only input directory,
  `$HERMES_HOME/scripts/.discord-reminder-inputs/`, with `0700` mode. Runtime
  creates at most 128 request subdirectories; bounded JSON input/receipt files
  use `0600`. These are literal task inputs and request-to-native-job bindings,
  **not a second due-job queue or delivery ledger**.
- Explicit plugin enablement, an affirmative per-profile reminder entry,
  an explicit requester subset, and selected reminder-toolset exposure. This
  grants only the fixed native no-agent reminder service, not general script or
  job administration. Do not remove any existing native disable
  or enable `cronjob` implicitly. An effective native cron denial blocks creation
  pending a separate operator decision. Work receives no mount/helper/config
  change or implied opt-in.

No helper is installed and no runtime input directory is created by registration
or by the source tests.

## Scope, authorization and input binding

Capture only a fresh, external, authorized Discord ingress with a current
registered receiving adapter. Use the native source-aware authorization and
profile/transport identity. The first scope is DEFAULT's own transport and
runtime; reject routed/cross-profile sources, missing/inferred ownership,
missing user/message/channel/thread identity and delegated/background contexts.
Do not trust model-supplied routing or register-time bot/token capture.

Bind the tool to the exact ingress plus the native session/task/turn execution,
consume it once and recheck authorization immediately before creating/cancelling.
A natural-language create remains model-mediated interpretation of the current
explicit request, as in the original tool; it is not an injection-proof intent
classifier. Missing/ambiguous/multiple requests clarify without scheduling.
An explicit authorized reply can supply the task. Preserve `m`/`h`/`d` compound
relative durations, positive components and the existing 365-day upper bound.
Normalize to one native `in <minutes>m` schedule, never a recurring interval.

A deterministic request key covers profile/transport, platform, requester,
channel/thread and incoming message identity. The original task is bounded and
stored as JSON **data**, never interpolated into Python, shell, a filename or
native delivery syntax. A retry with matching input returns the same receipt
and due time; conflicting reuse clarifies and cannot retime or duplicate it.

Creation must honor the actual turn's native cron selection/denials and
`check_cronjob_requirements`, not just the presence of a registry entry. In
particular, `PluginContext.dispatch_tool` calls `registry.dispatch`, which does
**not** apply the tool's availability check or the turn's selected tool list.
Do not use that raw dispatch as an authorization shortcut.

The supported execution binding is the existing three-stage plugin surface:
`pre_gateway_dispatch` captures the authorized ingress in task-local context;
`pre_llm_call` binds its exact native session/task/turn and original user-message
correlation once; `tool_execution` middleware places that call's native IDs in a
ContextVar around `next_call`. The synchronous registered handler consumes only
that context and rechecks its task/session kwargs. Concurrent turns cannot use a
global last-request/cached-agent lookup; delegated or inherited later turns do
not rebind consumed ingress. Prove this actual native producer/consumer path
with concurrent synthetic profiles and turn-ID mismatches before acceptance.

The proposed grant is **an explicitly enrolled reminder service**, not a claim
that the provider exposed generic `cronjob_manage` in its final catalog. The
profile's own native turn-toolset resolver must still select `cronjob_manage`
for this direct source after adapter overrides and composite/global exclusions;
missing support or a native denial blocks scheduling. Apply current native
availability/check_fn as well. Do not change saved toolsets to obtain a pass.
Then use `model_tools.handle_function_call` for the fixed internal cron call so
native pre-tool policy hooks and execution middleware see it; carry the bound
IDs and selected lists, and do not set skip flags. An error/block is not creation.

This does not certify a cached agent's exact build-time tool list. Approval of
the explicit fixed-service grant is required before implementation: it permits
only this requested reminder operation, not generic cron tool exposure. If the
owner instead requires an authenticated snapshot of every underlying tool in
that cached agent, the inspected plugin handler contract does not supply it;
that is a native-contract gate, not permission to scrape the agent cache or use
`_last_resolved_tool_names`. Drop the earlier proposed provider-catalog observer:
request middleware is not a final authorization boundary and later middleware
may change it. No generic verifier or enrollment workaround is proposed.

## Native job and literal input handoff

Use a generated request directory beneath the trusted input root as the fixed
job's `workdir`. The static script receives no arbitrary argv. Its JSON contains
schema, request/profile binding, original literal task, due/expiry, and the
returned native job binding; it contains no credentials or arbitrary filesystem
or delivery destination options.

Serialize the entire lookup/create/bind/resume sequence, cancellation, cleanup
and helper input validation with one owner-only feature-root advisory `flock`.
Acquire it with a bounded wait; no unbounded polling or stale lease service.
Process exit releases the lock. Hold it across native filesystem operations,
not network delivery. This also makes the directory cap atomic. Use one lock
order (feature-root lock before native job operations); the helper performs no
native job operation while holding it. Conflicting retries inspect the existing
input under that lock; cancellation writes its tombstone before any retry can
resume. Atomic replacement alone is not request exclusion.

Create a native paused one-shot first, with fixed trusted script, `no_agent=True`,
repeat once, no skills/monitor/context/model override or attached continuation.
Use a deterministic owned job name for bounded crash recovery. Atomically save
and read back the exact input/job binding, then resume the same native job.
A crash before recording its ID leaves a **paused**, non-delivering job; resolve
only that deterministic owned name and validate its full plugin binding on retry.
Ambiguous or mismatched records fail closed, never delete foreign jobs. Do not
create another runnable job to work around uncertainty.

Pin delivery to exactly the validated current Discord conversation through the
native explicit-target syntax and owning profile's adapters; retain the matching
source provenance in native origin metadata. Do not use `origin` alone: native
thread-horizon expiry/home-channel fallback must not redirect this reminder.
No `all`, home-channel fallback, cross-profile, bot-chat or extra recipients.
If the actual current source cannot be represented without losing its receiving
transport, refuse rather than guess. Do not change OLD's configured shared
reminder destination or migrate its data; this proposal grants only the current
request's authorized conversation, not the historical dispatcher's global target.

At firing time the helper derives its profile root from its own trusted installed
location, not cwd or model arguments. Validate its exact provisioned script/input
roots, owner/modes, no symlink traversal, bounded schema and saved binding's
internal consistency. **The native subprocess receives no authenticated job ID.**
A request directory is therefore the helper's input capability, not proof of
which job invoked it. Explicit trust boundary for approval: same-profile native
script/job administration and same-UID filesystem writers remain trusted. Such
an administrator can reuse a request directory or run arbitrary code already;
this bridge is not a sandbox against that existing authority. The reminder tools
do not expose paths or arbitrary-job administration. Do not claim job-level
caller authentication, add execution tokens/a job reader, or patch native cron.
If isolation from other same-profile native jobs is required, this design cannot
provide it with the current no-argv/no-authenticated-job contract.

Read only the selected profile's native plugin enable/disable, reminder opt-in
and explicit requester-subset declarations with a non-executing bounded YAML
parser; the saved requester must remain selected. This replaces private hardcoded
requester exceptions with a narrower operator-selected scope, not public account
IDs or silent all-user permission. Do not
initialize a gateway, load dotenv or inspect credentials/jobs/other profiles.
Malformed/missing declarations, disabled plugin, missing/revoked input, wrong
cwd/profile or expired grant produce empty stdout, never a model fallback.
Native cron itself retains its existing same-profile dotenv/environment and
subprocess sanitization behavior; this design does not remove or duplicate it.

Emit a deterministic bounded reminder envelope containing the saved literal
text, with mention neutralization matching the original safety intent. Escape
outbound protocol directives (`MEDIA:`, global media tags, silence/wake sentinels)
as inert display data so hostile task text cannot attach local files, request
voice/document delivery, suppress the reminder or introduce mentions/recipients.
Preserve original task bytes in the input; safe display escaping and native
redaction/wrapping are explicit, not generated/paraphrased reminder content.
Exercise the actual native output parsers, including multiline/fence escapes,
rather than trusting a code fence or testing only the formatter in isolation.

## Cancel, revoke, expiry and recovery

The cancel tool requires fresh authorized ingress from the same profile,
requester and exact conversation as the receipt. Resolve only its request
binding; verify the native job still has the owned fixed script/workdir,
one-shot/no-agent and destination before touching it. Persist revocation first,
then use native **pause** semantics: native removal also deletes job output and
must not erase uncertain-delivery evidence. Preserve the paused record and
request tombstone; do not automatically resume it. No access to other
jobs/profiles is exposed. Do not silently reconstruct deleted requests from
historical prompts.

Disabling the selected plugin/reminder entry, removing the requester from the
explicit feature subset, or revoking a request input stops future helper output.
Native Discord permission revocation remains enforced at
send time. Creation authorization is not a perpetual Discord-role grant: native
cron does not expose a live source/role authorization callback to this subprocess.
Do not claim that merely editing a user's allowlist automatically cancels every
previously authorized delayed job; revoke those grants or disable this feature.
If that stronger semantics is required, it is a separate native-contract gate.

Bound each grant to its original due instant plus native one-shot grace, never
extend it on retry/resume or recover an expired grant as a new task. The
implementation binds its grace to the supported native constant (currently
`ONESHOT_GRACE_SECONDS = 120`), verified against program source in the native
gate, not another independent scheduling policy. No background expiry worker. Reconcile or
remove only this feature's terminal/expired input artifacts during a bounded
request operation or an explicit operator action; a full input cap denies new
creation rather than evicting active grants. Cancellation after output is
claimed/in flight must be reported as too late/uncertain, not an unsent guarantee.
Native execution/delivery claims and acknowledged/unknown-send records own
retries and delivery status; the helper neither sends nor replays. Persistence
readback is not receipt. Preserve unknown delivery for operator inspection, do
not automatically rearm a one-shot or delete its evidence as successful cleanup.

## Provisioning, rollback and verification gates

Before any approved DEFAULT provisioning, verify actual runtime UID/GID, exact
profile root, helper/input vacancy, filesystem ownership/modes and native plugin
and scheduling controls. Do not repair existing personal scripts/data. Compare
expected absent/old bytes immediately before changes; install the helper via an
owned temporary regular file and atomic replace, verify candidate SHA-256 and
mode, then enroll only with a compare-and-swap against the observed config.
No broad scripts mount. Recheck helper hash during creation; mismatch refuses.
No image build is planned. Any shared-process reload/quiet window belongs to the
operator and must preserve Work and existing jobs.

Rollback first refuses new creation and revokes/pauses only bound plugin-created
pending requests, accounting for in-flight/unknown delivery. Then restore only
the exact helper/config bytes this operation replaced if still matching the
installed candidate; retain nonterminal evidence. Remove only proven-owned vacant
or terminal artifacts. Never restore an entire config, jobs file or scripts tree.

Before implementation readiness: independently review this design, its native
contracts and the new helper/data provisioning surface. Before source acceptance:
focused fail-before/pass-after and synthetic native execution tests must prove
registration and turn controls, allowed/refused profile journeys, literal hostile
text with zero attachments/mentions/inference, no cwd fallback, duration bounds,
retry/crash/paused handoff, scoped cancellation/revocation/expiry and native
unknown-send behavior. Match installed program identity; do not equate sliced
fixtures with full runtime registration. Run existing unchanged TOP policy,
Compose render if a template changes, and independent exact-candidate review.
No real reminders, Discord calls, services, live enrollment or paid inference
are authorized by this document. Actual DEFAULT discovery, registration and
human adoption remain separately approved operator gates.
