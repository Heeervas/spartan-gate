# Profile creation and native multiplexer — 13 October 2026

Status: applied documentation change only. No profile configuration, credentials,
runtime state, image or service lifecycle changed as part of this update.

Profile creation guidance now reflects native multiplexing: fresh named profiles
can enroll immediately, use the shared API listener at `/p/<profile>`, and need
no separate API port or autostart entry. Immediately park a fresh profile and
confirm it is unserved before adding credentials. Creation and parking are not
atomic. The default API uses the base endpoint; each API-enabled profile requires
its own key, with no default-key fallback.

Host-side native named stop/start controls parking and multiplex enrollment.
Named restart also unparks, so manual/stopped profiles must not be restarted in
bulk. The operator's default `sg-hermes-restart` instead restarts the full shared
process; its named form is for a separate running s6 gateway. Hermes must not
self-target its gateway through tools.

Prefer native fresh creation over cloning. Clones strip messaging credentials
by default but can retain other secrets. Do not copy runtime databases, locks,
sessions, OAuth or browser credential stores. Inherit managed Route/proxy policy;
only the approved internal Route client token belongs in shared managed dotenv,
not upstream provider or messaging credentials. Keep `skills.external_dirs` empty for curated profiles. Preserve
explicit plugin disables, native SearXNG and mandatory managed Camofox. See `local/camofox.md` and `local/private/profile-creation.md` under the
runtime docs root for operational guidance.

Talk's named endpoint remains profile scoped. Acceptance requires a real
delegated tool operation and spoken result, not only a no-tools probe. Hot
profile enrollment does not perform container-init Talk/voice provisioning;
that and Compose env/mount changes may require recreation. Live documentation
updates need no restart. The older port/autostart profile helper is standalone
only and must not be combined with native multiplex creation.
