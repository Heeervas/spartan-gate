# Pi native Responses through Spartan Route

The versioned Pi Route provider selects Pi's built-in `openai-responses`
implementation for models that Route advertises with `api_modes: ["chat_completions",
"responses"]` and a supported `reasoning_realm`. Other discovered models retain
Chat Completions. The auto alias advertises Responses only when every possible
active routing choice uses the same supported realm; this prevents routing one
encrypted conversation between subscription and API-key realms.

This replaces the earlier local provider whose single provider-level
`openai-completions` setting forced every model through Chat Completions. It
does not replace Pi's native HTTP transport, SSE parsing, tool handling, session
storage, cancellation, or message conversion. The small wrapper delegates to
Pi's `openAIResponsesApi().streamSimple` and adds request-local binding and
replay provenance through its native callbacks.

## Signed reasoning

Before a native request, Pi asks authenticated `POST /api/pi/reasoning-binding`
for a binding containing `spartan_chain_id`, `spartan_expected_realm`, and
`spartan_binding_mac`. The request contains a SHA-256 hash of the canonical Pi
agent directory, the native request session UUID, and the desired realm.
Route derives the chain using a Pi-specific HMAC namespace, signs it with its
existing server secret, and binds the chain to the realm in its existing
reasoning binding store. The secret and provider OAuth credentials stay in Route.
The endpoint rejects malformed input, arbitrary caller-chosen chain IDs,
unavailable signing/storage, and attempts to change the chain's realm.

Pi stamps received reasoning items before native normalization. Pi's own
`thinkingSignature` and session JSONL retain those tags, including after resume.
Before the next request the wrapper verifies provenance and removes private
tags from the wire representation. Foreign or untagged opaque reasoning is
dropped; a reset also removes provider item IDs while retaining tool call/result
relationships and visible conversation text. A fork or another agent directory
gets a different chain. A same-session switch between realms is rejected;
start a new session or branch for that switch.

Binding failures abort the native request before its HTTP submission. This is
enforced inside the provider callback, because ordinary Pi extension hook
exceptions are best-effort and cannot enforce this security boundary. Request
callbacks hold their own binding, so concurrent requests do not share mutable
reasoning identity. Existing explicit cache keys are preserved; otherwise the
wrapper uses the native Pi session key.

## Installation

The source owner is `infra/pi/extensions/spartan-route-provider.ts` plus its
`spartan-route-provider/` helpers. Install these alongside the existing
`spartan-route-usage/gateway.py` helper in Pi's configured agent extension
directory. The gateway reuses the existing Route configuration and Caddy
authentication; no second credential store is needed. Keep only one active
Route provider entry: replace the old `spartan-route.ts` loader when installing
the versioned provider, rather than loading both registrations.

Retain the separate cache/usage extensions. The usage helper's `binding` action
uses the same HTTPS origin and bounded JSON transport as the existing usage
actions. Native Responses now also participates in Route's Pi usage correlation,
including each Codex retry and deferred stream cleanup. Telemetry failures
remain subordinate to inference; signing failures remain fail-closed.

Use a fresh Pi process after updating helpers. Pi 1.3.0 can retain imported
`.mjs` bytes across `/reload`, so a reload alone is not installation verification.
An older cached catalog without native capability metadata remains on Chat
Completions until live discovery refreshes it. Existing recorded Chat
Completions history may continue as visible text/tool history; unsigned opaque
reasoning is not imported into the new native chain.

## Verification and rollback

Use the owning commands in `.agents/validation.md`: Route and Pi regressions,
native installed-Pi loader/stream tests, and TOP quality measurement. The tests
exercise authenticated signing, realm/profile separation, native normalization,
tool continuations, save/resume, fork reset, binding failures, cancellation and
per-attempt usage. These tests use synthetic providers; real gateway acceptance
must additionally check the deployed image, discovered native API, a streamed
tool turn, continuation and resume.

Preserve installed provider/loader bytes before activation and retain the prior
Route image. Restore only those task-owned files and the prior image/config
to roll back. Provider authentication, account stores and existing session
records require no migration or deletion. A rollback restores the earlier
Chat Completions path; it does not rewrite native reasoning already recorded.
