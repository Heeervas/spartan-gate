# Standard Responses conversations and native Pi

Spartan Route supports a **stateless Responses subset**, not every OpenAI endpoint
or provider feature. An unmodified Responses client can start a conversation,
replay returned opaque reasoning and function-tool output, persist the transcript,
and resume in a fresh process using ordinary endpoint authentication. No Spartan
signing, binding endpoint, filesystem scope or session UUID is required.

## Standard client contract

Use `POST /v1/responses` with a supported explicit `codex/*` subscription or
`openai/*` API-key model. Auto aliases advertise Responses only while all active
choices share one supported realm. Use `store=false` (also the omission default),
`input` as a first-turn string or full-history item array, optional instructions,
supported reasoning effort, function tools and matching `function_call_output`.
Both collected JSON and SSE are supported. Request `reasoning.encrypted_content`
and retain the complete returned output; Route also requests it upstream when
include is omitted.

For example, a normal OpenAI SDK client can extend its history without extra body
fields or a preflight call:

```js
const response = await client.responses.create({ model, input, store: false });
input.push(...response.output);
// If output contains function_call, run the authorized tool and append:
input.push({ type: 'function_call_output', call_id, output: toolResult });
const next = await client.responses.create({ model, input, store: false });
```

Save the history as ordinary JSON and replay it after process restart. Treat
`encrypted_content` as an **opaque endpoint-owned string**: do not decode, edit or
import it from a different provider/gateway. Preserve tool `call_id` relationships.
A successful conversation requires terminal completion, not merely HTTP 200 or
partial streamed text. Both `responses.create({stream:true})` iteration and the
OpenAI SDK 7.19 `responses.stream({...}).finalResponse()` accumulator are covered
by local interoperability tests. Check the returned terminal status in either
case; `finalResponse()` alone does not guarantee successful completion.

At the Codex public-SSE boundary, Route excludes the exact proprietary
`response.metadata` event after raw telemetry observation. It is not a public
Responses event and the SDK accumulator rejects it. Output/tool events, terminal
usage and failures retain their existing semantics; unknown events are not
blanket-filtered. Collected JSON, API-key passthrough and legacy replay are
unchanged.

SDK convenience accumulation also adds output-only parsed values such as
`function_call.parsed_arguments`. For manually replayed convenience-helper
history, the SDK documents its own normalizer:

```js
import { toResponseInputItems } from 'openai/lib/responses/ResponseInputItems';
input.push(...toResponseInputItems(response.output));
```

This retains reasoning/tool items while removing SDK-only parsed fields; it is
not a Spartan signing adapter. The local synthetic upstream tolerates extra
input fields and its Route restart is within one OS process, not a new server
process. Live follow-up confirmed the metadata fix on an inert first/tool call,
but raw accumulated-output continuation received `unknown_parameter`; normalized
helper continuation remains unverified live. See the bounded acceptance record
in [the follow-up review](reviews/route-sdk-stream-20261024.md).

Codex subscription does not support `store=true`, `previous_response_id`, native
compaction/context management, reasoning modes or non-function tools in this
subset. Response retrieval/deletion, background jobs and client WebSockets are
not promised. Native image/provider extras retain existing behavior but are not
new interoperability claims. Codex discards `max_output_tokens`; it is **not an
enforceable output budget** on that backend.

## Server-owned Codex replay

For unsigned Codex requests, Route returns authenticated replay receipts in the
existing `encrypted_content` field. Standard clients simply store and echo them.
Route verifies the actual ciphertext, version, realm and issuing account scope,
then restores raw provider ciphertext before upstream dispatch. No conversation
registry, new schema, binding-store write or extra client request is needed.

The last receipt in input order supplies an **advisory issuer preference**, not
authorization or an account pin. Route validates every receipt, including earlier
items in mixed-issuer histories. After operational and model/effort eligibility,
it prefers matching issuers before ordinary lease/load ordering. A conflicting
valid lease is not invalidated merely for being outside that preferred subset.
If no preferred issuer remains eligible, the ordinary eligible pool can serve the
unchanged raw ciphertext; existing safe-retry gates still govern failover. Disable,
cooldown, auth, model/effort and retry exclusions always apply. Committed streams
and ambiguous sends are not replayed.

Known-account issuer identity survives OAuth refresh and slot moves. An unknown
account's scope is credential-specific, but replacing that credential only loses
the preference: it does not invalidate the authentic receipt. New output receipts
identify the actual selected attempt. Tampering and wrong realms still fail before
submission; raw unbound Codex ciphertext is not accepted as a fallback.

Cross-account completion has bounded synthetic live evidence, **not proof of
lossless hidden-reasoning reuse**. Foreign reasoning was attributed zero input
tokens in that probe. Latent continuity and cache reuse remain upstream-dependent
and uncertain; do not treat a completed response as proof of either. This source
policy change is pending separate review/activation, not a deployed-state claim.

The existing root secret must remain stable and recoverable across Route restarts.
Missing signing configuration denies even a first standard Codex request before
inference. Rotation invalidates old receipts; there is no previous-key ring.
Purpose separation prevents accidental cross-protocol use, **not** trust in an
exclusive server key: Hermes also holds the root secret. Receipt integrity assumes
uncompromised authorized secret holders. A secret holder can forge receipts.

A valid receipt plus gateway access is a replay bearer capability, not an
individual/profile/session identity. Shared gateway credentials do not isolate
hostile clients or Hermes. Existing OpenAI API-key Responses retains raw opaque
passthrough and provider-owned validation; Codex receipts are rejected on that
foreign realm before dispatch. API-key portability remains the upstream contract.

## Pi's local safety and legacy sessions

The versioned provider still delegates HTTP, SSE, conversion, tools, cancellation
and persistence to Pi's native `openai-responses` implementation. Dynamic discovery
selects that API only with advertised capability/realm metadata; other models
retain Chat Completions and supported effort mapping.

New Pi sessions make **no reasoning-binding RPC**. A native non-context custom
entry locks the local scope/session identity to one realm. Reasoning signatures
retain versioned local provenance; request-local callbacks strip private tags.
Fork/profile copies drop foreign opaque reasoning while preserving visible text
and tool relationships. Provider message/tool item IDs are removed on outgoing
full-history replay even when copied history contains **zero reasoning**. Reasoning
IDs and call/result IDs remain intact. A matching same-session realm change fails;
start a new session/fork instead. Local tags/locks protect cooperative mistakes,
not malicious edits of session files or requests.

Realm locks survive resume, compaction and branch navigation. Provider-stream
entry captures the active native extension context, its activation, the actual
SessionManager object and its native session ID before conversion/callbacks can
await. Reservation and commit both re-read the guarded context and require the
same activation, manager and native ID. Shutdown, replacement or reload cannot
revive an old pending request, even on a retained manager or a different manager
with the same textual ID. Requests reserve local concurrency ownership
provisionally, validate any saved legacy provenance, then commit the durable realm
lock before HTTP submission. Wrong-realm legacy rejection releases that reservation
without writing a new lock; the original realm remains resumable, including after
reopen. Native conversion may already have removed cross-model reasoning, so
retained session provenance is checked too.

Pi's stream `options.sessionId` is a separate **routing/cache ID**, not proof of
native persisted identity. Built-in branch summaries (including provider-neutral
navigation commands such as `/return`) and default manual/automatic compaction
intentionally use fresh routing IDs with cache retention disabled. Route leaves
those options unchanged and uses the captured native ID only for replay identity,
durable realm locks and legacy binding. Cache-key preparation still uses the
original routing ID; no summary detection, ID substitution or navigator dependency
is involved. Missing/invalid native identity or stale ownership still fails closed.

This integration supports Pi's native per-session model-runtime lifecycle,
validated on Pi 1.3 and 1.4. SDK hosts must keep this provider registration/runtime
session-owned: deliberately sharing one mutable `ModelRuntime` across independent
sessions can overwrite another session's provider closure and is not supported.
This adapter is not a universal SDK request-provenance or hostile-tenant boundary.

An append failure latches that identity unavailable on the retained SessionManager,
including replacement extension modules/fences after `/reload`: native Pi may
mutate memory before disk persistence. Retry or reload cannot trust that phantom
lock, even after disk repair. Repair storage and start a fresh process to recover
only persisted disk state. The manager-keyed process-local latch is not a new
persistent session or credential store.

Saved legacy signed Hermes/Pi histories remain supported without rewriting files.
When Pi encounters legacy-stamped raw reasoning, it lazily requests the old binding
once per scope/session/realm per process and caches success. Requests retaining
that history stay signed and receive raw legacy output until compaction or a
deliberate visible-history fork removes it. This is not automatic ciphertext
conversion: the original issuing account cannot be recovered from old tags.
Failure still aborts raw replay before HTTP, with a sanitized error category.

Legacy chain MACs authenticate chain/realm, **not ciphertext**; their old
cooperative-provenance guarantee is not retroactively strengthened. The old
server binding store and signed Hermes wire format remain unchanged. A copied
legacy history drops mismatching opaque items. Pre-upgrade history with no opaque
items has no server-proven old realm; its requested realm initializes the new
local lock. Normal telemetry remains subordinate to inference.

Pi's existing native-ID canonicalization is unchanged: UUIDs are preserved; valid
named IDs such as `subagent.<hash>` deterministically map to UUIDv8 for shared
identity and usage. Raw session names/files/cache keys remain unchanged, including
case. Invalid native IDs still fail before submission. Unsigned traffic uses Route's existing
cache-key/content dashboard fallback, not the signed-chain grouping label. Cache
keys do not guarantee cache residency or savings.

## Installation, evidence and rollback

Source is `infra/pi/extensions/spartan-route-provider.ts` and its helper directory,
plus the existing shared usage gateway/session helpers. Install a coherent helper
set beside the single configured provider entry; an installed entry may be named
`spartan-route.ts`. Do not load both registrations or overwrite unrelated settings.
Preserve existing authentication, usage/cache extensions and native disables.
Back up only task-owned bytes outside discovery before installation. **Restart
Pi fully** after helper changes; `/reload` alone can retain imported `.mjs` bytes.
No session deletion, bulk rewrite or credential migration is required.

The validation owner is `.agents/validation.md`; public setup is in
[Development](development.md). Tests use actual OpenAI SDK 7.19.0 and native Pi
1.3/1.4, real loopback Route/native selection and synthetic upstream HTTP. They
cover tool/encrypted continuation, fresh-client/Route resume, receipt integrity,
advisory issuer selection, mixed-issuer failover and legacy raw compatibility.
Native command navigation, default manual/automatic compaction and reload tests
also check summary/usage attachment, independent routing/cache behavior and
fail-closed pending-request ownership. Those are not live-provider proof.
Source review, isolated image acceptance and parent-authorized live acceptance
remain separate gates.

Retain the prior image and exact installed bytes. Old signed Pi works with the
new Route. **Old Route cannot consume new receipts**: a full old-server rollback
must switch affected clients to the retained Chat Completions/visible-history
configuration in a fresh process, or a reviewed new visible-only session/fork
excluding opaque reasoning/provider item IDs. Keep the original session intact.
Do not restore old native code and forward receipt strings into the old server.
Legacy signed raw Hermes/Pi histories remain compatible.
