# Managed search defaults and browser denial diagnosis — 27 October 2026

Status: source correction installed after separate operator approval; tool-access
activated. The running Pi process still requires replacement to load helpers.
Active policy is unchanged.
This host-only operator note is not a published Hermes runtime guide.

## Confirmed causes

- A schema-shaped search call supplied `provider:"auto"`, `proxy:""` and
  `queries:[]` alongside a valid `query`. The strict guard rejected the first
  two neutral defaults. Independently, native pi-web-access 0.44.0 gives an
  empty queries array precedence over query, producing no-query rejection.
- The reported Wikipedia create reached Camofox and failed with the bounded
  diagnostic marker `NS_ERROR_PROXY_FORBIDDEN`: backend HTTP 403 in 233 ms.
  This was policy denial, not expiry of the 29/30-second chain. The adapter
  collapsed this into unknown mutation outcome and retired the workflow;
  the subsequent stale-workflow response followed that retirement.

## Source correction

Validated neutral search defaults now use the existing strict SearXNG route;
nonempty proxies, foreign/mixed providers, content fetching and summary workflows
remain blocked. A usable single query is no longer shadowed by an empty list.
Confirmed backend 403 plus structured proxy_forbidden becomes egress_denied;
raw backend messages remain hidden. Handle retirement, exact-ID ownership,
no automatic remint/replay, and all timeouts remain unchanged.

## Operational boundary

The source-repair phase performed no repeat Wikipedia navigation, service restart,
whitelist modification, profile inspection, active Pi install or W6 activation.
The separately approved activation below supersedes only the installation status.
The source change does not make a forbidden destination reachable. An operator
must separately authorize a narrowly scoped, preferably temporary domain grant
through the existing whitelist helper if Wikipedia is wanted. Do not add broad
wildcards, adopt orphan tabs, reset sessions or restart healthy neighbors.
A later explicit canary needs deliberate owned-workflow recovery and updated
client/server bytes; never retry the previous unknown-result mutation blindly.

## Approved activation

The operator explicitly approved installation/activation and tests against already
permitted destinations. Installed the native Pi entry/helpers with the existing
configuration owner; only config.mjs and errors.mjs changed. Existing settings,
credentials and local workflow records remained unchanged; protected backups are
outside discovery. Rebuilt only tool-access, retaining its previous image for
rollback. Its built image passed all 29 Python tests with no network, no profiles,
read-only root, dropped capabilities and a temporary scratch filesystem.
Recreated only tool-access through the main-stack helper with no dependencies.
All installed Pi program bytes and active adapter Python files match source
commit 300d366. Other pre-existing container IDs, images and start times are
unchanged. Live unauthenticated workflow admission returns 401; Reader succeeds
through the managed ingress against an already-permitted example destination.
No browser canary or Wikipedia replay was performed; no whitelist was changed.

Pi helper imports can remain cached after /reload. Fully restart only the selected
Pi process. Adapter recreation invalidated in-memory workflow capabilities; old
saved records are not silently replaced. For a new browser canary use a deliberate
fresh native session, not a resumed session with stale workflow authority. The
previous ambiguous navigation must not be retried as an automatic recovery step.
This scoped activation does not activate the rest of W6.

## Verification scope

Regression fixtures use synthetic loopback backends and native Pi with a synthetic
model, not real public browsing. They cover the exact search defaults, provider
and proxy refusal, sanitized known denial, retained stale authority, no replay,
and existing transport cancellation/timeout behavior. Live diagnosis was limited
to fixed error markers and response status/duration from existing backend logs;
no page, credential or profile contents were collected.
