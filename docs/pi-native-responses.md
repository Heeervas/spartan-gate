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
partial streamed text.

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

Replay stays on the issuing account, including after OAuth refresh or slot moves.
An unavailable/disabled account cannot be replaced with another account for opaque
replay; retry later or deliberately start with visible history only. When no stable
account identity is known, receipts are credential-specific and token replacement
invalidates them. Mixed-account histories, tampering and wrong realms fail before
submission. Raw unbound Codex ciphertext is not accepted as a fallback.

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

Realm locks survive resume, compaction and branch navigation. Requests reserve
local concurrency ownership provisionally, validate any saved legacy provenance,
then commit the durable realm lock before HTTP submission. Wrong-realm legacy
rejection releases that reservation without writing a new lock; the original
realm remains resumable, including after reopen. Native conversion may already
have removed cross-model reasoning, so retained session provenance is checked too.

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

Pi's existing native-ID adapter is unchanged: UUIDs are preserved; valid named
IDs such as `subagent.<hash>` deterministically map to UUIDv8 for shared identity
and usage. Raw session names/files/cache keys remain unchanged, including case.
Invalid IDs still fail before submission. Unsigned traffic uses Route's existing
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
account pinning and legacy raw compatibility. Those are not live-provider proof.
Source review, isolated image acceptance and parent-authorized live acceptance
remain separate gates.

Retain the prior image and exact installed bytes. Old signed Pi works with the
new Route. **Old Route cannot consume new receipts**: a full old-server rollback
must switch affected clients to the retained Chat Completions/visible-history
configuration in a fresh process, or a reviewed new visible-only session/fork
excluding opaque reasoning/provider item IDs. Keep the original session intact.
Do not restore old native code and forward receipt strings into the old server.
Legacy signed raw Hermes/Pi histories remain compatible.
