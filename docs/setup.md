# Setup

This path creates a fresh Spartan Gate instance. It does not reuse old Hermes
data unless you explicitly point `private/compose.local.yml` at old paths.

Public setup docs intentionally avoid machine-specific values. Put personal paths, exact ports, profile names, Tailscale addresses, and migration notes in `private/docs/`.

## Requirements

- Docker with the Compose plugin.
- Git, `curl`, and basic shell tools.
- Node.js 22 or newer for ClawRoute development.
- At least one LLM provider credential, or a private Codex/Ollama setup configured outside the public files.

On Debian, Ubuntu, or Raspberry Pi OS, the shortest development bootstrap is:

```sh
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
sudo docker version
sudo docker compose version
```

Use Docker's official repository install flow instead of the convenience script
for production hosts: <https://docs.docker.com/engine/install/>.

After installation, log out and back in, or run `newgrp docker`, so `docker`
works without `sudo`.

## Clone And Install A Tier

```sh
git clone <repo-url> spartan-gate
cd spartan-gate
scripts/install.sh --tier L0
```

Use the smallest tier that fits the machine:

| Tier | Services |
| --- | --- |
| L0 | Hermes only, free user-level Python/Node package installs |
| L1 | Hermes + Camofox/noVNC, free user-level Python/Node package installs |
| L2 | L1 + proxy, DNS, and Caddy edge |
| L3 | L2 + ClawRoute internal LLM routing compatibility alias |
| L4 | Full Spartan Gate topology |

The installer creates ignored `private/env/local.env`, generates local secrets,
runs `hermes setup`, starts the selected tier, and recreates Hermes once setup
has completed. It does not print generated secrets.

ClawRoute can be enabled independently on lower tiers without moving provider
auth into Hermes:

```sh
scripts/install.sh --tier L1 --with clawroute
```

For the complete layer matrix, addon behavior, and switching commands, see
[layers.md](layers.md).

## Manual Validation

```sh
cp .env.example .env
scripts/doctor.sh
docker compose -f infra/compose/compose.yml --env-file .env config
```

`doctor` validates the local `.env` instead of rejecting it. It fails early if Docker is unavailable, Compose is invalid, the configured subnet overlaps another Docker network, a published port is already occupied, or private placeholders/data paths are not ready.

If you already run other local stacks, `doctor` checks whether anything else is listening on the resolved published ports. If it reports a port conflict, pick a different value in `.env` or `private/env/local.env` before retrying.

The default Caddy password hash in `.env.example` is only a placeholder. The public edge is HTTP and bound to `127.0.0.1` only. Add a private Tailscale bind through `private/compose.local.yml`.

Hermes data defaults to `runtime/hermes/` inside the repo. Change the `- ../../runtime/hermes:/opt/data` line in `infra/compose/compose.yml` if you want a different public data path.

## Private Local Config

```sh
cp -R private.example private
mv private/env/local.env.example private/env/local.env
mkdir -p private/caddy.local.d
```

Edit `private/env/local.env` and replace placeholders with local tokens, ports, and private data paths. For another user, the file can still be named `local.env`, or they can choose their own path and export `SPARTAN_ENV_FILE`.

The private example already shifts the common conflict points away from the public defaults. Keep the alternate ports and subnet from `private.example/env/local.env.example` unless you have a reason to choose something else.

Use isolated data paths for a fresh install. Do not point
`SPARTAN_HERMES_DATA_PATH`, `SPARTAN_CLAWROUTE_DATA_PATH`,
`SPARTAN_GOGCLI_DATA_PATH`, or `SPARTAN_CAMOFOX_DATA_PATH` at an old stack
unless you are intentionally migrating.

For an intentional cutover to existing data paths, stop the old stack first and
set `SPARTAN_ALLOW_EXISTING_STACK_PATHS=true` in `private/env/local.env`. That
flag only allows `doctor` to continue; it does not copy, delete, or modify paths
by itself.

Document any local choices in `private/docs/`, not in public docs.

Private outbound domains live in `private/outbound-proxy/whitelist.private.txt`.
The public whitelist is only a generic baseline. Add local domains with:

```sh
source scripts/aliases.sh
sg-whitelist-domain docs.example.com      # permanent
sg-whitelist-domain 15d docs.example.com  # temporary
```

Temporary entries support `m`, `h`, and `d`. Expired entries leave the runtime
whitelist on the next proxy/DNS recreate or the daily `02:00` Europe/Madrid
refresh window.

Validate the private config:

```sh
scripts/doctor.sh
docker compose \
  -f infra/compose/compose.yml \
  -f private/compose.local.yml \
  --env-file private/env/local.env \
  config
```

Use `private/compose.local.yml` for local mounts/Tailscale binds and `private/env/local.env` for real tokens. `private/` is ignored by Git.

## Private HTTPS

The public edge is HTTP-only and localhost-bound. For a private HTTPS edge, use manual certificates from ignored private files:

```sh
mkdir -p private/caddy
cp private.example/caddy/Caddyfile.https.example private/caddy/Caddyfile.https
```

In `private/env/local.env`, set a DNS name covered by the cert and the cert directory to mount:

```env
TAILSCALE_HOST=machine-name.tailnet.ts.net
SPARTAN_EDGE_SCHEME=https
SPARTAN_CADDY_CERTS_PATH=/absolute/path/to/certs
SPARTAN_CADDY_TLS_CERT_FILE=/certs/cert.pem
SPARTAN_CADDY_TLS_KEY_FILE=/certs/key.pem
```

Then mount the HTTPS Caddyfile and certs from `private/compose.local.yml`. Use the DNS name in the browser. A Tailscale certificate for `machine.tailnet.ts.net` will not validate when opened as `https://100.x.y.z:port`.

## First Start

Start or switch tiers:

```sh
source scripts/aliases.sh
sg-tier list
sg-tier set L2
# Optional addon shape: sg-tier set L1 --with clawroute --hermes free
sg-tier show
sg-up
sg-status
```

`sg-up` starts the selected tier. `sg-tier setup` runs `hermes setup` again for
the selected tier when needed. `sg-tier apply` is equivalent to `sg-tier up`.
Layer changes preserve the stable `spartan-gate` Compose project name and the
configured data paths. L4 remains the full public Compose topology with the
bundled Hermes agent.

L4 also exposes native Hermes web search through the internal SearXNG service.
Hermes receives `SEARXNG_URL=http://searxng:8080`; SearXNG is search-only, so
configure a separate Hermes extract backend when `web_extract` is required.

For L4, expect `proxy`, `dns`, `reader`, and `searxng` to become healthy
quickly. `clawroute` and browser services can need 30-60 seconds before
`sg-status` shows them as healthy. For smaller tiers, check only the services
that tier includes.

If a service stays in `starting` or turns `unhealthy`, inspect logs:

```sh
sg-logs clawroute
sg-logs browserless
sg-logs camofox
sg-logs hermes
```

Optional reader smoke test:

```sh
sg-reader-test https://example.com
```

If it fails because the target domain is blocked, add the domain to the private
whitelist and retry.

## Agent Runtime

Hermes is intentionally part of the default stack. To use another agent, keep the public Compose file intact and add or replace the agent service from `private/compose.local.yml`, reusing the same internal services where possible.

To recreate only Hermes after changing agent env or private mounts:

```sh
sg up -d hermes
sg-logs-hermes
```

This creates or updates Hermes runtime state under the configured `runtime/` or private data path.
