# Managed search defaults and browser denial diagnosis — 27 October 2026

Status: source correction, not installed/deployed; active policy change pending.
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

No repeat Wikipedia navigation, service restart, whitelist modification, profile
inspection, active Pi install or W6 activation was performed for this repair.
The source change does not make a forbidden destination reachable. An operator
must separately authorize a narrowly scoped, preferably temporary domain grant
through the existing whitelist helper if Wikipedia is wanted. Do not add broad
wildcards, adopt orphan tabs, reset sessions or restart healthy neighbors.
A later explicit canary needs deliberate owned-workflow recovery and updated
client/server bytes; never retry the previous unknown-result mutation blindly.

## Verification scope

Regression fixtures use synthetic loopback backends and native Pi with a synthetic
model, not real public browsing. They cover the exact search defaults, provider
and proxy refusal, sanitized known denial, retained stale authority, no replay,
and existing transport cancellation/timeout behavior. Live diagnosis was limited
to fixed error markers and response status/duration from existing backend logs;
no page, credential or profile contents were collected.
