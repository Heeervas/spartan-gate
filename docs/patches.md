# Hermes Patch Taxonomy

Spartan Gate currently carries Hermes patches for four different reasons:
container safety, Spartan Gate integration, plugin-like behavior, and likely
upstream bugs. Keep this classification current when adding or removing a
patch.

## Container Safety

These patches are part of the Docker security boundary. Keep them in the image
unless upstream provides the same invariant.

| Patch | Role |
| --- | --- |
| `patch_stage2_install_chown_guard.py` | Keeps installed Hermes code root-owned, migrates config before profile reconciliation, and avoids broad runtime chown. |
| `patch_runtime_identity_drop.py` | Drops runtime workloads to configured numeric UID/GID. |
| `patch_s6_service_permissions.py` | Keeps dynamic s6 service definitions root-owned and disables unsafe runtime service registration. |
| `patch_stage2_cdp_env.py` | Materializes browser/CDP env before supervised services start. |
| `patch_gateway_service_cdp_env.py` | Imports browser/CDP env into supervised gateway services. |
| `patch_chrome_devtools_ws_auth.py` | Migrates browser tooling to authenticated configured CDP endpoints. |

## Spartan Gate Adapters

These are not generic Hermes behavior. They connect Hermes to Spartan Gate
services and should stay owned by this repo unless Hermes gains matching
extension points.

| Patch or adapter | Role |
| --- | --- |
| `patch_clawroute_named_provider.py` | Migrates Hermes config to the named `custom-1` ClawRoute provider. |
| `patch_clawroute_prompt_cache_key.py` | Adds stable session cache metadata for ClawRoute. |
| `patch_camofox_access_key.py` | Sends Camofox bearer auth from Hermes browser calls. |
| `proxy-bootstrap.py` | Routes selected web/search traffic through Spartan Gate controls and logs audited egress. |
| `browserless-cdp-broker.py` | Shares one persistent Browserless profile session across local CDP clients. |
| `browserless-cdp-url.js` and `cdp-patch.js` | Normalize Browserless CDP URLs for container networking and auth. |

## Removed Bundled Plugins

| Former bundle | Decision |
| --- | --- |
| `hermes-web-search-plus` | Removed from the image because Hermes supports native SearXNG `web_search` through `SEARXNG_URL`. Existing user-owned plugin directories are not deleted automatically. Keep `proxy-bootstrap.py` for now as a Brave-to-SearXNG compatibility and audit bridge. |

## Plugin Candidates

These could become Hermes plugins or upstream extension packages if their
hooks are stable enough.

| Patch | Candidate shape |
| --- | --- |
| `patch_google_meet_cdp.py` | Google Meet browser backend selector. |
| `patch_google_meet_transcript.py` | Google Meet transcript and muted-join behavior. |
| `patch_prompt_load_callback.py` | Telegram prompt-load callback handler. Upstream now ships Telegram as a platform plugin, so this patch targets that plugin adapter and is a strong candidate for extraction into a Spartan Gate plugin. |
| `patch_checkpoint_rollback_scan.py` | Rollback command handler with scoped checkpoint lookup. Keep conservative until upstream rollback APIs settle. |

## Upstream Bug Candidates

These should be proposed upstream or deleted when upstream fixes the behavior.

| Patch | Why it looks upstreamable |
| --- | --- |
| `patch_gateway_retryable_startup.py` | Retryable startup outages should not kill the gateway process when background reconnect is possible. |
| `patch_mcp_proxy_env.py` | Stdio MCP subprocesses need proxy variables in network-constrained containers. |
| Camofox display/noVNC/Playwright pinning in `infra/camofox/` | Operational browser container fixes, not specific to Spartan Gate policy. |

## Review Rule

Before changing a patch, verify both intended transformation and already-patched
input. Before deleting one, prove the replacement preserves the same boundary:
fail-closed egress, authenticated browser access, non-root runtime identity, or
the documented plugin behavior above. Before converting a patch to a plugin,
prove the plugin can be installed per data home or layer without weakening
startup fail-closed behavior.
