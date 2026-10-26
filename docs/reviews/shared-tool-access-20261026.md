# Shared tool access — candidate evidence and activation gate

Date: 2026-10-26. Base: `0080763ef836c220bbe30d7d0f70f7017243db89`.
Status: **W5 source accepted / deployment pending; not deployed**.
The feature branch contains no authorization to merge, install, build production
images, recreate services, inspect private stores or push.

## Contract and reviewed decisions

[Shared tool contract](../shared-tool-access.md) is the durable public feature
owner; executable commands remain in the existing validation authority and
[development mirror](../development.md). No MCP migration or general gateway.

Independent adversarial plan review approved the bounded fixed-target transport,
per-request token policy, opaque workflow capabilities, exact owned-tab registry,
native sidecars and additive quality coverage. It identified and resolved:

1. Stock native search parsed unbounded responses and could follow public
   redirects: a fixed byte/deadline-bounded transport rejects redirects/encoding.
2. Attaching legacy Caddy to client ingress would expose unrelated listeners:
   use dedicated tool-only Caddy. Actual Docker testing required an edge-only
   normal publishing bridge; clients/adapter/backend ingress remain internal.
3. Reader's inactivity timeout did not establish total fetch/worker release:
   add one monotonic budget, owned-socket shutdown and four accounted residual
   DNS jobs. Independent candidate review reran all 31 Reader tests successfully.
4. Backend click fallback could replay after timeout: add opt-in `singleAttempt`
   plus advertised capability, preserving every existing default.

Native image evidence required header/byte/pixel checks before Pi normalization;
Pi's normalization alone is not a security bound. Numeric browser generation is
not a process epoch; exact registry IDs, missing-tab retirement and no rebind/
replay supplement it. Real short snapshots omit pagination fields; the adapter
now normalizes only the valid nontruncated case and rejects inconsistent shapes.
Successful structured search is verified at native `tool_execution_end.result`;
native history projects content rather than preserving `structuredContent`.

## Actual isolated joint journey

One candidate served a restricted Node HTTP container and **four fresh native
Pi 1.4 SDK processes**, using real extension loading, `session.prompt()`, tool
hooks, image normalization and saved native sessions:

| Phase | Result | Boundary |
|---|---|---|
| Missing/wrong client auth | PASS | Actual edge/adapter; fixture counters unchanged |
| Container search/Reader/browser | PASS | Search JSON; actual Reader HTTP; real browser create/snapshot/type/single click/scroll/PNG/list/close |
| Container bypass attempts | PASS | Backend/intermediary/publishing-peer HTTP and legacy listener attempts failed; client ingress-only membership |
| Revoked client auth | PASS | Next request denied; fixture counters unchanged |
| Native Pi first | PASS | Structured search, untrusted Reader, real browser actions and image in subsequent synthetic-model transcript |
| Native Pi resume | PASS | Fresh process opens actual saved session; same tab/workflow, no remint |
| Native Pi fork | PASS | New native ID/workflow, empty own list, parent tab denied, own cleanup |
| Native Pi cleanup | PASS | Parent-owned tab/workflow cleanup; no storage/profile reset |

Runtime: Caddy 2.11.4; Python 3.12.15/aiohttp 3.14.4; Camofox 1.15.0;
container Node 22.23.2, host test Node 22.23.1; native Pi 1.4.0 and public
pi-web-access 0.44.0. Actual port publication was loopback only. Native input
hashes before/after matched. Owned containers and four fixture networks were
removed; cleanup receipt reports no remaining owned resources.

Critical tested source hashes:

| Source | SHA-256 |
|---|---|
| Browser policy | `838fd605056cf7ce9c45cd7f39921c85a9f12d1c53170b72980531f6a2792e4e` |
| Reader | `ccca39f355f5bd8560b5f4700ddd42161dc5b06d9d3d4ff531fb46261bf7fc3a` |
| Camofox patch | `24badf8e548ab108b2bbcf03662a1a79762ac4b4255f1c889dd07615ba5297fe` |
| Native client | `e51777abe75c3a819de07ea77e7de1d2a70110c379e5dea09b3f08a4fb59353c` |
| Native acceptance helper | `e619de943ff4a8799b90bde7a371ea851c1c704a8a7e2adbae57014494b02b69` |
| HTTP boundary at W4 | `cadf13e653b08b81e4839bf31c6b60924838177002616ae08cc65686e2f3930b` |
| HTTP boundary after ingress hardening | `d7f749be9356b45951d59a0ebd84c2de32b7f71b0deeabc241a6ee901a8ef326` |

The post-journey harness parameterization requires explicit program-image/source
inputs instead of coordinator-local defaults. Source-only/missing/wrong-input
preflights pass. Separately, final source inspection found inbound aiohttp parser
inflation occurred before handler byte accounting. A compressed-empty GET was
incorrectly accepted (RED 200 versus 400). The HTTP boundary now disables parser
auto-decompression and rejects every Content-Encoding header. A committed real
HTTP regression checks small and expanding gzip plus identity; decoder call
instrumentation proves no inflation and backend counters stay zero. All 28 tool
HTTP/policy/browser tests and four installer tests pass afterward. Browser,
transport, Reader, native Pi and image/helper bytes remain the tested versions.

Integrated review must adjudicate reuse of the unchanged normal-request browser
journey plus renewed affected HTTP proof. Neither this ingress change nor the
CLI preflight is represented as a second browser run or new live evidence.

### Required limitations

- Browser used existing **test-only analytics/addons-disabled mode**. This is
  actual browser parity, **not default-uBlock runtime admission proof**.
- Positive Reader HTTP used a declared test-only globally classified hostname/
  numeric-connection mapping to the owned fixture. Its real validation/redirect/
  checked-connect/HTTP paths ran; this is **not real public-network retrieval**.
- Native Pi used supported SDK processes and a **synthetic model transport**,
  not paid inference or terminal/TUI/active-installation acceptance.
- Normal publishing bridge gives edge ordinary reachability. Do not call the
  edge itself network-confined or the trusted host user OS-sandboxed.
- PNGs enter native saved history/provider context; absence of another artifact
  file does not erase those copies. No real accounts or profiles were used.

## Validation

Existing command authority was retained. Unique results, not summed alias reruns:

| Gate | Result |
|---|---|
| Scoped tool HTTP/policy/browser + installer | 28 Python + 4 Node PASS |
| Native Pi 1.3 and actual 1.4 | Full group each 5 Python + 60 Node PASS; after installer repair, affected native file 6 + installer 4 PASS each, zero skips/cancellations |
| Reader | 31 PASS; real drips, redirect budget, bounded DNS and worker recovery |
| Native Caddy | 8 PASS, including private-query outage logging regression |
| Camofox | 28 Python + 76 native Node PASS, zero native skips/cancellations |
| Hermes mandatory native source | 86 PASS, zero skips |
| Hermes portable/full group | 329 run; 7 explicit skips: six unchanged optional Docker bind cases and one container-root authority case; not counted as native proof |
| Route sealed group | 510 Python + 47 Node PASS, zero skips/cancellations |
| Operator/public Compose matrix | 495 PASS |
| Compose/source syntax/whitespace | Public default/example/opt-in renders and affected syntax/diff checks |
| TOP | `QUALITY_PASS_WITH_EXCEPTION`, zero evidence gaps, seven unchanged inherited exceptions; no new unexcepted strong finding; 35 JS files / 986 measurements |

Source-dependent skips are not accepted native evidence. The untouched optional
Hermes physical cases above were not used to satisfy any new native boundary.
Four genuine new JS complexity findings were repaired by responsibility, not
exceptions. A late installer coverage omission first produced
`QUALITY_INCOMPLETE`; independently approved exact additive coverage repaired the
applicability mismatch. This is **not retroactive preflight PASS**. Manifest/CI
quality coverage is inventory/provenance only, not semantic validation.

## Independent integrated source review

The initial frozen 50-path manifest `e487575a…` was independently reviewed in
full. All 50 checksums matched before/after. One F2/F9 blocker was reproduced:
the installer preserved legacy `searchRouting.providers=[tavily]` while allowing
only SearXNG; APPLY exited zero but the actual pi-web-access 0.44 loader rejected
it and loaded zero extensions. This was an installation failure, not a bypass.

The minimal repair removes installer-owned prior routing without loosening the
provider allowlist. The committed regression starts with that supported legacy
routing, applies the actual installer, loads **the installed copied extension**
and actual web provider, and performs structured search through native
`session.prompt()`. It verifies unrelated fields survive. RED reproduced the
native disabled-provider error; renewed Pi 1.3 and actual 1.4 each pass six native
checks plus four installer checks. Independent focused rereview accepted the
entire R2 candidate (`80e135bb…`) with unchanged residual operational limits.

Implementation commit: `44d21d463f8616f453ad2cfe8904fb36948f998b`.
Independent native committed-range review approved
`0080763ef836c220bbe30d7d0f70f7017243db89..44d21d463f8616f453ad2cfe8904fb36948f998b`
with no findings: exact 50-path set and committed blobs match accepted R2; normal
hooks introduced no drift. The reviewer verified postcommit TOP artifacts and
accepted modular evidence reuse without running or claiming new suites/runtime
checks. This acceptance receipt changes no implementation or deployment contract.

The reviewer explicitly accepted modular reuse of W4 alongside the new real-HTTP
parser proof and CLI-only portability preflight: unchanged browser/native-image
paths need no ceremonial heavy rerun. No other blocking finding was reported in
API, ownership, transport, Reader, click patch, native client, CI or Compose.
Existing Reader/Route-isolation reviews remain byte-valid. Original effects and
all operational limitations below remain unresolved rather than hidden.

## Unintended inherited validation effects — do not omit

1. **Native Caddy default XDG:** an early native fixture logged an autosave write
   under the host's default Caddy configuration directory and storage maintenance
   activity. The config was synthetic, but pre-existing overwrite/read effects
   are unknown without a preimage. No host cleanup/readback was performed.
2. **Unsealed Route run:** the original passing suite opened public TCP443
   sockets. Source startup could select ambient Codex auth and fetch the model
   catalog. TLS/HTTP completion, credential source/use and private-file effects
   remain **unknown**; no credential-store/env-value investigation is authorized.
   “No inference requested” does not mean “no external contact.”

Root was notified immediately. Tests now bind scratch HOME/XDG and Route auth/
DNS/TCP before discovery; independent focused review and a sealed full run pass.
The original unsafe run is not accepted fixture evidence. These corrections do
not erase historical effects or prove the installation was untouched. Exact raw
receipts/observed paths remain in ignored operator evidence; public files do not
publish personal paths or credential material. No rotation, private-store scan,
global network changes or deletion follows by inference. Any cleanup/assessment
of the exact observed host targets needs a separate owner decision.

## F1–F9 disposition

All operational portions remain **INCOMPLETE until separately approved W6**.

| ID | Source/isolated evidence | Operational boundary still pending |
|---|---|---|
| F1 | Source regressions PASS; Hermes/Route production source and connections not changed by the feature | Live preservation/readback and historical validation-effect uncertainty remain unresolved |
| F2 | PASS actual native Pi SDK search/Reader/browser/image plus fresh-process resume/fork | Approved active Pi/CLI installation and live services |
| F3 | PASS restricted HTTP container, distinct credential, same isolated services | Approved selected-installation client ingress |
| F4 | PASS fail-closed policy, scoped principals, zero-action auth/revocation tests | Real scoped credential provisioning/revocation canary |
| F5 | PASS actual resume/fork/owned lifecycle plus fault fixtures for all cross-workflow operations, capacity and stale/unknown outcomes | Live scratch identities; no existing-account adoption |
| F6 | PASS strict managed paths, loopback publication and negative ingress tests; defaults preserved in native source | Default-UBO/live edge and neighbor acceptance not exercised here |
| F7 | PASS real deadline/resource tests and bounded transports; declared public-address fixture seam | Live safe-public target and selected runtime limits/readback |
| F8 | PASS explicit allowlist, single application-level click, no admin/evaluate/storage reset | Human/downstream approval remains necessary for high-impact actions; no generic click classifier guarantee |
| F9 | PASS source procedures/rollback/evidence, independent plan/source/evidence review and exact committed-byte readback | Canonical integration, installed-byte/config readback and approved rollback canary |

## One W6 proposal — approval required, not applied

Designate one integration/activation owner. The feature remains based on
`0080763`; read-only reconciliation found canonical advanced through `5d1262d`
to `f1d275e` for Pi-container delegation/Builder enrollment. Those commits do not
cover this feature. Their only shared changed surface is `.agents/validation.md`;
reconcile it with the Pi-jobs owner before integration and recheck any newer
README/CI drift. No canonical merge/rebase or runtime adoption is included here.

1. Integrate only the accepted feature commit(s), preserving unrelated work.
   Preflight disk/memory, loopback port 18792, effective Compose profiles/mounts,
   confidential backups and previous Reader/Camofox image references. Consider
   the disclosed validation effects separately; do not silently clean/rotate.
2. Provision two independent scoped tokens/digests and new scratch browser users
   through `private/tool-access/clients.json` and separate owner-only token files.
   Mount only the scoped policy directory. Never copy full env/account state or
   provide backend/master keys to either client. Keep existing profiles enabled
   and add `tool-access`; no tailnet/LAN/public exposure.
3. Build only new tool-access, changed Reader and changed Camofox images,
   sequentially. Retain prior images. During an explicitly agreed browser-work
   pause, recreate only Reader/Camofox and start tool-access/tool-edge using main
   stack `sg` helpers. No Hermes/Route rebuild, recreation or global agent restart.
4. Run the redacted Pi installer preview for the explicitly selected agent
   directory, inspect its scoped plan/backups, then apply only approved files.
   Restart only that selected Pi process. The duplicated scoped bearer in
   web-search config/backups remains confidential. Do not change model settings.
5. Verify effective image/source/config bytes and loopback-only binding. Search
   uses the non-sensitive query `site:example.com example domain`; Reader uses
   `https://example.com/`. For safe interactive browser acceptance, temporarily
   run the tracked static fixture on an owned internal alias
   `tool-canary.test:8080`, with **no published port or persistent data**. Approve
   only the narrow temporary `tool-canary.test` private proxy allowlist entry
   needed to reach it; do not alter the generic public whitelist or disable
   proxy/DNS enforcement. Use `http://tool-canary.test:8080/page`, new scratch
   browser identities, `#fixture-input` with synthetic text, and the DOM-only
   `#fixture-button` (no submission). Verify snapshot, one click, scroll, PNG,
   list/close, denial/revocation and owned cleanup from host Pi and the restricted
   container. Remove the temporary fixture/private allowlist entry afterward.
   No real login/payment/message, existing-account sharing or paid model call.
   Verify existing Hermes Default/Work and Route health/workflows and scoped
   rollback without deleting persistent data. Operational F1–F9 pass only after
   those receipts and user acceptance.

Rollback is scoped: revoke new digests, stop only new access services, remove the
selected client files or restore exact scoped backups, and use retained prior
Reader/Camofox images if necessary. Never reset browser profiles, remove volumes,
use main-stack `--remove-orphans`, or roll back unrelated changes.
