# Spartan Gate Layers

Layers are the public install shapes for Spartan Gate. They select Compose
files, optional addons, and Hermes runtime mode while preserving one stable
Compose project name and the configured data paths.

## Layer Matrix

| Layer | Services | Hermes mode | Package installs | Typical use |
| --- | --- | --- | --- | --- |
| L0 | Hermes only | `free` | User-level Python and Node installs allowed | Fast portable Hermes baseline |
| L1 | Hermes + Camofox/noVNC | `free` | User-level Python and Node installs allowed | Portable Hermes with persistent browser login |
| L2 | L1 + Tinyproxy, DNS, and Caddy edge | `gated` | User-level installs still allowed through the gate | Local security boundary without ClawRoute |
| L3 | L2 + ClawRoute compatibility alias | `gated` | Same as L2 | Legacy gated runtime with ClawRoute |
| L4 | Full public topology with Spartan Route | `full` | Same as L2/L3 | Current full Spartan Gate stack |

`L0` and `L1` are intentionally not root shells. Hermes workloads are
unprivileged, but they can install Python and Node packages into `/opt/data` or
the workspace through the user-level paths exposed by the image. System package
installs are image/operator work, not normal runtime behavior.

## Installer

Fresh install:

```sh
scripts/install.sh --tier L0
```

Choose a larger layer:

```sh
scripts/install.sh --tier L2
```

Enable ClawRoute below L3 when you want provider auth and routing to stay in
ClawRoute without taking the rest of the full stack:

```sh
scripts/install.sh --tier L0 --with clawroute --hermes free
scripts/install.sh --tier L1 --with clawroute --hermes free
scripts/install.sh --tier L2 --with clawroute --hermes gated
```

`L3` always includes ClawRoute for the legacy tier contract. `L4` uses Spartan
Route as the base router. The installer creates
`private/env/local.env`, generates local secrets, runs `hermes setup`, starts
the selected layer, and recreates Hermes once setup completes.

## Switching Layers

Load helpers:

```sh
source scripts/aliases.sh
```

Select and start:

```sh
sg-tier list
sg-tier set L1 --with clawroute --hermes free
sg-tier show
sg-up
```

Operational commands:

```sh
sg-tier apply     # equivalent to sg-up
sg-tier down
sg-tier setup     # rerun hermes setup for the selected layer
sg-tier doctor    # validate selected Compose config
sg-status
```

In the full L4 stack, `sg up -d` starts Spartan Route as the normal router.
`sg route ...` remains a compatibility alias for older operator muscle memory.

Layer selection is stored in `private/env/local.env`:

- `SPARTAN_TIER`: `L0`, `L1`, `L2`, `L3`, or `L4`.
- `SPARTAN_ADDONS`: currently `clawroute` or empty.
- `SPARTAN_HERMES_MODE`: `free`, `gated`, or `full`.

The Compose project name remains `spartan-gate`, so layer changes do not create
separate stacks or move data paths by themselves. Use fresh private data paths
for new installs, and only reuse old paths when you are intentionally migrating.

## Native Web Search

Hermes now supports SearXNG as a native `web_search` backend through
`SEARXNG_URL`. Spartan Gate therefore no longer bundles the old
`hermes-web-search-plus` plugin for fresh Hermes homes.

In the full L4 topology, Hermes receives:

```env
SEARXNG_URL=http://searxng:8080
SEARXNG_INSTANCE_URL=http://searxng:8080
READER_PROXY_URL=http://reader:3000
```

Hermes auto-detects SearXNG from `SEARXNG_URL` when no explicit web backend is
configured. Operators can also set this in Hermes config:

```yaml
web:
  search_backend: "searxng"
```

SearXNG is search-only. Reader is exposed to Hermes as the internal
`READER_PROXY_URL` service for direct bounded GET-only page text retrieval.
It is not mounted as the default native `web_extract` backend because Hermes'
native wrapper performs its own URL safety preflight before provider dispatch.
That preflight can block domains before Reader receives the URL. Use direct
Reader calls or the Reader skill/instruction path when Reader should own the
DNS, redirect, and connection checks.

Existing user-owned plugin directories such as
`/opt/data/plugins/web-search-plus` are not deleted automatically. Remove them
manually only after confirming the native tool path works for that data home.

## Layer Rules

- Hermes must not join the external Docker network in gated/full layers.
- L0 and L1 are convenience layers, not secret-sharing modes; real tokens still
  belong in ignored private env files.
- Spartan Route owns provider credentials and OpenAI-compatible routing in L4.
  ClawRoute owns that boundary only when the legacy addon/tier is enabled.
- Browserless and Camofox are separate browser modes. Do not claim the inactive
  browser service is available.
- Private domains, host paths, browser profiles, Codex auth, certificates, and
  account inventories stay under `private/`.
- Public edge bindings stay on localhost. Tailscale or other private exposure
  belongs in private overrides.

Resource and privilege contracts do not weaken on smaller tiers. Every
rendered service has memory and PID ceilings; L0–L3 Hermes uses the same
read-only root, capability allowlist, no-new-privileges, `/tmp`/`/run`, and
2 GiB/512-PID ceiling as L4. Browser services use 512 PIDs, routers 256,
search/proxy/Caddy 128, and DNS/Reader 64. Spartan Route and ClawRoute run
non-root with only their data mount writable. Caddy is non-root and retains
only the upstream binary's required `NET_BIND_SERVICE` capability.

## Compose Mapping

| Selection | Compose files |
| --- | --- |
| L0 | `infra/compose/tiers/compose.l0.yml` |
| L0 + ClawRoute | L0 + `infra/compose/tiers/compose.clawroute.yml` |
| L1 | `infra/compose/tiers/compose.l1.yml` |
| L1 + ClawRoute | L1 + `infra/compose/tiers/compose.clawroute.yml` |
| L2 | `infra/compose/tiers/compose.l2.yml` |
| L2 + ClawRoute | L2 + `infra/compose/tiers/compose.l3.yml` |
| L3 | L2 + `infra/compose/tiers/compose.l3.yml` |
| L4 | `infra/compose/compose.yml` plus `private/compose.local.yml` when present |

Validate a selected layer without starting services:

```sh
sg-tier doctor
```
