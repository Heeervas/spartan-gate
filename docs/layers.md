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
| L3 | L2 + ClawRoute compatibility alias | `gated` | Same as L2 | Gated runtime with internal LLM router |
| L4 | Full public topology | `full` | Same as L2/L3 | Current full Spartan Gate stack |

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

`L3` and `L4` always include ClawRoute. The installer creates
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
```

Hermes auto-detects SearXNG from `SEARXNG_URL` when no explicit web backend is
configured. Operators can also set this in Hermes config:

```yaml
web:
  search_backend: "searxng"
```

SearXNG is search-only. `web_extract` needs a separate Hermes-supported extract
backend, or a Spartan Gate Reader flow when bounded GET-only page text is the
right contract.

Existing user-owned plugin directories such as
`/opt/data/plugins/web-search-plus` are not deleted automatically. Remove them
manually only after confirming the native tool path works for that data home.

## Layer Rules

- Hermes must not join the external Docker network in gated/full layers.
- L0 and L1 are convenience layers, not secret-sharing modes; real tokens still
  belong in ignored private env files.
- ClawRoute owns provider credentials and OpenAI-compatible routing when the
  addon is enabled.
- Browserless and Camofox are separate browser modes. Do not claim the inactive
  browser service is available.
- Private domains, host paths, browser profiles, Codex auth, certificates, and
  account inventories stay under `private/`.
- Public edge bindings stay on localhost. Tailscale or other private exposure
  belongs in private overrides.

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

