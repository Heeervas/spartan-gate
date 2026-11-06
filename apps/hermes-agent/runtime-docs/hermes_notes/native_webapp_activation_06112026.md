# Native Hermes Webapp activation — 6 November 2026

**Status: applied operator-local configuration.** Public Compose defaults remain
unchanged. This note is an operator archive, not a mounted runtime guide.

The pinned Hermes image already contains the native chat Webapp renderer. It is
now enabled on its own internal listener, distinct from the existing dashboard.
Access is through the existing private HTTPS Caddy edge, not a published Hermes
port. Native Basic sign-in and the edge authentication layer are both retained;
no Nous/OAuth registration, authentication bypass or credential rotation was used.

## Applied boundary

- Native `webapp.enabled`, `host`, `port` and `public_url` were configured through
  the native setter. The non-loopback bind is inside the internal-only container.
- The private edge route was prepared with the supported add-port helper and the
  complete effective Compose/Caddy configuration was checked before activation.
- Native `dashboard.trusted_proxies` trusts only Caddy's exact internal address.
  The private override pins that same existing address on the existing network,
  preserving its alias and other memberships. No subnet-wide proxy trust was added.
- Trust is necessary for native Uvicorn to recognize Caddy's HTTPS forwarding
  header and emit `Secure` authentication cookies. An actual login caught the
  missing trust setting; the failing cookie assertion was not waived or replaced
  by edge cookie rewriting.
- Only Caddy was recreated. The idle Hermes gateway was gracefully restarted;
  Hermes's container, pinned image, internal networks and security controls were
  unchanged. No image rebuild, dependency installation or other service rollout
  was needed.

## Verified result and limits

Trusted HTTPS and hostname verification, anonymous HTTP/WebSocket denials, both
sign-in layers, native `Secure`/`HttpOnly` cookies, authenticated native API access,
Webapp HTML, and every declared module/preload asset passed. The baked index has
no separate stylesheet link; acceptance checks its actual declared assets rather
than assuming a CSS file. Native serving status, configured platform
connectivity and both service health checks passed. All unrelated service
identities/images/start times remained unchanged.

No model/chat request, visual browser walkthrough, authenticated WebSocket journey,
vault action, browser-profile access or session-database copy was performed.
Native status still reports an install/pnpm freshness warning; the existing baked
renderer serves successfully. No build was attempted merely to remove that warning.

Restricted configuration before/postimages and exact runtime evidence remain
with the existing task's recovery owner. Rollback requires checking current
configuration drift and restoring only the relevant settings/private edge through
supported controls; it does not imply database or image-schema rollback. Existing
manual upgrade observation/rollback follow-ups and broader task closure remain
separate from this applied Webapp activation.
