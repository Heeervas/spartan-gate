# Hermes Profiles

Native Hermes multiplexing serves the default and eligible named profiles in
one gateway process and one API listener. Named profiles use `/p/<profile>`;
the default uses the listener's base endpoint. Keep real inventories, endpoints
and credential ownership in private documentation.

## Modes and lifecycle

The running multiplexer discovers named profile directories immediately on
native creation and through a periodic rescan (currently 30 seconds). A named
profile is excluded when `gateway.standalone: true` is set in its configuration
or its `gateway.parked` marker exists. Do not assume a new profile is manual
merely because it is absent from `HERMES_AUTOSTART_PROFILES`.

Native lifecycle commands below run from the **operator host**, outside the
Hermes gateway's own tool process. The CLI guards self-targeting lifecycle
operations from inside a gateway.

```sh
source scripts/aliases.sh
sg exec hermes hermes -p coach gateway stop
sg exec hermes hermes -p coach gateway status
# Only after the profile is configured and explicitly approved to serve:
sg exec hermes hermes -p coach gateway start
```

For a multiplex profile, `stop` parks it and removes its bots and cron tasks
from the shared runtime. `start` unparks and enrolls it in the live default
host. Named `restart` hot unserves/serves the profile and also unparks it; never
restart parked/manual profiles as a blanket reload procedure.

`sg-hermes-restart` is a different operation: it gracefully restarts the full
default shared gateway process. `sg-hermes-restart coach` targets only a separate
running s6 service, not a named profile hosted by the multiplexer. See
[operations](operations.md) for the drain and readiness contract.

## Create and complete a fresh profile

The [published profile recipe](../apps/hermes-agent/runtime-guides/local/private/profile-creation.md)
is the curated runtime procedure at
`/opt/data/brain/runtime_docs/local/private/profile-creation.md`. Its `private`
path component is historical; its contents are public safe. The sections below
explain native integration and safety boundaries, not a private inventory.

Prefer native fresh creation. With explicit authorization for creation and
immediate multiplex enrollment of the intended profile, the supported CLI may
run inside the gateway's tool process:

```sh
hermes profile create coach --no-alias --no-skills --description 'Focused coaching assistant'
```

`hermes profile create` creates an empty mode-0600 `.env`, atomically publishes
the profile directory, and notifies the running multiplexer. Publication can
enroll the profile before the next command. Enrollment uses the shared listener
at `/p/<profile>`; no per-profile port or autostart entry is required.
Enrollment alone proves neither API authentication/readiness nor bot readiness.

There is no `--parked` creation option. Creation and parking are **not atomic**. When the authorized outcome is a parked/manual profile, creation must
account for immediate enrollment, and the operator must stop it from the host
and confirm it is unserved before adding credentials:

```sh
source scripts/aliases.sh
sg exec hermes hermes -p coach gateway stop
sg exec hermes hermes -p coach gateway status
sg-hermes-profile show coach
```

The show subcommand precedes the name. If discovery has not completed, ensure
`gateway.parked` exists in this newly created, known empty profile, then repeat
native stop/status and confirm removal. A marker alone does not prove that an
already served profile stopped. Gateway self-targeting stop/restart guards
remain in force: perform protected lifecycle operations from the operator host,
without bypassing the guard. An authorized native creation is not a lifecycle
bypass, and neither is a supported profile configuration edit. Never retry a
denied action with `--force` or another bypass.

Prepare the profile's own `SOUL.md` and compact memories. AGENTS loading follows
the effective cwd: prefer an explicit named workspace cwd and
`workspace/AGENTS.md` with one instruction body. `AGENTS_<name>.md` is not
automatically injected; verify effective instruction loading in a fresh session.
Curate skills
deliberately: link complete skill directories, including their supporting
files, and preserve intentional per-profile divergences and
`.no-bundled-skills`. Keep `skills.external_dirs` empty for curated profiles;
do not import another profile's whole skill catalog or memory history.

Configure credentials only in their intended scope. Dedicated messaging bots
need their own credentials; a profile routed through the default shared Discord
bot uses `gateway.profile_routes` on that default gateway and does not receive
a copy of the bot token. A profile needing API access requires a distinct
`API_SERVER_KEY`. Confirm effective values under the named Hermes context,
including inheritance: empty `.env` files do not prove that container
credentials are absent. Hermes scoped dotenv loading may not be visible in a
plain Python `os.environ` inspection. Validate the effective profile-aware
configuration without printing secrets.

Do not copy DEFAULT `.env`, config, identity or bot credentials into a fresh
profile. Native cloning is not history restoration; existing-profile recovery is
a separately authorized recovery operation, not a generic clone step. Native cloning copies
configuration, `.env`, identity and memories. It strips
messaging credentials by default, but other secrets can remain. Do not blanket
clone an existing gateway or copy OAuth/provider state. If deliberate cloning
is required, review each retained configuration and credential locally before
activation. Never copy `state.db*`, `response_store.db*`, `auth.lock`, gateway
locks/PIDs, logs, sessions, sandboxes or generated caches into a new identity.

Complete the checklist below and use only the authorized lifecycle transition.
A manual specialist stays parked, uses `API_SERVER_ENABLED=false`, and has
blank Telegram/Discord credentials; do not start it to populate an API
selector.

## Routing, tools and plugins

Inherit the deployment's managed Route/proxy settings rather than copying
upstream provider credentials into profiles. Managed dotenv may contain the
approved shared internal Route client token; it must not contain upstream
provider or messaging credentials. Keep per-profile messaging and API keys in
their intended private scope. See [operations](operations.md) for managed
inheritance and the active Route endpoint contract.

Use native `SEARXNG_URL` for SearXNG. Browser configuration follows the selected
Compose browser mode. When using the Camofox-only image, managed Camofox is
required; follow the
[runtime Camofox guide](../apps/hermes-agent/runtime-guides/local/camofox.md).
Do not configure Chromium or another backend as an availability fallback.

The prepared new-profile BASIC config enables exactly `shared-masterv2`, `node-search`,
`profile-delegate`, `discord-deferred-thread-title`, `spartan-cache-lineage`,
`web-spartan-reader`, `hermes-lcm`, and `self-improvement-governor`, with
`context.engine: lcm` and `web.extract_backend: spartan-reader`. Use individual
per-profile implementation links and profile-local config, not a whole plugin
directory link or DEFAULT config copy. NodeSearch requires an explicit narrow
vault root, profile-local cache and PyYAML in the actual Hermes interpreter.
Delegation availability is not authority: `allowed_profiles` defaults empty.
These are new-profile requirements, not a migration of existing exceptions.

BASIC is not only a plugin list: the linked runtime recipe is the single owner
of the complete COMMON configuration. It enables native memory/USER (2200/1375
characters, nudge 10), native background review (skill nudge 90), and weekly
native curator (168 hours, stale/archive 14/30 days, backup keep 5), with LLM
consolidation and bundled-skill pruning off. Native memory/skill write-approval
gates are off to retain normal learning; destructive background memory changes
still use native staged approval. These tool gates are not a filesystem sandbox,
and skill-selection guidance is not independent enforcement. The gateway's
curator poll does not prove measured inactivity; do not infer that from CLI/serve
idle documentation.

The recipe uses advertised `codex/gpt-6-luna-282kcontext` for vision (120 seconds)
and approval (30), not the unsupported `gpt-6.1-luna` spelling. Main Sol reasoning
and identity may have explicitly approved per-profile exceptions. LCM threshold
is explicitly 0.8; retain its five supported environment controls rather than
inventing YAML settings. Native `delegate_task` remains 250 iterations, 600
seconds, 10 children and depth 1 without MCP inheritance or auto-approval.
Separately, `profile_delegate` uses concurrency 8, async 2, depth 2 and wait/cap
1200/1800 seconds. Its empty target policy still denies unapproved delegation;
concurrency is not permission to restore old target allowlists or cron jobs.

Reference only installed, enabled plugins in toolsets and profile config. Every
**new profile** must link the mounted `spartan-cache-lineage` plugin, include
it in `plugins.enabled`, and pass plugin doctor, discovery/import and actual
registration checks for its `llm_request` middleware and `post_api_request`
hook. A symlink or list entry alone is insufficient. This new-profile
completion requirement does not retroactively override existing intentional
exceptions or change the [explicit runtime activation policy](spartan-route.md). Do not bulk-enable domain plugins to satisfy it.

### Common instructions: shared-masterv2 (prepared source, activation pending)

`shared-masterv2` registers one native SYSTEM section for every profile where
it is enabled, without name exclusions: `empty`, `workstation`, `ssr`, `ssr-*`
and `ssr_*` names receive the same common instructions as other profiles.
This does not enable memory, history or any capability disabled in a profile.
The public Compose source mounts only this plugin read-only at
`/opt/data/plugins/shared-masterv2`; use individual named-profile links and
`plugins.enabled: [shared-masterv2, ...]`, replacing v1 enablement rather than
running both. Do not link the whole plugins directory.

The callback reads `get_default_hermes_root()/MASTER.md`, independent of cwd.
In the standard container this is `/opt/data/MASTER.md`; a custom
`<root>/profiles/<name>` home resolves to `<root>/MASTER.md`. Follow the native
root resolver, not a profile-local MASTER copy. Native cwd-based project/gateway
`AGENTS.md` discovery remains independent and unchanged. When cwd moves outside
the profile workspace and its AGENTS instructions are absent, conditionally read
the intended profile AGENTS directly; do not make routine MASTER rereads part of
the workflow or assume a specially named AGENTS file is auto-injected.

The measured common content is **16,416 stripped characters / 16,576 with native
framing** for this one-section inventory. The build patch retains the ordinary
**4,000** default, raises the native maximum to **20,000** per opted-in section
and aggregate budget to **24,000** including framing. V2 uses that native maximum,
not its own cap, truncation or provider middleware. Other enabled sections also
consume the aggregate budget; verify the actual inventory before activation.
Missing, empty or unreadable/invalid-UTF8 MASTER produces a useful warning
without its contents. Native oversized/reserved-marker/aggregate failures omit
the section with a warning; they do not truncate it or refuse the user request.

Native assembly reads once per render/freeze boundary, reusing the snapshot
within that prompt's life. Persisted sessions retain their full prompt snapshot;
native invalidation (for example compaction) rereads the file and adopts edits.
This is not v1's per-request reconciliation. The patched image **and** plugin
mount must be adopted by an explicitly authorized user recreation, followed by
fresh sessions and prompt verification; a plugin reload alone cannot install
the core patch. Source preparation is not live adoption. The currently mounted
v1 payload remains intact for its existing consumer until recreation and
separately authorized retirement. See the
[v2 preparation note](../apps/hermes-agent/runtime-docs/hermes_notes/shared_masterv2_31102026.md).
The [historical v1 transport note](../apps/hermes-agent/runtime-docs/hermes_notes/shared_master_instructions_17102026.md)
is an unserved operator archive, not the v2 contract.

For Reader, use the Gate-owned `infra/hermes-plugins/web/spartan-reader`
package, discovered as `web/spartan-reader` (manifest alias `web-spartan-reader`
is also accepted for enablement), and set `web.extract_backend: spartan-reader`.
Do not substitute the legacy flat plugin merely because its manifest name matches:
the Gate provider pins the internal Reader endpoint and rejects responses missing
the untrusted-content header or envelope. Verify the effective provider selection
when activation is authorized. Preserve explicit plugin disables, particularly
`prompt-load-bridge`, and existing peer tool overrides. For profile delegation, add the intended new target to each enabled
caller's `plugins.entries.profile-delegate.allowed_profiles` only where
authorized. The effective `PROFILE_DELEGATE_ALLOWED_PROFILES` environment value
overrides the YAML policy, including values loaded through scoped dotenv.
Preserve caller limits, modes, existing targets and disabled delegation;
availability in the multiplexer does not grant delegation authority.

### Reload scope

The installed `/opt/hermes/gateway/run_plugin_rewire.py` supports native plugin
install/enable and Desktop `plugins.manage` notifications to the running
gateway and rewires adapter handlers through `on_plugin_loaded`. Tools and
system-prompt changes take effect in the next session; discovery or adapter
rewiring alone does not prove that an existing session has loaded them. Confirm
the installed implementation before relying on hot rewire.

This notification does **not** reload `gateway.profile_routes`, which the
shared gateway runner caches in its configuration. Changing the default shared
bot's Discord routes requires a controlled host-side `sg-hermes-restart` and
route acceptance afterward. A named profile restart is not proof that the
default runner reloaded its routes. Do not perform this shared restart from
inside the gateway or turn a lifecycle denial into a forced action.

New profile Talk/voice boot provisioning may require container initialization;
hot multiplex discovery alone does not install those settings. Changes to
private Compose environment or mounts require `sg up -d hermes` to recreate
Hermes. Live-mounted documentation edits need no restart.

### Completion checklist and proof limits

Record each surface as verified, pending or intentionally disabled, without
publishing private inventories, routing IDs, credentials or session IDs:

- **Creation and lifecycle:** native fresh identity, intended served/parked
  state, shared-listener path and effective API settings; directory existence,
  enrollment and command success alone do not prove readiness.
- **Identity and skills:** profile-local identity and compact memories, curated
  complete skill links, retained divergences, `.no-bundled-skills` and empty
  `skills.external_dirs` where curated.
- **Plugins and tools:** effective enable/disable lists, cache-lineage link,
  discovery and real middleware/hook registration; Reader link, enablement and
  effective extract backend; retained peer overrides and next-session tool and
  prompt loading. Registration proves wiring, not an upstream cache hit or a
  successful Reader fetch.
- **Delegation:** each enabled caller's effective target allowlist, limits and
  mode, including environment overrides; preserved intentional disables. A
  configuration check does not prove a completed delegated task.
- **Discord and API:** default shared-bot routes without copied tokens, profile
  key isolation, effective profile-aware credentials, controlled shared reload
  if routes changed, and intended route selection. Static route review does not
  prove delivery; authenticated API readiness does not prove messaging readiness.
- **Acceptance:** inspect supported status, discovery and authenticated health
  locally without exposing secrets. Any live message, delegated task or voice
  operation needs its own authorization; record unperformed checks as pending.

## Browser Extension Gateways

A private Caddy override can publish an explicit profile allowlist through one
authenticated extension edge. For multiplex mode, map literal external paths
to the shared listener and the intended native prefix, for example:

| External path | Internal request target |
| --- | --- |
| `/agents/default/*` | shared Hermes listener, base endpoint |
| `/agents/coach/*` | shared Hermes listener, `/p/coach/*` |
| `/agents/specialist/*` | shared Hermes listener, `/p/specialist/*` |

These are examples, not an inventory or automatic routing rule. Keep the
listener port private and configure each mapping explicitly. Never derive an
upstream host, port or profile from unchecked headers, query values or captures.
Unknown `/agents/*` paths must be denied, never routed to default. Remove
`X-Hermes-Profile` before proxying.

A shared external edge key grants authority over every exposed profile. Caddy
must replace external authorization with the **selected profile's** internal
API credential. Each profile needs its own `API_SERVER_KEY`; do not substitute
one global internal key or fall back to the default profile key. Missing or
invalid API credentials return 401; an unknown or parked multiplex profile
returns 404. Expose only confirmed served profiles, and verify both path
selection and credential isolation after changing mappings.

Recreating Hermes preserves its Docker service name, so Caddy can reconnect to
the shared listener. During restart, requests can fail temporarily; they must
never fall through to another profile.

## Separate standalone gateways

A deliberate standalone profile sets `gateway.standalone: true` and runs its
own gateway/service. Its enabled API listener needs a literal, unique
`API_SERVER_PORT`; inspect existing listeners with `sg-profile-ports` before
choosing a port. Keep ports on the internal network. Dedicated messaging
credentials or explicit blank values must be ready before any gateway starts.

`HERMES_AUTOSTART_PROFILES` is a legacy first-boot bootstrap for missing s6
state; persisted native start/stop state takes precedence. Newly required s6
slots are provisioned by container initialization, not by writing root-owned
`/run/service` from a running Hermes process.

The older `sg-hermes-profile-new` helper implements this standalone port and
autostart workflow and rejects a native seeded `.env`. Do not use it for native
multiplex creation or combine it with the fresh-creation sequence above.

## Hermes Talk delegation

With the running native multiplex gateway, the adapted Talk plugin selects the
live shared API listener automatically: the default profile uses its base URL,
and each named profile uses `/p/<profile>`. This takes precedence over stale
`TALK_API_SERVER_URL` values pointing at retired per-profile listeners. A missing
or malformed shared listener refuses endpoint selection; it never falls back
to the default profile. API credentials remain scoped to the calling profile.

For a standalone gateway profile, set `TALK_API_SERVER_URL` in that profile's
`.env` to `http://127.0.0.1:<API_SERVER_PORT>`, using its existing literal API
port. Without the running multiplexer, the ordinary plugin configuration still
applies; the default port 8642 may otherwise address a different gateway.

Leave `TALK_API_SERVER_KEY` unset when the profile already has a working
`API_SERVER_KEY`; Talk inherits it after Hermes loads the profile's `.env`.
A plain `docker exec` Python process does not reproduce that loading, so its
credential result alone is not a gateway diagnosis. Do not copy keys between
profiles or replace their existing credentials to fix an endpoint mismatch.

Preflight the effective endpoint, authentication and `run_submission` capability
for every affected gateway before updating any file. Back up the private config
and preserve unrelated values. From the **operator host**, use native
`sg exec hermes hermes -p coach gateway restart` for a served named multiplex
profile; this hot reloads that profile. It also unparks a parked profile, so
never use it indiscriminately across manual or stopped profiles.

`sg-hermes-restart` instead sends drain-aware SIGUSR1 to the default shared
process and restarts all its served profiles. Its optional named argument is
only for a separate running s6 gateway. Verify the intended profile's readiness
after either operation; command success alone is insufficient. Existing profile
`.env` edits need no image rebuild or container recreation. New profile Talk or
voice provisioning performed by container initialization may need a Hermes
recreation even though multiplex enrollment is hot. Compose env or mount edits
also require recreation; live-mounted documentation edits need no restart.
Keep manual and stopped profiles in their existing state.

Acceptance must include an actual delegated tool operation and its spoken result
in Discord. The plugin's `hermes talk check` requests no tools and can pass via a
detached process; inspect `details.lane` and require `api-server` when validating
this API configuration. If its API availability check fails, the installed Talk
plugin may use its existing detached Hermes backend. Verify the effective
profile there too; there is no API-only fallback switch in this plugin revision.

The installed plugin uses `TALK_API_SERVER_PROBE_TIMEOUT_S` (default 1.5) for
its capability probe, and four times that value for run submission and polling.
A slow local API can accept a task and still trigger `ReadTimeout` while Talk
polls it. If repeated live checks demonstrate this, configure a measured,
bounded value in the affected profile. A value of `5` gives run requests a
20-second timeout; it does not change tool authority or the overall job deadline.

This config enables the plugin's existing background delegation and result
watchers. It does not add the optional host execution attachment or merge the
voice transcript into the text channel's conversation history.
