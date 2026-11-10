# Managed browser MCP (source prepared; activation is separate)

`spartan-browser-tools` is a shared stdio backend for MCPHub using its already
bundled official MCP SDK-v1 1.29.0. It adds no browser service and changes no Pi,
Hermes or MCPHub code. Search/Reader remain in their separate backend.

The path is **MCP client → MCPHub → browser adapter → tool-access → Camofox**.
Only tool-access has the browser master credential. The adapter receives a fresh
scoped policy token, never that master key or a profile directory.

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
Pi REST and operator profiles/tabs are never adopted.

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
cookie access, administration and generic JavaScript. There is no download,
arbitrary wait, browser relaunch, file chooser/trigger fallback or auxiliary
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
   folder. Prepare outside that folder and atomically rename the complete file
   into place. Do not publish a file while still editing/writing it.
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
`browser-state.mjs`, `browser-http.mjs` and `browser-contracts.mjs`. A regular
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
23 exact tools in the selected group, preserving the closed sentinel and all
other backends/grants. Discovery alone is not runtime acceptance.

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

## Verification

Executable gates belong to `.agents/validation.md`, publicly mirrored in
[development.md](development.md). Distinguish:
- journal and real temporary-file regression tests;
- official SDK/stdio tests with a fault HTTP peer;
- actual REST/file integration on a disposable internal Docker network, with
  an **inert browser peer**, including Docker RO enforcement;
- executing the patched native Camofox upload handler with inert Playwright
  resources, target/call-count regressions, and full ordered-patch idempotency;
- later actual Firefox, selected native clients and deployment readback.

The isolated acceptance does not prove real-site receipt, CSS rendering, Firefox
upload behavior, production mount deployment or current-client discovery. Those
remain explicit runtime gates; no paid inference or real credentials are needed
for source acceptance.
