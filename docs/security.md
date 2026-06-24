# Security

## Threat Model

Spartan Gate assumes a capable agent may try to reach arbitrary network destinations, leak data through DNS or HTTP, call tools unexpectedly, or expose sensitive logs. The system is designed to reduce direct egress and make boundary crossings explicit.

## Controls

- Hermes is internal-network only.
- Browserless and Camofox are internal-network only and configured to use Tinyproxy.
- DNS forwarding is whitelist based.
- Tinyproxy denies non-whitelisted destinations.
- Reader only allows GET, blocks private/internal targets, revalidates the
  address used for each outbound connection, and is the narrow direct-egress
  exception for public page text.
- Reader labels fetched text as untrusted content; agents must extract facts from it before using it to decide on tool calls.
- Caddy requires Basic Auth for browser-facing HTTP entry points and the public
  Compose binds edge ports to `127.0.0.1` only.
- Browserless debugger WebSockets do not rely on browser Basic Auth prompts,
  which can loop or drop credentials in Safari/Firefox. Profile-seeding URLs
  carry a generated `BROWSERLESS_EDGE_TOKEN` as a short edge authorization
  guard, while Caddy still injects the internal `BROWSERLESS_TOKEN` only toward
  Browserless.
- The Hermes dashboard keeps same-origin `/api/*` and `/ws/*` requests
  pass-through behind the localhost/private edge. Protect it with local binding,
  Tailscale/private exposure, and Hermes' own application controls; do not
  publish the dashboard edge broadly.
- Tailscale exposure belongs in `private/compose.local.yml`; do not publish edge ports on `0.0.0.0` or LAN addresses in public files.
- Extra Hermes app ports belong in `private/caddy.local.d` plus `private/compose.local.yml`; use `scripts/add-port.sh` instead of editing public Compose.
- ClawRoute stats, dashboards, and API endpoints require bearer auth when
  `CLAWROUTE_TOKEN` is configured; `/health` remains public for local health
  checks.
- ClawRoute content logging is disabled by default.
- Hermes application workloads run as the unprivileged `hermes` user under s6.
  Local deployments may drop those workloads directly to the host owner's
  numeric UID/GID for `/opt/data`; PID 1 and supervisors remain root only so
  they can initialize s6 and drop privileges for services.
- Hermes installed code under `/opt/hermes` is image-built and root-owned at
  runtime. Spartan Gate patches that modify installed code must apply during
  image build; boot hooks may only migrate runtime data/config under
  `/opt/data`.
- Dynamic s6 service definitions under `/run/service` are root-owned. Service
  run scripts drop to `hermes` before application code starts, and runtime
  profile registration is deferred to container recreation rather than making
  `/run/service` writable by the workload user.
  System package installs are operator/image-build actions, not normal agent
  runtime behavior. User-level tool installs must stay under `/opt/data` or
  language caches and still pass through Tinyproxy/DNS controls.

## Ownership Boundaries

Keep service ownership explicit when changing security-sensitive behavior:

- Hermes consumes services and must remain off the external network in gate
  tiers.
- ClawRoute owns model routing, provider credentials, quota telemetry, and
  OpenAI-compatible API behavior.
- Tinyproxy and DNS own general egress policy. Invalid, malformed, expired, or
  overflowing policy data must fail closed.
- Reader owns bounded GET-only page retrieval. Reader content is untrusted
  input and must not be turned directly into tool actions.
- Browserless and Camofox are separate browser modes. Do not claim an inactive
  browser Compose profile remains available.
- Caddy owns authenticated edge routing and public host bindings.

## Secrets

Public files must not contain real tokens, personal paths, account inventories,
private domains, certificates, or Codex profile mounts. Put those in `private/`
only. Runtime data belongs in ignored `runtime/` or private host paths.

Browserless profile directories contain Chromium state, including cookies and
site storage for any accounts seeded through the Browserless debugger. Treat
`SPARTAN_BROWSERLESS_PROFILES_PATH` and `runtime/browserless/profiles/` like
credential stores. Keep real profile inventories, account ownership notes, and
target-site lists in `private/docs/`, not public docs or tracked config.

Camofox state directories contain Playwright storage state, cookies, and traces.
Treat `SPARTAN_CAMOFOX_DATA_PATH` and `runtime/camofox/` like credential stores
when Camofox is enabled.

The public whitelist is only a generic baseline. Local/project/personal domains
belong in `private/outbound-proxy/whitelist.private.txt`; do not publish your
real browsing or integration inventory by adding it to `infra/outbound-proxy/whitelist.txt`.

## Logging

Keep `CLAWROUTE_LOG_CONTENT=false` unless you are debugging in a private environment and accept the privacy impact. Reader audit logs redact URL query strings, but hosts and paths can still be sensitive; treat logs as private operational data.
