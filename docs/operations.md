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
Compose mounts the versioned runtime guides read-only under
`/opt/data/brain/runtime_docs/local/`; no Hermes image rebuild or startup copy
is required. The package-install guide covers the supported `uv` and npm flows;
the Camofox guide covers Hermes browser capabilities, API endpoints, tab
limits, recovery timing, and runtime viewports. Installation-specific guidance
can be mounted read-only under `/opt/data/brain/runtime_docs/local/private/` so
both sources remain distinct. Extra postinstall domains remain private policy;
add them with `sg-whitelist-domain 15d <domain>` after inspecting the failed
request.

The Hermes image includes native plugin test tooling in
`/opt/hermes/.venv`: `pytest==9.1.1`, `iniconfig==2.3.0` and `pluggy==1.6.0`,
pinned to the current upstream lock. PyYAML 6.0.3 is supplied by the runtime.
The build installs only these test packages without resolving or replacing
their dependencies, checks pytest and PyYAML with the native interpreter, and
checks the resulting environment for dependency conflicts. Additional Python
packages in the Dockerfile are pinned to the versions validated in the running
image; this does not freeze their complete transitive dependency closure.

An existing image without pytest needs a rebuild and container recreation;
restarting its gateway does not install packages. Do not write into the live,
read-only `/opt/hermes` install tree or sync the full upstream development
group. Before a Hermes build, require more than 10 GB free and retain a rollback
image. Building an image does not activate it. After recreation, use
`/opt/hermes/.venv/bin/python -m pytest --version` and the plugin's native
prerequisite check before running its pending tests and accepting it. A gateway
restart alone cannot substitute for those checks.

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

For routine gateway restarts, use the graceful restart helper:

```sh
sg-hermes-restart            # default gateway, including its multiplexed profiles
sg-hermes-restart coach      # a separate running gateway for this profile
# Equivalent grouped form: sg-hermes restart [profile]
```

This signals the existing s6-supervised gateway with SIGUSR1, allowing active
work to drain before s6 relaunches it. The signal is sent as the gateway process
owner because the root supervisor lacks CAP_KILL. It skips container initialization
and waits up to 180 seconds for a replacement PID, returning an error if none
appears. Platform/API readiness can still take longer. Missing or
stopped profile gateways produce an error; the helper never starts them. Profiles
served by the default multiplexer use the default restart command.
`sg restart hermes` / `sg-restart-hermes` still restart the whole container.
Changed Compose environment or mounts require recreation instead.

## Pocket TTS

Pocket TTS 2.1.0 runs as an optional local English/Spanish sidecar.
Hermes uses its existing command-provider interface. The client is baked into
the root-owned Hermes image rather than mounted over `/opt/hermes`. The defaults
are Anna for English and Eponine for Spanish;
automatic detection is heuristic, so prefix short or ambiguous text with
`[en]` or `[es]` to force the language.

Enable `pocket-tts` beside the selected browser profile in
`private/env/local.env`; browser-mode helpers preserve it once enabled. Set the
private cache path there as well. Keep at least 8 GiB free before the first prefetch:

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

After the cache and provider are configured and the private profile enables it,
normal full-stack startup includes Pocket TTS:

```sh
sg up -d
sg ps pocket-tts hermes
```

The configuration helper is idempotent, keeps the first pre-Pocket config
backup, and restores the previous provider with `--disable`. A stopped or
unhealthy sidecar returns an explicit `pocket-tts` error; Hermes does not fall
through to Edge TTS. See `infra/pocket-tts/README.md` for validation and
rollback details.

## Raw Compose Start

The tier helper path is preferred because it selects the expected Compose files
and env file. If you use raw Compose for L4, select the active browser and add
`pocket-tts` only when configured; `pocket-tts-bootstrap` is never persistent.

```sh
docker compose -f infra/compose/compose.yml --env-file .env up -d
```

## Bounded Workstation Access

`sg-workstation` operates manual, host-side SSH and application relays to one
Tailscale-connected PC while Hermes remains on `spartan_internal`. Configuration,
keys, host-key verification, profile setup, Blender risk, and teardown are
documented in [Bounded Workstation Access](workstation.md).

`sg-workstation status` distinguishes PID liveness from terminal and app-tunnel
readiness. Only the app-tunnel SSH child is automatically restarted; its
execution, Blender, application-relay, and terminal peers remain untouched. The
SSH child is parent-death-contained and `down` reconciles it by PID identity. A
legacy running state is migrated on the next `up` by replacing only
`app-tunnel`; every other managed PID remains unchanged.
Mutating workstation commands are serialized by an owner-only operation lock;
SSH connection and readiness attempts are bounded before backoff.

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
the intended account's current grant was rejected terminally. Restart only
Spartan Route with `sg restart spartan-route`, then use the slot's focused
**Check usage** action: a successful refresh repairs managed state without changing the
read-only bootstrap file. Only if the slot still reports `reauth_required`, log
in again in that slot's original Codex home (for example,
`CODEX_HOME=/path/to/original-slot-home codex login --device-auth`) and retry.
Changed bootstrap credentials are detected automatically, including on healthy
slots with cached auth. If a slot remains stale after changing its login or
account, reload only Route with `sg restart spartan-route`, then run that slot's
focused **Check usage** action. No Hermes rebuild is needed. Do not delete the
managed store or copy credentials from another slot.
`refresh_rate_limited`,
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
sg-memory
sg-connectivity-check
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

### Host connectivity status

`sg-health` appends the sanitized host snapshot published by the local Wi-Fi
watchdog at `/run/check-wifi-status.json`. The versioned snapshot contains only
enumerated local/WAN/DNS/Tailscale states, failure counters, and timestamps; it
does not contain an SSID, interface, gateway, address, connection UUID, or
Tailscale identity. Missing, malformed, future-dated, and older-than-five-minute
snapshots are reported explicitly.

The added line is informational and does not change `sg-health` exit behavior.
For monitoring that must fail closed when the snapshot is unavailable, stale,
or degraded, use:

```sh
sg-connectivity-check
```

The watchdog remains the owner of host recovery. These helpers do not probe the
Internet, restart Wi-Fi, restart Tailscale, or restart containers.

### Memory and swap audit

Install the per-user daily audit once:

```sh
scripts/setup-memory-audit.sh install
```

The persistent systemd timer runs at 04:20 with up to ten minutes of randomized
delay. It summarizes the previous 24 hours from the host's existing `sysstat`
archive, then records current RAM, swap, memory PSI, Docker cgroup limits,
restarts, OOM signals, and the expected 2 GiB swap/`vm.swappiness=10` contract.
It only reads state and publishes a bounded status snapshot; it never runs
`swapoff`, drops caches, restarts containers, or prunes Docker data.

Use the sourced helpers to inspect or enforce the result:

```sh
sg-memory                 # informational; equivalent to `sg-memory show`
sg-memory check           # non-zero for stale, warning, or critical status
sg-memory collect         # refresh now; collection itself remains read-only
```

`sg-health` appends the informational memory line without changing its existing
exit behavior. A high swap occupancy by itself is healthy: the audit warns on
active swap-out churn, low available RAM, PSI pressure, cgroup saturation,
restarts/OOM events, missing history, or memory-contract drift.

### Interactive tmux sessions

The public `sg-tmux` group and the personal shortcuts in
`private/aliases.local.sh` cover the normal session workflow:

```sh
tnew scratch                         # named interactive shell
tcodex gate-work                     # Codex with the normal $HOME/.codex home
tcodex review gate-work              # CODEX_HOME=$HOME/.codex_review codex
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

For `tcodex`, an existing `$HOME/.codex_<profile>` may be placed before the tmux
session name. The session keeps exactly the requested name, and `CODEX_HOME` is
set only for the Codex process. Remaining arguments are forwarded unchanged;
Codex's `-p/--profile` option selects a layered config inside the chosen
`CODEX_HOME` and does not select the home itself.

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
`GOGCLI_EXTRA_SCOPES`. The default extra scopes include Google Ads
(`https://www.googleapis.com/auth/adwords`). If you set a nonempty
`GOGCLI_EXTRA_SCOPES` override, include that scope explicitly. Run the login
again to grant newly added scopes. Google Ads API calls also require a developer
token. Verify the active grant at any time with:

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

## Hermes log remediation

`scripts/hermes_log_remediation.py` prepares a bounded profile migration. It
adds a missing internal `SEARXNG_URL` to existing profile `.env` files, removes
dormant retired reset timers, and removes absent legacy plugin names only when
their command owner is enabled. It preserves existing search overrides, tool
allowlists, cron destinations, unrelated values, ownership, and file modes.
Missing `.env`, duplicate settings, symlinks, or changed preflight inputs abort.

```sh
python3 scripts/hermes_log_remediation.py --home /path/to/hermes-data
```

`--repair-plugins` also prepares deterministic repairs for installed node-search
and Toolaria sources; `--talk-plugin /path/to/talk-plugin` includes transcript
worker context propagation. Toolaria uses the native profile home, requires
session identity for retrieval and pass-reference expansion, and keeps audit
out of the model tool. If ambient retrieval is unavailable, oversized results
pass through intact rather than becoming inaccessible handles. Existing shared
store handles are not copied into other profile stores.

Apply only as the data owner with `--apply --backup-dir` pointing to an empty
owner-only directory. Every candidate is validated before writes; originals
and a hash/ownership manifest are retained, and a failed write rolls back
completed replacements. Quiesce affected profile writers during application.
Reloading plugin source is required before the running gateway uses it.

For the diagnosed empty-feature PM generation,
`scripts/hermes_dependency_recovery.py` runs under the image's original Python.
Its default is a read-only preflight against an explicit facts fingerprint.
Application backs up the full record and uses native PM locking/publication
to return to the image dependencies, restoring their actual feature selection
and preserving the original stamp. It does not install packages or delete
generations. This is recovery, not a permanent replacement for rebuilding a
correctly declared dependency graph; future PM synchronization can rebuild it.
Verify enabled plugin imports, cold launcher selection, and each configured
adapter as well as container health before accepting activation.

Keep at least 5 GB free for runtime operations unless the operator explicitly
authorizes a bounded exception. OOM tuning, automatic session resets, trust
expansion, and checkpoint history deletion are separate operations.

For a Discord catalog reporting absent superseded command plugins, use
`--discord-catalog-only` with the existing migration helper. This changes only
absent legacy plugin names when their replacement owner is enabled. It does
not modify search, reset timers, tools, cron, or remote application commands.
Quiesce the affected profile writers before applying; the operator lock only
serializes this helper. Rollback restores only destinations still matching the
applied version and preserves concurrent edits, retaining the private manifest.
Connection health does not prove message admission, execution or delivery.
Keep the native remote-command plan approval and rate-limit backoff intact.

## Hermes image-only dependency admission

`SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY=1` selects the existing baked-image
contract. Its startup diagnosis now validates the actual executing interpreter,
feature inventory, enabled plugin dependency declarations and their installed
transitive requirements. Changing these inputs during admission rejects startup;
publish plugin changes only with a fresh admission. Validation performs no
resolution, installation, generation collection or PM facts writes.

In this mode, PM's writable-generation fingerprint is not the dependency-health
verdict. The stamp includes mutable plugin members and can remain different
after an image rebuild. Raw `pm.check()` remains available and unchanged;
successful image admission does not claim full reproducibility against `uv.lock`.
Other PM tool failures remain visible. Invalid image authority, a selected
generation, missing/incompatible dependencies or validation failure exits nonzero
before CLI/gateway dispatch instead of becoming a swallowed startup warning.

An existing image can use the guarded read-only adaptation:

```sh
python3 scripts/hermes-image-dependencies-overlay.py prepare
sg config --quiet
sg up -d --no-deps --force-recreate hermes
```

This temporary overlay is scoped to the L4 main stack; remove it before
starting Hermes in another tier. The helper pins the running container's immutable image and fingerprints source,
anchors and rollback originals. `sg` checks those fingerprints and blocks Hermes
rebuilds while `private/compose.hermes-image-dependencies.yml` exists. The next
image includes the same adaptation. Before changing images, remove the overlay
and verify the new image's admission. To roll back this adaptation on the same
image, retain the private bundle, remove that overlay and recreate only Hermes;
the original generation warning may return. Do not restore session databases or
browser profiles. Each startup admission is bounded to 90 seconds and its cost
must be included in readiness checks.

## Hermes fast-mode overlay

For a Hermes image without `patch_spartan_fast_mode.py`, prepare the temporary
image-pinned code overlay, build only Spartan Route, then recreate both services:

```sh
python3 scripts/hermes-fast-mode-overlay.py prepare
source scripts/aliases.sh
sg config --quiet
sg build spartan-route
sg up -d --no-deps --force-recreate spartan-route hermes
```

The helper publishes ignored `private/compose.hermes-fast-mode.yml` only after
source and Compose validation. `sg` and tier helpers load it alongside the
Camofox policy overlay. It mounts only the patched model resolver, validator,
manifest and native s6 init hook read-only; it leaves profile settings intact.
Image, source, manifest or startup-hook drift stops initialization. The existing
Spartan browser-policy helper must be present for native s6 caller validation.
Preparation normally requires 5 GiB free. `--allow-low-disk` is reserved for an
explicit operator exception and does not authorize a Hermes rebuild.

Keep the original private bundle and rollback router image before activation.
To roll back, move `private/compose.hermes-fast-mode.yml` out of its active
filename, restore the previous Spartan Route image tag, and recreate just
`spartan-route` and `hermes` with the command above. Keep the Camofox overlay
active. Preparing after deployment captures already-patched code; retain the
original source snapshot separately.

Before the next Hermes build, deactivate the fast-mode override and retain its
bundle for rollback. `sg` blocks rebuilding Hermes while the override is active;
the future image applies the patch directly. Never reuse a bundle on a different
image. Verify authenticated gateway/router health and resolver behavior after
activation. See [supported commands](spartan-route.md#hermes-fast-mode).

## Camofox Profiles

When `CAMOFOX_URL` selects Camofox, Spartan Gate enforces that browser across
all Hermes profiles. Endpoint/authentication enter the native managed secret
scope before gateways start; identities, sessions and persistent browser data
stay profile scoped. Built-in browser tools remain available and `browser_exec`
is disabled. A terminal environment without `CAMOFOX_URL` is not a backend test.

For an existing Hermes image that predates the managed-policy patch, prepare
the fingerprinted code overlay without rebuilding:

```sh
python3 scripts/hermes-browser-policy-overlay.py prepare
source scripts/aliases.sh
sg config --quiet
sg up -d --no-deps --force-recreate hermes
```

The helper creates ignored `private/compose.hermes-browser-policy.yml` and an
immutable private code bundle. `sg` and layer helpers load that override after
the normal Compose files. Only Hermes is pinned; other images/services are
unchanged. Preparation validates all source transformations and Compose before
publishing. Startup checks mounted code, unmounted image anchors and policy
files, then imports the selectors as the workload user in a disposable home.
Failures stop initialization without exposing credentials. The policy helper
is also installed in the Hermes project root because the native launcher
replaces `PYTHONPATH`; the cold import check uses that same project import
path. Docker health alone does not establish browser-tool readiness: after
activation, verify built-in tools and a Camofox navigation in each affected
profile. Validate the init hook through container initialization, not direct
invocation: it requires the native s6 cont-init caller and `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`. Unsupported or
manual invocation intentionally waits and must be interrupted.

Keep the overlay until a Hermes build includes `patch_camofox_managed_policy.py`.
The helper rejects image drift, and `sg` blocks a Hermes rebuild while the
override remains active. Before the next planned build, move the overlay out
of its active filename, retain its bundle for rollback, then build and validate
the new image under the normal disk gate. Do not reuse the old code overlay on
a different image. Reverting to the pre-fix runtime requires restoring the
prior Compose boot wiring as well as removing the overlay, because the strict
new boot hook deliberately rejects unadapted code. Retain the pre-deployment
container configuration and code bundle for rollback. Preparing again after
deployment captures the currently installed, already-patched selectors; those
captures do not replace the original pre-deployment snapshot.

After changing the selected browser endpoint, authentication, or administrator
managed source, recreate Hermes with `sg up -d --no-deps --force-recreate hermes`.
A restart keeps the existing container environment and does not apply changed
Compose environment. Recreation regenerates the `/run` policy from the new
configuration. Initialization rejects changes to an existing policy instead
of partially replacing it.

For gated deployments, secondary-profile terminals can lose proxy variables
when Hermes removes the launch profile's dotenv settings. Pin only the shared
network settings through Hermes' native managed scope; no Hermes source patch
or image rebuild is required. Create `private/hermes-managed/proxy.env` with
`HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy`, `NO_PROXY`, and
`no_proxy`, using the effective values from the selected Compose configuration.
Keep both capitalizations consistent and preserve the internal-service bypass
list. Do not add `ALL_PROXY` when the deployment does not configure it.

Add this mount to the Hermes service in `private/compose.local.yml`:

```yaml
volumes:
  - type: bind
    source: ../../private/hermes-managed/proxy.env
    target: /etc/hermes/.env
    read_only: true
    bind:
      create_host_path: false
```

Keep the source directory owner-only (`0700`) and the file owner-readable and
writable (`0600`). The root initialization helper merges these settings with
the browser policy into the existing protected runtime managed layer. If an
administrator managed source already exists, merge the six settings into it
instead of replacing its mount or discarding its other settings. A custom
`HERMES_MANAGED_SOURCE_DIR` requires the corresponding mount destination.

Never mount `private/env/local.env` as the managed dotenv or copy its complete
contents there: Compose interpolation and shared Hermes policy have different
purposes, and doing so would share profile/provider credentials across bots.
Leave credential files and existing service `env_file` entries unchanged.
Docker recommends [service-scoped secret files](https://docs.docker.com/compose/how-tos/use-secrets/)
for credentials; this network-only managed dotenv must not become a shared
credential store. Migrating existing credential consumers is a separate change.
Validate with `sg config --quiet`; at the planned activation, recreate only
Hermes with the command above and verify proxy inheritance in a secondary
profile's terminal. Re-render and update the six-key file when deployment proxy
settings change. Configuration preparation alone does not change running sessions.
The same native residue filter can remove other launch-dotenv settings, such
as `SEARXNG_URL`, `OPENAI_BASE_URL` and `PLAYWRIGHT_BROWSERS_PATH`. Decide their
scope individually; this fix shares only the six network keys and does not
promote provider endpoints or credentials into global policy.

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
inside the deterministic, profile-scoped Camofox identity derived from that
Hermes profile's `HERMES_HOME`. Hermes registry tab groups are keyed by the
durable Hermes `session_id`, so operator and agent tabs share profile cookies
but never adopt or navigate each other's tab. Operator creation is
non-destructive and carries a renewable 30-minute lease. Capacity cleanup,
idle reaping, session timeout, and soft-memory pressure cannot recycle it while
leased. The helper verifies versioned, authenticated safety and lease
capabilities before creating the tab; an older running image fails closed.
Reopening the helper renews and reuses the existing operator tab. Failed
operator navigation removes only a newly created tab. Camofox structured logs
strip query strings and fragments from page URLs, including OAuth callbacks.

Before opening any derived operator tab on an upgraded stack, finish active
browser work and run the offline migration:

```sh
sg-camofox-profile-isolate apply
```

`profile-live` fails closed until the migration journal is applied.

For a profile Hermes already uses, only open and checkpoint the login; do not
run `use` or recreate services:

```sh
sg-camofox-profile-live main https://accounts.google.com
sg-camofox-profile-save main
sg-camofox-profile-close main
```

`save` invokes an authenticated server-side checkpoint and succeeds only after
valid storage state is atomically durable. `close` checkpoints first and then
deletes only the reserved operator tab. A failed checkpoint retains the live
context for retry. Managed Camofox storage preserves cookies and origin
localStorage; it is not a complete Firefox profile and does not promise
extensions, preferences, downloads, IndexedDB, or every site-storage API.

For first-time profile selection after migration and manual login, run:

```sh
sg-camofox-profile-use main
sg-browser-mode-apply
```

`sg-camofox-profile-use` rechecks the offline transaction before changing the
private env, then clears legacy global ownership and leaves global adoption
disabled. The initial migration helper
records service state, stops Hermes and Camofox, discovers the default and every
existing named Hermes profile, and copies the legacy login into all of their
derived identities. Existing derived targets and every changed config are
archived and verified before publication; failure or interruption restores the
complete prior state, and previously running services are restarted. Repeated
apply is a no-op, so it never overwrites a login created after migration. New
profiles created later start empty. During the retained rollback window use:

```sh
sg-camofox-profile-isolate rollback
```

Credential files remain opaque throughout and their contents are never logged.
Google Meet remains an explicit single-owner compatibility path for the default
profile. Use `sg-camofox-profile-reset <profile>` only for an intentional
destructive reset, never for recovery.

For HTTPS, configure a certificate-valid `TAILSCALE_HOST` that resolves to
`TAILSCALE_IP`. The helper aborts instead of using the raw IP when DNS is wrong.
For add-ons, mount `SPARTAN_CAMOFOX_ADDONS_PATH` read-only and set
`CAMOFOX_ADDONS` to comma-separated container paths below
`/opt/camofox-addons`.

## Wrangler Authentication

Prefer an API token for unattended work:

```sh
sg-wrangler-token
sg up -d hermes
sg-wrangler whoami
```

The token prompt is hidden, the value is stored only in the private env file,
and an existing token survives installer reruns. Hermes must be recreated
before the new Compose environment is visible.

For OAuth, run `sg-wrangler-login`. Open the printed URL in the Camofox noVNC
browser, finish authorization, then copy the final
`http://localhost:8976/oauth/callback?...` URL from the browser bar into the
hidden terminal prompt. The browser cannot reach Hermes' loopback callback
directly; the helper validates the exact localhost/port/path plus state and
submits it from inside Hermes. Callback secrets never enter argv or environment.
Wrangler is pinned to 4.45.0, its process group is always reaped, and successful
OAuth credentials persist in the Hermes data mount under `/opt/data`.

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
Cgroup policy rejects new browser sessions at 75% memory. At or above 82% for
two consecutive five-second samples, Camofox checkpoints and closes at most one
least-recently-used non-leased context, then latches until usage falls below
70%. Operator tabs are never soft-eviction victims. A cgroup OOM event or 90%
hard threshold triggers immediate supervised recovery and may override an
operator lease because retaining the process is no longer safe.

A PID-1 supervisor validates a real raw context/page lifecycle.
Public GET /health is now a passive, generation-bound view of the same proof
and never creates a page in its request path; GET /live remains
browser-independent event-loop liveness. Three terminal real-navigation
timeouts latch the current browser generation unhealthy. The next supervisor
poll performs at most one recovery for that child PID/generation tuple, and a
replacement is healthy only after a different generation passes a deep probe.
A busy owner is healthy only while it remains inside its declared deadline and
the last completed real probe is at most 45 seconds old; busy never refreshes
that proof. Once the proof becomes stale, health remains 503 but bounded busy
samples do not consume the supervisor's recovery threshold. A real-ready probe
resets earlier failures; an owner that exceeds its deadline reports a terminal
failure and advances recovery normally. A queued create is registered before
lifecycle admission, and an overdue token also makes deep health fail. The
token uses the effective request
deadline, including the bounded proxy-rotation allowance, rather than the base
30-second handler timeout. Dead-session cleanup invalidates a create bound to
that exact context and defers close until its underlying promise settles.
The unsafe deprecated `POST /tabs/open` alias returns HTTP 410; use
`POST /tabs`. Overdue or stale state is unhealthy.
The supervisor performs one
internal recovery after three
failures, and then recycles its child process tree under persistent exponential
backoff. Delays through two minutes are handled inside PID 1. A later escalation
clears only the pending wait timestamp, exits non-zero, and relies on Compose's
`restart: unless-stopped` policy for immediate container restart; profiles,
cookies, and the accumulated recycle level remain persistent. The supervisor
does not use Docker control APIs. If an unkillable `D` process survives, Docker
cannot finish the old container and no second browser is launched. Preserve
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
