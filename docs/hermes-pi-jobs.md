# Native Pi jobs from Hermes (source candidate)

This is a source-only wiring proposal, not an activated integration. Hermes owns
intake, assignment permission, native process control and acceptance; Pi owns the
coding work inside the container. No daemon, queue, host bridge or new RPC client
is needed. Container Node 26 compatibility and live lifecycle/inference remain
separate acceptance gates; source smoke checks on Node 22 do not satisfy them.

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
The launcher execs **container** `node /opt/pi-package/dist/bundle/cli.js`; local
subagent uses `getPackageDir()` and the package's RPC export with `process.execPath`.
The selected complete local subagent package is v3.1.0, Git `65dac36`, including
its `node_modules`; do not replace it with an entry-file-only mount.

Mount the entire selected installed Route extension root read-only at
`/opt/pi-route-extensions`. This candidate selects the coherent installed bundle,
not an implicit upgrade: provider entry SHA-256
`6733afc80e13c6834b41bb111fa98728d3825e1b783c1acd4f48dc281352d7a1`;
provider/cache match versioned source. Installed usage is older and has no
`correlationHeaders` import/call; its lack of `correlation.mjs` is coherent, not a
missing-file repair request. Preserve its matching helpers. Freeze/read back the
whole chosen bundle before activation; do not combine current source usage with
installed helpers. Explicit settings load only its provider/cache/usage entries
and the local subagent. No other package is enrolled, including the disabled
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
$HERMES_HOME/pi/agent/settings.json       reviewed copy of /opt/pi-container/settings.json
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

Replace the example's `.invalid` endpoint with an **already-approved,
credential-free HTTPS** Route `/v1` URL reachable under unchanged proxy/DNS/TLS
rules. `http://spartan-route:18790/v1` is unsupported. The existing gateway parser
reads only `CADDY_AUTH_USER` and `CADDY_AUTH_PASSWORD` from the authorized env file
at `/run/pi-route/credentials.env`; it never sources it. No credentials in URLs,
CLI arguments or public files; do not copy laptop auth. Confirm credential scope,
catalog/model metadata and Sol/Luna permission separately. Initial settings select
only `spartan-route/codex/gpt-6.1-sol-282kcontext`; cached synthetic metadata in
source tests is not evidence of the live catalog, auth or cache hits.

New mounts require separately authorized Hermes **container recreation** through
the approved `sg` private selection, never an image rebuild. Coordinate all affected
gateways/jobs. Then verify workload UID, version, dependency/RPC imports, resources,
one provider registration, HTTPS/auth/catalog and unchanged neighbors before a
separately authorized live assignment. Stop if any gate fails; do not install,
upgrade or weaken networking to make the candidate run.

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
