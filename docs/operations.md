# Operations

## Safe Start

Run doctor first. It validates Compose, Docker availability, subnet overlap, occupied published ports, private placeholders, and private data paths before you start containers.

```sh
scripts/doctor.sh
source scripts/aliases.sh
sg-up
sg-status
```

`sg-up` starts the selected tier from `SPARTAN_TIER`, `SPARTAN_ADDONS`, and
`SPARTAN_HERMES_MODE`. Use `sg-tier list`, `sg-tier show`, and
`sg-tier set L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]` to
change it. L0 and L1 keep Hermes unprivileged but allow user-level Python/Node
package installs under `/opt/data` or the workspace. L4 starts proxy, DNS,
SearXNG, reader, ClawRoute, Caddy, Hermes, and the selected browser service.
See [layers.md](layers.md) for the full layer/addon contract.

After startup, expect `clawroute` and the selected browser service to spend
roughly 30-60 seconds in `starting` while they warm up. That is normal. Treat
it as a problem only if the state stays `starting` for much longer or flips to
`unhealthy`.

## Agent Runtime

Hermes is the default public agent. If you want to use another agent, keep that integration in private Compose files and point it at Spartan Gate's internal ClawRoute, Browserless, reader, SearXNG, proxy, and DNS services.

Hermes includes native Pango/Cairo, ffmpeg, and a curated TeX subset for
creative tools such as Manim, but Python and Node packages should still be
installed into project-local sandboxes under `/opt/data` or the workspace.
Hermes seeds `/opt/data/brain/runtime_docs/package-installs.md` on first boot
with the supported `uv` and npm flows. Extra postinstall domains remain private
policy; add them with `sg-whitelist-domain 15d <domain>` after inspecting the
failed request.

L4 uses Hermes native SearXNG support for `web_search` through
`SEARXNG_URL=http://searxng:8080`. Spartan Gate no longer bundles
`web-search-plus` on fresh installs. SearXNG does not implement `web_extract`;
use a separate Hermes extract backend or Reader for bounded page text.

Recreate Hermes explicitly after changing its private env or mounts:

```sh
sg up -d hermes
sg-logs-hermes
```

## Raw Compose Start

The tier helper path is preferred because it selects the expected Compose files
and env file. If you use raw Compose for L4, make `COMPOSE_PROFILES` match
`CAMOFOX_URL`.

```sh
docker compose -f infra/compose/compose.yml --env-file .env up -d
```

Private full start:

```sh
docker compose \
  -f infra/compose/compose.yml \
  -f private/compose.local.yml \
  --env-file private/env/local.env \
  up -d
```

Do not use private commands against old stack data paths unless you are intentionally migrating data. The provided private override should use isolated Spartan Gate paths.

## Logs

```sh
sg-logs-hermes
sg-logs-clawroute
sg-logs proxy dns browserless
```

Equivalent raw Compose commands:

```sh
docker compose -f infra/compose/compose.yml logs -f hermes
docker compose -f infra/compose/compose.yml logs -f clawroute
docker compose -f infra/compose/compose.yml logs -f proxy dns browserless
```

## Helper Aliases

```sh
source scripts/aliases.sh
sg -h
sg-hermes -h
sg-browser -h
sg-meet -h
sg-disk -h
```

The help commands group the sourced shell helpers by workflow. The most common
flat aliases remain available:

```sh
sg-up            # selected tier, including Hermes
sg-tier list
sg-tier set L2
sg-tier set L1 --with clawroute --hermes free  # optional addon shape
sg-tier show
sg-browser-mode-apply
sg up -d hermes  # explicit Hermes recreate/start
sg-status
sg-health
sg-logs-hermes
sg-restart-caddy
sg-rebuild-hermes
sg-whitelist-domain example.com
sg-reader-test https://example.com
sg-browserless-snapshot
sg-browserless-profile-live main https://example.com
sg-camofox-profile-live main https://accounts.google.com
sg-camofox-profile-use main
sg-add-port --localhost 8787
sg-urls
sg-caddy-validate
sg-cache-top
sg-cache-clean
sg-profile-ports
sg-gog-check
sg-gog-login
sg-docker-df
sg-disk overview
sg-disk top /var 2 30
sg-disk apport
sg-disk spartan
```

`sg-up-core`, `sg-up-hermes`, and `sg-up-private-hermes` remain as compatibility
helpers, but they are deprecated. Prefer `sg-up` for the selected tier and
`sg up -d hermes` for explicit Hermes service targeting.

Machine-specific disk shortcuts belong in ignored `private/aliases.local.sh`.
That file is sourced automatically when present, so personal paths and host
cleanup commands do not need to be committed.

## Google Meet via Real Chrome

For Google Meet, prefer a real host Chrome profile over Browserless when Google
bot-detection blocks the Browserless profile. Open a named host Chrome profile
and keep it open while Hermes uses it:

```sh
sg-hermes-chrome-profile google2 https://accounts.google.com
```

Then, from another terminal, join by Meet ID or full URL:

```sh
sg-hermes-meet-join uif-uhgp-fxo
sg-hermes-meet-join https://meet.google.com/uif-uhgp-fxo 60m
```

The join helper writes the current Chrome WebSocket to `private/env/local.env`,
recreates Hermes, runs the Meet preflight, and starts the Google Meet plugin.
Do not use the plain `http://...:9222` endpoint for `HERMES_MEET_CDP_URL`.

This keeps Meet on the same human Chrome/IP/device trust surface and bypasses
Browserless for the Meet plugin only.

If `CAMOFOX_URL` is set, `sg-hermes-meet-join` uses the selected Camofox
profile for `mode=transcribe` and ignores stale Meet CDP values. Realtime mode
is unavailable in Camofox mode; switch browser modes before using realtime.

## Browserless Profiles

Seed Browserless profiles manually before asking Hermes to use accounts that
require login:

```sh
sg stop hermes
sg-browserless-profile-live main https://example.com
sg up -d hermes
```

For a Google Meet account profile, prefer the real Chrome flow above. If you
need the Browserless web debugger instead, use
`sg-browserless-profile-live google https://accounts.google.com` while Hermes is stopped.

The command fails before opening the debugger if Hermes is using the same
`BROWSERLESS_PROFILE`; one live Chromium process can own a profile directory.
During normal Hermes operation, its local CDP broker owns that single
Browserless process and shares it among Hermes' internal browser clients; it
does not make the active profile available for a separate debugger session.
Open the printed debugger URL in your browser and pass Caddy Basic Auth. The
helper asks Browserless to use the selected `BROWSERLESS_ROUTE` with
`stealth=true` while writing the profile. The helper also appends the generated
`BROWSERLESS_EDGE_TOKEN` to debugger WebSocket URLs so Caddy can authorize the
upgrade without depending on browser Basic Auth behavior during the WebSocket
handshake.
For `accounts.google.com`, click Run manually and complete the login yourself.
A TLS edge prints the certificate hostname, not a raw Tailscale IP. Complete
the login, then close the debugger tab before starting Hermes. If login state is
missing, confirm Hermes and the helper use the same profile, no second browser
is holding its directory, and `/profiles` is writable through
`BROWSERLESS_PROFILES_GID`.

## Camofox Profiles

Use Camofox profiles when Hermes should reuse a manual website login through
Camofox browser tools. Camofox noVNC is exposed through the private Caddy edge
on `CAMOFOX_NOVNC_PORT`; the helper starts Caddy, verifies internal noVNC and
checks that the public listener responds before printing the URL.

```sh
sg-camofox-profile-live main https://accounts.google.com
sg-camofox-profile-save main
sg-camofox-profile-use main
sg-browser-mode-apply
```

`sg-camofox-profile-use` updates only Camofox mode/profile keys in
`private/env/local.env` and writes a timestamped backup. It does not change
tokens or private host paths. Use `sg-camofox-profile-reset <profile>` only for
an explicit destructive reset of that Camofox profile.

For HTTPS, configure a certificate-valid `TAILSCALE_HOST` that resolves to
`TAILSCALE_IP`. The helper aborts instead of using the raw IP when DNS is wrong.
For add-ons, mount `SPARTAN_CAMOFOX_ADDONS_PATH` read-only and set
`CAMOFOX_ADDONS` to comma-separated container paths below
`/opt/camofox-addons`.

## Health

```sh
sg-health
sg-caddy-validate
```

Raw checks:

```sh
sg-status
docker compose -f infra/compose/compose.yml exec clawroute node -e "fetch('http://127.0.0.1:18790/health').then(r=>r.text()).then(console.log)"
```

Reader check:

```sh
sg-reader-test https://example.com
```

If startup fails before containers are healthy, the most common causes are now caught by `scripts/doctor.sh`:

- Overlapping Docker subnet. Fix `SPARTAN_INTERNAL_SUBNET` and `SPARTAN_DNS_IP` in `.env` or `private/env/local.env`.
- Occupied local edge ports. Fix `SPARTAN_GATE_PORT`, `CLAWROUTE_EDGE_PORT`, `BROWSERLESS_DEBUG_PORT`, or `HERMES_DASHBOARD_PORT`.
- Private env placeholders. Replace `change-me`, `100.x.y.z`, and `/absolute/path` values before starting.
- Missing private data directories. Create `SPARTAN_HERMES_DATA_PATH`, `SPARTAN_CLAWROUTE_DATA_PATH`, and `SPARTAN_GOGCLI_DATA_PATH`; when Camofox is selected, also create `SPARTAN_CAMOFOX_DATA_PATH` and `SPARTAN_CAMOFOX_ADDONS_PATH`.
- Slow first warm-up. Check `sg-logs clawroute browserless camofox` before restarting everything.

## Whitelist Changes

Public domains live in `infra/outbound-proxy/whitelist.txt`. Local domains live
in `private/outbound-proxy/whitelist.private.txt`.

Use the helper for normal local additions:

```sh
sg-whitelist-domain docs.example.com        # permanent
sg-whitelist-domain 15m docs.example.com    # temporary
sg-whitelist-domain 6h api.example.com
sg-whitelist-domain 15d vendor.example.com
```

The helper writes to the private file by default and recreates only running
`proxy`/`dns` services so the generated merged whitelist is rebuilt. If the stack
is stopped, the next `sg-up` will load the merged whitelist. Temporary entries
are ignored after their timestamp expires on the next recreate/reload, including
the daily runtime refresh at `02:00` Europe/Madrid. The private file is not
automatically pruned, so expired lines can remain as history.

## HTTPS Edge

When `SPARTAN_EDGE_SCHEME=https`, `sg-urls` prints HTTPS URLs. Use the hostname covered by the certificate, usually `TAILSCALE_HOST`, not the raw Tailscale IP. If a browser shows `SSL_ERROR_RX_RECORD_TOO_LONG`, it is usually trying HTTPS against a Caddy listener that is still serving plain HTTP. Recreate or restart Caddy after enabling the HTTPS private override.

```sh
sg-caddy-validate
sg-restart-caddy
sg-urls
```

## Backup

Back up `runtime/` for the public setup, or the private host paths configured in `private/compose.local.yml`.

## Update

```sh
scripts/doctor.sh
docker compose -f infra/compose/compose.yml pull
sg-up
```

Use `sg up -d hermes` when you only need to recreate Hermes.
