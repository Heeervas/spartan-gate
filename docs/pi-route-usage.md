# Pi session usage through Spartan Route

The Pi usage extension adds a compact quota bar and cumulative session consumption
to the existing Spartan Route provider. It uses native Pi extension hooks and the
existing authenticated gateway. Pi itself is unchanged.

See [the Pi extension instructions](../infra/pi/README.md) for installation and
focused checks. Select a `spartan-route` Chat Completions model and restart Pi after
installing or updating the managed extension files and provider marker. A full
process restart also clears Node's cached helper imports; `/reload` alone can
retain an older `.mjs` helper after an update.

## Display and commands

The default status combines a compact two-window pool bar, session tokens and an
approximate normalized quota-unit estimate. The upper half of the bar represents
the five-hour window; the lower half represents the weekly window. A single known
window is displayed directly. Missing windows are unknown, not a full balance.

- `/spartan-usage` opens numeric totals, coverage and observation details.
- `/spartan-usage view pool` selects the default capacity-weighted pool.
- `/spartan-usage view accounts` displays compact account rows labeled by slot.
- `/spartan-usage view lease` displays the exact latest observed session binding.
- `/spartan-usage refresh` requests a due quota refresh through Route.

The view preference uses Pi's native agent directory. Pool membership includes
each configured account once when it has a routing-enabled slot. Temporarily
cooled accounts remain in the capacity denominator; this balance does not certify
availability for the selected model. x1 accounts have 100 units and x5 accounts
have 500 units. Unknown accounts and unavailable windows remain visible as coverage
gaps. A pool reset countdown refers to the next account reset, not a simultaneous
reset of the entire pool.

Lease scope reads the actual affinity recorded for tagged requests, including
explicit prompt-cache keys. It never selects or prolongs a lease. A global lease
is labeled global; disabled, expired and not-yet-observed bindings are unavailable.
After Route restart, a new tagged request is needed to establish the live binding.

## Persistent consumption

Route registers each native Pi session once, durably storing an immutable baseline
of its existing recorded usage. Each newly correlated Route attempt contributes
provider-reported usage to the session's cumulative ledger. Displayed consumption
is that baseline plus new Route observations. Later Pi history totals are never
added again.

The baseline follows Pi's persisted history, including entries outside the active
branch and recorded compaction, summary, warming and tool-result usage. A native
fork gets a new origin with its copied recorded history; the parent's current
Route aggregate is not imported. Resume and reload recover the same origin.
Subagent sessions remain independent; no custom parent-child aggregation is added.

The ledger survives normal routing-history retention and Route restart. Cumulative
session rows have no automatic expiry. Deleting/replacing the Route database loses
that accounting; retain the existing database backup procedures during upgrades.

Token totals distinguish inclusive input, cached input and output. Cached input is
a subset of input; total tokens are input plus output. The quota estimate uses
only attributed Codex tokens and Route's current weekly pool calibration. Estimates
are marked approximate and can change when calibration changes. Mixed-provider or
historically unclassified usage is disclosed separately, not priced as Codex usage.

## Freshness and coverage

Interactive online Pi polls passive status about every 30 seconds and asks Route
for due quota refreshes about every minute. Route coalesces upstream checks across
clients and applies backoff on failure. Reset-credit redemption is never invoked.
Offline and headless sessions do not start background polling timers; headless
requests can still register and report their own session usage.

Quota observations older than two minutes are marked stale, and those older than
one hour are unavailable. Expired windows remain unknown until refreshed. The last
committed session total remains available with a freshness warning during outages.

This is observed consumption, not a billing ledger. Failed calls, retries and
aborted streams contribute when the provider exposes usage. Missing usage, failed
writes and a crash before commitment can leave gaps. Historical native records
also omit some failed summaries and attempts; inherited history retains those
limits. Tracking failures do not deny inference. Native-recorded fallback is
labeled separately and is never added to overlapping Route totals.

V1 covers the existing Chat Completions integration. Native Responses and calls
outside the instrumented Route provider are not included in new Route accounting.

## Interfaces and security

- `POST /api/pi/sessions` registers or recovers an immutable session origin.
- `GET /api/pi/status?sessionId=...` reads sanitized snapshots and compact totals.
- `POST /api/pi/usage/refresh` requests a server-due-gated shared refresh.

All three interfaces use Route's existing authentication boundary. Pi resolves
existing Caddy credentials locally; Caddy injects the internal bearer. Session and
origin identifiers are correlation values, not independent authorization tokens.

The actual Route provider supplies a marker header. The native awaited header
hook adds session and origin identifiers only after registration succeeds. Route
consumes those values without sending them upstream. Existing explicit cache keys
and provider transport behavior are preserved. Marker-only untracked requests may
continue inference; malformed or mismatched supplied identifier pairs are rejected.

Status reads do not refresh credentials, call upstream, scan retained prompts or
change lease state. Responses exclude credential paths, emails, raw cache keys,
prompt/context content and unrelated leases. The local gateway helper bounds
input/output, requires HTTPS, refuses redirects and does not print credentials
from usage actions.

## Rollback

Remove the managed usage extension and its helper directory, remove only its
provider marker, and reload Pi. Preserve the existing provider and cache extension.
The cumulative database tables may remain; they do not alter inference selection.
A Route image rollback can retain those additive tables. Do not restore an older
database over new live usage merely to remove the extension.
