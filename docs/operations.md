# Operations

## Safe Start

Run doctor first. It validates Compose, Docker availability, subnet/port
conflicts, private placeholders, data paths, and least-privilege metadata.

```sh
scripts/doctor.sh
source scripts/aliases.sh
sg-up
sg-status
```

For a metadata-only permission check or a preflighted repair with a local
recovery snapshot:

```sh
python3 scripts/private-permissions.py check --private-dir private --env-file private/env/local.env
python3 scripts/private-permissions.py fix --private-dir private --env-file private/env/local.env \\
  --snapshot /tmp/spartan-gate-private-permissions.json
```

`sg-up` starts the selected tier from `SPARTAN_TIER`, `SPARTAN_ADDONS`, and
`SPARTAN_HERMES_MODE`. Use `sg-tier list`, `sg-tier show`, and
`sg-tier set L0|L1|L2|L3|L4 [--with clawroute] [--hermes free|gated|full]` to
change it. L0 and L1 keep Hermes unprivileged but allow user-level Python/Node
package installs under `/opt/data` or the workspace. L4 starts proxy, DNS,
SearXNG, reader, Spartan Route, Caddy, Hermes, and the selected browser service.
See [layers.md](layers.md) for the full layer/addon contract.

After startup, expect `spartan-route` and the selected browser service to spend
roughly 30-60 seconds in `starting` while they warm up. That is normal. Treat
it as a problem only if the state stays `starting` for much longer or flips to
`unhealthy`.

## Agent Runtime

Hermes is the default public agent. If you want to use another agent, keep that integration in private Compose files and point it at Spartan Gate's internal Spartan Route, Browserless, reader, SearXNG, proxy, and DNS services.

Hermes includes native Pango/Cairo, ffmpeg, and a curated TeX subset for
creative tools such as Manim, but Python and Node packages should still be
installed into project-local sandboxes under `/opt/data` or the workspace.
Hermes seeds `/opt/data/brain/runtime_docs/package-installs.md` on first boot
with the supported `uv` and npm flows when the brain path is writable. Mount
private runtime guidance under a child directory such as
`/opt/data/brain/runtime_docs/local:ro`, not over `runtime_docs` itself, so the
packaged doc and local docs can coexist. Extra postinstall domains remain
private policy; add them with `sg-whitelist-domain 15d <domain>` after
inspecting the failed request.

L4 uses Hermes native SearXNG support for `web_search` through
`SEARXNG_URL=http://searxng:8080`. Spartan Gate no longer bundles
`web-search-plus` on fresh installs. SearXNG does not implement `web_extract`;
use Reader directly when bounded GET-only page text is the right contract.
Native `web_extract` performs Hermes URL safety checks before provider dispatch
and can block domains before Reader receives the URL. Direct Reader calls keep
the DNS, redirect, and connection checks inside Reader.

Recreate Hermes explicitly after changing its private env or mounts:

```sh
sg up -d hermes
sg-logs-hermes
```

## Pocket TTS

Pocket TTS 2.1.0 runs as an optional local English/Spanish sidecar. Hermes
uses its existing command-provider interface, so activation does not rebuild
the Hermes image. The defaults are Anna for English and Eponine for Spanish;
automatic detection is heuristic, so prefix short or ambiguous text with
`[en]` or `[es]` to force the language.

Set the private cache path and add `pocket-tts` to the existing browser profile
in `private/env/local.env`. Keep at least 8 GiB free before the first prefetch:

```env
COMPOSE_PROFILES=camofox,pocket-tts
POCKET_TTS_DATA_PATH=/absolute/private/path/pocket-tts
POCKET_TTS_IMAGE=spartan-gate-pocket-tts:2.1.0
```

Build only the sidecar, prefetch the public non-cloning assets, then enable the
Hermes command provider:

```sh
sg build pocket-tts
sg run --rm pocket-tts-prefetch
scripts/configure-pocket-tts.py \
  --enable \
  --config /absolute/private/path/hermes/config.yaml
```

Start/recreate only the affected services from existing images:

```sh
sg up -d --no-build pocket-tts
sg up -d --no-build --force-recreate hermes
sg ps pocket-tts hermes
```

The configuration helper is idempotent, keeps the first pre-Pocket config
backup, and restores the previous provider with `--disable`. A stopped or
unhealthy sidecar returns an explicit `pocket-tts` error; Hermes does not fall
through to Edge TTS. See `infra/pocket-tts/README.md` for validation and
rollback details.

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

Spartan Route is the normal L4 router:

```sh
source scripts/aliases.sh
sg up -d
```

Hermes points at `http://spartan-route:18790/v1`. Spartan Route is exposed
through Caddy on `CLAWROUTE_EDGE_PORT`, default `18790`, so existing local
router URLs continue to work while the backend changes. `sg route ...` remains
a compatibility alias for older commands. The Codex dashboard includes
reset-credit telemetry.

Spartan Route releases from before the non-root Compose contract may have
created root-owned SQLite files. Normal `sg up` and rebuild helpers now stop
before Compose if the fixed database, WAL, or shared-memory file is unsafe.
Check or migrate that state with the dedicated helpers:

```sh
sg-spartan-route-data-check
sg stop spartan-route
sg-spartan-route-data-migrate
sg-rebuild-spartan-route
```

The migration refuses to run while Spartan Route is active, validates every
managed path before writing, makes a verified timestamped SQLite backup, and
repairs only `spartan-route.db`, `spartan-route.db-wal`,
`spartan-route.db-shm`, and their data directory. It does not recursively
traverse other state. The fixed SQLite family is inventoried again after backup
verification so a WAL or shared-memory sidecar materialized by SQLite receives
the configured owner and private mode too. Fresh installer paths are prepared
with the configured owner and private modes. Apply the same ownership principle
to a pre-existing legacy ClawRoute data root before enabling its non-root addon.

Codex OAuth profiles mounted into Spartan Route are bootstrap-only and stay
read-only. Refresh-token rotation is stored under the existing private data
mount at `/app/data/codex-auth`. A dashboard state of `reauth_required` means
the current grant was rejected terminally; log in again in that slot's original
Codex home and retry. `refresh_rate_limited`,
`refresh_temporarily_unavailable`, `refresh_lock_timeout`,
`auth_store_unavailable`, `refresh_persist_failed`, `refresh_invalid_response`,
and `refresh_internal_error` are retryable and should
not trigger a new login. Never seed two slots from a copied refresh grant, and
protect any backup of the managed store as a live credential.

Do not use private commands against old stack data paths unless you are intentionally migrating data. The provided private override should use isolated Spartan Gate paths.

## Logs

```sh
sg-logs-hermes
sg-logs-spartan-route
sg-logs proxy dns browserless
```

Equivalent raw Compose commands:

```sh
docker compose -f infra/compose/compose.yml logs -f hermes
docker compose -f infra/compose/compose.yml logs -f spartan-route
docker compose -f infra/compose/compose.yml logs -f proxy dns browserless
```

## Helper Aliases

```sh
source scripts/aliases.sh
sg -h
sg-hermes -h
sg-tmux -h
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

### Interactive tmux sessions

The public `sg-tmux` group and the personal shortcuts in
`private/aliases.local.sh` cover the normal session workflow:

```sh
tnew scratch                         # named interactive shell
tcodex gate-work -p deep             # Codex in the current directory
thermes h-default                    # runs: hermes
thermes h-chat chat                  # runs: hermes chat
thermes h-coach -p coach chat        # runs: hermes -p coach chat
thermes h-browser -p coach sessions browse
tlist
tattach gate-work                    # attach an exact session
tattach                              # native interactive session tree
tchoose                              # same native tree explicitly
tkeys                                # print the navigation keys
```

The first `thermes` argument is only the tmux session name. Every remaining
argument is forwarded to Hermes in the same order; the helper never infers a
profile or inserts `chat`. After Hermes exits, the pane remains in the Hermes
container shell. After Codex exits, its pane remains in a host login shell.

Within tmux, use `Ctrl-b s` for the native session tree, `Ctrl-b (` and
`Ctrl-b )` for previous/next session, `Ctrl-b L` for the last-used session,
and `Ctrl-b d` to detach. Creating or launching with an existing name switches
or attaches to that session without starting a duplicate process.

`sg-gog-login` requires a running Hermes container and performs GogCLI auth
with the GogCLI version installed in Hermes, so the host CLI cannot write an
older credential format into the shared store. In the manual browser flow,
paste the complete final callback URL into the terminal after Google sign-in;
the browser sign-in alone does not complete the refresh-token exchange. The
helper fails unless the stored grant contains every scope requested through
`GOGCLI_EXTRA_SCOPES`. Verify the active grant at any time with:

```sh
sg-gog-check --json
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
Safari may omit Basic Auth on WebSockets and on noVNC module requests. Caddy
therefore exchanges one successful Basic Auth for a one-hour, host-only,
HttpOnly session covering later HTTP assets and the WebSocket. Camofox uses the
dedicated `CADDY_CAMOFOX_EDGE_TOKEN`; missing or invalid WebSocket sessions fail
closed without reopening Safari's authentication dialog. Edge cookies and the
Basic `Authorization` header are removed before requests reach Camofox.
The live-profile URL includes a stable version query to bypass any older cached
noVNC shell, and Caddy marks authenticated noVNC responses `no-store`. This
forces the page request that refreshes the edge session before noVNC opens its
WebSocket.

`sg-camofox-profile-live` uses the reserved `spartan-operator-login` tab group
inside the selected Camofox `userId`. Hermes uses `spartan-hermes`, so both
groups share cookies but neither adopts or navigates the other's tab. Operator
tab creation is non-destructive: capacity and navigation/proxy failures abort
instead of recycling a Hermes tab or rotating the shared browser session. The
helper verifies a versioned, authenticated safety capability before creating
the tab; an older running Camofox image fails closed. Failed operator
navigation removes its newly created tab.

For a profile Hermes already uses, only open and checkpoint the login; do not
run `use` or recreate services:

```sh
sg-camofox-profile-live main https://accounts.google.com
sg-camofox-profile-save main
```

For first-time profile selection, wait until active browser work has finished,
then run:

```sh
sg-camofox-profile-use main
sg-browser-mode-apply
```

`sg-camofox-profile-use` enables Camofox mode but clears legacy global
user/session ownership and leaves global tab adoption disabled. Supervised
Hermes profiles derive distinct identities from their own `HERMES_HOME` when
managed persistence is enabled. The human login helper retains its reserved,
non-destructive operator tab group; Google Meet remains a single-owner
compatibility path for the default profile. The migration helper discovers
configured profiles, preserves one original backup across repeated applies,
proves the derived identities are distinct without printing them, and does not
change tokens or private host paths. Use `sg-camofox-profile-reset <profile>`
only for an explicit destructive reset, never for recovery.

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
docker compose -f infra/compose/compose.yml exec spartan-route python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18790/health').read().decode())"
```

Camofox Docker health and public GET /health both reflect deep readiness.
A PID-1 supervisor validates a real raw context/page lifecycle.
Public GET /health is now a passive, generation-bound view of the same proof
and never creates a page in its request path; GET /live remains
browser-independent event-loop liveness. Three terminal real-navigation
timeouts latch the current browser generation unhealthy. The next supervisor
poll performs at most one recovery for that child PID/generation tuple, and a
replacement is healthy only after a different generation passes a deep probe.
A busy owner is healthy only while it remains inside its declared deadline and
the last completed real probe is at most 45 seconds old; busy never refreshes
that proof. A queued create is registered before lifecycle admission, and an
overdue token also makes deep health fail. The token uses the effective request
deadline, including the bounded proxy-rotation allowance, rather than the base
30-second handler timeout. Dead-session cleanup invalidates a create bound to
that exact context and defers close until its underlying promise settles.
The unsafe deprecated `POST /tabs/open` alias returns HTTP 410; use
`POST /tabs`. Overdue or stale state is unhealthy.
The supervisor performs one
internal recovery after three
failures, and then recycles its child process tree under persistent exponential
backoff. It does not use Docker control APIs. If an unkillable `D` process
survives, it refuses to launch a second browser and remains unhealthy. Preserve
redacted Camofox logs before any explicit recreate; do not delete profiles or
watchdog state as a diagnostic shortcut.

Hermes is healthy only when its local API, Tinyproxy, and Spartan Route are
reachable. Three 15-second retries tolerate expected proxy/DNS restarts for
roughly 45 seconds while exposing persistent loss of `spartan_internal`.

Reader check:

```sh
sg-reader-test https://example.com
```

If startup fails before containers are healthy, the most common causes are now caught by `scripts/doctor.sh`:

- Overlapping Docker subnet. Fix `SPARTAN_INTERNAL_SUBNET` and `SPARTAN_DNS_IP` in `.env` or `private/env/local.env`.
- Occupied local edge ports. Fix `SPARTAN_GATE_PORT`, `CLAWROUTE_EDGE_PORT`, `BROWSERLESS_DEBUG_PORT`, or `HERMES_DASHBOARD_PORT`.
- Private env placeholders. Replace `change-me`, `100.x.y.z`, and `/absolute/path` values before starting.
- Missing private data directories. Create `SPARTAN_HERMES_DATA_PATH`, `SPARTAN_ROUTE_DATA_PATH`, and `SPARTAN_GOGCLI_DATA_PATH`; when Camofox is selected, also create `SPARTAN_CAMOFOX_DATA_PATH` and `SPARTAN_CAMOFOX_ADDONS_PATH`.
- Slow first warm-up. Check `sg-logs spartan-route browserless camofox` before restarting everything.

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

The helper writes to the private file by default and hot-reloads running
`proxy`/`dns` services by executing an in-container `USR1` signal. The normal
path does not use `docker compose kill` and does not recreate containers. Before
replacing the private file, the helper validates the byte limit for each public
or private fragment and the combined normalized active-entry limit. Defaults are
262144 bytes per fragment and 2048 active unique domains; configure them with
`SPARTAN_WHITELIST_MAX_BYTES` and `SPARTAN_WHITELIST_MAX_ENTRIES`.

If the helper cannot deliver its outer reload request, it exits non-zero and
the previous runtime policy remains active; the private file still contains the
requested entry for the next successful reload or startup. Once a refresh is
accepted inside a container, regeneration failure, scheduled-time evaluation
failure, or failure to signal Tinyproxy/dnsmasq restores the complete previous
generated policy set, then exits so Docker restarts the service through
fail-closed startup. DNS treats its server and dnsmasq fragments as one
transaction, so it never deliberately leaves a mixed generation installed.
Use `--no-reload` to write only without touching running services; legacy
`--no-restart` is accepted as the same operation. Use
`--recreate-on-reload-failure` or
`SPARTAN_WHITELIST_RELOAD_FALLBACK=recreate` only when an explicit disruptive
fallback is acceptable. Temporary entries are ignored after their timestamp
expires on the next startup, hot reload, explicit fallback recreate, or the
daily runtime refresh at `02:00` Europe/Madrid. The private file is not
automatically pruned, so expired lines can remain as history.

After updating Spartan Gate source that changes proxy/DNS entrypoints, roll only
those services to load the new reload handlers:

```sh
sg up -d --build proxy dns
```

## HTTPS Edge

When `SPARTAN_EDGE_SCHEME=https`, `sg-urls` prints HTTPS URLs. Use the hostname covered by the certificate, usually `TAILSCALE_HOST`, not the raw Tailscale IP. If a browser shows `SSL_ERROR_RX_RECORD_TOO_LONG`, it is usually trying HTTPS against a Caddy listener that is still serving plain HTTP. Recreate or restart Caddy after enabling the HTTPS private override.

Caddy runs with `SPARTAN_CADDY_UID`/`SPARTAN_CADDY_GID`; those values must
match the owner that can read the mounted Caddyfile and certificate directory.
The healthcheck runs `caddy validate` and probes the local authenticated
listener. Public HTTP returns unauthenticated `401`; a private TLS listener
returns `400` to the healthcheck's deliberate plain-HTTP probe. Either proves
the listener is serving without placing credentials in the check.

```sh
sg-caddy-validate
sg-restart-caddy
sg-urls
```

Existing private listeners created before the full Safari session contract can
be migrated transactionally. The helper preflights all files, keeps `.bak`
copies, validates the installed candidate as a set, rolls back on failure, and
recreates only Caddy:

```sh
scripts/migrate_caddy_safari_session.py
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
