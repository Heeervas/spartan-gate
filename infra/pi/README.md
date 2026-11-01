# Pi integrations

## Prepared host/Hermes parity and subagent migration

[`common-baseline.json`](common-baseline.json) owns approved nonsecret shared
behavior. [`prepare_parity.py`](prepare_parity.py) materializes/checks native
settings and the **separate** `extensions/subagent/config.json`, preserving foreign
fields and native disables. It writes candidates only, outside discovery; no auth,
trust, models or sessions are copied. Container JSON files are generated examples.
See [`docs/hermes-pi-jobs.md`](../../docs/hermes-pi-jobs.md) for full preparation,
private-path binding, cutover/rollback and live acceptance gates.

Prepared dependency: nicobailon/pi-subagents v0.84.0, commit
`0398b9e8d6f03dc6054dbf13dfe851c7a20bff3a`, complete production dependencies,
actual Pi 1.4 SDK and Node >=22.19. A small native entry binds temp state to each
owner's agent directory; the shared program checkout remains read-only in Hermes.
Only async local native children with explicit coherent Route provider/cache/usage
and reviewed fast-extension paths are certified. The fast extension snapshots
parent effective CLI/live Fast, Ultrafast or off into a nonsecret inherited scalar;
children apply it within unchanged model/tier eligibility, never upstream `fast:true`.
Hermes uses the same narrow program-file mount, never host mode/settings/state. Old pi-subagent-local code/sessions and disabled
pi-subagents-lite remain for rollback. Fresh-process adoption is separately
reviewed: do not hot-reload a running parent across tool schemas.

New calls use `agent`/`task`, `model:"provider/id:high"` (reasoning chosen separately
then encoded as a suffix), `async:true`, `options.context:"fresh"`, and returned
run IDs for `action:"resume"`. Dispatch `thinking` is ignored; old `calls`,
`prompt`, `initialContext` and logical `session` handles do not apply. With workflow
scripts disabled, parallel work uses one `tasks` array. Automatic resumption,
schedules, missions and unselected upstream agents are off. Host-only resources
stay host-only; parity never means copying the host home into Hermes.

```sh
python3 -m unittest discover -s infra/pi -p test_prepare_parity.py -v
PI_CODING_AGENT_DIR=/selected/complete/pi-package \
PI_CODEX_FAST_SOURCE=/selected/pi-codex-fast/extensions/codex-fast.ts \
node --test infra/pi/test_parity_runtime.mjs
```

The native fixture uses synthetic HTTPS, credentials and catalog only. It verifies
actual loading, separate replay ownership, detached continuation, parent replay
preservation and missing-child-provider refusal, not live model quality, TUI or
container deployment. The older install recipes below describe individual Route
extensions, not permission to activate the migration.

For native Responses and the standard-client subset, see
[`docs/pi-native-responses.md`](../../docs/pi-native-responses.md). New native
sessions use server-owned opaque replay receipts without a binding RPC. The
provider's `session.mjs` helper owns provisional concurrency reservations and the
post-validation durable realm lock, including manager-owned append-failure retry/
reload safety; install it with the other provider helpers. Native ownership is
captured from the guarded per-session extension context and revalidated before
reservation/commit, independently of the unchanged routing/cache ID. This lets
native branch summaries and manual/automatic compaction use fresh routing IDs
without depending on navigator commands. Deliberately shared SDK model runtimes
across independent sessions are outside this integration's supported lifecycle.
Legacy raw saved histories use a lazy cached binding and are not rewritten. Both Pi 1.3 and 1.4
are validated with isolated configuration; restart fully after installation.
No active installation or Route deployment is implied by source tests. Codex
receipts authenticate ciphertext/realm; their last issuer is an advisory scheduling
preference, not an account pin. Mixed-issuer continuation can fall back to the
ordinary eligible pool without changing Pi production code or stored histories.
Cross-account completion is not proof of lossless hidden-reasoning reuse.

## Pi session cache key

`extensions/spartan-route-cache.ts` supplies an explicit prompt-cache key for
ordinary Pi requests whose selected model uses provider `spartan-route` and API
`openai-completions`. It targets Pi's `before_provider_request` extension hook;
the hook contract was inspected in `@earendil-works/pi-coding-agent` 1.3.0.

For an object payload with a missing or blank `prompt_cache_key`, the extension
returns a shallow copy containing `prompt_cache_key: "pi:<session-id>"`, using
the current Pi session ID. It preserves an existing nonblank key, leaves the
original payload untouched, and skips invalid payloads or unavailable session
IDs. New sessions and forks receive their own keys; resuming the same session
keeps its ID. A first request after switching from a different key may be cold.

## Install

With Pi installed, run from the repository root:

```sh
mkdir -p ~/.pi/agent/extensions
cp infra/pi/extensions/spartan-route-cache.ts ~/.pi/agent/extensions/
```

Start Pi, or run `/reload` in an existing Pi session. Select the `spartan-route`
provider with an `openai-completions` model. Copy the source again and reload
after future extension updates. To remove it, remove the copied file and reload.

## Check

The focused regression suite requires an installed Pi package and its bundled
dependencies. For a global npm installation:

```sh
PI_CODING_AGENT_DIR="$(npm root -g)/@earendil-works/pi-coding-agent" node --test infra/pi/test_spartan_route_cache.mjs
```

For another installation layout, set `PI_CODING_AGENT_DIR` to the installed
`@earendil-works/pi-coding-agent` package directory. Local checks establish the
extension and payload contract; they do not establish live provider cache hits.

## Limits

An explicit key helps identify the session to Route and the provider. Actual
provider prompt caching still depends on compatible prompt prefixes, account
routing and provider cache availability. A stable key does not guarantee a hit,
preserve cache residency or establish a fixed saving. Costs remain unknown
until actual requests and their usage are measured.

The extension adds no warming requests, timers or TTL settings. Pi's separate
cache-warming feature may make extra requests if enabled; those requests can
have a cost. The extension supplies the key independently of Pi's cache-retention
setting, including `none`; it does not change the retention setting itself.

Pi 1.3.0 exposes the selected model through the hook context, without the actual
request model or its request options. This extension therefore covers ordinary
requests for the selected provider/API. It cannot reliably distinguish calls
redirected by other extensions or virtual models. Nested calls must pass through
Pi's hook to receive this behavior; direct provider calls outside that hook are
not covered. Another payload handler can also change the final result.

## Native Route usage display (Pi 1.3)

`extensions/spartan-route-usage.ts` adds native footer status and optional account
rows without replacing Pi's footer or changing its cache key or transport.
The compact bar has five-hour remaining quota on its upper half and weekly
remaining quota on its lower half. Reset countdowns follow it, then cumulative
session tokens and `~` calibrated quota units. Units are an estimate, never an
exact measured debit. `/spartan-usage` shows numeric windows and coverage;
`/spartan-usage view pool|accounts|lease` changes the global view preference.
`/spartan-usage refresh` requests a server-due-gated upstream refresh.

Install the entry and its helper directory together in Pi's native agent
extension directory (`getAgentDir()/extensions`, normally `~/.pi/agent/extensions`):

```sh
mkdir -p ~/.pi/agent/extensions/spartan-route-usage
cp infra/pi/extensions/spartan-route-usage.ts ~/.pi/agent/extensions/
cp infra/pi/extensions/spartan-route-usage/*.mjs infra/pi/extensions/spartan-route-usage/gateway.py ~/.pi/agent/extensions/spartan-route-usage/
```

For a customized Pi agent directory, replace `~/.pi/agent` with the configured
agent directory. Python 3 is required. Existing Route discovery/provider code
must add the constant marker to its actual provider headers alongside its
existing authorization header:

```ts
headers: { Authorization: existingAuthorization, 'X-Spartan-Pi-Client': '1' },
```

Keep the existing authorization expression. The marker must be configured in
the provider's assembled transport headers; merely testing the selected model
is insufficient for redirected/virtual calls. The extension adds session/origin
headers only to marker-bearing requests, after awaited server registration.
Restart the Pi process after installing or updating; `/reload` can retain cached
`.mjs` helper imports after an update. Remove the entry and helper directory and
restart to uninstall. The separate cache extension can remain installed.

The versioned `gateway.py` preserves `header`, `catalog`, and `check` commands
from the local discovery helper and adds JSON-only `status`, `register`, and
`refresh` actions, plus `binding` for lazy legacy Responses migration. Binding
failures return only allowlisted sanitized error codes; arbitrary server messages
and parser/credential errors are never echoed. Usage actions never print authorization. It reads the existing
`$XDG_DATA_HOME/pi-spartan-route/config.json` (default
`~/.local/share/pi-spartan-route/config.json`), or `PI_SPARTAN_ROUTE_CONFIG`.
The existing `baseUrl` must be credential-free HTTPS; usage calls use `/api/pi/*`
on that same origin. The existing `credentialsFile` remains the only credential
source. No shell sourcing, redirects, OAuth reads, or additional auth store are
used. Requests have a 10-second network timeout, an 11-second process deadline,
and a 256 KiB response bound. Do not check private configuration into Git.

The server owns the immutable origin and baseline. Resume recovers that origin
before reading native history; a fork has a new native ID and registers its
copied history. The reducer includes every native entry, including off-branch
assistant usage, compaction, branch summaries, usage notices and tool results.
Pi's uncached input, cache reads and cache writes become Route's inclusive
input. Reasoning is already part of output. Historical quota attribution uses
only positively identified Codex calls; unattributed summaries remain unknown.
No custom session records or local origin/baseline sidecars are written.
A pending tracking gap is recorded as an empty per-session UUID marker under
`getAgentDir()/spartan-route-usage-gaps/` before an uncorrelated request proceeds.
It survives reload and is removed only when the server acknowledges the
monotonic incomplete-coverage flag. It contains no credentials or origin data.
If the local marker cannot be written, inference remains available and the UI
still warns of incomplete tracking; a crash before recovery may lose that gap.

Interactive sessions with the Route provider selected poll stored status every
30 seconds and independently request due-gated quota refresh every 60–70
seconds. Transient outages retain last totals and use one retry timer with
exponential backoff from 60 seconds to a maximum of 300 seconds. Headless sessions
have no polling timers; headless requests still attempt origin registration.
`--offline` / `PI_OFFLINE=1|true|yes` also disables startup usage network
operations and all polling; a later explicit Route provider request still
attempts lazy registration. Other selected providers have no usage polling or
Route status. The actual assembled marker remains the transport boundary.
When full-pool percentages are unknown, valid reported account windows can
provide a capacity-weighted known subset. The footer labels this `partial`; it
does not represent the full pool. `/spartan-usage` reports per-window known and
unknown source counts and observed capacity, while full-pool coverage remains
unavailable. Unresolved slots count as sources, since they may share an account.
Missing, expired, malformed or invalid-capacity sources never enter the subset.
If no valid sources remain, quota stays unavailable. Positive quota estimates
below 0.01 units keep three decimals; values below 0.001 display `<0.001u`.

Single known windows show their percentage explicitly; absent or expired windows
show unavailable. Global lease scope and nonready account states remain visible.
Quota older than 120 seconds is stale, and older than one hour is unavailable.
The last session total remains visible when offline. If telemetry fails,
inference continues without correlation headers, the UI marks tracking
incomplete, and recovery persists that gap on the server. Direct provider calls
that bypass Pi's header hook are outside this tracking boundary. Native
subagents use their own session IDs.

Focused synthetic checks (no provider charge):

```sh
PI_CODING_AGENT_DIR="$(npm root -g)/@earendil-works/pi-coding-agent" node --test infra/pi/test_spartan_route_cache.mjs infra/pi/test_spartan_route_usage.mjs
python3 -m unittest discover -s infra/pi -p 'test_*.py'
```
