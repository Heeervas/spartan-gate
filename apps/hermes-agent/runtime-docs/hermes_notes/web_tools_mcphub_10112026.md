# Search and Reader through MCPHub — 10 November 2026

**Applied change.** The optional `spartan-web-tools` MCPHub backend exposes only
`web_search` and `read_page`, using the existing authenticated tool-access REST
operations. It runs as a trusted stdio child inside MCPHub with the image's bundled
official MCP SDK; no additional container, dependency download, network expansion,
service restart or browser migration was performed.

The selected Hermes and host/container Pi connections discover 103 hub tools
(previously 101). Real public search and Reader calls succeeded with all three
client keys. Fresh native Pi sessions and Hermes MCP discovery report the two new
tools. Existing open sessions should reconnect their MCP connection to refresh
cached catalogs; Hermes supports `/reload-mcp`, and Pi exposes reconnect through
`/mcp` (restart the selected client session if needed, not the Docker stack).

The backend has its own search/Reader-only policy principal. Browser operations,
private-page URLs and management access were denied during acceptance. Two
concurrent calls are shared across this backend's consumers; this is not per-agent
identity isolation. Search results and fetched pages remain untrusted data and
can enter native model/session histories. No fallback browser/provider is added.

Existing native search, Reader and browser tools remain available; their removal
or browser-MCP migration is not part of this change. The two existing tool-access
containers and current browser workflows were preserved. Generic setup, limits,
validation and rollback are maintained in `docs/mcphub.md` and
`docs/shared-tool-access.md`.

Hermes CLI discovery also emitted its existing Docker-managed pnpm/artifact
out-of-sync warning, but MCP discovery passed. This change did not rebuild or
repair that unrelated artifact state; any such repair remains separately scoped.

This is an operator archive note, not a published Hermes runtime guide. Private
credentials, bindings and recovery copies remain outside public source.
