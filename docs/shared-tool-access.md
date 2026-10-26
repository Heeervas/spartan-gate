# Shared managed tools (source candidate; not activated)

This opt-in contract lets trusted host Pi and a restricted HTTP container client
use existing SearXNG, Reader and Camofox. It does not replace Hermes, Spartan
Route, egress policy or operator administration. Source acceptance and isolated
fixtures are not production acceptance. No active installation is authorized by
this document.

## Transport and authority

REST v1 uses a dedicated tool-only Caddy ingress, published on loopback port
18792 only. The existing multi-listener Caddy service never joins client ingress.
The restricted client joins only internal tool-client ingress; tool-edge joins
that and an internal adapter-only intermediary network, plus a dedicated normal
`tool_edge_publish` bridge required for effective Docker host publication.
Only tool-edge joins that publishing bridge in the installation. Tool-access
joins intermediary and the existing backend network. Tool-edge itself therefore
has ordinary bridge network reachability; it is not network-confined. Its fixed
single upstream and disabled admin/forward-proxy behavior remain essential. No backend/master keys or broad environment files are
provided to consumers. A trusted desktop process is not an OS sandbox.

Client requests carry `Authorization: Bearer <64-lowercase-hex client token>`.
A mode-0600 policy file stores SHA-256 token digests and fixed client principal,
allowed capabilities (`search`, `reader`, `browser`) and browser user identity.
Every request reloads the complete policy fail closed. Revocation stops new
admission immediately; admitted mutations are not undone. Origin-bearing
requests, unexpected Host, URL credentials, cookie authentication and redirects
are rejected. No positive auth cache or browser-origin CORS access is supported.
Policy updates are atomic operator actions; malformed/missing policy denies all.
Policy input is at most 64 KiB / 128 clients; principal and browserUser are unique
ASCII identities (1–128 letters, digits, dots, underscores or hyphens).

The source configuration schema is:

```json
{
  "version": 1,
  "clients": [{
    "principal": "example-host",
    "tokenSha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "capabilities": ["search", "reader", "browser"],
    "browserUser": "example-host-scratch"
  }]
}
```

Use a different browserUser for each trust/account zone. Browser groups under
one user share cookies, even though tools cannot access one another's tabs.
Tokens and workflow capabilities never belong in URLs, argv, model-visible
errors, routine logs or public examples. The operator owns real provisioning;
source tests use synthetic identities only.

## Fixed operations and limits

- `GET /search?q=...&format=json`: fixed SearXNG `/search`, search capability.
  Necessary query fields only; repeated/encoded query values remain intact.
  Maximum decoded response 128 KiB, validated JSON, no upstream redirects.
- `GET /fetch?url=...`: fixed Reader `/fetch`, reader capability. Reader owns
  GET-only/public-address/redirect/connection enforcement. Maximum UTF-8 response
  512 KiB, mandatory complete untrusted markers and trust header. No direct or
  hosted fallback, no forwarding a private target to a public reader.
- `POST /v1/workflows`: browser capability; server mints an opaque workflow
  capability and random backend group. Return `{workflowToken: ...}` only to the
  protocol client, never as a Pi tool result. A client token and guessed group
  string do not authorize existing workflows.
- Browser requests carry `X-Spartan-Workflow: <workflowToken>` as well as client
  authorization. `POST /v1/browser/create`, `GET /v1/browser/tabs`, and
  `POST /v1/browser/{snapshot,navigate,click,type,scroll,screenshot,close}`.
  `DELETE /v1/workflows` explicitly closes only registry-owned tabs and releases
  the workflow; no profile/session-storage deletion. Native `cleanup` is explicit,
  not an automatic shutdown action. Failed/uncertain cleanup remains an explicit
  error and never promises a remote mutation was undone.
  JSON input includes only action arguments; existing-tab operations require
  `tabId`. No caller-supplied principal/userId/sessionKey/profile is accepted.
  Create uses optional `url`; navigate uses `url`; click uses `ref` or `selector`;
  type uses `text` and `ref` or `selector`; scroll uses `direction` and `amount`.
  Screenshot returns raw PNG. Other operations return bounded JSON.

The browser service owns every tab ID it returns and checks the exact workflow
registry on every operation. List reads only that registry. All creates use
`nonDestructive:true`; at capacity reject, never steal tabs. Every click requires
an advertised single-attempt backend capability and uses `singleAttempt:true`;
no adapter retries or backend click fallback. Type does not submit or press
Enter. Generic evaluate, viewport/relaunch, profile deletion, session reset,
cookie/vault/supervisor operations and arbitrary upload/download are excluded.
Browser actions are not read-only; clicks/navigation/type can send information
and cause high-impact actions. This contract does not pretend to classify safe
clicks or substitute for human approval/downstream authorization.

Bounds: 16 KiB request bodies; 30-second total transport deadline; finite
immediate admission; at most 8 workflows/client, 64 total, 4 tabs/workflow.
Screenshots are non-full-page PNG, at most 2 MiB, at most 4096 pixels/dimension
and 16 megapixels before returning native image data. Count actual bytes before
parsing. Inbound parser auto-decompression is disabled and every Content-Encoding
header is rejected before body handling; encoded requests are unsupported.
Transport requests identity encoding and rejects encoded responses before reading;
it does not decompress upstream bodies. Oversize/malformed/slow
responses return bounded sanitized errors, not partial JSON/trust markers or
upstream exception text. No query/page bodies are logged. Dedicated Caddy error
logging is discarded too: its default proxy errors otherwise include full query
URLs even with access logging disabled. Diagnose by bounded client error codes
and health checks; enabling verbose private diagnostics is a separate privacy
choice.

Backend numeric generation is not a process epoch. Generation changes, missing
tabs (404/410), and ambiguous mutation outcomes retire handles explicitly. Exact
IDs are never rebound from backend listing. No automatic click/submission replay,
replacement workflow or local browser spawn/download/backend fallback occurs.
An uncertain initial capability mint can retain an empty server workflow; no tab
operation is dispatched before local persistence. The same call is not retried;
a later explicit call without a saved capability may mint anew, consuming the
finite workflow quota. No exactly-once allocation guarantee is claimed.
Service restart invalidates in-memory workflow authority; a retained client
record gets an explicit stale error, not silent reattachment. Cleanup closes
only known owned tabs and never deletes persisted browser storage. Cancellation
bounds the client's wait, not proof that a remote mutation did not happen.

## Native Pi and privacy

Reuse installed pi-web-access SearXNG, not a duplicate search tool. Strict setup
selects only SearXNG, disables alternate fetch/source-check/search/curator
commands, and guards ordinary and nested `web_search` calls against provider,
content-fetch, workflow and proxy overrides. Invalid managed configuration blocks
managed tools and known alternate fetch/source-check tools, not unrelated local
terminal/editor tools; other plugins are outside this supported contract. The configured loopback ingress
must be the sole search endpoint, including environment precedence. Reader and
browser use the small native extension. Real Pi 1.4 is the current consumer;
1.3 remains the inherited compatibility gate.

A private directory (0700) stores atomic workflow records (0600), keyed by
endpoint, client identity and actual native session ID. Resume recovers its
record; fresh/forked sessions get new native IDs and must not inherit copied
parent capabilities. Bearers never enter session custom entries/tool details.
Concurrent minting is single-flight with session-epoch checks after awaits.

PNG results use Pi native image content. Screenshots may contain secrets and
enter saved conversation/model history; absence of an extra artifact file does
not erase those copies. Search queries go to search engines; Reader allows
arbitrary public GET disclosure through host/path/query. Trust markers are not
prompt-injection prevention. Host process confinement is explicitly not claimed.

## Installation owner, revocation and rollback

No installation is implied by source availability. After explicit operator
approval, enable the `tool-access` Compose profile **in addition to** existing
profiles. Keep Camofox selected for browsing; an inactive browser profile is not
available merely because search/Reader work. The new listener defaults to
`127.0.0.1:18792`; `SPARTAN_TOOL_EDGE_PORT` changes only its loopback host port.
Never attach the legacy Caddy service or a client to the backend/publishing nets.

One operator owns `private/tool-access/clients.json` (0600) and its containing
private directory (0700), mounted as a directory so atomic replacement is visible
on the next admission. The policy example above contains a **nonworking digest
placeholder**, not a credential. Provision independent random
256-bit client tokens into separate owner-only files; put only SHA-256 digests of
the exact token text (without a trailing newline) in policy. Browser user IDs are
new scratch identities, never an existing operator/Hermes account by default.
The adapter alone receives the existing browser access key; neither client does.

The Pi configuration owner is the explicit helper below. It installs only this
extension and helper files, `spartan-tools.json` and strict `web-search.json`.
It removes prior `searchRouting` state when selecting strict SearXNG, because
retaining a disabled provider there prevents the reused extension from loading.
Unrelated settings are preserved. It never changes model/Route settings or
installs packages. Native Pi and the
supported `pi-web-access@0.44.0` program must already be provisioned. Select an
agent directory explicitly; symlink/ownership/permission failures stop rather
than switch to another home. A same-owner group-writable directory is admitted
only beneath a verified same-owner, symlink-free `0700` ancestor; exposed or
foreign writable paths and world-writable descendants remain rejected. This does
not protect against a hostile same-UID writer. Default preview prints paths/status only:

```sh
PI_CODING_AGENT_DIR="$PI_AGENT_DIR" node scripts/configure-shared-tools.mjs \
  --agent-dir "$PI_AGENT_DIR" --base-url http://127.0.0.1:18792 \
  --client-id host-pi --credential-file "$CLIENT_TOKEN_FILE" \
  --state-dir "$PI_TOOL_STATE_DIR"
```

Only after approving that preview, use the same arguments with
`--apply --backup-dir "$PRIVATE_BACKUP_DIR"`. Backups are credential-bearing,
0700/0600 and outside extension discovery. The helper performs all validation
before writes, per-file atomic replacement and scoped rollback on failure—not a
globally atomic multi-file transaction or protection against a hostile same-user
writer. Normal native Pi launch selects the existing
`<getAgentDir()>/spartan-tools.json` only when `PI_SPARTAN_TOOLS_CONFIG` is unset.
The directory comes solely from Pi's native `getAgentDir()`; there is no cwd or
upward search, configuration creation, or token prompt. An explicit
`PI_SPARTAN_TOOLS_CONFIG` always selects exactly that value, including empty,
invalid or missing paths: rejection never falls back to the default. Missing,
unsafe or profile-mismatched defaults fail closed through the same validators
before managed HTTP requests. Keep `PI_CODING_AGENT_DIR` bound to the selected
profile; explicit selectors must also pass that profile's strict search checks.
Restart only the selected Pi process after separately approved installation;
there is no global agent restart or active reload during source verification.
This source default does not authorize production installation, which still
requires independent review and explicit operator approval.

The reused provider supports static headers only: the scoped token is duplicated
in mode-0600 `web-search.json` and its backups as well as the credential file.
Rotation must update both via this owner. `SEARXNG_BASE_URL`, if set, must match
exactly; strict mismatch fails rather than bypassing the selected ingress.

For revocation, atomically remove/replace the client's policy digest. New requests
fail immediately at admission; already-admitted remote effects are not undone.
A workflow's token/permission fingerprint is fixed, so rotation invalidates old
workflow authority too. Prefer explicit owned-workflow cleanup **before**
rotation/revocation; no automatic profile reset or destructive administrator
cleanup follows. Service restart invalidates in-memory workflow records; orphaned
remote tabs may remain until existing backend lifecycle cleanup. Empty uncertain
mints and revoked records can consume finite registry quota until service restart.
Do not promise revocation alone deletes browser resources or persisted storage.

Rollback: revoke the new client digests, disable only the new client extension
and tool-access profile/edge, and restore only the helper's scoped configuration
backups after checking for later edits. Keep existing Hermes/Route connections,
browser data and policy unchanged. If reverting new Reader/Camofox images, use
the retained prior image references and declared service-specific recreation;
never delete profiles/volumes or use `--remove-orphans`.

## Validation and operational gate

Executable command authority remains `.agents/validation.md`, publicly mirrored
by [development.md](development.md). TOP quality measurement does not validate
Caddy/Compose/Dockerfile semantics; actual native tests and independent review
remain required. Real Caddy, actual native Pi lifecycle/image hooks, actual
isolated Camofox and a restricted protocol container must be distinguished from
fault fixtures. Missing required inputs are INCOMPLETE, never accepted skips.

Reader's existing per-socket timeout alone does not bound total outbound work or
prove worker release on client cancellation. A narrow total-fetch/redirect
budget and slow-drip capacity-recovery evidence are required before claiming
that F7 portion. Existing fixed workers/finite pending queue are preserved.

Installation, real credentials, image preparation, canonical integration and live
canaries require one separately approved W6 proposal. Disable the opt-in ingress
and client extension and revoke scoped tokens to roll back; do not reset browser
profiles, delete data, rebuild Hermes or change Route merely to remove access.
