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
| `patch_stage2_runtime_owner.py` | Adds only Spartan Gate's numeric runtime owner, writable data-dir allowlist, and pre-reconciliation route migration. Upstream keeps ownership of the immutable install-tree and schema-migration logic. |
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
| `patch_clawroute_named_provider.py` | Migrates legacy Hermes route provider config to native `openai-api` while preserving the Spartan Route base URL. |
| `patch_route_env_placeholders.py` | Atomically resolves only `OPENAI_API_KEY` and legacy `CUSTOM_1_API_KEY` aliases in config, profile secret scopes, and credential-pool seeding. It preserves upstream `op://` precedence, rejects anchor drift before writing any file, and never expands arbitrary profile secrets. |
| `patch_clawroute_prompt_cache_key.py` | Forwards Hermes `session_id` as Spartan Route `prompt_cache_key` and preserves raw `reasoning_config.effort`, including `ultra`, before upstream profile normalization. Transport ownership remains necessary because plugin middleware cannot observe the original config. |
| `patch_camofox_tab_isolation.py` | Scopes Camofox registry state by durable Hermes `session_id` (with task fallback), adopts only the exact tab group, recreates stale 404/410 tabs only for explicit navigation, and makes other stale actions require a new navigation. Managed close deletes the tab without deleting its persistent profile context. This remains a build-time adapter because Hermes plugins and hooks do not expose these browser-tool decisions. |
| Camofox auth env mapping | Passes the protected-route `CAMOFOX_ACCESS_KEY` to Hermes under upstream's expected `CAMOFOX_API_KEY` client variable. |
| `proxy-bootstrap.py` | Routes selected web/search traffic through Spartan Gate controls and logs audited egress. |
| `browserless-cdp-broker.py` | Shares one persistent Browserless profile session across local CDP clients. |
| `browserless-cdp-url.js` and `cdp-patch.js` | Normalize Browserless CDP URLs for container networking and auth. |

## Archived Patches

| Patch | Decision |
| --- | --- |
| `archive/2026-06-30-patch_camofox_access_key.py` | Archived after upstream Hermes added Camofox `_auth_headers()` support. Spartan Gate now maps the Camofox protected-route access key into Hermes' upstream `CAMOFOX_API_KEY` client variable instead of patching Hermes browser calls. |
| `archive/2026-06-30-patch_gateway_retryable_startup.py` | Archived after upstream Hermes added degraded startup behavior for retryable platform outages. |

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
| `patch_prompt_load_callback.py` | Telegram prompt-load callback handler with the upstream chat/thread/user authorization gate applied before any synthetic event. Upstream now ships Telegram as a platform plugin, so this remains a candidate for extraction when an equivalent callback hook is available. |
| `patch_checkpoint_rollback_scan.py` | Rollback command handler with scoped checkpoint lookup. Keep conservative until upstream rollback APIs settle. |

## Upstream Bug Candidates

These should be proposed upstream or deleted when upstream fixes the behavior.

| Patch | Why it looks upstreamable |
| --- | --- |
| `patch_mcp_proxy_env.py` | Stdio MCP subprocesses need proxy variables in network-constrained containers. |
| Camofox display/noVNC/Playwright pinning in `infra/camofox/` | Operational browser container fixes, not specific to Spartan Gate policy. |
| Camofox runtime viewport route in `infra/camofox/patch-server.js` | Upstream should resize the page runtime viewport and report effective dimensions instead of returning `ok` for screenshot-only crops or no-ops. |

## Review Rule

Before changing a patch, verify both intended transformation and already-patched
input. Before deleting one, prove the replacement preserves the same boundary:
fail-closed egress, authenticated browser access, non-root runtime identity, or
the documented plugin behavior above. Before converting a patch to a plugin,
prove the plugin can be installed per data home or layer without weakening
startup fail-closed behavior.
