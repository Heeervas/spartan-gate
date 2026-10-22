# Caddy query-string authentication repair — 2026-10-22

Source repair; runtime activation pending separate verification.

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
