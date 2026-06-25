# Environment

Start from `.env.example`.

Public env docs describe names and behavior only. Real values and machine-specific explanations belong in `private/docs/`.

## Edge

- `SPARTAN_BIND_LOCALHOST`: public host bind, default `127.0.0.1`.
- `SPARTAN_GATE_PORT`: Caddy edge port for the Hermes API.
- `CLAWROUTE_EDGE_PORT`: Caddy edge port for ClawRoute.
- `BROWSERLESS_DEBUG_PORT`: Caddy edge port for Browserless debugging.
- `HERMES_DASHBOARD_PORT`: Caddy edge port for the Hermes dashboard.
- `CADDY_AUTH_USER`: Basic Auth username.
- `CADDY_AUTH_HASH`: Caddy password hash.
- `BROWSERLESS_EDGE_TOKEN`: generated edge token added to Browserless debugger
  WebSocket URLs by the profile-live helper. It prevents unauthenticated
  Browserless WebSocket upgrades without relying on browser Basic Auth for the
  WebSocket handshake.
- `SPARTAN_INTERNAL_SUBNET`: internal Docker subnet.
- `SPARTAN_DNS_IP`: fixed DNS relay address inside that subnet.
- `SPARTAN_ALLOW_EXISTING_STACK_PATHS`: keep `false` for fresh installs; set
  `true` only for intentional cutover to old/existing data paths with the old
  stack stopped.

Bcrypt hashes contain `$`. Wrap them in single quotes in `.env` files, or escape each dollar sign as `$$` in private env files copied from older stacks.

Tailscale binds such as `TAILSCALE_IP=100.x.y.z` belong in your private env file.

## Hermes

- `SPARTAN_HERMES_DATA_PATH`: private host path mounted as `/opt/data`.
  Spartan Gate keeps `/opt/hermes` root-owned and immutable to Hermes workloads.
- `SPARTAN_HERMES_RUN_UID` and `SPARTAN_HERMES_RUN_GID`: optional runtime
  identity for Hermes workloads. Set both to the host owner of
  `SPARTAN_HERMES_DATA_PATH` when the data folder should behave like a normal
  user-owned directory on the host. The container still starts as root for s6
  initialization, then Hermes processes drop directly to this numeric UID/GID
  without modifying `/etc/passwd` or `/etc/group`. Leave unset to use the image
  default Hermes identity.
- `SPARTAN_PROXY_AUDIT_LOG`: Hermes Python HTTP audit destination. Defaults to
  `/opt/data/logs/proxy-audit.log`; set to `stderr` only for debugging when
  audit lines should appear in process stderr.
- `HERMES_API_KEY`: API server bearer token.
- `HERMES_GATEWAY_TOKEN`: gateway/shared runtime token.
- `HERMES_MAX_ITERATIONS`: maximum iterations per task.
- `HERMES_AUTOSTART_PROFILES`: legacy bootstrap comma-separated profile names.
  On first boot it seeds missing per-profile `gateway_state.json` files as
  `running` so upstream s6 reconciliation starts them. Existing gateway state
  wins, so profiles stopped with `hermes -p <profile> gateway stop` stay
  stopped across restarts.
- `HERMES_TELEGRAM_BOT_TOKEN` and `HERMES_TELEGRAM_ALLOWED_USERS`: main Telegram gateway.
- `HERMES_TELEGRAM_BOT_TOKEN_<PROFILE>` and `HERMES_TELEGRAM_ALLOWED_USERS_<PROFILE>`: dedicated Telegram credentials for gateway profiles.
- `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS`, `DISCORD_HOME_CHANNEL`: main Discord gateway.
- `DISCORD_BOT_TOKEN_<PROFILE>`, `DISCORD_ALLOWED_USERS_<PROFILE>`, `DISCORD_HOME_CHANNEL_<PROFILE>`: dedicated Discord credentials for gateway profiles.
- `GOOGLE_PROJECT_ID` and `GOOGLE_CLOUD_PROJECT`: optional Google/GogCLI/MCP project identifiers.

Profile-specific variables must be present in your private env file and exposed in `private/compose.local.yml` if the profile `.env` references them.
Named profiles that enable the API server need explicit, non-conflicting
`API_SERVER_PORT` / `platforms.api_server.port` values. Spartan Gate no longer
auto-increments occupied ports because s6 profile ports must be stable.

Hermes profiles are not Browserless profiles. Hermes profiles control gateway
and agent configuration; Browserless profiles control Chromium login state.

## Browserless

- `BROWSERLESS_TOKEN`: internal bearer token shared between Browserless,
  Hermes, and Caddy. Do not expose it in profile-live URLs; Caddy injects it
  only when proxying to Browserless.
- `BROWSERLESS_IMAGE`: Browserless container image, default
  `ghcr.io/browserless/chromium:v2.51.0`. Pin this in private env during
  incidents before recreating Browserless.
- `BROWSERLESS_PROFILE`: named Chromium login profile exposed as Hermes browser
  profile `main`, default `main`. The normal/default browser path is ephemeral
  and does not use this `userDataDir`.
- `BROWSERLESS_CDP_BROKER_ENABLED`: run the local shared CDP broker for the
  optional persistent `main` browser profile, default `true`. The default
  browser path does not go through the broker. If this is disabled, Hermes will
  only expose the persistent `main` browser profile when `BROWSER_CDP_MAIN_URL`
  is supplied explicitly. In Browserless mode the broker is supervised by s6 and
  shared by all Hermes profiles through the configured `BROWSERLESS_PROFILE`.
- `BROWSERLESS_CDP_BROKER_PORT`: internal-only Hermes broker listener port,
  default `9229`.
- `BROWSERLESS_CDP_BROKER_TRACE`: verbose per-command CDP broker logging for
  incident debugging, default `false`.
- `BROWSERLESS_SESSION_TIMEOUT_MS`: Browserless maximum session lifetime,
  default `2147483647` (about 24.8 days). This mainly matters for the optional
  long-lived `main` profile broker; default ephemeral launches are short lived.
- `BROWSERLESS_WS_BASE`: Browserless CDP launch endpoint used by Hermes, default
  `ws://browserless:3000/chromium`.
- `BROWSERLESS_HEADLESS`: Browserless launch headless mode, default `true`.
- `BROWSERLESS_STEALTH_ENDPOINT`: whether to force Browserless launch endpoint
  paths for ephemeral, persistent, and profile-seeding launches, default `true`.
- `BROWSERLESS_ROUTE`: Browserless launch endpoint family, default `chromium`.
  Use `stealth`, `chromium-stealth`, or `chrome-stealth` only after canary
  testing against the target flow.
- `BROWSERLESS_STEALTH_ROUTE`: legacy alias for `BROWSERLESS_ROUTE`; keep unset
  in new configs unless you need compatibility with an older override.
- `BROWSERLESS_LANG`: Chromium `--lang` value, default `es-ES`.
- `BROWSERLESS_USER_AGENT`: optional diagnostic user-agent override, default
  unset. Keep unset unless testing a specific fingerprint hypothesis.
- `BROWSERLESS_TZ`: timezone exposed by the Browserless container, default
  `Europe/Madrid`; keep it aligned with the account's normal login geography.
- `BROWSERLESS_IGNORE_DEFAULT_ARGS`: advanced debugging override for Browserless
  launch defaults, default `false`; keep disabled for normal account login flows.
- `BROWSERLESS_DEBUG_ORIGIN`: optional externally reachable debugger origin for
  overriding printed profile-seeding URLs. When unset, the helper prints local
  HTTP and available Tailscale URLs, or the TLS hostname when HTTPS is configured.
- `SPARTAN_EDGE_SCHEME`: optional `http` or `https` edge scheme. The helper also
  infers HTTPS when private Caddy certificate variables are set. With HTTPS,
  configure `TAILSCALE_HOST` to the hostname covered by the certificate.
- `SPARTAN_BROWSERLESS_PROFILES_PATH`: private host path mounted to `/profiles`
  in the Browserless container by `private/compose.local.yml`.
- `BROWSERLESS_PROFILES_GID`: host group ID with write access to the mounted
  Browserless profiles path, default `1000`; Browserless joins this group.
- `BROWSERLESS_DEBUG_AUTOSTART`: whether seeded debugger URLs click run
  automatically, default `true`.
- `BROWSERLESS_LIVE_TIMEOUT_MS`: legacy maximum interactive seeding duration,
  default `900000`.
- `BROWSERLESS_LIVE_QUALITY`: JPEG stream quality for profile seeding, default
  `90`.
- `BROWSERLESS_LIVE_RESIZABLE`: legacy interactive-session option retained for
  older Browserless images; the current debugger flow uses a fixed viewport.

Browserless profile names are path segments under `/profiles`; use simple names
such as `main`, `work`, or `search-a`. Spartan Gate uses two browser modes:
`${BROWSER_CDP_URL}` is the normal ephemeral Browserless launch. Both
that path and the optional persistent profile use `BROWSERLESS_ROUTE=chromium`
with `stealth=true` by default. `${BROWSER_CDP_MAIN_URL}` is the optional persistent login profile. The broker is
only for that persistent profile, because Browserless allows one live Chromium
process per `userDataDir`. The broker can remove stale Chromium `Singleton*`
lock files from the selected persistent profile only after Browserless reports
no sessions and zero running jobs.

For private installs, always verify the rendered Compose config with `sg config`
or `sg-browserless-snapshot`; private paths such as
`SPARTAN_BROWSERLESS_PROFILES_PATH` override public `runtime/` defaults.

`HERMES_MEET_CDP_PROFILE` controls the Google Meet plugin browser mode. Keep the
default `guest` to use the ephemeral Browserless launch; set it to `main`, or
to the configured `BROWSERLESS_PROFILE` name, when that persistent Browserless
profile is already signed in and Meet should join as that Google account. For a
Google-account Meet bot, prefer `sg-hermes-chrome-profile google2`, then
`sg-hermes-meet-join <meet-id-or-url>`. The join helper writes
`HERMES_MEET_CDP_PROFILE` plus the real host Chrome WebSocket
`HERMES_MEET_CDP_URL=ws://.../devtools/browser/...` to `private/env/local.env`.
Do not use the plain `http://...:9222` endpoint. Use
`sg-browserless-profile-live google https://accounts.google.com` only when you
explicitly want the Browserless web debugger while Hermes is stopped.

When `CAMOFOX_URL` is set, the Google Meet plugin uses Camofox for
`mode=transcribe` and ignores stale `HERMES_MEET_CDP_URL` values. Realtime
audio remains Browserless/CDP-only; Camofox mode falls realtime requests back
to transcribe.

## Camofox

Camofox is an optional local browser backend for Hermes browser tools. It runs
as the internal Compose service `camofox`, but Hermes keeps using Browserless
unless `CAMOFOX_URL` is set.

- `COMPOSE_PROFILES`: Compose browser service selector. Use `browserless` when
  `CAMOFOX_URL` is empty and `camofox` when `CAMOFOX_URL=http://camofox:9377`.
  `CAMOFOX_URL` is the canonical Hermes mode selector; helpers reject an
  explicit `COMPOSE_PROFILES` value that disagrees with it.
- `CAMOFOX_URL`: optional Hermes Camofox endpoint. Leave empty to keep
  Browserless as the default browser/CDP backend; set to
  `http://camofox:9377` to opt in for generic Hermes browser tools and Google
  Meet transcribe mode.
- `CAMOFOX_BASE_IMAGE`: pinned upstream Camofox base image, default
  `ghcr.io/jo-inc/camofox-browser:1.11.2`.
- `CAMOFOX_IMAGE`: local Spartan Gate Camofox wrapper image, default
  `spartan-gate-camofox:1.11.2`. The wrapper pre-fetches Camofox's default
  add-on at build time so the runtime service can stay internal-network only.
- `CAMOFOX_ACCESS_KEY`: bearer token required by protected Camofox routes.
- `CAMOFOX_API_KEY`: optional dedicated key for cookie/storage-related routes.
- `CAMOFOX_ADMIN_KEY`: optional dedicated key for admin stop behavior.
- `CAMOFOX_USER_ID`: stable Camofox user/profile identity used by Hermes, for
  example `spartan-camofox-main`.
- `CAMOFOX_SESSION_KEY`: Camofox session key for the selected profile, default
  manual-login helper value `manual-login`.
- `CAMOFOX_ADOPT_EXISTING_TAB`: when true, Hermes should adopt the live/manual
  tab for the selected Camofox session when possible.
  `sg-camofox-profile-use` writes these profile-selection keys to the private
  env file, and Compose passes them through to the Hermes container.
- `CAMOFOX_GEOIP`: whether the Spartan Gate Camofox wrapper lets upstream
  Camofox run public-IP geo probes for configured proxies, default `false`.
  Keep false for the local Tinyproxy path; only enable for a proxy provider
  where those probes are intended and whitelisted.
- `SPARTAN_CAMOFOX_DATA_PATH`: private host path mounted to `/root/.camofox`
  by `private/compose.local.yml`.
- `SPARTAN_CAMOFOX_ADDONS_PATH`: private host directory mounted read-only at
  `/opt/camofox-addons` when Camofox mode is enabled.
- `CAMOFOX_ADDONS`: comma-separated extracted Firefox add-on directories
  inside the container, normally below `/opt/camofox-addons`. Leave empty when
  no private add-ons are required.
- `CAMOFOX_ENABLE_VNC`: enable Camofox noVNC support, default `0`.
- `CAMOFOX_VNC_PASSWORD`: password for VNC/noVNC when VNC is enabled.
- `CAMOFOX_NOVNC_PORT`: private noVNC host port served through Caddy. The
  default Caddyfile listens on container port `26080`. In HTTPS setups,
  `TAILSCALE_HOST` must resolve to `TAILSCALE_IP` and match the certificate;
  the helper aborts instead of falling back to a raw IP.
- `CAMOFOX_MAX_CONCURRENT_PER_USER`, `CAMOFOX_MAX_SESSIONS`,
  `CAMOFOX_MAX_TABS_PER_SESSION`, and `CAMOFOX_MAX_TABS_GLOBAL`: Camofox
  resource limits.
- `CAMOFOX_SESSION_TIMEOUT_MS`, `CAMOFOX_TAB_INACTIVITY_MS`, and
  `CAMOFOX_BROWSER_IDLE_TIMEOUT_MS`: Camofox idle/session cleanup windows.
- `CAMOFOX_TRACES_MAX_BYTES` and `CAMOFOX_TRACES_TTL_HOURS`: local trace
  retention limits.
- `CAMOFOX_MAX_OLD_SPACE_SIZE`: Node heap limit passed to the Camofox process.

Camofox uses Camofox's `PROXY_HOST=proxy` and `PROXY_PORT=8888` browser proxy
configuration in Compose, plus the generic `HTTP_PROXY`/`HTTPS_PROXY` env vars.
Browserless remains required for CDP, Google Meet realtime/CDP flows, Chrome
DevTools MCP, and Browserless profile seeding. See `infra/camofox/README.md`.

## ClawRoute

- `CLAWROUTE_TOKEN`: shared bearer token between Hermes and ClawRoute.
- `CLAWROUTE_BASELINE_MODEL`: default model when no route matches.
- `CLAWROUTE_PROVIDER`: optional provider profile override.
- `CLAWROUTE_DEBUG`: redacted debug logging.
- `CLAWROUTE_DRY_RUN`: classify without forwarding.
- `CLAWROUTE_LOG_CONTENT`: sanitized, truncated user/assistant/tool-result
  previews in routing telemetry. The default `false` stores structure only.
  System prompts, complete tool arguments, schemas, binary content, and data
  URLs are never included in the live-routing view.

Provider keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, and `OPENROUTER_API_KEY`.
Additional providers: `XAI_API_KEY` and `STEPFUN_API_KEY`.
Codex subscription routing: `OPENAI_CODEX_TOKEN`, `OPENAI_CODEX_AUTH_PATH`,
`OPENAI_CODEX_AUTH_PATHS`, `CODEX_HOME`, `CODEX_BALANCE_LOADER_MODE`,
`CODEX_SCHEDULE_START_DAY`, `CODEX_EARLY_ACTIVATION_ENABLED`,
`CODEX_EARLY_ACTIVATION_WEEKLY_PERCENT`,
`CODEX_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT`,
`CODEX_COLD_MIGRATION_DECISION_TTL_HOURS`, `CODEX_ROTATION_INTERVAL_HOURS`, and
`CODEX_ROTATION_IDLE_MINUTES`. For multi-account Codex pools,
`CODEX_BALANCE_LOADER_MODE=shadow` computes the recommendation while keeping
legacy slot choice. With `on`, UTC weekday lanes are used only while one or more
accounts lack fresh weekly telemetry, including after a weekly reset. Once all
relevant accounts are active, selection ignores the activation day and
prioritizes the account with the highest required remaining weekly burn per
fraction of time left before reset, constrained by its five-hour headroom,
cooldowns, leases, exclusions, and safe affinity. This makes a day-six account
more urgent than a comparable day-one account and aims to bring every weekly
window close to 100% at reset. `CODEX_SCHEDULE_START_DAY` accepts
`mon|tue|wed|thu|fri|sat|sun` and defaults to `mon`. Activation uses UTC
weekdays and does not immediately de-align already-clustered weekly reset
windows; judge staggering over at least one weekly cycle.
`CODEX_EARLY_ACTIVATION_ENABLED` defaults to `true`. When enabled, the next
scheduled slot becomes eligible once every activated slot has fresh weekly
telemetry at or above `CODEX_EARLY_ACTIVATION_WEEKLY_PERCENT`, which defaults
to `70`. ClawRoute waits for fresh telemetry from that newly activated slot
before another early activation. In `on` mode, automatic `/wham/usage` checks
are limited to slot 0 and slots activated by the schedule, early activation, or
a manual activation. A successful check stores the expected weekly reset for
that slot. Once that time passes, a non-primary slot remains dormant until its
next activation point. Slot 0 is always active and keeps its existing usage
polling behavior. Persistent mode, threshold, slot enablement, manual
activation overrides, and expected reset times can be managed from
`/dashboard-codex`. The per-slot `Check usage` action explicitly bypasses the
automatic activation filter for testing or recovery. Disabled slots remain
visible in the balancer view, but background usage polling skips them while
balance-loader mode is `on`. The dashboard also reports sanitized per-slot auth
availability so an expired or refresh-failed auth file can be distinguished
from quota cooldown. These controls use the dashboard's existing bearer token.
Low five-hour or weekly `/wham/usage` percentages do not guarantee immediate
slot eligibility: upstream can still return a temporary rate limit. ClawRoute
keeps long slot cooldowns for explicit `usage_limit_reached` responses with
reset metadata, but generic streaming rate-limit errors are treated as
request-local failures instead of account exhaustion.
Cold prompt-cache migrations are blocked before upstream when an old session
would move to a different Codex account and the calibrated estimated impact is
at or above `CODEX_COLD_MIGRATION_FIVE_HOUR_THRESHOLD_PERCENT`, which defaults
to `7`. The dashboard can persist a different threshold and shows pending
decisions above the slot list with estimated million tokens, estimated five-hour
impact, and remaining five-hour/weekly headroom. Approving a decision allows one
matching retry; dismissing it keeps the request blocked. Pending decisions expire
after `CODEX_COLD_MIGRATION_DECISION_TTL_HOURS`, default `6`, and store only
session/account hashes, slot indexes, estimates, and timestamps.
The Codex cache-miss breaker is enabled by default with
`CODEX_CACHE_BREAKER_ENABLED=true`. It blocks before upstream when the same
prompt-cache key, model, account, slot, and tool schema repeatedly return low
provider cache on large expected-hit requests. Defaults are
`CODEX_CACHE_BREAKER_MIN_INPUT_TOKENS=20000`,
`CODEX_CACHE_BREAKER_LOW_CACHE_RATIO=0.20`,
`CODEX_CACHE_BREAKER_CONSECUTIVE_MISSES=2`,
`CODEX_CACHE_BREAKER_WINDOW_MISSES=3`,
`CODEX_CACHE_BREAKER_WINDOW_REQUESTS=5`, and
`CODEX_CACHE_BREAKER_APPROVAL_TTL_MINUTES=15`. The authenticated
`/api/codex/cache-breaker` route reports active breaker state. Operators can
temporarily continue a matching session with
`POST /api/codex/cache-breaker/:id/approve` or remove the blocker with
`POST /api/codex/cache-breaker/:id/clear`; neither action rotates accounts
automatically.
`/dashboard-codex` also shows read-only banked Codex reset credits when the
ChatGPT reset-credit endpoint returns them. ClawRoute displays sanitized
available counts plus grant and expiry dates where available, persists only
hashed account/credit identifiers as latest-known telemetry, and never redeems
or consumes a reset credit.

Codex usage analysis is available from `/dashboard-codex-analysis` and
`/api/codex/analysis`. The analysis view compares logged request/token/cache
activity with current Codex quota state, model mix, tier mix, API kind, and
requested reasoning effort. `routing_log` is still the raw request source of
truth. `codex_usage_snapshots` remains a latest-known quota-state table, while
`codex_usage_snapshot_history` stores append-only observations for trend
checks. `routing_daily_rollups` is rebuildable from `routing_log` and exists to
make repeated operator comparisons cheap.

The main `/dashboard` groups new routing rows as session → user turn → request.
Hermes supplies its stable session through `prompt_cache_key`; ClawRoute stores
only a namespaced hash and falls back to the legacy hashed `sender_id` contract
for other callers. `/api/routing/live` returns retained session and turn
summaries, while `/api/routing/turns/:turnId` loads request deltas on demand.
Both routes use the existing dashboard bearer token. Token metrics are sums of
provider-reported request usage: input therefore includes replayed context and
is not a count of unique conversation tokens. Rows created before turn tracing
remain available through the legacy request renderer until normal retention
removes them.

Session, turn, and attributed Codex request rows also show quota-equivalent
estimates. `/api/routing/live` reuses the seven-day total-token calibration from
Codex Analysis and returns compact 5h/weekly rates plus their sample sizes; the
dashboard applies those rates only to Codex tokens with a selected slot. The
weekly estimate is the stable reference. The 5h estimate is explicitly marked
burst-sensitive, and neither value is presented as an observed quota delta.
Mixed-provider and unattributed rows expose coverage instead of being counted
as zero-cost Codex traffic.

Personal integrations such as Telegram, Discord, GitHub, Raindrop, GogCLI,
Google MCP project IDs, Codex auth paths, and autostart profiles are
private/local fields and should be set through `private/`. For the GogCLI and
Google Workspace helper, set `GOGCLI_ACCOUNT`, `GOGCLI_CLIENT_SECRET_PATH`, and
optionally `GOGCLI_EXTRA_SCOPES` in `private/env/local.env`; leave it unset to
use the helper default for Analytics, Tag Manager, and Apps Script. Keep
`GOG_KEYRING_PASSWORD` and `SPARTAN_GOGCLI_DATA_PATH` there as well.

## Whitelist Files

These are files rather than env vars:

- `infra/outbound-proxy/whitelist.txt`: public baseline, safe to publish.
- `private/outbound-proxy/whitelist.private.txt`: private extension, ignored by Git.

Tinyproxy and DNS merge both at startup. `scripts/whitelist-domain.sh` writes to
the private extension by default and accepts an optional TTL such as `15m`,
`6h`, or `15d` before the domain list. Runtime whitelist refresh defaults to
`SPARTAN_WHITELIST_REFRESH_TIME=02:00` and
`SPARTAN_WHITELIST_REFRESH_TZ=Europe/Madrid`; expired entries are ignored at
startup, helper-triggered recreates, and that daily refresh window.
