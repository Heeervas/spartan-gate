# Subscription-only Realtime for Hermes Talk

## Status and evidence

Spartan Route now implements native HTTP mint and WebSocket relay for Hermes
Talk. The feature is **disabled by default and has not been enabled in
production**. Hermes retains Discord, playback, memory, permissions, tool
execution and delegation. Route only transports provider events and owns OAuth.

A real authorized account passed the complete isolated Route acceptance sequence:
local invalid auth denied, mint, session configuration, synthetic PCM audio,
inert function output, audio continuation, cancellation during output, playback
truncation, a valid next audio turn, normal client close and zero remaining
reservations/tickets. No Discord channel or human recording was used.

Measured passing gateway run: 6,003 ms total, first audio 1,237 ms after socket
exercise began, cancellation terminal response in 165 ms, 189,600 decoded PCM
bytes and 359,471 received event bytes. Route separately measured mint at 387 ms
and upstream handshake at 670 ms. First-packet timings are transport arrival
measurements, not perceived playback latency. These are real upstream results,
not fixtures or performance percentiles.

This proves current OAuth access for the tested model, voice and protocol. It
**does not prove free usage, shared Codex quota, permanent entitlement or how the
subscription is billed**. Diagnostics report billing as `unknown`; usage counts
are included only when supplied by upstream. No API-key/provider/account fallback
exists in the Realtime path, even when API keys are present in the environment.

The pinned Hermes Talk revision is
[`d23a8b257c6d6dcccb0c8af49884862adac7d17a`](https://github.com/TheSmokeDev/hermes-talk/tree/d23a8b257c6d6dcccb0c8af49884862adac7d17a);
its main matched at inspection. Its OAuth-reuse implementation is not itself
entitlement evidence. Official references describe the
[model](https://developers.openai.com/api/docs/models/gpt-realtime-2.1),
[WebSocket protocol](https://developers.openai.com/api/docs/guides/realtime-websocket)
and [client secrets](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create).
The verified voice is `cedar`, using `gpt-realtime-2.1` and PCM16 mono at 24 kHz;
input transcription with `gpt-4o-mini-transcribe` was also acknowledged.

## HTTP and WebSocket contract

Internal trusted-network base: `http://spartan-route:18790`.

| Endpoint | Authentication | Behavior |
| --- | --- | --- |
| `POST /v1/realtime/client_secrets` | Dedicated voice-client Bearer credential | Accepts `{"session": ...}`; returns `{"value": "<route-ticket>", "expires_at": <Unix seconds>}` |
| `GET /v1/realtime?model=gpt-realtime-2.1` | `Authorization: Bearer <route-ticket>` plus WS upgrade | Opens a pinned upstream connection and relays duplex JSON events |
| `GET /api/realtime/capabilities` | Dedicated voice-client Bearer credential | Protocol, configured limits and this client's timestamped observed stage; no live probe |
| `GET /v1/models` and model detail | Existing discovery behavior | Additive public protocol/model capabilities when enabled; never account observations |

Responses containing tickets or observations use `Cache-Control: no-store`.
The public model catalog is not an entitlement check. Capabilities may initially
have `observation: null`; a historical observation is not a future guarantee.

Mint requires `session.type=realtime` and the explicit verified model ID. The
voice defaults to `cedar`; output defaults to audio and 4,096 maximum output
tokens. Session/response overrides must stay within the verified model, voice,
PCM format and function-only tool policy. Textual models, auto routing, hosted
prompts, provider-hosted tools, other transcription models, other voices and
text-only output are rejected. Hermes can supply function definitions,
instructions and function results, and can select manual commit or server VAD.

Every ticket is a random 256-bit bearer capability; only its digest is indexed.
It is bound server-side to the issuing client, model, actual account identity and
session reservation. It expires within 30 seconds and never later than the
upstream secret. Expiration uses a monotonic deadline internally and Unix seconds
on the wire. Redemption is atomic and single use; a new connection needs a new
mint. The upstream secret remains in Route memory and is never returned to Hermes.

A ticket proves possession, not the presenter's identity: possession of a stolen
ticket could redeem it. No caller can override its owner/account/model. Use the
trusted internal network or correctly configured TLS; do not put credentials in
query strings or share tickets between sessions.

Reservations begin before OAuth/mint, count against bounded admission and pin
one configured slot. OAuth uses the same `CodexRouter` instance as textual Route
traffic, including existing refresh locks and managed storage. Disabled slots,
cooldowns, identity changes and missing auth fail closed. No second pool, copied
auth file or competing refresh algorithm is introduced. Upstream opens only at
redemption, before downstream upgrade. Expiration, rejection, disconnect and
shutdown release reservations; restarts invalidate tickets. Never replay old
audio or tool output into a replacement session.

Supported client events are `session.update`, `input_audio_buffer.append`,
`input_audio_buffer.commit`, `input_audio_buffer.clear`, `response.create`,
`response.cancel`, `conversation.item.create`, `conversation.item.delete` and
`conversation.item.truncate`. Ordinary valid JSON strings preserve wire order,
IDs and payloads. Policy is checked again on session updates and response
creation, so an initially safe session cannot later enable hosted tools.
Provider session/audio/transcription/function-call/completion events are relayed.
Route never executes a function or delegates an agent.

Hermes stops playback and sends cancellation/truncation commands with actual
played-audio timing. Route forwards them once; it does not reconstruct playback,
automatically retry a response or manufacture a continuation. Function results
are `conversation.item.create` items of type `function_call_output`, followed by
an explicit client `response.create` when appropriate.

Errors before upgrade are JSON with `error.type`, fixed `error.code` and a
sanitized message. Missing/invalid voice auth and invalid/expired/reused tickets
return 401; policy/configuration errors return 400/403; account identity changes
409; limits 413/429; unavailable auth/transport 503/502; deadlines 504. A plain
HTTP request to the WS route receives 426. Disabled Realtime returns 503 even
when the legacy global token is absent.

Provider error bodies are never copied verbatim. Known codes distinguish quota,
rate limits, model and authentication failures. A generic upstream 403 remains
`realtime_upstream_forbidden_unclassified`, not proof of entitlement denial.
After upgrade, Route sends at most one sanitized `type:error` event when the
socket permits, preserving an upstream event ID, then closes. Policy errors use
1008, oversize 1009 and internal/upstream failures 1011; normal close is 1000.
The frame reader may close an oversized connection before an error event fits.
There is no silent reconnect or fallback.

## Configuration and operator boundaries

Public defaults leave all three settings disabled/unset:

```dotenv
SPARTAN_ROUTE_REALTIME_ENABLED=false
SPARTAN_ROUTE_REALTIME_MODEL=
SPARTAN_ROUTE_REALTIME_CLIENTS_FILE=
```

For a later approved activation, set the model explicitly and point the file
setting to a private registry under the existing Route data mount, for example
`/app/data/realtime-clients.json`. The JSON shape is:

```json
{
  "clients": [
    {"id": "hermes-talk", "token_sha256": "<64 lowercase hex characters>", "slot": 0}
  ]
}
```

The slot above is a schema example, not an account selection. Use only an
explicitly authorized existing slot. Provision a dedicated unpredictable client
credential through the operator's secret-management process; Hermes receives
that credential, while Route stores its SHA-256 digest. Credentials must be
32–512 characters without whitespace, distinct from `CLAWROUTE_TOKEN` and
provider credentials. No credential creation, migration or token export is
performed automatically. Real digests/account mappings belong in private files,
never the public baseline. The registry must be regular, non-symlinked, no more
than 64 KiB and not group/world writable. IDs/digests must be unique; at most
64 clients are accepted. Invalid enabled configuration fails startup.

No additional ingress is exposed. The existing Caddy listener replaces
Authorization and is **not a supported voice ingress**. External TLS ingress
preserving voice authentication requires a separate operator change. Realtime
uses the existing HTTPS proxy for both mint and WSS; missing or bypassed proxy
configuration fails before OAuth. This explicit WSS mapping is necessary because
[aiohttp selects environment proxies by scheme](https://docs.aiohttp.org/en/v3.12.14/client_advanced.html#proxy-support).
Tinyproxy/DNS policy stays in force; no allowlist change was needed.

The v1 operational bounds are fixed and exposed by authenticated capabilities:

| Bound | Value and rationale |
| --- | --- |
| Reservations/sessions | 4 total, 1/client, 2/account; bounded in-flight memory and isolated sessions |
| Mint JSON / WS message | 256 KiB / 1 MiB; permits tool schemas and chunks well above the measured 12,000-byte synthetic PCM input |
| Ticket / mint / handshake | 30 s / 25 s / 25 s; fit the pinned client's 30 s mint/connect budgets with margin over sub-second observed upstream operations |
| Write deadline | 5 s; terminate a stalled consumer rather than accumulate audio |
| Idle / session lifetime | 1,800 s / 3,600 s; bounded lifecycle with room for the pinned client's long tool/delegation work |
| Heartbeat / shutdown | 20 s / 5 s; client heartbeat baseline and existing Route shutdown budget |
| Output tokens | 4,096 maximum per response; bounded output with room above the observed 128-token tool truncation |

These are conservative operational bounds, not measured capacity guarantees or
provider entitlement limits. Limits report names, configured/observed values and
recovery hints where observable; aiohttp oversize errors identify that the bound
was exceeded without inventing an exact byte count. Each direction awaits writes
and uses bounded transport buffers; there is no unbounded application queue or
full-conversation buffer. Slow writes and idle/lifetime deadlines close both
sides. Forwarding tasks are cancelled and awaited before releasing resources.

Content-free stderr records include an opaque correlation ID, subscription route,
model, timings, byte counts, fixed close reason and upstream token totals when
available. They never use the text logger's content-retention setting. No audio,
transcript, prompt, tool arguments, credentials, slot path or account ID is logged.

## Reproduction, tests and Hermes handoff

Both harnesses default to offline descriptions:

```sh
python3 scripts/spartan_route_realtime_probe.py
python3 scripts/spartan_route_realtime_check.py
```

After applicable account-use authorization, the raw feasibility probe runs inside
the existing service using its exact auth paths, managed store and UID:

```sh
source scripts/aliases.sh
: "${APPROVED_REALTIME_SLOT:?Set the explicitly approved slot}"
sg exec -T spartan-route python3 - \
  --live --slot "$APPROVED_REALTIME_SLOT" --approved-account-use \
  < scripts/spartan_route_realtime_probe.py
```

The complete implemented-Route acceptance runs a temporary loopback-only service
inside that same container, streams source code only, uses existing OAuth locks,
checks cached slot policy and leaves production serving unchanged:

```sh
python3 scripts/spartan_route_realtime_check.py \
  --stage --slot "$APPROVED_REALTIME_SLOT" --approved-account-use
```

Staging reuses the existing Route client credential only within this temporary
voice-only server; it exposes no text/admin routes and creates no credentials.
This is an explicit testing arrangement. Production configuration rejects using
the global text credential as a voice credential.

For the Hermes implementer, use the dedicated provisioned voice credential and
configurable Route mint/WS endpoints. Bypass Hermes Talk's local OAuth lookup and
refresh. Do not interpret the Route credential as an OpenAI API key or send it
directly to OpenAI. The flat mint response matches `parse_client_secret`; its
expiry is seconds, while the pinned client converts to milliseconds internally.

Once a Route instance is enabled, run the same acceptance client using environment
variables without embedding or printing credentials:

```sh
# Set SPARTAN_REALTIME_BASE_URL and SPARTAN_REALTIME_TOKEN via secret management.
python3 scripts/spartan_route_realtime_check.py --check --approved-account-use
```

The checks use 250 ms of synthetic PCM and an inert function, not human audio.
The operation budget is 60 seconds plus bounded cancellation cleanup. The inert
tool response uses an explicit no-speech instruction and a 1,024-token cap after
observed 128-token truncation; ordinary short audio turns stay at 128. Staging
adds a bounded long-output cancellation and next-turn check. Never treat fixture
results as account-access evidence or claim Discord validation from these tests.

Local regressions cover scope/missing auth, config isolation, model/tool policy,
expiry/reuse/capacity, actual app middleware ordering and shared OAuth ownership,
raw event order, same-account sessions, provider errors/redaction, oversized data,
slow writers and shutdown. The complete existing Route suite retains textual,
Responses, image and concurrent managed-OAuth refresh coverage. Run with the
repository's pinned aiohttp dependency:

```sh
python3 -m unittest discover -s infra/spartan-route -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_spartan_route_realtime*.py'
docker compose -f infra/compose/compose.yml config --quiet
```

## Activation and rollback

Production activation and credential provisioning remain pending operator action.
Keep the feature disabled until dedicated credentials and private registry are
ready. Validate configuration with `sg config --quiet`; an approved deployment
may then update only Route. This work does not restart production or enter Discord.

Disable admission by setting `SPARTAN_ROUTE_REALTIME_ENABLED=false` for the next
approved Route replacement. Shutdown closes sessions and invalidates tickets.
Revert Realtime commits to return to the preceding source checkpoint; the earlier
Hermes/Camofox/Route checkpoint commits preserve unrelated work. Never roll back
or copy an OAuth refresh grant as part of source rollback: the old generation may
already be invalid. Real Discord playback, interruption timing and delegation
acceptance belong to the Hermes implementer and require a real approved call.
