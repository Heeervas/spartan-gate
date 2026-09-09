# Subscription-only Realtime: gated implementation and probe

## Current status

The first implementation batch supplies an offline-by-default feasibility probe
and its tests. **Route does not yet expose Realtime mint or WebSocket routes.**
Subscription entitlement, voice round-trip and live tools remain unverified.
The full transport implementation is gated on explicit account-use authorization
and a successful real probe. No API-key fallback is permitted.

The independently reviewed plan preserves the original objective: Hermes owns
Discord, playback, memory, permissions, tool execution and delegation; Route
owns provider transport and centralized OAuth custody. No Hermes client edits,
Discord access, production restart or deployment belong to this batch.

Pinned client source: [hermes-talk at d23a8b2](https://github.com/TheSmokeDev/hermes-talk/tree/d23a8b257c6d6dcccb0c8af49884862adac7d17a).
Its `main` matched the full revision at inspection. `talk_auth.py` supports
Codex OAuth reuse; that is implementation evidence, not account entitlement.
The pinned wire uses client-secret mint followed by a bearer-authenticated
WebSocket. It requests `gpt-realtime-2.1`, voice `cedar`, and input transcription
with `gpt-4o-mini-transcribe`. These are probe candidates, not advertised Route
capabilities.

Official references confirm the [model exists](https://developers.openai.com/api/docs/models/gpt-realtime-2.1)
and describe [WebSocket transport](https://developers.openai.com/api/docs/guides/realtime-websocket)
and [client-secret creation](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create).
They do not establish that an existing ChatGPT/Codex subscription grants this
account access, shares Codex limits, or covers this usage without additional
billing. Successful authentication alone would not resolve those questions.

## Prepare and authorize the probe

Read the no-network operation summary:

```sh
python3 scripts/spartan_route_realtime_probe.py
python3 -m unittest discover -s scripts -p 'test_spartan_route_realtime_probe.py'
```

Before live execution, obtain explicit authorization naming **one existing
zero-based slot reference**, one mint and one WebSocket session. State that the
probe sends 250 ms of synthetic PCM audio, requests up to three responses capped
at 128 output tokens each, returns one fixed inert function result, and may use
Realtime audio/transcription quota with unknown subscription attribution. OAuth
refresh is permitted only through the existing Route manager and lock domain.
A CLI flag records that approval; it is not itself user authorization.

The operation budget is 60 seconds, with cancellation/connection cleanup allowed
to finish afterward. This is not a strict process wall-clock cap or a measured
production timeout. No automatic retry, alternative account, paid API key,
provider or model fallback occurs. Slot cooldowns and disabled routing are
respected. A blocked slot requires a later authorized attempt, not pool rotation.

After approval, set only the non-secret reference and run through the main-stack
alias from the repository root:

```sh
source scripts/aliases.sh
# Set APPROVED_REALTIME_SLOT to the exact slot explicitly approved for this run.
: "${APPROVED_REALTIME_SLOT:?Set the approved non-secret slot reference}"
sg exec -T spartan-route python3 - \
  --live --slot "$APPROVED_REALTIME_SLOT" --approved-account-use \
  < scripts/spartan_route_realtime_probe.py
```

This streams **code only** into a short-lived process in the existing container;
it does not replace the service or copy credentials. It imports the service's
own `/app/spartan_route/codex.py`, uses unchanged configured bootstrap paths and
`/app/data/codex-auth`, and runs with the container's configured ownership.
Source-path hashing and managed-store locks must remain identical to production.
Do not translate source paths to host paths or inject a temporary auth store.
The probe refuses a host invocation or static `OPENAI_CODEX_TOKEN` mode.

A passive authenticated loopback balancer read supplies current slot policy,
cooldown and identity information without refreshing usage. Only the selected
slot is passed to `load_auth()`. The probe does not initialize the telemetry
store, run database maintenance, call `choose_slot()`, or refresh the pool.
`OPENAI_API_KEY` and all other provider keys are ignored. Provider hosts are
fixed, HTTP redirects are disabled, and WebSocket redirects are rejected before
following them. Existing HTTP proxy configuration remains in force.

## Evidence and failure interpretation

A live report records independent checks in order:

1. Existing-manager OAuth credential usable.
2. Upstream mint accepted with a valid expiring secret kept in memory.
3. WebSocket opened.
4. Session created; requested model, voice, PCM format, audio output modality,
   transcription model, noise reduction, token cap and VAD configuration acknowledged.
5. VAD disabled for deterministic manual commit; synthetic audio committed;
   correlated, decodable PCM output and completed response received.
6. Exactly one inert `route_probe_ping` call with empty arguments received;
   its fixed `{"ok":true}` result returned without tool dispatch.
7. Continuation produces correlated PCM audio and response completion.
8. Socket closes normally and resources are released.

A 200, a 101, or a text-only response does not satisfy the later checks. The
probe does not measure speech comprehension, playback latency, Discord behavior
or interruption recovery. Those remain final staging/client acceptance items.

Reports contain fixed error categories, numeric HTTP statuses where available,
phase, fixed endpoint, source-code hash, aggregate byte counts and timings. Raw
provider errors, headers, credentials, transcripts, tool arguments, account IDs,
paths and audio are omitted. First-audio time starts at socket exercise; it is
transport arrival time, not perceived playback latency. Mint and handshake are
measured separately. Quota and cost attribution always remain `unknown` here.

Known provider codes distinguish authentication, model, quota and rate limiting.
A generic 403 is `upstream_forbidden_unclassified`, not proof of entitlement
denial. A 429 without a specific code remains unclassified. Record the failing
stage and stop; do not implement the large gateway while real access is blocked.

Probe ceilings are deliberately conservative local safety bounds: 64 KiB HTTP
JSON, 1 MiB per WebSocket message, 4 MiB received in total, and 1,024 events.
The 250 ms PCM input is 12,000 bytes before base64. These limits bound a tiny
three-response experiment; they are not provider limits or production defaults.
Limit failures report the bound, configured/observed values and recovery action.

## Reviewed implementation sequence after a successful probe

Use local verified commit batches and keep the worktree clean at batch boundaries.
Do not push or activate production as part of implementation.

1. **Contract and authorization.** Add `POST /v1/realtime/client_secrets` with
   `{"session": ...}` and `{"value": "<route-ticket>", "expires_at": <unix-seconds>}`.
   Add `GET /v1/realtime?model=<verified-id>` for WebSocket upgrade with bearer
   ticket. Dispatch independent voice authentication before the legacy middleware's
   missing-token/public-discovery bypass. Global text tokens do not grant voice.
   Voice credentials map to client identity, permission and an authorized slot;
   provisioning credentials remains a separate operator action.
2. **Account reservations and tickets.** Reuse the existing OAuth manager; add
   bounded Realtime reservations separate from textual cache leases. Pin the
   actual account identity after auth loading; never fail over. Reserve before
   upstream mint and open upstream on ticket redemption, before downstream
   upgrade. Keep upstream secrets private. Store Route ticket digests, consume
   atomically once, use monotonic expiry no later than upstream expiry, and
   release exactly once on expiry, failure, disconnect or shutdown. Restart
   invalidates tickets. Bearer possession permits redemption; stored client
   identity does not prove the presenter owns a stolen ticket.
3. **Duplex transport.** Preserve event order, IDs, audio deltas, tool calls/results,
   configuration and completion. Support append/commit/clear, VAD, response
   create/cancel, item create/delete/truncate and continuation. Enforce subscription,
   model and function-only tool policy at mint and later session updates.
   Hermes owns playback cancellation; Route does not invent actions or replay
   audio/results. Bound reads/writes, own and await forwarding tasks, and close
   sessions before shared HTTP/OAuth resources. Freeze production limits only
   after documented measurements and focused regressions.
4. **Discovery and diagnostics.** Add public protocol/model capabilities through
   existing discovery, but keep timestamped account-access observations behind
   voice authentication. Never label historical success as permanent entitlement.
   Redact HTTP/WS errors and emit content-free lifecycle telemetry. Preserve
   verified upstream usage without assigning invented subscription prices.
5. **Acceptance and handoff.** Run full Route regressions plus affected Compose
   and operator checks. With applicable explicit authorization, run isolated
   staging through implemented Route: mint, session, synthetic audio, inert tool,
   continuation, cancellation during output, next-turn recovery and closure.
   Test local invalid auth without provider calls. Deliver exact verified
   model/voice, endpoint/auth/ticket contract, harness, limits and redacted evidence
   to the Hermes implementer. Its Route mode must bypass local OAuth resolution
   and make mint/WebSocket endpoints configurable.

Architecture A (mint plus ticket plus native aiohttp WS) preserves the pinned
client's wire contract. Direct Route auth on WS adds no demonstrated advantage;
direct provider voice bypasses custody, and STT/LLM/TTS changes the objective.
WebRTC, dashboards, other providers and provider-executed tools remain outside
this implementation.

Supported future internal endpoints use `http://spartan-route:18790` and
`ws://spartan-route:18790` on the trusted Compose network. The current Caddy
listener overwrites Authorization and is **not a voice ingress**. External
exposure requires separately approved TLS configuration preserving voice auth.

Required regressions cover absent/invalid/scoped auth, expired/reused/mismatched
tickets, atomic redemption races, zero API-key fallback, concurrent OAuth
refresh, account/session isolation, denied models, classified provider failures,
ordered audio/tools/cancellation, slow consumers, malformed/oversized frames,
partial disconnects, shutdown cleanup, and existing text/Responses/image routes.

## Preparation validation

The preparation batch passes 15 focused offline tests for default/approval
behavior, slot filtering and account identity, PCM and tool evidence, malformed
expirations, session acknowledgements, redaction, receive limits and timeout.
The pre-existing baseline passed 227 Route tests, 197 Hermes Python tests,
18 Hermes JavaScript tests, 24 Camofox Python tests, the 302-test scripts suite
(one skip), Camofox JavaScript tests (12 pass, one skip), and Compose rendering.
The pristine upstream Camofox fixture and live browser acceptance were not run.
No live Realtime test or OAuth refresh has been performed for this batch.

## Enablement and rollback

Nothing in the probe batch enables Realtime endpoints or changes production.
Stop a running probe with interruption; allow its connection cleanup to finish.
Do not undo a successfully persisted OAuth refresh: the existing manager owns
that state and the previous grant may already be invalid.

Before future enablement, require successful gated evidence, scoped credentials,
verified model configuration, measured limits and explicit activation approval.
Default the feature to disabled. Disable admission before draining active sessions;
all remaining sessions/tickets must be closed or invalidated on service shutdown.

Revert only Realtime commits when reverting this work. The three preceding
checkpoint commits preserve pre-existing Hermes, Camofox and Route changes;
they are independent of voice and are not deployment approval. Never revert
credential-store state as part of source rollback. Production rollback/restarts
and real Discord acceptance remain separately authorized operator actions.
