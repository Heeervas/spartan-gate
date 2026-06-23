Browserless is provided by the public `ghcr.io/browserless/chromium` image in
Compose. The image is configured with `BROWSERLESS_IMAGE`; pin it in private env
before incident recreates.

This directory is reserved for local launch policy, debugger patches, or Browserless-specific hardening that belongs in the public Spartan Gate infrastructure.

## Persistent Profiles

Browserless Chromium profiles are stored under `/profiles` inside the container.
The public Compose maps that path to `runtime/browserless/profiles/`; private
installs should override it with `SPARTAN_BROWSERLESS_PROFILES_PATH` in
`private/env/local.env` and `private/compose.local.yml`. Set
`BROWSERLESS_PROFILES_GID` to a host group with write access to that directory.
Use `sg config` or `sg-browserless-snapshot` as the source of truth because
private Compose overrides can replace the public path.

Hermes' normal browser endpoint is an ephemeral Browserless launch and does not
set `userDataDir`. The optional browser profile `main` uses
`userDataDir=/profiles/${BROWSERLESS_PROFILE}` behind a local CDP broker. Both
paths use `BROWSERLESS_ROUTE=chromium` with `stealth=true` by default. Alternate
routes are available through
`BROWSERLESS_ROUTE=stealth|chromium-stealth|chrome-stealth`; the legacy
`BROWSERLESS_STEALTH_ROUTE` name is still accepted for compatibility. Without
that broker, multiple clients trying to use the same persistent profile would
make Browserless reject the second connection. The default persistent login
profile is `main`; changing it requires recreating Hermes.
If the broker is disabled, the persistent `main` browser profile is available
only when `BROWSER_CDP_MAIN_URL` is provided by a private override.

The Browserless service defaults `TIMEOUT` to `2147483647` through
`BROWSERLESS_SESSION_TIMEOUT_MS` (about 24.8 days), mainly for broker-owned
persistent sessions. Stopping Hermes closes the broker session earlier. If
Chromium leaves stale `Singleton*` files behind, the Hermes broker removes only
those lock files after Browserless reports no active sessions and no running
jobs.

Use `sg-browserless-profile-live <profile> [url]` to open an interactive
Browserless debugger session and seed login state manually. The command prints a
debugger URL with the persistent launch configuration embedded, including the
selected `BROWSERLESS_ROUTE` and `stealth=true`; open it in your browser, pass Caddy Basic Auth, click
run if needed, then close the debugger tab when finished. Google account login
URLs intentionally do not autostart the debugger run.

Only one browser should use a given profile directory at a time. Stop Hermes or
close the seeding debugger session before reusing the same profile elsewhere.
When seeding with local Chrome, use the project helper; it launches Chrome with
`--password-store=basic` so Browserless can read the saved cookies later.
