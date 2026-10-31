# MCPHub (opt-in, L4)

MCPHub 1.1.14 is a small shared MCP control plane, not a replacement for managed
search, Reader, Camofox or Spartan Route. Source availability is not activation.
The standard image is pinned to
`sha256:700013d712b7e74750bb2ce05492688833f62a39472db5e88753557002735dab`.
There is no fork, database service, Docker socket or custom client extension.

## Boundary and endpoints

- The hub joins **only** `spartan_internal`. General HTTP/HTTPS uses existing
  Tinyproxy and whitelist-controlled DNS; it has no external/publishing bridge.
  Hermes networking and its existing proxy bypass list are unchanged.
- Existing Caddy adds a separate `:18793` listener. It passes `Authorization`
  unchanged; it does **not** inherit browser Basic Auth or header stripping.
  MCPHub owns dashboard authentication and verifies native MCP bearer keys.
- Default dashboard: `http://127.0.0.1:18793`; host MCP:
  `http://127.0.0.1:18793/mcp/agents`; internal MCP:
  `http://caddy:18793/mcp/agents`. `SPARTAN_MCPHUB_PORT` changes the host port only.
  Public Compose always binds that Caddy port to loopback, even when the hub
  profile is disabled (then there is no hub upstream). Private exposure needs
  a separate decision. Caddy already has a normal publishing bridge; neither
  MCPHub nor Hermes gains one.
- One hub, file mode, non-root UID/GID and read-only root. Defaults are 512 MiB
  memory, 128 PIDs and bounded `/tmp`; connector-specific private limits must be
  based on measured combined use. Writable HOME/cache is inside protected state.
  Stdio backend processes share this container, **not** one sandbox per backend.
  Only the administrator should enroll trusted backend commands/packages.

Initial supported onboarding is **admin-owned shared HTTP/API-key connectors**.
The actual pinned transport uses the proxy for these. Some personal/non-admin
SSRF-protected transports intentionally connect directly in upstream; Gate's
network still blocks external access, but those modes may not function. Personal
OAuth, URL credential templates and connector-specific refresh need separate
compatibility validation; do not loosen the network to make them work.

## Prepare the selected clients (operator only)

The helper never discovers homes, installs packages, starts services or rewrites
other profiles. Python requires the existing pinned `ruamel.yaml` dependency.
Choose the three exact native files/roots; the already-shared container Pi root
is one client, not enrollment of every Hermes profile. Pi must support native
Streamable HTTP and direct exposure (validated target: Pi 1.4).

Create an owner-only JSON target manifest outside native discovery, following
this **nonworking path example**. The internal URL is deliberately through Caddy,
which is already on Hermes' managed `NO_PROXY` list:

```json
{
  "hermes": {
    "config": "/absolute/selected-hermes/config.yaml",
    "url": "http://caddy:18793/mcp/agents"
  },
  "pi": [
    {
      "client": "host-pi",
      "settings": "/absolute/selected-host-pi/settings.json",
      "mcp": "/absolute/selected-host-pi/mcp.json",
      "url": "http://127.0.0.1:18793/mcp/agents"
    },
    {
      "client": "container-pi",
      "settings": "/absolute/selected-container-pi/settings.json",
      "mcp": "/absolute/selected-container-pi/mcp.json",
      "url": "http://caddy:18793/mcp/agents"
    }
  ]
}
```

All selected existing files and the manifest must be regular, symlink-free and
mode 0600; hub state must be 0700. If an existing selected file is 0644/0640,
first take its private backup and record its original metadata, then change
**only that explicit file** to 0600. Do not run a recursive permission repair.
Native files with different owners require the authorized owner/root to run the
helper; writes preserve existing file ownership, and new `mcp.json` inherits its
selected parent owner. When preparing state as root, pass the actual non-root
service `--state-owner UID:GID`, matching Compose's `HERMES_UID:HERMES_GID`.

```sh
# STATE_DIR is an absolute private path; its parent must already exist.
# BACKUP_DIR must be vacant, outside state and all selected native roots.
.venv/quality/bin/python scripts/configure_mcphub.py \
  --state-dir "$STATE_DIR" --targets "$TARGETS_JSON" --backup-dir "$BACKUP_DIR"
# Only after successful preview and approval, repeat with --apply.
```

Omit `--targets` for state-only preparation. The helper preflights **every**
selected target before any write, refuses malformed/occupied foreign entries,
backs up all changed native beforeimages privately, and merges only server
`spartan-mcphub` plus Pi's explicit `builtin:mcp` enrollment. It does not enable
codemode, tool-search, other builtins or change providers/skills/sessions/trust.
Parity preparation preserves this explicit MCP opt-in; its default remains off.
Pi entries use `exposure: "direct"`. Hermes uses native `mcp_servers` URL/headers;
Pi uses native `mcp.json` `mcpServers`. Bearers are stored only in these private
configs and private state/backups, not tools, prompts or shell arguments.

Reruns reuse the prepared tokens and leave mutable hub settings/admin/permissions
untouched. Already-identical native files remain byte-identical; a changed foreign
entry is refused, not overwritten. A failed write rolls back prior native writes;
this is not an atomic multi-file filesystem transaction. Inspect a failure before
retrying. Never reinitialize or replace an occupied/incomplete state directory.

After independent candidate acceptance, the operator adds `mcphub` to the existing
Compose profiles without dropping the active browser/other profiles, sets the
absolute private state path, renders the effective private Compose, and starts
only the new hub plus the separately approved Caddy port/config change. Use
`source scripts/aliases.sh` and the main `sg` command, never legacy `sg route`.
No Hermes rebuild, unrelated recreation or wholesale stack restart is needed.
Existing private Caddy configurations must retain the public `route.d` import.

## First login and first backend

1. Open the loopback dashboard, username **admin**. Read the bootstrap password
   locally from `$STATE_DIR/bootstrap-password` in a private terminal/viewer;
   never paste it into chat, URLs, reports or a shell command argument. Change
   it using native account controls after first login. Startup initializes the
   admin only when no users exist; the file is not a password-reset mechanism.
2. In native server management, add the trusted **admin-owned** MCP server and
   its connector credentials. Leave caller-header passthrough off: hub client
   bearer authorization and upstream connector authorization are different.
   Admit the actual required domains via the normal operator whitelist workflow;
   no automatic admission or direct fallback is provided.
3. Put that server in group **agents**. Prefer the group's explicit tool selection,
   not `all`, and select only intended tools. The three client keys are system
   keys restricted to this actual group; they cannot use management APIs or the
   global endpoint. Never change them to `accessType: "all"`.
4. In the native system tool-policy editor, add exact **allow** rules with the
   agents group ID, actual server ID and original upstream tool name. Keep
   `agents-closed-until-granted`: it is an applicable allow rule pointing at the
   deliberately nonexistent all-zero server ID. A dangling-reference diagnostic
   for that sentinel is expected. It makes an otherwise empty allow set deny
   execution; absent/empty policies in MCPHub would allow access. Do not delete
   it or replace exact grants with wildcard/broad group/server-only grants.
   Added backend tools remain denied until explicitly granted. Policy deny rules
   take precedence over allows.
5. Refresh selected clients: in Pi use `/reload` after external config changes,
   then `/mcp` → reconnect (or restart that Pi process). Check that the ordinary
   session reports the hub connected; `pi mcp list` alone does not load session
   extensions and cannot prove MCP is enabled. Keep codemode/tool-search disabled.
   Restart/reload only the selected Hermes native client session so it rediscovers
   MCP tools; do not rebuild Hermes. Reconnect after hub restarts too.

An authenticated empty tool list before step 4 is **expected**, not a broken
installation. No sample backend or test tool is retained in production. Local
source canaries prove protocol/auth/proxy behavior with an inert peer, not a real
connector account, browser login or OAuth refresh.

## Local stdio runtimes and account readiness

A stdio definition needs an actual executable and its compatible dependencies
inside the hub; an old host/workspace path or `enabled: true` is not a restored
runtime. Keep trusted runtime closures under protected `/state/runtimes` and
connector-specific bindings under `/state/bindings`, rather than mounting an old
home or whole workspace. Preserve each selected context and its matching encrypted
state/key together. Bound source access and use trusted, non-world-writable cache
ancestry when a backend requires it. Copied virtual environments can depend on the
exact pinned image's interpreter ABI and their deployment path.

Preflight all required artifacts/bindings before activation. Retain exact group
selections and execution policies; do not auto-grant newly discovered tools or
broaden write/publish/trading access. Measure the hub and its children together,
including startup peak; a successful isolated per-backend probe is not a combined
memory acceptance.

Connected/discovered means MCP protocol readiness, not authenticated account
access. Some status tools only check credential-file presence, and some backends
return business errors inside a successful MCP result. Use a minimal authorized
read-only check before claiming account usability. Expired credentials need the
operator's login; desktop adapters need their running application/addon. An
already-admitted provider endpoint can still fail DNS or be unavailable. Do not
relax egress, consent on the user's behalf or repair an unrelated service to hide
these blockers.

## Secrets, persistence, upgrades and rollback

Protect the **entire state directory** and its backups, including
`mcp_settings.json`, `clients.json`, `bootstrap-password`, writable HOME/cache and
any `.credentials.key` sidecar created for personal credentials. Shared connector
secrets can be plaintext in file-mode settings; do not describe all state as
encrypted. Personal encryption is useless without its original key. Back up the
whole directory privately while the hub is stopped, including any installed
runtime closures and connector bindings. Preserve private directory/credential
permissions, executable bits and runtime symlinks; do not chmod executable
programs to 0600. Keep the compatible image and exact runtime paths. Never mount
state or backups into an agent workspace.

Hub Docker log retention is disabled because upstream logs may contain tool
traffic; activity tool-payload storage is explicitly off. Caddy access logging for
this listener is discarded. Native client/model session histories may still contain
MCP results; tool content is untrusted and can be sensitive. Do not enable verbose
logs or publish settings exports to diagnose a connection.

Revoke a client by disabling its named native bearer key (`hermes`, `host-pi`,
`container-pi`) in MCPHub. New admissions fail immediately; already-admitted side
effects are not undone. Retain distinct tokens. Rotation requires updating the
selected private client config and managed token record together; the bootstrap
helper does not silently rotate or restore disabled hub keys.

Before any upgrade, stop only the hub, retain the old pinned image and complete
private state backup, then test the candidate against the same boundaries. Change
the digest deliberately; never auto-update. Rollback restores the old image and
compatible complete state while stopped. For removal, revoke these keys, stop
only the hub, remove only this listener/mapping after approval, and restore only
owned native changes after checking for later edits. Backups include exact
beforeimages and a private `manifest.json`; a null entry means that file was newly
created. Do not overwrite later foreign config edits or delete persistent state,
backend accounts, other profiles, Camofox, Reader or Route.
