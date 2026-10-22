# Spartan Gate layers

Layers select supported Compose shapes; they are not separate products.

| Layer | Services | Hermes mode |
| --- | --- | --- |
| L0 | Hermes | `free` |
| L1 | Hermes + Camofox/noVNC | `free` |
| L2 | L1 + Tinyproxy, DNS and authenticated Caddy edge | `gated` |
| L3 | L2 + internal Spartan Route, reached through Caddy | `gated` |
| L4 | Full topology: Spartan Route, Reader, SearXNG and gate services | `full` |

L0/L1 are convenience layers, not network security boundaries. Workloads remain
unprivileged and may install user-level packages in writable data/workspace paths.
System packages remain image/operator work. Gated/full Hermes never joins an
external network. L3 Route also stays internal and uses the whitelist proxy.
Public edge bindings remain localhost; private exposure belongs in ignored overrides.

## Install and select

```sh
scripts/install.sh --tier L3
source scripts/aliases.sh
sg-tier show
sg-tier set L2 --hermes gated
sg-tier doctor
sg-up
```

The installer prepares an unpublished environment candidate and renders Compose
before publishing the selection. Configuration publication rolls back on write or
permission failure. Setup and Docker application are subsequent operations, not
an atomic Docker transaction. Inspect failures before retrying; do not infer that
a failed start reverted already-created containers.

Selection uses `SPARTAN_TIER`, empty `SPARTAN_ADDONS`, and
`SPARTAN_HERMES_MODE` in `private/env/local.env`. Project name stays `spartan-gate`.
L0/L1/L2 use `infra/compose/tiers/compose.l0.yml`, `compose.l1.yml`, or
`compose.l2.yml`; L3 combines L2 with `compose.l3.yml`; L4 uses
`infra/compose/compose.yml` and the private override when present.

## Retired source and migration

Browserless and the legacy TypeScript ClawRoute application are retired. The
`--with clawroute` option and persisted `SPARTAN_ADDONS=clawroute` fail before
selection writes. Review the old selection, clear the addon explicitly and select
L3/L4 for Spartan Route. No lower-tier routing addon replaces it. Shared
`config/clawroute`, `CLAWROUTE_*` Route configuration and `clawroute/auto` model
aliases remain supported compatibility names; do not delete or rename them.
Legacy ClawRoute databases are not automatically converted or removed.

Camofox is the supported persistent browser. Browserless debugger/profile seeding,
shared CDP broker, Chrome DevTools CDP and host-Chrome relay workflows are retired;
there is no claimed Camofox equivalent for every CDP capability. Meet retains
Camofox caption transcription, not realtime/audio. Configure `CAMOFOX_URL` and use
`sg-hermes-meet-join <meet-id-or-url> [duration]`.

Source retirement never reads, migrates or deletes legacy Browserless profiles.
Keep them untouched; any optional offline backup/export or deletion needs a
separate operator decision. Existing private Compose overrides must be reviewed
for removed services, `/profiles` mounts, debugger ports and stale image/code
mounts before separately authorized activation. Do not use remove-orphans on a
live installation until that migration is reviewed.

## Policy and search

L2/L3 mount the same public and private whitelist fragments into proxy, DNS and
Hermes. The installer records `SPARTAN_PRIVATE_WHITELIST_PATH`; custom selections
must point it at the same file edited by `sg-whitelist-domain`.

L4 provides native Hermes SearXNG through `SEARXNG_URL=http://searxng:8080`.
Reader is a separate bounded GET-only page retrieval service, not a replacement
for native extraction preflight. Existing user plugin directories are not deleted.
Real credentials, profiles, private domains and personal paths stay ignored.

## Preview ports and edge sessions

`sg-add-port` prepares both listener and mapping candidates and renders Compose
before publication; it does not restart services. Apply an accepted mapping with
`sg up -d --no-deps --force-recreate caddy` (a restart cannot add a Docker port).

Edge cookies are HMAC-signed, audience-bound and server-checked on every HTTP
request/WebSocket upgrade by the bounded internal `edge-sessions` verifier.
Sessions expire exactly one hour after minting; future, malformed, duplicated,
wrong-audience and legacy static cookies are denied. Requests do not refresh the
TTL. Caddy mints only after successful Basic Auth; invalid WebSockets return 401
without a new challenge. Verifier failure denies access. Only Caddy and the
verifier share the internal `spartan_edge_auth` network, with no published port.
`CADDY_SESSION_SERVICE_KEY` is an independent 64-hex internal credential; it is
never placed in browser cookies. Preserve all three independent keys in private
env; rotating the audience key revokes its sessions. Existing private Caddy
listeners must be regenerated/reviewed before activation: static-cookie listeners
are not upgraded merely by recreating a container.
