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

Manual profiles are not listed in `HERMES_AUTOSTART_PROFILES`. They can be used interactively from the main gateway and should not define `API_SERVER_PORT`.

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
OPENAI_BASE_URL=http://clawroute:18790/v1
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
HERMES_GATEWAY_TOKEN=${HERMES_GATEWAY_TOKEN}
CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}
OPENAI_BASE_URL=http://clawroute:18790/v1
```

Do not start manual profiles as gateways.

## Rules

- Do not copy runtime state into a new profile: `state.db`, `auth.lock`, `logs/`, `sessions/`, and `sandboxes/`.
- Keep `skills.external_dirs` empty for curated profiles.
- Do not run two live gateways with the same Telegram or Discord token.
- Add new profile credential names to your private env file.
- Expose those names in `private/compose.local.yml` when the profile `.env` references them.
- Keep profile API ports explicit and stable; port conflicts should fail visibly
  instead of shifting automatically.
