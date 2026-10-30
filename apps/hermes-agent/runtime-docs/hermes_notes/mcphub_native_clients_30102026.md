# MCPHub native clients — applied operator update

Applied change: the optional pinned MCPHub integration has been activated and the selected Hermes native HTTP client plus selected host/container Pi clients enrolled. No real backend account or OAuth consent was enrolled.

- Hermes uses native `mcp_servers`; Pi uses native `mcp.json` and direct tool exposure. Only Pi's MCP builtin was enabled; other builtin disables and unrelated configuration were preserved.
- Authenticated empty discovery is intentional before backend enrollment and exact tool grants. Client keys are distinct, agents-group scoped and cannot administer the hub.
- Existing Pi processes need `/reload` after external settings changes, then `/mcp reconnect spartan-mcphub` (or a restart). Existing Hermes chats can use native `/reload-mcp` after enrollment/grant changes. No Hermes image rebuild is required.
- Use admin-owned shared HTTP/API-key connectors first. Connector-specific personal OAuth compatibility remains unverified; do not weaken proxy/DNS or direct-egress restrictions to make a connector work.
- See `docs/mcphub.md` for first login, closed onboarding policy, full-state private backup and scoped rollback. Keep bootstrap/admin credentials and actual native client paths in private operator documentation, never model prompts or this archive.

Validation: ordinary host/container Pi sessions loaded `builtin:mcp`, connected with zero direct-exposure tools and zero inference usage; native Hermes HTTP discovery passed. Native admin login and client management denial, production proxy allow/deny/direct rejection, and live restart persistence passed. Browser UI interaction and real connector consent/refresh were not observed.

This archive note is not runtime guide publication and must not be mounted into Hermes.
