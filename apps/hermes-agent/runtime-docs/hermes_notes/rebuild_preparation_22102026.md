# Rebuild preparation — 22 October 2026

Status: applied offline preparation, NOT deployment or successful image builds.

The selected installation's Compose and Caddy configuration was prepared for
source retirement and signed edge sessions. Retired browser references and
baked-code overlays were removed; persistent data, existing authentication keys,
resource controls and independent extensions were preserved. Native Caddy
validation used the selected TLS inputs without starting a listener.

Route source now requires aiohttp 3.14.4. Behavioral regressions cover initial
connection/header timeout and Secure shared-cookie handling; this does not assert
the version running in an existing container.

Builds must use the reviewed selected configuration and pass the private
production-input drift guard. New images, isolated canaries, recreation and live
acceptance are separate next steps. Do not use orphan/volume cleanup, restore old
static cookies, or automatically restart gateways to apply this note. Old browser
cookies require reauthentication after the signed-session cutover.
