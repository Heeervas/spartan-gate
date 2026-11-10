# MCPHub enrollment across existing Hermes profiles

Status: applied configuration; already-running session reload remains user-owned.

- Enroll only the six operator-approved existing profiles, excluding the SSR
  synthetic consumer. Preserve the already-configured principal.
- Merge only `mcp_servers.spartan-mcphub` from the principal; preserve all other
  configuration, native file ownership and permissions.
- This is one-time enrollment, not automatic inheritance for future profiles.
- The enrolled profiles share the principal's existing hub bearer and grants.
  Enrollment does not create per-profile authorization isolation.
- No Hermes/MCPHub core, Camofox, Pi, Compose or hub policy changes were made.
- Verification: each profile's native read-only config loader selected the hub;
  authenticated MCP initialization and catalog discovery returned 126 tools each.
- Discovery is not proof that every backend account is authenticated or usable.
  No backend account operation was exercised for this enrollment.
- Already-open sessions should run `/reload-mcp`; no gateway restart was forced.
- Recovery beforeimages were archived privately as a restricted compressed bundle,
  with file identities and restoration instructions. Actual restricted extraction
  compared byte-for-byte before applying changes; no loose backup files remain.
- Restore only explicitly selected files after checking for concurrent drift,
  preserving ownership and permissions, then reload the affected native session.
