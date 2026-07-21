# Current State

Date: 2026-07-19

## Latest Update

Spartan Route now classifies Codex balance windows by
`limit_window_seconds`, accepts weekly-only responses, suppresses obsolete
five-hour rows, and filters persisted balances to current account identities.
Usage refresh is progressive and single-flight through the nonblocking POST,
while polling reads incremental daily rollups instead of scanning
`routing_log`. The full suite passes 179/179. Only `spartan-route` was rebuilt
and recreated; neighboring services remained unchanged, healthy, and at restart
count 0. Runtime acceptance measured the initial authenticated GET at 0.0227 s,
subsequent polling at 0.0075 s maximum, and POST 202 at 0.021 s. Live refresh
returned weekly balances for all four accounts with no false `fiveHour` rows.
Slot 4 still reports the pre-existing `reauth_required` condition.

## Onest Update

Onest 1.000 is now installed at build time in the Hermes and Camofox images
and in the local Browserless wrapper, with checksum verification, Fontconfig
cache refresh, and an `fc-list` build assertion. Hermes and Camofox were
rebuilt and are healthy; each exposes all seven Onest TTFs at runtime. Hermes
rendered a PDF with Onest-Regular, and Camofox smoke/viewport screenshots pass.
The focused contract/installer suite passes 21/21 and the full scripts suite
passes 110/110, alongside Bash syntax, diff, and `sg config` checks.
Browserless completed a build before the local no-build hard rule was added,
but has no current local image and received no runtime validation. It will not
be rebuilt under that rule.

## Caddy Update

Caddy now exchanges one successful Basic Auth for a one-hour HttpOnly session
covering noVNC/Hermes-preview HTTP assets and WebSockets. Camofox and preview
listeners use separate 64-hex secrets; malformed startup values fail closed,
legacy/edge cookies and Basic Authorization are stripped upstream, and invalid
WebSockets return a non-challenging 401. The active private files were migrated
transactionally and only Caddy was recreated. Runtime checks pass for noVNC
module HTTP 200, invalid HTTP 401 with challenge, invalid WS 401 without
challenge, and valid WS 101.

## Previous Update

Spartan Route now has an authenticated `/dashboard-config` page for atomic,
database-backed overrides of the four active `clawroute/auto` primary tiers.
Overrides use ETag preconditions, serialized commit/publication, startup
revalidation, and whole-set fallback to read-only file defaults. The Codex
dashboard now marks current routing-enabled account failures with a red border,
badge, and reason while keeping cooldowns, cached history, and disabled slots
distinct. The full Spartan Route suite passes with 110 tests.

## Earlier Update

Camofox viewport work now uses the Spartan fail-closed route as the supported
contract and can relaunch the shared Camoufox browser with a launch-time window
size when `page.setViewportSize` leaves `innerWidth` at the display size. The
active acceptance path is Camofox-only: rebuild/recreate `camofox`, then run
`sg-camofox-viewport-check 390 844`. Hermes/Meet viewport patching is
intentionally out of scope because Hermes will not be rebuilt for this phase.

## Older Update

`sg-whitelist-domain` no longer uses `docker compose kill -s USR1` for normal
proxy/DNS hot reloads. The helper now sends an in-container `USR1` with
`docker compose exec -T`, keeps `--no-restart` as a compatibility alias for the
new `--no-reload`, and only recreates failed reload services when explicitly
requested.

## Active Task

Complete the privileged Tailscale hostname/certificate cutover.

## Blockers

`tailscale cert` requires interactive `sudo`; the current agent session cannot
provide the host password. The candidate certificate was not issued and the
old hostname/certificate remain active.
