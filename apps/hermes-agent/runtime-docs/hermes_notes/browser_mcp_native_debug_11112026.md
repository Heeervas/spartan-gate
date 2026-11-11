# Native browser MCP debugging — pending activation

Status: source implementation prepared; no native-profile binding, grants or
runtime activation applied by this change. This operator archive is not mounted
into Hermes. The public contract is `docs/browser-mcp.md`.

The optional MCP tools add evaluate, bounded selector wait, stats, download
metadata, exact native-tab listing/attachment and temporary console/error/fetch/XHR
capture. They use the existing Camofox API; no Hermes, Camofox or tool-access
source patch, rebuild or restart is needed for this addition.

Hermes can navigate/type normally, then use MCP for a selected upload/debug action
on the same physical tab. An operator must first authorize the exact native
identity and task groups in the private MCP binding. The profile alias alone
cannot identify the current Hermes tab; select its exact tab/group. Do not merge
all tasks through one global session key or expose every profile as a shortcut.

Start capture before the action and stop it explicitly afterwards. This records
future main-page JS observations, not past events/full Network/CORS-internal or
worker traffic. Console strings and URL paths may contain private data. Native
evaluate is mutating and its timeout can close the tab. Borrowed detach/session
cleanup deliberately performs no evaluate or native DELETE; it warns that hooks
may remain until page-local expiry/navigation. Suspended pages cannot promise
wall-clock expiry. Normal native/MCP actions must alternate with refreshed refs.

Staging still uses the sole REST FileStore writer. Native upload persists file
claims before dispatch; unknown outcomes block file removal/detach/cleanup across
reconnect. Native attachment upgrades the journal to version2 so old adapters
cannot silently ignore those guards. Never downgrade or reset a pending journal.

Source checks exercise the real SDK, real REST/FileStore with an inert native
peer, and the actual page producer in disposable Camoufox with network disabled
and a synthetic file-triggered403. They do not prove current Hermes attachment,
real-site receipt or deployed configuration. Full Playwright traces are excluded.

## Multi-profile provisioning — prepared, not activated

The separate operator provisioner can select existing MCP-enabled profiles and
create one fixed-user native binding, REST scratch principal, journal/backend
and group-restricted key for each. Explicit profile-wide group access permits
future tasks under that identity; it never combines native users or guesses the
current tab. Common grants and exposed browser tool names are preserved.

Use protected pinned manifests and recovery jobs, native management APIs and an
exclusive configuration window. Keys start disabled; lost create/reset replies
remain recoverable without duplicate key creation. Rollback restores only matching
client beforeimages and disables owned keys, retaining journals, spool and REST
principals. Keep old consumers/state until reconciled. Use supported native MCP
reload, not a gateway/service restart. Source/synthetic tests do not certify an
already-open session reloaded or a real native same-tab upload/debug workflow.
