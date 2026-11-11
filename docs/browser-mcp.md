# Managed browser MCP (source prepared; activation is separate)

`spartan-browser-tools` is a shared stdio backend for MCPHub using its already
bundled official MCP SDK-v1 1.29.0. It adds no browser service and changes no Pi,
Hermes or MCPHub code. Search/Reader remain in their separate backend.

The base path is **MCP client → MCPHub → browser adapter → tool-access → Camofox**.
In base mode only tool-access has the browser master credential; the adapter
receives a scoped policy token, never a profile directory. The optional native
mode below additionally gives this MCP backend a private Camofox access binding.
That is an explicit authority expansion, not another use of the scoped REST token.
It needs no Camofox/tool-access/Hermes/Pi/MCPHub source patch or new service.

## Explicit sessions, not native conversations

1. Call `browser_session_create` with a new UUIDv4 `operation_id`.
2. Retain the returned server-generated `session_id`.
3. Pass it to subsequent tools. `browser_create_tab` returns an owned `tab_id`.
4. Use fresh snapshots/refs after page changes. Close tabs and the explicit
   session when done.

A session ID is a shareable application-level capability handle **within the
clients authorized for this backend**. Reusing/copying it intentionally shares
control; it is not a native conversation ID. Unknown IDs cannot create/adopt
resources. Forking or copying a transcript does not establish a new isolation
boundary. All sessions on this backend share its dedicated scratch cookie
context; tabs are session-owned, cookies are not per-session. Existing Hermes,
Pi REST and operator profiles/tabs are never adopted automatically. Optional
native attachment explicitly borrows only an exact privately authorized tab/group;
it does not change the base-mode resource boundary.

The MCPHub backend is shared: leave `perSessionClient` **false/unset**. Its state
file admits only one process writer. A second writer fails closed instead of
sharing or resetting the journal. An uncleanly terminated writer can leave its
lock; reconcile actual writer cessation before an operator removes a stale lock.
No automatic stale-lock takeover is performed.

## Tools

| Surface | Tools / behavior |
| --- | --- |
| Session/tabs | `browser_session_create`, `browser_session_close`, `browser_create_tab`, `browser_list_tabs`, `browser_close_tab` |
| Navigation | `browser_navigate`, `browser_back`, `browser_forward`, `browser_refresh` |
| Interaction | `browser_click`, `browser_type`, `browser_press`, `browser_select`, `browser_scroll` |
| Inspection | `browser_snapshot` (offset pagination), `browser_screenshot`, `browser_links`, `browser_images`, `browser_extract` |
| Layout | `browser_viewport(width, height)` verifies effective CSS dimensions |
| Files | `browser_file_stage(path)`, `browser_upload(file_ids, ref/selector)`, `browser_file_remove(file_id)` |

Inputs reject unknown fields, arbitrary headers/backend URLs, profile/user IDs,
cookie-import/export APIs and administration. Base mode has no generic JavaScript; optional
native mode explicitly grants `browser_evaluate`. There is no file-byte download,
arbitrary sleep, browser relaunch, file chooser/trigger fallback or auxiliary
vision-model call. Structured extraction projects existing accessibility-ref
names to a bounded primitive-field subset; missing refs yield null, not invented
values. It is not general JSON-Schema validation or semantic extraction.

Type is fill-only; Enter is a separate explicit key action and can submit.
Clicks, typing, navigation and uploads can disclose information or cause actions;
obtain human authorization for high-impact operations. MCP annotations describe
these effects but are not a mechanical human-approval boundary.

Viewport supports integer sizes 100–4096, always fixes `relaunch:false` and
`reload:false`, and checks effective inner dimensions within two pixels. Failure
is `viewport_mismatch`/`viewport_unsupported`, never a successful crop or a
recommendation to restart the shared browser. This is not UA/touch/device-scale
emulation. Snapshots/link/image/extraction text remains framed as untrusted page
content. Screenshots return MCP PNG image blocks; they may contain secrets and
enter saved/model history. Limits remain 128 KiB JSON, 2 MiB PNG, 4096 pixels per
dimension and 16 megapixels; oversized results fail explicitly.

## Optional native utilities and shared Hermes tabs

The source adds nine tools (32 total catalog entries). Without
`SPARTAN_BROWSER_NATIVE_BINDING`, the nine return `unsupported_backend` and the
original REST mode is unchanged. Installed catalogs may still have only the
previous 23 tools until separately authorized activation. Native utilities are:

| Tools | Contract |
| --- | --- |
| `browser_evaluate(expression)` | Explicitly mutating page JavaScript, not a read-only console. Can disclose page data/send requests/act. Native evaluation timeout may close the tab. |
| `browser_wait_for(selector, timeout_ms?)` | Bounded selector polling through a fixed evaluate script, 0–10000 ms (default5000). Native `/wait` ignores selectors and waits readiness/network instead. |
| `browser_stats` | Sanitized URL and numeric tab counters, not request history. |
| `browser_downloads` | Metadata only (`id`, `suggestedFilename`, `bytes`, `createdAt`, `mimeType`, normalized `failed`; absent sizes may be null); forces `includeData=false`, `consume=false`. No paths or bytes. |
| `browser_attachable_tabs(profile)` | Candidates only from a privately configured alias and its allowed groups (exact-list mode by default). |
| `browser_attach(profile, session_key, tab_id)` | Borrow the exact native tab using a fresh MCP session with no owned tabs. No last-tab/URL fallback. |
| `browser_debug_start(ttl_ms?)`, `browser_debug_read(capture_id, cursor?, limit?)`, `browser_debug_stop(capture_id)` | Temporary page-local console/error/fetch/XHR observations; details below. |

Create a normal MCP session first. The backend rechecks that REST workflow's
current authority/generation before native calls, plus the native tab's current
identity/group. For ordinary MCP-owned tabs it also requires an exact match in
its REST-created-tab registry. A changed/stale binding or group never selects
another tab. Missing native configuration fails closed rather than falling back.

Native Hermes already tracks its own user/group/tab. Sharing only a `userId`
shares cookies, **not the selected tab**. The operator must explicitly map the
selected Hermes identity and allowed task groups in the private binding, then
select the exact candidate `tab_id` and `session_key`. Native tool output is not
automatic proof of its current backend tab. No client hook, native-conversation
identity injection, profile-directory read/copy, or cookie-export API is introduced. Explicit page
JavaScript can read JS-accessible cookies/storage and other private page data;
it is not a read-only or secret-isolating capability.

A typical sequence is: native Hermes opens a page → MCP `session_create` →
`attachable_tabs(profile)` → explicit `attach` → optional debug/start or upload →
`debug_stop` → MCP detach/session-close → native Hermes continues. Alternate
controllers; take a fresh snapshot after DOM changes. The native actions do not
gain this MCP's operation journal guarantees. Shared MCPHub tool grants are not
per-Hermes-profile/client ACLs: all callers authorized to use this backend's
native tools can access its configured aliases. Use a separately bound backend
and appropriate tool grants for a different trust zone; never combine all native
profile identities into one shared backend as an enrollment shortcut.

Borrowed handles support the four native utilities, debug tools, upload, listing
the exact borrowed tab, and detach. Ordinary navigation/interactions stay native;
unsupported borrowed actions fail explicitly. A second active MCP handle on this backend cannot
borrow the same native tab. `browser_close_tab` detaches a borrowed handle instead
of closing its physical tab. `browser_session_close` only closes its own backing
REST workflow/files. Neither operation performs native evaluate or native DELETE.
They report `capture_restoration:not_confirmed`; explicit stop is a separate
operation and capture may remain until its page-local TTL/navigation. Never close
a native profile/context as a cleanup workaround.

### Native binding and upload lifecycle

Keep the original binding unchanged. Optionally set
`SPARTAN_BROWSER_NATIVE_BINDING` to another regular non-symlink0600 file owned by
the backend UID in a canonical0700 directory. Exact schema:

```json
{
  "baseUrl": "http://camofox:9377",
  "token": "<private-camofox-access-key>",
  "managedUserId": "example-mcp-scratch",
  "profiles": {
    "example": {"userId": "example-hermes-user", "sessionKeys": ["example-task-group"]}
  },
  "uploadRoot": "/root/.camofox/uploads/spartan-managed"
}
```

`managedUserId` must match the existing REST principal's browser identity.
Profiles are explicit aliases to exact native user/group allowlists, not
model-supplied identities. Neither key nor physical staging paths enter tool
output. Root URL is fixed to internal Camofox (loopback is permitted for isolated
tests). Do not give the model credentials or expose generic proxy/admin routes.
Malformed/present native bindings prevent startup; omission preserves base mode.

An explicit alternative is `"profiles": {"native": {"userId":
"example-hermes-user", "groupAccess": "profile"}}`. This mode requires **exactly
one alias and fixed user identity**, with no `sessionKeys` field. It admits present
and future task groups under that one user, never other users. Exact tab/group
metadata is still checked before each action; it does not identify the current
Hermes task automatically. The original exact-list schema remains the default.

Staging remains on the existing REST FileStore, its sole filesystem writer.
The published layout contract for this integration is
`uploadRoot/<file_id>/<original-basename>`, immutable within the session. Only
validated stage results remembered by this MCP session may enter native upload;
old/unknown file IDs cannot be guessed or adopted. The adapter persists native
file claims **before** dispatching strict single-attempt upload. Ambiguous
uploads retain their claims across reconnect and block removal/detach/session
cleanup until operator reconciliation. A known result releases only its own
claims. No additional spool writer/mount or automatic retry is introduced.

Old version1 journals remain readable. The first native attachment (or native
file claim) atomically upgrades the journal to **version2**, which old adapters
refuse. This prevents a rollback from silently ignoring borrowed ownership or
uncertain-file guards. Do not downgrade JSON, delete pending records or start an
old adapter against that journal; reconcile native claims and choose a supported
state generation before any rollback. The single-writer lock and original bounds
remain unchanged. Generic evaluate results must fit the existing **2048-byte
serialized MCP receipt**; select/paginate small values. `response_too_large` is a
retained result, not proof that JavaScript did not execute; replay never executes
it again. Debug reads are separately bounded read-only result retrieval.

### Console and page-level network observations

Start capture **before** the action, retain its returned `capture_id`, read with
cursor/limit and call explicit stop when done. Defaults:60000 ms, maximum300000 ms;
200 events/65536 serialized event bytes,20 events per read,100 pending fetch and
100 pending XHR observers. Overflow is reported in `dropped`; it is not silently
presented as a complete history. Console strings/primitives are truncated; page
objects/getters are not serialized. Request URLs omit userinfo/query/fragment;
headers, bodies and file bytes are not captured. Console strings and URL paths
can still contain private data; redaction is not a secrecy guarantee. Returned
observations/evaluate results are framed as untrusted web content and may remain
in client/model history after capture expiry.

This is **not DevTools Network or Playwright tracing**. It sees future main-page
JS console calls/errors and fetch/XHR, not past messages, browser-internal CORS or
preflight details, cached original API references, workers or all frames. A CORS
failure may expose only an error/status0, not the server status. Navigation loses
the capture (`capture_missing`); starting over an active capture returns
`capture_busy`, and non-writable hooks return `capture_unsupported` after rollback.
Instrumenting promise rejection observation can affect the page's
unhandled-rejection reporting; stop after the diagnostic. No automatic navigation,
form retry or candidate submission follows.

Explicit stop restores only hooks still owned by this capture; later page changes
are preserved. TTL stops/restores/clears data only when the page executes its
scheduled callback, not at a guaranteed wall-clock deadline on a suspended page.
Detach never evaluates cleanup on a potentially unresponsive native tab: that
native evaluate timeout could itself destroy the tab. Call `debug_stop` explicitly
or allow expiry/navigation, with that distinction visible to the caller.

## Mutation replay and recovery

Every mutation requires an `operation_id` UUIDv4. Use a fresh ID for a new action;
repeat **the identical ID and arguments** only to retrieve that action's retained
outcome. A different argument set with that ID returns `operation_conflict`.
A changed/new ID can cause a new effect.

Before dispatch, the adapter durably records a pending operation and reserves
space for its bounded result. Duplicate completed calls return their **historical**
result, not fresh readback. Pending/ambiguous calls return
`remote_outcome_unknown` without redispatch, even after a transport reconnect.
A crash before dispatch can therefore sacrifice progress. This is at-most-once
**dispatch per retained ID**, not exactly-once remote execution or rollback.
Cancellation/timeouts/lost responses never prove a click/upload was undone.

The private journal is bounded: eight active sessions, 512 retained operations,
1 MiB including completion reservations. Reads do not consume operation entries.
Cleanup does not erase history or replenish its ceiling. Capacity fails before
new effects; no automatic journal deletion, quota increase or restart workaround.
An operator must reconcile consumers/outcomes before retiring state or moving to
a fresh credential/state generation. Do not delete a journal to retry an action.
Unsafe/corrupt/wrong-binding state and persistence uncertainty fail closed.
A tool-access restart invalidates its in-memory workflows; saved MCP sessions
then return stale errors, never silently attach to another browser session.

## General file transfer

This supports ordinary regular files—documents, images, archives, text and other
binary files—not just PDF or one workspace. Archives are not unpacked or executed.
The MCP cannot read an arbitrary path in a caller's filesystem.

1. An authorized caller copies the selected file into the dedicated `incoming/`
   folder. Prepare a temporary file on the **same filesystem/mount**, then
   atomically rename it to its final published name. With an incoming-only bind,
   use an unadvertised temporary name there; consumers must never stage temporary
   names. Do not publish a file while still editing/writing it.
2. `browser_file_stage(session_id, operation_id, path)` accepts a **relative**
   path under incoming. It opens anchored no-follow descriptors, rejects symlink
   leaves/intermediate directories, traversal, devices/FIFOs and directories,
   copies incrementally, hashes the staged bytes and rejects detected changes.
3. The result contains opaque `file_id`, original filename, size and SHA-256.
   The snapshot has an unpredictable per-file directory preserving its name and
   extension. Neither internal paths nor a browser key are returned.
4. `browser_upload` attaches 1–8 IDs owned by that session to one exact
   `input[type=file]` identified by ref or selector. The target must be unique;
   a button opening a chooser is not a supported upload target in strict mode.
5. `browser_file_remove` deletes only that staged copy. Session cleanup removes
   its staged copies and tabs, never the incoming original or browser cookies.

Staging requires both `browser` and the separate **`upload` policy capability**.
Old browser-only tokens do not gain filesystem access. Limits: 20 MiB/file,
eight staged files/workflow, 64 files and 256 MiB globally including reservations,
partials/orphans/uncertain files; 40 MiB per attachment call. Duplicate IDs are
rejected before browser dispatch. There is no type-specific whitelist.

**Attaching can immediately transmit bytes via page JavaScript.** Success means
attachment to the selected input, not confirmed remote receipt or form
submission. Verify the UI before claiming receipt; submission is a separate
user-authorized action. Never use a real document/site merely as a smoke test.

Incoming is an intentionally shared transfer zone: authorized callers able to
name/read a file there can stage it in their own session. Do not leave unrelated
private files there or claim per-conversation ACLs for that folder. This is not
a sandbox against hostile same-UID writers or privileged backend code.

Tool-access owns the outgoing spool. If it starts with a nonempty/unindexed
spool, stage/upload return `reconciliation_required`; it never adopts/deletes
those files automatically. Browser-only actions remain independent. A copy
worker keeps its reservation until actual completion/confirmed cleanup. Ambiguous
uploads retain charged uncertain files; remove/cleanup refuse them pending
explicit operator reconciliation. Destination pathnames are never recycled.
There is no automatic age-based cleanup. Preserve pending files/evidence before
any separately authorized retirement.

## Operator preparation and activation

Use existing protected storage, not a task checkout. Prepare dedicated
`incoming` and `outgoing` directories owned by the effective tool-access UID/GID,
mode 0700. Never reuse a profile root, a whole home, Work tree, or an occupied
unclassified directory as the transfer root.

The optional `infra/compose/compose.browser-tools.yml` adds **only** these mounts:
- tool-access reads incoming RO and writes outgoing RW;
- Camofox reads outgoing RO under its existing configured uploads directory.

It requires `SPARTAN_BROWSER_TRANSFER_PATH`, the reviewed
`SPARTAN_BROWSER_TOOL_ACCESS_IMAGE` and `SPARTAN_BROWSER_CAMOFOX_IMAGE`.
`SPARTAN_BROWSER_CAMOFOX_UPLOAD_ROOT` defaults to
`/root/.camofox/uploads/spartan-managed`; if the installation uses another
`CAMOFOX_UPLOADS_DIR`, select a descendant of that actual root. The overlay never
changes the legacy uploads-directory setting. Do not overmount an occupied
unclassified target or hide another workflow's files. Missing directories fail rather
than being silently created by Docker. Verify effective ownership, paths and RO
behavior before approval. Host callers use the host incoming path. Optional
`compose.browser-transfer-hermes.yml` exposes **only incoming** at
`/opt/data/browser-transfer/incoming` to Hermes's existing file tools; it adds no
plugin/tool code, but applying that mount recreates Hermes and needs its own quiet
window. Other callers need their own explicitly approved narrow file transport.

Registration uses one protected runtime directory containing `browser-tools.mjs`,
`browser-state.mjs`, `browser-http.mjs`, `browser-contracts.mjs`,
`browser-camofox.mjs` and `browser-debug.mjs`. A regular
non-symlink binding 0600 owned by the backend UID has exactly:

```json
{
  "baseUrl": "http://tool-access:9081",
  "host": "tool-edge:18792",
  "token": "<fresh-browser-and-upload-client-bearer>",
  "statePath": "/state/browser-tools/state.json"
}
```

The state parent must exist, 0700 with trusted ownership and no symlink ancestry.
Initialize state only through the adapter, not hand-written approval records.
The new policy principal uses a fresh scratch `browserUser`; preserve all prior
policy entries/fingerprints. Register an admin-owned stdio backend with
`command: node`, args pointing at its stable `browser-tools.mjs`, and env
`SPARTAN_BROWSER_TOOLS_BINDING` pointing at the protected binding. Grant only the
base23 tools in the selected group, preserving the closed sentinel and all
other backends/grants. Native utilities add nine explicitly selected grants;
never assume existing native-profile access from a catalog entry. Discovery
alone is not runtime acceptance.

Uploads additionally require Camofox's advertised `singleAttemptBrowserUpload`
capability. Without the reviewed patch, upload fails `unsupported_backend`
before dispatch. Strict mode matches exact accessible names (including empty names) for refs and
performs one `setInputFiles` on the selected input, never the first unrelated
input, a missing iframe fallback or a second attempt.
Legacy upload calls without `singleAttempt:true` retain upstream behavior.

Source availability does not authorize image builds, new mounts, service
recreation, registration or real-file exposure. Applying tool-access/Camofox
images can invalidate current browser workflows/tabs; preserve rollback images,
private beforeimages and a quiet window first. Use the main `sg` workflow and
explicitly include reviewed overlays in the selected installation; this source
overlay is not automatically loaded by `sg`. Do not weaken network policy,
restart unrelated services or remove volumes/orphans. Disable/revoke only this
new backend/principal for rollback; never restore whole settings over later work.

## Per-profile provisioning (operator only)

`scripts/configure_browser_profiles.py` handles explicitly selected, already
MCP-enabled Hermes configs; `configure_mcphub.py` retains its original three-client
bootstrap contract. See `infra/mcphub/browser-profiles.example.json` and
`browser-native-template.example.json` in that directory for **nonworking** inputs.
Supply actual private paths/digests, the current admin password in a0600 file,
and native identities derived by the **installed Hermes identity function in the
selected profile context**, not from host paths or guessed profile names.

The protected manifest pins the six reviewed runtime modules and native template.
Verify host/container path correspondence in the actual installation first. The
helper does not install/copy code, discover homes, start services or alter network
policy. Directory modes are0700; bindings/job/beforeimages are0600 with the runtime
UID. Explicit selected native configs may be0600/0640/0644; their original bytes,
owner/group and mode are backed up before atomic0600 publication. The job must be
outside native discovery and retained for recovery; it contains credentials.

```sh
# Authenticated preflight; no configuration writes. Paths are operator variables.
.venv/quality/bin/python scripts/configure_browser_profiles.py --manifest "$MANIFEST" --job "$JOB"
# After source acceptance and installation-specific approval:
.venv/quality/bin/python scripts/configure_browser_profiles.py --manifest "$MANIFEST" --job "$JOB" --apply
# Only after checking exact current afterimages and affected consumers:
.venv/quality/bin/python scripts/configure_browser_profiles.py --manifest "$MANIFEST" --job "$JOB" --rollback
```

Each selected profile gets a distinct REST browser/upload principal, scratch
`browserUser`, native binding, shared-process stdio backend, journal, private
MCPHub group and group-restricted key. Common backend selections/policies and
source-group instructions are retained. A group alias preserves the existing
browser tool namespace; only that profile's native URL/Authorization changes.
The closed sentinel and exact native tool grants precede usable key publication.
Identity-specific legacy policies or an already-native legacy backend require
explicit mapping/consumer reconciliation, not guessed translation or silently
leaving a broad native-access path active. Native API calls never rewrite live `mcp_settings.json` directly.

New hub keys are created **disabled**. Their one-time secret must reach the
fsynced private job before enable/publication. A lost create reply reconciles the
owned disabled object and uses native reset; it never blindly creates another
key. Lost reset replies remain disabled/reconcilable. Ambiguous operations stop,
not retry automatically. Repeat the same manifest/job to reconcile; changed
inputs, foreign afterimages or occupied unrelated objects refuse. This is a
serialized operator rollout, **not** a distributed transaction or protection
against concurrent administrators; use an exclusive configuration window.

Configuration rollback restores only matching selected clients and disables only
owned keys. It deliberately retains backend registrations, policy principals,
bindings, journals and spool for pending-consumer/claim reconciliation. It does
not reset journals, delete browser contexts, remove files by age or restore a
whole hub snapshot. Keep legacy backend/keys/state while old consumers need them.
For a canary then remaining profiles, preflight the full selection first and use
separate protected subset jobs; do not change a running job's manifest.

Reconnect selected native clients through supported controls (`/reload-mcp` for
Hermes), preserving active work and respecting confirmations. Fresh-process
discovery does not prove an already-open gateway/session reloaded. Verify a fresh
synthetic native task/tab → exact MCP attachment → staged upload/debug → explicit
stop/detach → native continuation for every selected profile before claiming
operational acceptance. A new profile needs its own reviewed enrollment; copying
another profile's key is intentional capability sharing, not identity isolation.
Global REST workflow/FileStore capacities are still shared; per-profile journals
do not reserve or raise them. The transfer incoming zone and same-UID backend
processes remain shared trust surfaces, not hostile-tenant isolation.

## Verification

Executable gates belong to `.agents/validation.md`, publicly mirrored in
[development.md](development.md). Distinguish:
- journal and real temporary-file regression tests;
- official SDK/stdio tests with a fault HTTP peer;
- actual REST/file integration on a disposable internal Docker network, with
  an **inert browser peer**, including Docker RO enforcement;
- executing the patched native Camofox upload handler with inert Playwright
  resources, target/call-count regressions, and full ordered-patch idempotency;
- an optional real Camoufox page-producer fixture using a disposable existing
  image, synthetic intercepted requests and network disabled; not native Hermes;
- later selected native clients and deployment readback.

The isolated acceptance does not prove real-site receipt, CSS rendering, Firefox
upload behavior, production mount deployment or current-client discovery. Those
remain explicit runtime gates; no paid inference or real credentials are needed
for source acceptance.
