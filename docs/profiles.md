# Hermes Profiles

Hermes profiles have two modes: s6-supervised gateway or manual specialist. Keep real profile inventories and token ownership notes in `private/docs/`.

Gateway profiles run as upstream Hermes s6 services inside the same container.
They need their own Telegram or Discord token and, if their API server is
enabled, a literal non-conflicting `API_SERVER_PORT` in the profile `.env`.
`HERMES_AUTOSTART_PROFILES` is only a legacy first-boot bootstrap that seeds
missing gateway state as `running`; after that, `hermes -p <profile> gateway
start|stop` controls persisted s6 state. Spartan Gate keeps `/run/service`
root-owned, so newly created profiles get their supervised service slot on the
next Hermes recreate rather than by mutating s6 service definitions from a
running `hermes` process.

Do not start or seed a gateway profile until its `.env` has dedicated messaging
credentials or explicit blank credentials. Otherwise the profile can inherit the
default gateway tokens from the container environment and prevent the default
gateway from starting.

Manual profiles are not listed in `HERMES_AUTOSTART_PROFILES`. They can be used
interactively from the main gateway, should set `API_SERVER_ENABLED=false`, and
should not define `API_SERVER_PORT`.

## Browser Extension Gateways

The private HTTPS Caddy override can publish running profile API servers under
one authenticated extension edge. Each profile mapping is a literal allowlist
entry; never derive an upstream host or port from a request header, query value,
or path capture.

The supported private layout keeps the existing root endpoint on `default` and
strips the selected profile prefix before proxying:

| Public path | Internal API server |
| --- | --- |
| `/agents/default/*` | `hermes:8642` |
| `/agents/coach/*` | `hermes:8643` |
| `/agents/work/*` | `hermes:8644` |
| `/agents/market/*` | `hermes:8645` |
| `/agents/dreamer/*` | `hermes:8646` |

All allowlisted paths may intentionally share one
`HERMES_EXTENSION_EDGE_KEY`. Compromise of that key therefore grants access to
every exposed profile and its tools. Caddy must reject unknown `/agents/*`
paths instead of falling back to `default`, replace the exterior authorization
with `HERMES_API_KEY`, and remove `X-Hermes-Profile` before proxying.

Only publish profiles whose API server is already running. Manual specialists
remain on demand with `API_SERVER_ENABLED=false`; do not start them as gateways
only to make them appear in a browser selector. Keep profile API ports on the
internal Docker network rather than publishing them on the host.

Caddy targets the stable Docker DNS name `hermes` plus each literal profile
port. Restarting one gateway therefore causes only temporary unavailability for
that profile. Recreating the Hermes container updates the Docker DNS record;
s6 restores the persisted running gateway set, and Caddy reconnects without a
configuration change. Requests made during startup may receive `502`, but a
profile route never falls back to another profile.

## Create A Profile

```sh
source scripts/aliases.sh
sg up -d hermes
sg-hermes-profile-create coach
sg-hermes-profile coach show
```

Hermes is part of the default stack; recreate it explicitly after changing profile-related private env or mounts.

## Gateway Profile `.env`

Example for a profile named `coach`, assuming the variables are present in your private env file and exposed in `private/compose.local.yml`:

```env
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN_COACH}
TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS_COACH}
DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN_COACH}
DISCORD_ALLOWED_USERS=${DISCORD_ALLOWED_USERS_COACH}
DISCORD_HOME_CHANNEL=${DISCORD_HOME_CHANNEL_COACH}
HERMES_GATEWAY_TOKEN=${HERMES_GATEWAY_TOKEN}
CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}
OPENAI_BASE_URL=${OPENAI_BASE_URL}
API_SERVER_PORT=8643
```

Recreate Hermes after the profile has its own token and port, then start
`coach` with `sg exec hermes hermes -p coach gateway start`. For old private deployments,
`HERMES_AUTOSTART_PROFILES=coach` may be used once to seed missing s6 gateway
state, but it will not override an existing stopped state.

## Manual Profile `.env`

```env
DISCORD_BOT_TOKEN=
DISCORD_ALLOWED_USERS=
DISCORD_HOME_CHANNEL=
API_SERVER_ENABLED=false
HERMES_GATEWAY_TOKEN=${HERMES_GATEWAY_TOKEN}
CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}
OPENAI_BASE_URL=${OPENAI_BASE_URL}
```

Do not start manual profiles as gateways.

## Rules

- Do not copy runtime state into a new profile: `state.db`, `auth.lock`, `logs/`, `sessions/`, and `sandboxes/`.
- Keep `skills.external_dirs` empty for curated profiles.
- Do not run two live gateways with the same Telegram or Discord token.
- Add new profile credential names to your private env file.
- Expose those names in `private/compose.local.yml` when the profile `.env` references them.
- Inherit `OPENAI_BASE_URL` from the container so ClawRoute and Spartan Route
  overlays select the active router consistently.
- Keep profile API ports explicit and stable; port conflicts should fail visibly
  instead of shifting automatically.
