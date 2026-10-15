# Profile creation completion — 16 October 2026

Status: applied documentation change only. No profile, configuration,
credential, runtime state, image or service lifecycle was changed by this
documentation update. Runtime acceptance remains separate from the documented
procedure.

Fresh creation explicitly authorized for immediate multiplex enrollment can run
inside the gateway with the native CLI, for example:

```sh
hermes profile create coach --no-alias --no-skills --description 'Focused coaching assistant'
```

Native creation atomically publishes the directory and notifies the
multiplexer; it can enroll immediately on the shared listener at
`/p/<profile>`. It needs no per-profile API port or autostart entry. There is
no `--parked` creation flag, and creation followed by parking is not atomic.
For an intended parked/manual profile, the host operator must stop it and
confirm it is unserved before adding credentials. Protected self-targeting
stop/restart guards remain binding; use the host and never bypass denial with
`--force`. Supported native creation and configuration CLI edits do not
themselves bypass lifecycle protection.

Complete profile-local `SOUL.md`, the profile instruction file (for example,
`AGENTS_<profile>.md`) and compact memories; verify effective instruction loading.
Link curated whole skill
directories with their supporting files; preserve intentional profile
divergences, `.no-bundled-skills` and empty `skills.external_dirs` for curated
profiles. Never clone sessions, runtime databases, OAuth state or browser
credential stores into a new identity.

Every new profile requires the mounted `spartan-cache-lineage` plugin link,
`plugins.enabled` entry, doctor/discovery/import checks and actual registration
of its `llm_request` middleware and `post_api_request` hook. A plugin listing
alone is insufficient. Preserve existing intentional exceptions; this does not
bulk-enable domain plugins. Link and enable `web-spartan-reader`, set
`web.extract_backend: spartan-reader`, and verify effective selection. Retain
`prompt-load-bridge` disablement and existing peer tool overrides.

For each enabled delegation caller, review
`plugins.entries.profile-delegate.allowed_profiles` and any
`PROFILE_DELEGATE_ALLOWED_PROFILES` override before adding an authorized
target. Preserve existing targets, limits, modes and intentional disables. For
delegation policy, the effective environment value overrides YAML; Hermes
scoped dotenv loading may not appear in a plain Python `os.environ` inspection.
Use the named Hermes context to check effective settings without displaying
credentials.

Default shared-bot Discord routing belongs in the default gateway's
`gateway.profile_routes`; do not copy its token into named profiles. Each
API-enabled profile still requires its own API key. Enrollment is neither bot
readiness nor authenticated API readiness.

The installed `/opt/hermes/gateway/run_plugin_rewire.py` supports
install/enable and Desktop `plugins.manage` notifications to the running
gateway. Adapter handlers rewire through `on_plugin_loaded`; tools and system
prompts update for the next session. This does not refresh
`gateway.profile_routes`, cached by the shared runner. Route changes require a
controlled shared host-side `sg-hermes-restart` and subsequent route
acceptance. A named restart does not prove that the default runner refreshed
its routes. This note requests no restart or runtime action. New Talk/voice
boot provisioning and Compose env or mount changes have their separate
container-initialization/recreation contract.

The repository [public profile guide](../../../../docs/profiles.md) provides the
completion checklist. Inside the mounted runtime docs, use
`local/private/profile-creation.md` as the operator-maintained operational
authority; the repository-relative public link is for host-side reading. Cover
creation/lifecycle, identity/skills, plugins/tools, delegation, Discord/API and
acceptance. Record each as verified, pending or intentionally
disabled. Static configuration, discovery and hook registration prove only
their own surfaces: they do not establish actual cache hits, Reader fetches,
delegated execution or message delivery. Check next-session tools and prompts
separately from adapter rewire. Live messages, delegation and voice tests
require their own authorization; do not label unperformed acceptance complete.
Keep evidence free of secrets, private inventories, routing IDs and session
IDs.
