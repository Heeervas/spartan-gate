# Caddy query-string authentication repair — 2026-10-22

Applied and live-verified; source commit `839ab6b`.

Caddy's signed-session verifier previously compared the full request target
with its endpoint audience, so app URLs with query strings failed with 404
before reaching the app. The verifier now matches only the literal path before
the first `?`. Queries remain intact for the application; they cannot change
the verifier operation, audience or identity. Existing service-key, signature,
cookie and expiry checks remain enforced, and encoded/malformed endpoint aliases
remain rejected. This was an availability regression, with no observed auth bypass.

Updating this code requires rebuilding/recreating `edge-sessions`, retaining the
previous image for rollback. Restarting stock Caddy alone cannot update the
verifier. No Hermes rebuild, secret rotation or profile change is needed.

The isolated candidate image passed acceptance and only `edge-sessions` was
recreated. The original app query returns 200 with Basic bootstrap and with its
signed session cookie. Missing credentials and invalid/duplicate/expired/future/
wrong-audience cookies plus a spoofed verifier header return 401. All ten
services are healthy; the other nine containers and their settings are
unchanged. Caddy validation passed without restarting it. The old verifier image
is retained for rollback. Seven verifier/native tests, 26 related contracts,
TOP assurance with existing exceptions and independent security review passed.
