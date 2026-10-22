# Hermes Patch Taxonomy

Spartan Gate currently carries Hermes patches for four different reasons:
container safety, Spartan Gate integration, plugin-like behavior, and likely
upstream bugs. Keep this classification current when adding or removing a
patch.

The 2026-10-01 refresh targets upstream main
`e8c97320ac8691d4de92af49f98459f9ef9ddb08`, OCI digest
`sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde`.
The Dockerfile invokes 23 common build-time patches and one additional patch
for the Camofox-only variant (24 in that image), including the
post-refresh Camofox policy and Spartan Route fast-mode adaptations. Python 3.14 uses
`/opt/data/hermes-extra-site-py314`; the Python 3.13 directory is retained but
excluded from the new runtime import path. Synced plugin versions are retained.
The build-time count follows the Dockerfile's patch invocation list.
`patch_clawroute_named_provider.py` remains a boot-time migration; the retained
`patch_chrome_devtools_ws_auth.py` is not invoked by this image build.
The deployed image passed runtime health, scoped API authentication, and
configured Telegram/Discord connection checks after the authorized private
credential migration. Superseded build images were removed after acceptance.

## Container Safety

These patches are part of the Docker security boundary. Keep them in the image
unless upstream provides the same invariant.

| Patch | Role |
| --- | --- |
| `patch_stage2_runtime_owner.py` | Adds Spartan Gate's immutable numeric runtime identity, closed ownership/mount preflight, writable data-dir allowlist, and pre-reconciliation route migration. It reuses upstream's symlink refusal and warm-boot ownership scans, including `logs/gateways`; upstream keeps immutable-install-tree and schema-migration ownership. |
| `patch_runtime_identity_drop.py` | Drops runtime workloads to configured numeric UID/GID. |
| `patch_s6_service_permissions.py` | Keeps dynamic s6 service definitions root-owned and disables unsafe runtime service registration. |
| `patch_stage2_cdp_env.py` | Materializes browser/CDP env before supervised services start. |
| `patch_gateway_service_cdp_env.py` | Imports browser/CDP env into supervised gateways and applies the configured numeric identity to gateway/log execution. It preserves upstream's restart-safe log setup and never adds root-context ownership rewrites. |
| `patch_codex_429_fail_fast.py` | Treats only HTTP 429 with top-level `error.code=codex_429` as terminal Codex capacity exhaustion before credential rotation, fallback, or backoff. It still uses normal turn finalization and persistence. |
| `patch_workstation_approval_scope.py` | Keeps workstation approval requests bound to the intended caller and scope. |
| `patch_stage2_image_dependencies.py` | Admits explicitly selected baked-image dependencies without provisioning. Startup revalidates the executing baked interpreter and all enabled plugin requirements before omitting only PM's generation fingerprint verdict; rejection exits nonzero through both native entrypoints. PM tools and raw `pm.check()` retain their native checks. |

The Hermes image uses root-owned `COPY` metadata and then runs
`audit-install-tree.py` across `/opt/hermes`. The audit rejects non-root
ownership, group/world writes, set-ID modes, ACLs, file capabilities, broken
links, writable-state link targets, external links outside root-controlled
system-library roots, and unexpected mount boundaries. This preserves the
immutable install tree without a final recursive `chown` layer.

## Spartan Gate Adapters

These are not generic Hermes behavior. They connect Hermes to Spartan Gate
services and should stay owned by this repo unless Hermes gains matching
extension points.

| Patch or adapter | Role |
| --- | --- |
| `patch_clawroute_named_provider.py` (boot-time migration) | Migrates legacy Hermes route provider config to native `openai-api` while preserving the Spartan Route base URL. |
| `patch_route_env_placeholders.py` | Atomically resolves only `OPENAI_API_KEY` and legacy `CUSTOM_1_API_KEY` aliases in config, profile secret scopes, and credential-pool seeding. It preserves upstream `op://` precedence, rejects anchor drift before writing any file, and never expands arbitrary profile secrets. |
| `patch_clawroute_prompt_cache_key.py` | Uses the shared Chat Completions finalization seam to forward one cache key (native key preferred, session fallback, bounded to 64 characters) and preserve raw `reasoning_config.effort`, including `ultra`. Native Responses owns its top-level cache key and nested reasoning fields. |
| `patch_spartan_fast_mode.py` | Allows the shared upstream fast-mode resolver only for eligible explicit `codex/*` and `openai/*` models at the exact internal Spartan Route URL with `openai-api`. Keeps session/static/timed modes and other route restrictions upstream-owned. A request middleware cannot repair this earlier eligibility decision, so it remains a narrow build-time patch with a guarded overlay for existing images. |
| `patch_camofox_tab_isolation.py` | Scopes Camofox registry state by durable Hermes `session_id` (with task fallback), adopts only the exact tab group, recreates stale 404/410 tabs only for explicit navigation, and makes other stale actions require a new navigation. Managed close deletes the tab without deleting its persistent profile context. This remains a build-time adapter because Hermes plugins and hooks do not expose these browser-tool decisions. |
| `patch_camofox_managed_policy.py` | Enforces the administrator's Camofox mode before driver, cloud-provider, stored-selection and CDP fallbacks. Five selectors call the strict shared runtime policy; unrelated modalities and unmanaged browser modes retain upstream behavior. The cont-init hook supplies endpoint/auth through native managed secret scopes, without changing `get_secret` or profile identities. Current images can use fingerprinted read-only private mounts; the next build incorporates the same deterministic patch. |
| `spartan-cache-lineage` 0.4.0 plugin | HMAC-binds Responses reasoning to profile/session/realm and fingerprints Chat `messages` or Responses `instructions` plus `input`, with canonical tool hashing and transport-separated bounded state. Native background reviews inherit a compatible foreground cache lineage without overwriting foreground tracking, using the existing Hermes origin API. It stores only hashes and treats invalid or foreign reasoning as a cold chain. |
| Camofox auth env mapping | Passes the protected-route `CAMOFOX_ACCESS_KEY` to Hermes under upstream's expected `CAMOFOX_API_KEY` client variable. |
| `patch_camofox_vault.py` and `spartan_camofox_vault.py` | Adapt native vault page operations to the existing durable Camofox tab, preserve exact-origin checks and native prompts, bind OTP handles to allowed origins, and redact textual Camofox output. Secret expressions use the authenticated capability-versioned route; no CDP fallback. See [vault integration](camofox-vault.md). |
| Camofox `patch-vault-evaluate.js` | Adds the authenticated secret transport with bounded parsing before generic reporters, scope and tracing checks, sanitized count-only results, and lifecycle-coordinated timeout cleanup. |
| `proxy-bootstrap.py` | Routes selected web/search traffic through Spartan Gate controls and logs audited egress. |
| `freeze_image_features.py` | Inventories the image's actually installed native extras and writes Hermes's feature metadata after dependency installation. This preserves shipped optional features when PM creates its first writable generation; it does not declare arbitrary plugin dependencies. |
| `patch_gateway_service_cdp_env.py` | Compatibility filename: propagates Camofox settings and preserves numeric workload drops/root-owned supervision. No CDP broker fallback. |
| `patch_chrome_devtools_ws_auth.py` | Compatibility filename: removes only managed retired CDP settings at boot; preserves custom configuration and never reads browser profiles. |

## Archived Patches

Browserless broker/URL/debugger adapters were removed with the retired source.
Google Meet retains Camofox caption transcription only; realtime/CDP workflows
are unsupported. See [the migration contract](layers.md).

| Patch | Decision |
| --- | --- |
| `archive/2026-10-01-patch_startup_overlay_precedence.py` | Alberto Tools now owns its three Discord command helper modules inside the plugin. Native plugin loading replaces core-module shadows; no startup path-precedence patch is needed. Old overlay files remain outside the Python 3.14 import path. |
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
| `patch_google_meet_cdp.py` | Compatibility filename for Camofox Meet transcription and retired-CDP denial. |
| `patch_google_meet_transcript.py` | Google Meet transcript and muted-join behavior. |
| `patch_prompt_load_callback.py` | Telegram prompt-load callback handler with the upstream chat/thread/user authorization gate applied before any synthetic event. Upstream now ships Telegram as a platform plugin, so this remains a candidate for extraction when an equivalent callback hook is available. |
| `patch_checkpoint_rollback_scan.py` | Adds only conservative workdir selection to native rollback handlers. Native listing, restore, backend restrictions and chat undo remain upstream-owned. Ambiguous scopes require explicit selection. |

## Upstream Bug Candidates

These should be proposed upstream or deleted when upstream fixes the behavior.

| Patch | Why it looks upstreamable |
| --- | --- |
| `patch_mcp_proxy_env.py` | Stdio MCP subprocesses need proxy variables in network-constrained containers, and the current MCP SDK requires legacy YAML boolean/number environment scalars to be normalized to strings. |
| `patch_state_fts_empty_migration.py` | Upstream's empty v1/v2 FTS migration calls `executescript` inside a savepoint, implicitly committing it. Use the existing transactional DDL helper only for this empty-index branch; retain populated migration and rollback behavior. Remove when upstream fixes the branch. |
| `patch_async_completion_scope.py` | Requires a unique served owner for raw API delegation sessions before readiness checks, claims, durable delivery persistence, and settlement. Cached origins cannot disambiguate client-chosen IDs or turn a raw completion into a messaging wake. Missing or ambiguous ownership fails closed. |
| `patch_lsp_sdk_repair.py` | Validates the JavaScript TypeScript SDK alongside existing LSP executables, repairs only the supported companion SDK, and keeps install caches scoped to the profile. Manual/off policies remain probe-only; workspace trust remains unchanged. |
| `patch_cron_thread_diagnostic.py` | Warns about dropped thread metadata only for origin-resolved delivery, respecting native stale-thread fallback and Discord thread-as-channel addressing. Explicit job destinations and all delivery behavior remain unchanged. |
| Camofox launch-profile cleanup, immediate timeout-tab retirement, and bounded waiter retention in `infra/camofox/patch-server.js` | v1.15 owns fixed-proxy session preservation. Spartan retains immediate retirement of the timed-out tab, de-duplicated failure accounting, and bounded waiters. Periodic profile scans remain disabled because upstream's established-browser protection does not cover unfinished launches. The noVNC override was removed in v1.14. See [Camofox refresh](camofox-refresh.md). |
| Camofox runtime viewport route in `infra/camofox/patch-server.js` | Upstream should resize the page runtime viewport and report effective dimensions instead of returning `ok` for screenshot-only crops or no-ops. |

## Review Rule

The 2026-10-08 comparison against upstream `99a45ecc17cc51c0488ec3b8ccf7eaf483f7203d`
did not establish an equivalent replacement for the remaining security patches.
Track MCP [#107851](https://github.com/NousResearch/hermes-agent/pull/107851),
empty FTS [#132432](https://github.com/NousResearch/hermes-agent/pull/132432),
Camofox adoption [#120190](https://github.com/NousResearch/hermes-agent/pull/120190),
and cron diagnostics [#89702](https://github.com/NousResearch/hermes-agent/pull/89702).
An open proposal, or a closed unmerged PR, is not an integrated replacement.
Camofox adoption covers only part of our isolation adapter. Cache middleware
overlaps part of the cache adapter but does not recover raw `ultra` effort;
static provider priority does not replace dynamic `/fast`.
The route-placeholder config transformation needs adaptation to that upstream
snapshot's `op://` lookup before a future refresh; the deployed revision is
unchanged by this review.

Before changing a patch, verify both intended transformation and already-patched
input. Before deleting one, prove the replacement preserves the same boundary:
fail-closed egress, authenticated browser access, non-root runtime identity, or
the documented plugin behavior above. Before converting a patch to a plugin,
prove the plugin can be installed per data home or layer without weakening
startup fail-closed behavior.
