# Spartan Gate

**A private security gate for Hermes-class agents.**

Spartan Gate is defensive infrastructure for private AI agents. It puts Hermes inside a controlled Docker boundary with whitelisted network egress, guarded browsing, LLM routing, DNS filtering, and authenticated edge access. The public shape is a gate: hard limits, visible control points, and private execution by default.

Hermes is the primary agent/runtime. ClawRoute is the internal OpenAI-compatible LLM router. The surrounding services enforce controlled outbound access, browser mediation, search, read-only web fetching, and authenticated local entry points.

## Status

Spartan Gate is OSS-ready as a local/private defensive boundary for
Hermes-class agents. It is not a claim of production certification for public
Internet exposure. Keep public edge ports bound to localhost or private
networks, replace all placeholders before use, and review
[docs/security.md](docs/security.md) before widening access.

For machine-readable repository orientation, see [llms.txt](llms.txt).

## Quick Start

These commands are the basic path for a fresh machine. The installer creates
private local config, runs `hermes setup`, and starts the selected tier.

### 1. Install Prerequisites

On Debian, Ubuntu, or Raspberry Pi OS, install Git and Docker, then verify that
the Compose plugin works:

```sh
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
sudo docker version
sudo docker compose version
```

Docker documents the convenience script as a development bootstrap path. For a
production host, follow Docker's official Debian, Ubuntu, or Raspberry Pi OS
repository install guide instead:
<https://docs.docker.com/engine/install/>.

After Docker is installed, log out and back in, or run `newgrp docker`, so the
rest of the commands can use `docker` without `sudo`.

### 2. Clone

```sh
git clone https://github.com/Heeervas/spartan-gate.git spartan-gate
cd spartan-gate
```

### 3. Pick A Tier

```sh
scripts/install.sh --tier L0
```

Tiers let each operator choose how much of the gate to run:

| Tier | Services |
| --- | --- |
| L0 | Hermes only, free user-level Python/Node package installs |
| L1 | Hermes + Camofox/noVNC, free user-level Python/Node package installs |
| L2 | L1 + proxy, DNS, and Caddy edge |
| L3 | L2 + ClawRoute internal LLM routing compatibility alias |
| L4 | Full Spartan Gate topology |

Use `L0` for the fastest portable Hermes setup and `L4` for the full current
security gate. The installer writes secrets and paths under ignored `private/`.
ClawRoute is also available as an addon on lower tiers:

```sh
scripts/install.sh --tier L1 --with clawroute
```

See [docs/layers.md](docs/layers.md) for the full layer/addon contract,
switching commands, and native SearXNG web-search behavior.

### 4. Manual Public Local Config

```sh
cp .env.example .env
```

For a basic local run, only change these values if `doctor` tells you to:

- `SPARTAN_INTERNAL_SUBNET` and `SPARTAN_DNS_IP`: change together when another
  Docker network already uses `172.28.0.0/24`.
- `SPARTAN_GATE_PORT`, `CLAWROUTE_EDGE_PORT`, `BROWSERLESS_DEBUG_PORT`,
  `HERMES_DASHBOARD_PORT`: change if a host port is occupied.
- `CADDY_AUTH_HASH`: replace before exposing beyond localhost.

Validate without starting services:

```sh
scripts/doctor.sh
docker compose -f infra/compose/compose.yml --env-file .env config
```

`doctor` allows a local `.env`, but it must stay untracked. It also checks Docker availability, Compose config, subnet overlap, occupied published ports, private placeholders, and private data paths before `docker compose up`.

### 5. Load Helpers

Spartan Gate includes optional shell helpers so you do not have to repeat the
full Docker Compose command.

From the repo root:

```sh
cd /path/to/spartan-gate
source scripts/aliases.sh
```
To persist the helpers, replace /path/to/spartan-gate with your real absolute repo path and add the matching line to your shell rc:

```sh
echo 'cd /path/to/spartan-gate && source scripts/aliases.sh' >> ~/.bashrc
echo 'cd /path/to/spartan-gate && source scripts/aliases.sh' >> ~/.zshrc
```

Then open a new terminal, or reload your shell rc:

```sh
source ~/.bashrc    # or: source ~/.zshrc
```

Useful commands:

```sh
sg                 # cd into the repo
sg-tier list       # show L0-L4
sg-tier set L2     # persist a tier selection
sg-tier set L1 --with clawroute --hermes free  # optional addon shape
sg-tier show       # show selected tier, addons, and Hermes mode
sg-up              # start the selected tier
sg-tier down       # stop the selected tier
sg ps              # show services
sg logs -f         # follow logs
sg exec -it hermes bash
```

The helpers automatically use infra/compose/compose.yml, add private/compose.local.yml when it exists, and load private/env/local.env for Compose interpolation when it exists. If there is no private env file, they fall back to .env.

private/env/local.env is needed when Compose files interpolate local paths, ports, Tailscale IPs, or secrets. A service-level env_file: only injects variables into containers; it does not replace --env-file for Compose interpolation.

### 6. Start The Stack

Start the selected tier with:

```sh
sg-up
sg ps
sg-urls
```

Equivalent helper commands are available when you prefer short names:

```sh
sg-up
sg-ps
sg-urls
```

`sg-up` uses `SPARTAN_TIER`, `SPARTAN_ADDONS`, and `SPARTAN_HERMES_MODE` from
`private/env/local.env`, defaulting to L4 for older manual setups. Use
`sg-tier set L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]` to
change it. The Compose project name remains `spartan-gate`, so changing tiers
does not intentionally move data paths or create a separate stack name. Use
normal Compose passthrough commands for inspection and low-level operations:

```sh
sg logs -f
sg exec -it hermes bash
sg config
sg down
```

The public Compose stores Hermes data under `runtime/hermes/` inside this repo.
That path is ignored by Git and does not reuse old stack data.

Browserless has two browser modes in Spartan Gate. The default Hermes browser is
an ephemeral Browserless launch without `--user-data-dir`, matching the simpler
pre-profile behavior and allowing multiple browsers. The optional browser
profile `main` uses a persistent Chromium profile under
`runtime/browserless/profiles/` for cookies, localStorage, IndexedDB, cache, and
login state. Private installs can override that host path with
`SPARTAN_BROWSERLESS_PROFILES_PATH`; use `sg config` or
`sg-browserless-snapshot` to confirm the rendered path. Both modes use
`BROWSERLESS_ROUTE=chromium` with `stealth=true` by default.
`BROWSERLESS_PROFILE` names that persistent login directory and
defaults to `main`.

The persistent `main` browser is served through an internal shared CDP broker so
Hermes clients do not open the same `--user-data-dir` twice. The default
ephemeral browser does not use the broker. The Browserless session timeout
defaults to its long-lived safe value (`BROWSERLESS_SESSION_TIMEOUT_MS=2147483647`,
about 24.8 days), mainly for broker-owned persistent sessions.

Camofox is available as an alternative browser service for Hermes browser
tools. It is selected by setting `CAMOFOX_URL=http://camofox:9377`; leave
`CAMOFOX_URL` empty for Browserless mode. `COMPOSE_PROFILES` should match the
selected mode (`browserless` or `camofox`) when present. Browserless remains
the default CDP backend for Google Meet, Chrome DevTools MCP, Browserless
profile seeding, and the persistent `main` profile broker. Camofox state is
stored separately under `runtime/camofox/` or `SPARTAN_CAMOFOX_DATA_PATH`. See
`infra/camofox/README.md`.

For private/local machines, prefer changing `SPARTAN_HERMES_DATA_PATH` in `private/env/local.env` and mounting it from `private/compose.local.yml` instead of editing the public Compose file.

Example private mount:

```yaml
services:
  hermes:
    volumes:
      - ${SPARTAN_HERMES_DATA_PATH}:/opt/data
```

Private installs should also set `SPARTAN_BROWSERLESS_PROFILES_PATH` and let
`private/compose.local.yml` mount it to `/profiles` in the Browserless
container. Treat that directory like credentials, because it can contain live
account cookies. Set `BROWSERLESS_PROFILES_GID` to a host group that can write
that directory (normally your user's primary GID, commonly `1000`).

### Browserless Profiles

To seed the persistent login profile, stop Hermes if the `main` browser profile
is in use, then open the Browserless debugger with that profile preselected:

```sh
source scripts/aliases.sh
sg stop hermes
sg-browserless-profile-live main https://example.com
```

The command refuses to open the active persistent profile while Hermes is
running, because Chromium cannot open the same profile directory twice. The
debugger flow uses the selected `BROWSERLESS_ROUTE` and `stealth=true` while seeding the profile; for `accounts.google.com` it does not auto-click Run, so start the
debugger session yourself and complete the login manually. It prints
the usable `Debugger URL` link or links for the configured edge. For a
TLS/Tailscale edge, use the HTTPS hostname covered by the certificate rather
than a raw Tailscale IP. Open the link in your normal browser, pass Caddy Basic
Auth, and let the debugger start the session. Log in, then close the debugger
tab when done; the profile state is written under the Browserless profiles path.

Start Hermes after the profile is seeded:

```sh
sg up -d hermes
```

To switch the persistent login profile behind browser profile `main`, change
`BROWSERLESS_PROFILE` in `.env` or `private/env/local.env`:

```env
BROWSERLESS_PROFILE=work
```

Then recreate Hermes so it receives the new browser endpoint:

```sh
sg up -d hermes
```

Do not seed and use the same Browserless profile concurrently. Chromium expects
one live browser per `--user-data-dir`, so close the debugger session or stop
Hermes before opening the same profile elsewhere.

### Camofox Profiles

Camofox profiles are keyed by Camofox `userId`, not by Browserless profile
directories. To seed a logged-in Camofox profile through private noVNC:

```sh
sg-camofox-profile-live main https://accounts.google.com
```

Open the printed noVNC URL. Caddy serves it on `CAMOFOX_NOVNC_PORT` and the
helper verifies that the listener is reachable before printing the login
instructions. HTTPS requires `TAILSCALE_HOST` to resolve to `TAILSCALE_IP` and
to be covered by the configured certificate; the helper never falls back to a
raw IP with an invalid certificate. Log in manually. After the login is
complete:

```sh
sg-camofox-profile-save main
sg-camofox-profile-use main
sg-browser-mode-apply
```

Hermes receives the Camofox runtime access key needed to call the browser
service, but not the human account password, 2FA material, or personal API
keys. Use `sg-camofox-profile-live alt ...` for a separate login profile.

### 6. Private Local Setup

For a private local setup, copy `private.example/` to `private/`, create the
private env file, edit placeholders, then validate:

```sh
cp -R private.example private
mv private/env/local.env.example private/env/local.env
mkdir -p private/caddy.local.d
mkdir -p private/caddy
```

Edit `private/env/local.env`. The minimum values that usually need real local
input are:

- `TAILSCALE_IP` and optionally `TAILSCALE_HOST`.
- `SPARTAN_INTERNAL_SUBNET` and `SPARTAN_DNS_IP`, if your host already has an
  overlapping Docker network.
- `SPARTAN_*_PORT`, if your old stack still owns the default ports.
- `SPARTAN_HERMES_DATA_PATH`, `SPARTAN_CLAWROUTE_DATA_PATH`,
  `SPARTAN_GOGCLI_DATA_PATH`, `SPARTAN_BROWSERLESS_PROFILES_PATH`.
- `HERMES_*`, `CLAWROUTE_TOKEN`, `BROWSERLESS_TOKEN`, provider keys, chat bot
  tokens and profile variables, if you intend to start Hermes.

When selecting Camofox mode, also configure and create:

- `SPARTAN_CAMOFOX_DATA_PATH` and `SPARTAN_CAMOFOX_ADDONS_PATH`.
- `CAMOFOX_ACCESS_KEY` and `CAMOFOX_URL=http://camofox:9377`.
- `CAMOFOX_ENABLE_VNC=1`, `CAMOFOX_VNC_PASSWORD`, and
  `CAMOFOX_NOVNC_PORT` when manual login is required.
- `CAMOFOX_ADDONS` with container paths under `/opt/camofox-addons` when
  extracted Firefox add-ons are enabled.

For a fresh install, keep `SPARTAN_ALLOW_EXISTING_STACK_PATHS=false`. Set it to
`true` only when you intentionally point private data paths at an existing stack
and that old stack is stopped.

```sh
scripts/doctor.sh
docker compose \
  -f infra/compose/compose.yml \
  -f private/compose.local.yml \
  --env-file private/env/local.env \
  config
```

The helpers automatically use `private/compose.local.yml` and `private/env/local.env` when they exist, so the private start path is the same full-stack command:

```sh
source scripts/aliases.sh
sg up -d
```

`private/` is ignored by Git and is the only place for personal host paths, Codex profiles, local-only mounts, real tokens, and notes.

Add private domains to the private whitelist, not to the public one:

```sh
sg-whitelist-domain docs.example.com        # permanent
sg-whitelist-domain 15m docs.example.com    # temporary
sg-whitelist-domain 6h api.example.com
sg-whitelist-domain 15d vendor.example.com
```

Temporary entries are timestamped in the private file. Runtime whitelists drop
expired entries when proxy/DNS start, when the helper recreates running services,
or during the daily refresh window at `02:00` Europe/Madrid.

At startup, Tinyproxy and DNS merge:

- `infra/outbound-proxy/whitelist.txt`: public publishable baseline.
- `private/outbound-proxy/whitelist.private.txt`: local/project/personal
  extension.

The public Compose marks env and volume blocks as `required`, `recommended`, or `private/local`. Anything marked `private/local` belongs in `private/compose.local.yml` or `private/env/local.env`, not in public files.


Private HTTPS is supported by overriding Caddy from `private/`: copy `private.example/caddy/Caddyfile.https.example` to `private/caddy/Caddyfile.https`, mount a cert directory as `/certs`, set `SPARTAN_EDGE_SCHEME=https`, and use a hostname covered by the certificate. Tailscale certificates are normally valid for the MagicDNS name, not the raw `100.x.y.z` address.

## Common Edge Cases

- `doctor: docker subnet overlaps existing network: ...` means another Docker stack already uses an overlapping subnet. Edit `.env` or `private/env/local.env` and change both `SPARTAN_INTERNAL_SUBNET` and `SPARTAN_DNS_IP`.
- `doctor: host port already in use before compose up: ...` means a local published port is occupied by another process or stack. Change `SPARTAN_GATE_PORT`, `CLAWROUTE_EDGE_PORT`, `BROWSERLESS_DEBUG_PORT`, or `HERMES_DASHBOARD_PORT` in `.env` or `private/env/local.env`, then rerun `scripts/doctor.sh`.
- `sg-up` starts the selected tier, including Hermes. `sg up -d` remains raw Compose passthrough.
- `clawroute` and the selected browser service can stay in `starting` for roughly 30-60 seconds after startup. That is normal warm-up time. Check `sg ps` or `sg-ps` again before treating it as a failure.
- Reusing old Hermes or ClawRoute data paths is not a normal first install. Keep the public `runtime/` defaults or point the private override at fresh empty directories unless you are intentionally migrating.

## Use Cases

- Publish this repo: follow [docs/publishing.md](docs/publishing.md).
- Clone and run safely from scratch: follow [docs/setup.md](docs/setup.md).
- Choose or switch install layers: follow [docs/layers.md](docs/layers.md).
- Replace an existing Hermes stack: follow [docs/cutover.md](docs/cutover.md).
- Share with a colleague: follow [docs/share.md](docs/share.md).
- Decide if it is ready as the definitive stack: follow [docs/go-live.md](docs/go-live.md).
- Understand carried Hermes patches: follow [docs/patches.md](docs/patches.md).
- Test reader/page fetching: follow [docs/reader.md](docs/reader.md).
- Understand ClawRoute source versus runtime config: follow [docs/clawroute.md](docs/clawroute.md).
- Keep local/private runbooks in ignored `private/docs/`; see [docs/private-docs.md](docs/private-docs.md).

## Architecture

Spartan Gate is a monorepo:

- `apps/hermes-agent`: Hermes image customizations, runtime bootstrap, and patch scripts.
- `packages/clawroute`: ClawRoute router source, tests, dashboard, and Docker image.
- `infra/compose`: public Compose entry points.
- `infra/outbound-proxy`: Tinyproxy whitelist enforcement and public domain baseline.
- `infra/dns`: dnsmasq whitelist relay.
- `infra/reader`: GET-only reader with SSRF protection.
- `infra/caddy`: authenticated edge proxy.
- `infra/caddy/local.d`: optional local Caddy listeners, normally overridden from `private/caddy.local.d`.
- `infra/camofox`: optional Camofox browser backend notes.
- `infra/browserless`: Browserless integration notes and future policy.
- `config/clawroute` and `config/searxng`: public runtime configuration.

`packages/clawroute/` is the source package. `config/clawroute/` is the runtime
configuration mounted into the container. Keep secrets out of both and put them
in `.env` or `private/env/local.env`.

Hermes is only attached to the internal Docker network. Remote LLM calls go
through ClawRoute, which routes through the outbound proxy. Browser traffic goes
through Browserless and the same proxy/DNS controls. Reader is a direct GET-only
public text fetcher with SSRF protection and untrusted-content delimiters. Caddy
is the authenticated edge.

## Public Safety Rules

The public Compose files do not include personal host paths, private credentials, certificate material, Codex profile mounts, or LAN-wide port binds. Runtime data defaults to `runtime/`, which is ignored by Git. Hermes is the bundled default agent. Alternate agent integrations belong in private Compose overrides that reuse Spartan Gate's ClawRoute, Browserless, reader, SearXNG, proxy, and internal network contracts.

The public docs should remain generic and shareable. Machine-specific cutover notes, real port choices, token ownership, profile names, Tailscale hostnames, and migration notes belong in `private/docs/`.

Run the local checks before publishing:

```sh
scripts/doctor.sh
npm --prefix packages/clawroute ci
npm --prefix packages/clawroute test
npm --prefix packages/clawroute run build
docker compose -f infra/compose/compose.yml config
```

Useful helper scripts:

- `scripts/aliases.sh`: shell helpers; `sg` is a Docker Compose passthrough and `sg-up` starts the selected tier, including Hermes.
- `scripts/whitelist-domain.sh`: adds permanent or temporary private outbound whitelist domains and recreates only running proxy/DNS services.
- `scripts/add-port.sh`: adds private Caddy listeners and private Compose port mappings for Hermes app ports.

## Credits

Spartan Gate is maintained as an independent monorepo.

- **Lobster Cage** provided the original Docker cage, isolated network, outbound proxy, DNS relay, reader, and hardening ideas.
- **ClawRoute** provided the OpenAI-compatible routing service and model-routing logic.

The current product identity, structure, and maintenance path are Spartan Gate: a private security gate for Hermes-class agents.
