# Profile creation and native multiplexer — 13 October 2026

Status: applied documentation change only. No profile configuration, credentials,
runtime state, image or service lifecycle changed as part of this update.

Profile creation guidance now reflects native multiplexing: fresh named
profiles can enroll immediately, use the shared API listener at `/p/<profile>`,
and need no separate API port or autostart entry. Native creation explicitly
authorized for immediate enrollment may run inside the gateway; a profile
intended to remain manual must be parked from the host and confirmed unserved
before credentials are added. Creation and parking are not atomic; there is no
`--parked` creation option. Enrollment alone does not prove bot or API
readiness. The default API uses the base endpoint; each API-enabled profile
requires its own key, with no default-key fallback.

Host-side native named stop/start controls parking and multiplex enrollment.
Named restart also unparks, so manual/stopped profiles must not be restarted in
bulk. The operator's default `sg-hermes-restart` instead restarts the full
shared process; its named form is for a separate running s6 gateway. Protected
stop/restart operations must run from the host; Hermes must not bypass a
self-targeting lifecycle guard. Supported native creation/configuration CLI
operations are not bypasses; never use `--force` after a denial.

Prefer native fresh creation over cloning. Clones strip messaging credentials
by default but can retain other secrets. Do not copy runtime databases, locks,
sessions, OAuth or browser credential stores. Inherit managed Route/proxy
policy; only the approved internal Route client token belongs in shared managed
dotenv, not upstream provider or messaging credentials. Keep
`skills.external_dirs` empty for curated profiles. Preserve explicit plugin
disables, native SearXNG and mandatory managed Camofox. See
[Camofox](../local/camofox.md) and the [completion update](profile_creation_complete_16102026.md) for public guidance.

Talk's named endpoint remains profile scoped. Acceptance requires a real
delegated tool operation and spoken result, not only a no-tools probe. Hot
profile enrollment does not perform container-init Talk/voice provisioning;
that and Compose env/mount changes may require recreation. Live documentation
updates need no restart. The older port/autostart profile helper is standalone
only and must not be combined with native multiplex creation.
