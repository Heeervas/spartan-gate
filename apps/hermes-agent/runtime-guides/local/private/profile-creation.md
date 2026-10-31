---
title: Hermes Profile Creation
summary: Fresh native profiles, BASIC capabilities, isolated identity and authorized lifecycle.
---

# Hermes Profile Creation

This is the public-safe promotion of the existing operator profile procedure,
not a second installation-specific how-to. Its runtime path remains
`/opt/data/brain/runtime_docs/local/private/profile-creation.md` for continuity;
`private` in this path does not make its contents private. No operator inventory,
routes or account identifiers belong here. This procedure grants no lifecycle,
credential, inference, external-message or activation authority.

## 1. Choose lifecycle before creation

The native gateway multiplexes eligible profiles on one shared listener:
default uses the base endpoint and named profiles use `/p/<profile>`. Creation
notifies discovery; periodic rescans currently run every 30 seconds. Neither a
new port nor an autostart entry is required. `gateway.parked` or
`gateway.standalone: true` excludes a named profile from multiplex discovery.

Inspect the target first; never overwrite meaningful content. With explicit
permission for creation **and immediate enrollment**, prefer fresh creation:

```sh
hermes profile create coach --no-alias --no-skills --description 'Focused coaching assistant'
```

Native creation atomically publishes a directory and an empty mode-0600 `.env`.
There is no `--parked` creation flag: creation and parking are not atomic. If
no enrollment interval is permitted, resolve that requirement before creation.
For an authorized create-then-park workflow, stop from the operator host before
adding secrets, and confirm actual unserved state:

```sh
source scripts/aliases.sh
sg exec -T hermes hermes -p coach gateway stop
sg exec -T hermes hermes -p coach gateway status
sg-hermes-profile show coach
```

The show subcommand precedes the name. If discovery is pending, a parked marker
in this newly created, known empty profile can exclude it from the next scan;
a marker alone does not prove removal of an already served profile. Stop parks
and removes bots and cron tasks; start unparks/enrolls; restart reloads **and
unparks**. Never bulk-restart manual or parked profiles. Manual specialists keep
`API_SERVER_ENABLED=false` and explicit blank messaging credentials.

Protected lifecycle operations belong on the operator host, outside gateway
tools. Never remove environment guards, use `--force`, reach through control
sockets, or relay a denied command to bypass self-targeting protection. Native
creation and supported config edits do not authorize lifecycle bypasses.

## 2. Establish fresh, isolated identity

Prepare profile-local `config.yaml`, `.env`, `SOUL.md`, compact
`memories/MEMORY.md` and `memories/USER.md`, and curated `skills/` and `plugins/`.
Do not copy DEFAULT `.env`, config, identity, memories or bot credentials.
Preserve native timezone, memory and HOME isolation. SOUL owns tone, domain
boundaries and approvals; memories hold durable facts, not another profile's
history.

AGENTS loading follows the **effective cwd**. Prefer an explicit named workspace
cwd with `workspace/AGENTS.md` containing one instruction body for paths,
workflow and verification. A file named `AGENTS_<name>.md` is not automatically
injected. Verify effective cwd and actual instruction loading in a fresh session;
file existence is not prompt evidence. If cwd moves outside the profile workspace
and its AGENTS instructions are absent, conditionally read that profile's intended
AGENTS directly. Common MASTER injection does not replace this recovery. Keep outputs in an explicitly writable
domain root and sources read-only where applicable.

An empty dotenv does not prove absence of inherited container credentials.
Check effective presence, source and isolation in the named Hermes context,
without printing secrets; plain exec Python does not reproduce scoped dotenv.
Inherit managed Route/proxy policy instead of upstream credentials or stale
URLs. Managed dotenv may carry only the approved shared internal Route client
credential, not upstream provider or messaging credentials. Dedicated bots need
their own credentials and constraints; shared Discord routing does not require
copying the default bot token. Each API-enabled profile requires a distinct
private `API_SERVER_KEY`, never default-key fallback.

Native clone is a separately authorized exception: it copies config, dotenv,
identity and memories, stripping messaging credentials by default but possibly
retaining other secrets. Review retained files while unserved. Clone is **not
history restoration**. Existing-profile recovery requires a separately authorized
recovery operation, not a generic clone step. Never transplant OAuth/browser
stores, sessions, `state.db*`, `response_store.db*`, `auth.lock`, gateway
locks/PIDs, logs, sandboxes or generated caches. Never delete a lock/PID unless
its owner is confirmed dead and the profile is unserved.

## 3. Complete the BASIC capabilities

BASIC is the following native configuration, not just a plugin inventory.
This is a new-profile baseline, not authority to flatten approved existing
exceptions. Model identifiers must exist in the deployment's advertised Route
catalog: a saved config value alone is not support. In particular, use GPT-6
Luna below, never the invalid `gpt-6.1-luna` spelling. Main reasoning defaults
to medium; preserve explicitly approved profile exceptions such as high.

```yaml
model:
  default: codex/gpt-6.1-sol-282kcontext
  provider: openai-api
agent:
  max_turns: 90
  reasoning_effort: medium
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  nudge_interval: 10
  provider: ""
  write_approval: false
skills:
  external_dirs: []
  inline_shell: false
  selection_policy: required
  creation_nudge_interval: 90
  write_approval: false
curator:
  enabled: true
  interval_hours: 168
  min_idle_hours: 2
  stale_after_days: 14
  archive_after_days: 30
  consolidate: false
  prune_builtins: false
  backup:
    enabled: true
    keep: 5
delegation:
  model: codex/gpt-6.1-sol-282kcontext
  provider: openai-api
  reasoning_effort: medium
  max_iterations: 250
  child_timeout_seconds: 600
  max_concurrent_children: 10
  max_spawn_depth: 1
  inherit_mcp_toolsets: false
  subagent_auto_approve: false
  orchestrator_enabled: true
auxiliary:
  vision: {provider: openai-api, model: codex/gpt-6-luna-282kcontext, timeout: 120}
  approval: {provider: openai-api, model: codex/gpt-6-luna-282kcontext, timeout: 30}
  compression: {provider: openai-api, model: codex/gpt-6-luna-282kcontext, reasoning_effort: xhigh, timeout: 120}
  title_generation: {provider: openai-api, model: codex/gpt-6-luna-282kcontext, reasoning_effort: "", timeout: 30}
  skills_hub: {provider: openai-api, model: codex/gpt-6-luna-282kcontext, timeout: 30}
  mcp: {provider: openai-api, model: codex/gpt-6.1-sol-282kcontext, timeout: 30}
  background_review: {enabled: true, provider: auto, model: "", timeout: 120}
  curator: {provider: auto, model: "", timeout: 600}
terminal:
  backend: local
  timeout: 180
  env_passthrough: []
browser:
  camofox:
    managed_persistence: true
timezone: Europe/Madrid
platform_toolsets:
  discord:
    - connections
    - hermes-discord
plugins:
  enabled:
    - shared-masterv2
    - node-search
    - profile-delegate
    - discord-deferred-thread-title
    - spartan-cache-lineage
    - web-spartan-reader
    - hermes-lcm
    - self-improvement-governor
  entries:
    profile-delegate:
      allowed_profiles: []
      max_concurrent: 8
      max_async: 2
      max_depth: 2
      default_timeout_seconds: 1200
      max_timeout_seconds: 1800
context:
  engine: lcm
lcm:
  context_threshold: 0.8
web:
  extract_backend: spartan-reader
```

Set the profile's explicit `terminal.cwd` to its own workspace, as described
above; do not paste another profile's path or identity. Retain the deployment's
managed endpoint/authentication rather than copying provider credentials.

Native automatic reviews stay enabled: memory nudge 10, skill-creation nudge
90. Same-model reviews inherit the parent model and reasoning; the auxiliary
review timeout is 120 seconds. Native curator performs weekly deterministic
skill maintenance, not the retired custom orchestrator. Consolidation model
calls and built-in pruning are off. The gateway's curator poll does not measure
real inactivity even though the native CLI/serve documentation describes idle
gating; do not promise that every gateway pass waits for two measured idle hours.

`memory.write_approval` and `skills.write_approval` are native tool-write gates.
They are off here to preserve ordinary native learning. Native destructive
background memory replacements/removals still require staged approval. Turning
these flags on stages tool writes; it is not a global filesystem sandbox or a
blanket curator-archival gate. Required skill selection, MASTER/SOUL rules and
proportional verification are instructions, not independent tool confinement.

Discord explicitly selects the native `connections` and `hermes-discord`
capabilities plus the selected providers. Do not transplant retired `rescuer` or
`web-search-plus` toolset references. An explicit saved platform list differs
from an implicit native fallback: the native resolver treats it as opt-in to
that platform's default-off native tools. Preserve approved per-profile limits
rather than treating this list as new external-action authorization.

The two delegation mechanisms are deliberately different: native `delegate_task`
uses ten children, depth one and a 600-second child timeout; `profile_delegate`
allows eight concurrent and two asynchronous calls, depth two, a 1200-second
default wait and an 1800-second maximum. Its empty target allowlist still denies
unapproved outgoing profile delegation. Do not raise async capacity or restore
old targets merely to match the concurrency setting.

Other native auxiliary roles retain native AUTO defaults: profile describer and
monitor 60 seconds; audio tags 30; triage 120; Kanban decomposition 180; MoA
reference/aggregation 900 each if explicitly selected. Do not transplant retired
`auxiliary.web_extract`, `auxiliary.session_search` or `auxiliary.flush_memories`
blocks. Current extraction is deterministic; those old blocks are not supported
helper assignments. Native title generation's thinking-off request takes
precedence over a task reasoning setting.

Implementation availability and config enablement are independent. Make an
individual per-profile link for each required plugin from the mounted canonical
implementation into `/opt/data/profiles/<name>/plugins/`; never link the whole
plugins directory or copy another profile's config. For ordinary plugins the
source is `/opt/data/plugins/<plugin>`. Reader's actual discovery package is
`web/spartan-reader` (the `web-spartan-reader` manifest alias above is accepted):
link the Gate-owned `infra/hermes-plugins/web/spartan-reader` implementation at
its corresponding per-profile discovery path, preserving the nested layout.
Do not substitute a legacy flat Reader plugin with the same manifest name.
The Gate provider pins internal Reader and rejects missing untrusted-content
headers/envelopes. Verify effective backend selection.

Configure NodeSearch with an **explicit narrow vault root**, not the whole data
home or all mounted knowledge. Use a **profile-local cache**; do not share caches
or copy another profile's generated index. Confirm PyYAML is available in the
actual Hermes interpreter. In the profile's own scoped dotenv, the supported
root setting for the standard vault layout is:

```dotenv
NODE_SEARCH_ALLOWED_ROOTS=/opt/data/brain/obsidian-vault
LCM_ASSERTION_EXTRACTION_MODEL=codex/gpt-6.1-sol-282kcontext
LCM_EXPANSION_MODEL=codex/gpt-6.1-sol-282kcontext
LCM_EXTRACTION_MODEL=codex/gpt-6.1-sol-282kcontext
LCM_FRESH_TAIL_MAX_TOKENS=24000
LCM_MAX_ASSEMBLY_TOKENS=256000
PROFILE_DELEGATE_MAX_CONCURRENT=8
PROFILE_DELEGATE_MAX_ASYNC=2
PROFILE_DELEGATE_MAX_DEPTH=2
PROFILE_DELEGATE_DEFAULT_TIMEOUT_SECONDS=1200
PROFILE_DELEGATE_MAX_TIMEOUT_SECONDS=1800
```

The five LCM controls above are supported environment settings, not arbitrary
keys under YAML `lcm:`. That YAML section supports `context_threshold`; omitting
both it and the compatible compressor threshold falls back to the plugin's
0.35 threshold, not this baseline's 0.8. Check effective values after managed
scope resolution. Profile-delegate policy resolves defaults, then profile YAML,
then supported environment overrides; the env values above intentionally match
its YAML limits. Native `delegate_task` uses separate configuration and is not
changed by these variables.

Do not copy an entire dotenv to obtain this one setting. The native default cache
is `<profile-home>/.cache/hermes/node_search/index.json`; omit
`NODE_SEARCH_CACHE` to retain that isolation. Confirm that the selected vault
exists in the running deployment; do not silently broaden the root.

The prepared `shared-masterv2` baseline requires the patched image and its
individual read-only mount at `/opt/data/plugins/shared-masterv2`. Do not enable
it against old core or enable both v1 and v2. Existing mounted v1 source remains
owned by its current consumer until user recreation and separate retirement;
this recipe does not authorize deleting it. Source preparation is not live
activation: after authorized image preparation and user recreation, start fresh
sessions and verify complete prompt inclusion before claiming adoption.

V2 reads `get_default_hermes_root()/MASTER.md` once at each native render/freeze
boundary: `/opt/data/MASTER.md` in the standard container, or the common root
above `profiles/<name>` for custom roots. Cwd and profile-local MASTER files do
not change this source. There are no profile-name exclusions, including `empty`,
`workstation`, `ssr`, `ssr-*` and `ssr_*`; keep their independently configured
memory/history/tool restrictions. Link only the v2 implementation into each
named profile's plugins and select `shared-masterv2` in `plugins.enabled`.

The measured content is 16,416 stripped characters, 16,576 with native framing.
The native patch preserves the ordinary default of 4,000, permits at most 20,000
characters per opted-in section and 24,000 aggregate including framing. V2 uses
the native maximum, without its own cap/truncation or request middleware. Count
other enabled sections before activation. Missing, empty, unreadable or invalid
UTF-8 input warns without content leakage. Native limit or reserved-marker
failures omit the section with a warning, not truncation or request refusal.
Within the prompt's life native assembly reuses the frozen section; resume
retains the persisted snapshot. Native invalidation, such as compaction, rereads
MASTER and adopts edits. Routine manual MASTER reads are unnecessary; plugin
reload alone cannot install the core patch or refresh a persisted session.

Native project/gateway AGENTS stays separate and cwd-based. Keep
`prompt-load-bridge` disabled and preserve peer overrides and intentional
divergences. Every new profile must pass plugin doctor, discovery/import and
actual cache-lineage `llm_request` middleware and `post_api_request` hook
registration. Links/list entries alone do not prove registration; registration
does not prove cache hits or a successful fetch.

Curate complete individual skill directories with supporting files; retain
`.no-bundled-skills`, intentional divergences and `skills.external_dirs: []`.
Do not import a whole skill catalog, wipe an existing tree, or deduplicate by
`SKILL.md` alone. Skill restoration is separate from this publication procedure.

## 4. Authorize delegation and delivery separately

`profile-delegate` availability is **not authority**. Outgoing
`allowed_profiles` defaults empty. Add only explicitly authorized targets to
each caller; configuring inbound callers does not grant the target outgoing
authority. Inspect effective `PROFILE_DELEGATE_ALLOWED_PROFILES`, including
scoped dotenv overrides of YAML. Preserve limits, modes and explicit disables.
A saved allowlist is not proof of a completed delegated task.

Shared Discord routes live in the default gateway's `gateway.profile_routes`,
not in copied bot credentials. Preserve unrelated routes and private IDs; use
supported config editing and verify intended channel/thread selection and
unrelated-channel denial. Saved matcher checks do not prove live delivery.

Use native `SEARXNG_URL`, managed egress and the selected Camofox mode. Read
[the Camofox guide](../camofox.md); never add Chromium or other browser fallbacks.

## 5. Reload only the owner that changed

Verify installed `run_plugin_rewire.py` and real `on_plugin_loaded` handler
registration before relying on supported install/enable hot notifications.
Tools and prompt changes require a fresh session; discovery/rewiring alone does
not prove existing-session refresh. Process-cached environment may remain stale.

The default runner caches `gateway.profile_routes`. Route edits require a
controlled operator-host `sg-hermes-restart` and authorized route acceptance,
not a named restart or just a new chat. This drain-aware command restarts the
whole shared default process. Its optional named argument is only for a separate
running s6 service, not a multiplex profile. Confirm readiness afterward.

Existing profile dotenv edits require no image rebuild. New Talk/voice boot
provisioning may require container initialization; hot discovery cannot install
it. Compose environment/mount changes require validation and authorized Hermes
recreation. Live-mounted guide edits require no restart. Do not restart/rebuild
just to validate documentation.

A deliberate standalone profile uses `gateway.standalone: true`, its own service
and unique literal API port (inspect `sg-profile-ports`), internal-only listeners
and dedicated/blank messaging. `HERMES_AUTOSTART_PROFILES` seeds only missing s6
state; persisted state wins. New s6 slots require container initialization.
The old `sg-hermes-profile-new` workflow is standalone-only and rejects native
seeded dotenv; do not combine it with multiplex creation.

## 6. Accept only demonstrated capabilities

Record each check as verified, pending or intentionally disabled, without
publishing identifiers or secrets. Confirm native status and `served_profiles`,
intended parked/served state, effective instructions, skill set, eight BASIC
links/config enablements, registration, Reader backend, narrow NodeSearch root
and local cache, and effective delegation policy. Directory existence, enrollment
or command success alone is not readiness.

API acceptance is profile-scoped: missing/invalid keys return 401; unknown or
parked named profiles return 404, never default fallback. Edge mappings must be
literal allowlists, deny unknown paths, strip profile-selection headers and use
the selected profile's internal credential. A shared external edge key grants
authority over every exposed profile. API readiness does not prove bots.

When Talk is authorized, the multiplexer selects the shared base or named
`/p/<profile>` endpoint, overriding stale per-profile URLs. Missing or malformed
shared-listener selection must fail closed. Standalone Talk uses its own literal
port in `TALK_API_SERVER_URL`. Leave
`TALK_API_SERVER_KEY` unset when inheriting the profile's working API key; never
copy another profile's key. Verify effective endpoint, auth and `run_submission`
capability. `hermes talk check` requests no tools and can use the detached lane;
require `details.lane: api-server` for API-lane acceptance, then separately
validate an authorized delegated tool operation and spoken result. A detached
fallback is not API proof. Use measured bounded probe timeouts only after actual
failure evidence; do not treat timeout changes as new tool authority.
`TALK_API_SERVER_PROBE_TIMEOUT_S` defaults to 1.5 seconds; run submission and
polling use four times that value. A measured value of `5` gives 20-second run
requests, not a longer overall deadline. This revision has no API-only fallback
switch. Background watchers neither add host execution attachments nor merge
voice transcripts into text history.

Live messages, Reader fetches, inference, voice and delegation tests each need
their own authorization. Record unperformed checks as pending; static checks do
not establish live acceptance. Keep manual/stopped profiles in their intended
state throughout.

The native semantics above were reconciled against the
[matching upstream documentation at revision e8c97320](https://github.com/NousResearch/hermes-agent/tree/e8c97320ac8691d4de92af49f98459f9ef9ddb08/website/docs/user-guide):
configuration, memory, curator, skills, plugins, profiles, multi-profile gateways
and managed scope. Installed Gate patches and mounted plugin consumers govern
local differences. For example, installed native delegation defaults differ
from the documentation's concurrency prose, so the approved explicit limits
above avoid relying on that discrepancy. Reconcile this recipe with the actual
installed version before an upstream refresh; a saved key is not deployment or
capability evidence.
