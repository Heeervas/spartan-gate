# Security

## Threat Model

Spartan Gate assumes a capable agent may try to reach arbitrary network destinations, leak data through DNS or HTTP, call tools unexpectedly, or expose sensitive logs. The system is designed to reduce direct egress and make boundary crossings explicit.

## Controls

- Hermes is internal-network only.
- Camofox is internal-network only and configured to use Tinyproxy in gate tiers.
- Pocket TTS runtime is internal-network only, has no published port, proxy,
  external network, Hugging Face token, arbitrary voice URL, or voice-upload
  endpoint. It reads a versioned cache and runs with Hugging Face offline mode.
  The one-shot prefetch service is the only Pocket component with controlled
  download access through Tinyproxy and DNS. Prefetch uses public non-cloning
  weights, requires disk headroom before and after download, and activates a
  complete cache atomically.
- DNS forwarding is whitelist based.
- Tinyproxy denies non-whitelisted destinations.
- Reader only allows GET, permits globally routable destinations, revalidates
  the address used for each outbound connection, and is the narrow direct-egress
  exception for arbitrary public page text. A fixed worker pool and bounded
  pending admission shed overload with `503`; the service runs as fixed UID
  `10001` with no capabilities, no-new-privileges, read-only root, and a PID
  ceiling.
- Reader intentionally bypasses the Tinyproxy/DNS domain whitelist. Granting an
  agent Reader access therefore accepts public HTTP/DNS exfiltration through
  destination hostnames, paths, and queries; the control prevents non-global
  network access, not disclosure to a public server.
- Reader labels fetched text as untrusted content; agents must extract facts from it before using it to decide on tool calls.
- Caddy requires Basic Auth for browser-facing HTTP entry points and the public
  Compose binds edge ports to `127.0.0.1` only. It runs as the host-matching
  non-root UID/GID with a read-only root, no-new-privileges, and all
  capabilities dropped except `NET_BIND_SERVICE`, which the upstream
  capability-bearing binary requires even for high ports. Its healthcheck
  validates the effective config and the authenticated listener without
  credentials.
- Browserless/CDP source and debugger workflows are retired; legacy profile
  data remains untouched.
- Camofox noVNC and `sg-add-port` listeners set host-only,
  HttpOnly, SameSite=Strict cookies only after successful Caddy Basic Auth. A
  valid session authorizes subsequent HTTP assets and WebSockets, avoiding
  Safari Basic Auth loops; invalid WebSockets fail with a non-challenging `401`.
  Camofox uses a distinct `CADDY_CAMOFOX_EDGE_TOKEN`; Hermes preview ports share
  `CADDY_WS_EDGE_TOKEN` as one operator trust boundary. Both must be 64 lowercase
  hex, are stripped with legacy edge cookies and `Authorization` before proxying,
  and use `Secure` on HTTPS. Cookies contain an audience-bound HMAC and issue
  timestamp, never the signing key. The bounded internal verifier rejects replay
  at age >=3600 seconds, future timestamps, malformed/duplicate cookies and old
  static tokens. Caddy clears client verification headers before `forward_auth`,
  mints only after successful Basic Auth and removes internal headers upstream.
  Verifier access requires the independent `CADDY_SESSION_SERVICE_KEY`; only
  Caddy and verifier join its isolated internal network. No published verifier
  port or service-key exposure to Hermes/Camofox is allowed.
- The Hermes dashboard keeps its native `/login`, `/auth/*`, `/api/*`, and
  `/ws/*` application-session routes pass-through behind the localhost/private
  edge; Hermes owns authentication on those routes while Caddy Basic Auth remains
  on other browser-facing dashboard pages. Protect it with local binding,
  Tailscale/private exposure, and Hermes' own application controls; do not
  publish the dashboard edge broadly.
- Optional shared tool access uses a separate bearer-authenticated tool-only
  Caddy listener, never the legacy browser/dashboard listeners. Client policy
  reloads fail closed at each admission; opaque workflow authority and an exact
  tab registry enforce conversation ownership server-side. Default binding is
  loopback only. Its publishing bridge belongs only to tool-edge; confined
  clients join only internal ingress and cannot directly reach backend services.
  The edge itself has ordinary bridge reachability, not network confinement.
  No cookie/CORS authentication, raw browser key distribution, general evaluate,
  profile reset, supervisor or vault administration is exposed. Strict managed
  search/retrieval has no hosted/direct fallback. See
  [shared-tool-access.md](shared-tool-access.md) for disclosure, native-history,
  revocation and trusted-desktop limitations. This opt-in source is not proof of
  activation in a particular installation.
- Tailscale exposure belongs in `private/compose.local.yml`; do not publish edge ports on `0.0.0.0` or LAN addresses in public files.
- Extra Hermes app ports belong in `private/caddy.local.d` plus `private/compose.local.yml`; use `scripts/add-port.sh` instead of editing public Compose.
- The OpenAI-compatible router stats, dashboards, and API endpoints require
  bearer auth when `CLAWROUTE_TOKEN` is configured; `/health` remains public
  for local health checks.
- Router content logging is disabled by default.
- Spartan Route runs as an explicit host-matching non-root
  identities, with read-only roots, all capabilities dropped,
  no-new-privileges, bounded temporary storage, and only `/app/data` writable.
  Router auth/config mounts remain read-only. Spartan Route startup tooling
  validates its fixed SQLite files before Compose, and the stopped-state
  migration backs up and repairs only those files without traversing unrelated
  private content.
- Every rendered service has a memory and PID ceiling. PID defaults are 512 for
  Hermes and browsers, 256 for routers, 128 for search/proxy/Caddy and Pocket
  TTS, and 64 for DNS/Reader. Lower tiers carry the same Hermes read-only root,
  capability allowlist, no-new-privileges, temporary filesystems, and 2 GiB
  memory ceiling as L4.
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

## Optional MCPHub

The opt-in [MCPHub integration](mcphub.md) keeps the hub internal-only and routes
shared admin-owned HTTP connectors through Tinyproxy. Its separate localhost
Caddy listener preserves native bearer authorization, not browser Basic Auth.
Three distinct MCP-only system keys are restricted to the agents group; an
applicable closed allow set and exact tool policies deny unlisted/new tools at
execution. The admin controls trusted backend enrollment; stdio backends share
the hub container and are not separate sandboxes. Protect the complete file-mode
state and encryption sidecar/backups: shared connector secrets may be plaintext.
Personal/OAuth modes need connector-specific proxy/refresh validation. No active
installation or real backend account is implied by source availability.

## Ownership Boundaries

Keep service ownership explicit when changing security-sensitive behavior:

- Hermes consumes services and must remain off the external network in gate
  tiers.
- Spartan Route owns model routing, provider credentials, quota telemetry, and
  OpenAI-compatible API behavior in L3/L4. Legacy ClawRoute source is retired;
  shared `config/clawroute` and `CLAWROUTE_*` compatibility names remain.
- Tinyproxy and DNS own general egress policy. Invalid, malformed, expired, or
  overflowing policy data must fail closed.
- Reader owns bounded GET-only page retrieval. Reader content is untrusted
  input and must not be turned directly into tool actions.
- Camofox owns supported persistent browsing. Retired CDP-only workflows have
  no implicit replacement.
- Pocket TTS owns local speech synthesis and its versioned model cache. Hermes
  consumes it through a fixed command client; a sidecar failure must remain an
  explicit error rather than falling through to a cloud speech provider.
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

Private configuration files use mode 0600 and private configuration directories
and single-user data roots use 0700. Retired Browserless and legacy ClawRoute
roots are not modified by the permission helper. Permission checks inspect
only explicit private config trees and the top of configured data roots; they
must never traverse Browserless, Camofox, or Codex credential contents.

`scripts/private-permissions.py check` performs the metadata-only audit used by
doctor. Its `fix` action preflights every target before changing anything,
rejects symlinks, can write a mode-0600 recovery snapshot, and rolls back prior
metadata if a later repair fails.

The public whitelist is only a generic baseline. Local/project/personal domains
belong in `private/outbound-proxy/whitelist.private.txt`; do not publish your
real browsing or integration inventory by adding it to `infra/outbound-proxy/whitelist.txt`.

Whitelist generation is bounded before live replacement: each public/private
fragment defaults to a 256 KiB ceiling and the merged policy to 2048 normalized
active unique domains. Proxy and DNS generate complete candidates before
installation. DNS commits its two generated files as a single policy set, and
install or child-signal failure restores the complete previous generation
before the service exits into the fail-closed restart path.

## Logging

Keep `CLAWROUTE_LOG_CONTENT=false` unless you are debugging the router in a
private environment and accept the privacy impact. Reader audit logs retain the
target origin but replace paths and queries with redaction markers, reject
userinfo, and never include the encoded fetch target in access logs. Public
hostnames can still be sensitive, so treat logs as private operational data.
