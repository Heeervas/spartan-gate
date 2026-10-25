# Client-asserted Route correlation

`POST /v1/chat/completions` and `POST /v1/responses` optionally accept a single
`X-Spartan-Correlation` header. Header names are case-insensitive; repeated
instances are rejected even when identical. The value must be at most 1024 raw
ASCII bytes and a JSON object with no duplicate keys (including escaped aliases).

```json
{"version":1,"client":"pi","conversation":"native-session-id","purpose":"agent"}
```

Required fields, with no coercion:

- `version`: integer `1` (not `true` or `1.0`).
- `client`: `pi` or `ultimate`.
- `conversation`: token matching `^[A-Za-z0-9._:-]{1,128}$`.
- `purpose`: `agent`, `summary`, `compaction`, or `other`.

Optional `parent`, `root`, and `operation` use the same token syntax. Omit unknown
ancestry rather than inventing it. Unknown fields, versions and clients, malformed
JSON, duplicate keys, non-ASCII raw values or decoded identifiers, and wrong types
are rejected. ASCII JSON escapes are accepted when the decoded token is valid.
Authentication is enforced first; the existing policy-error wrapper is preserved
without introducing a new policy-state read. Invalid metadata returns a fixed,
sanitized HTTP 400 (`invalid_correlation`, `Invalid correlation header`) before
usage observation/registration, replay validation, cache probes or inference.
Requests without the header retain their existing behavior.

## Observability, not authority

Route hashes each ID independently as the first 16 lowercase hexadecimal digits
of SHA-256 over `client + NUL + decoded_id`. IDs share the client namespace, so
the same ID in conversation/parent/root/operation can be related, but Pi and
Ultimate cannot accidentally collide on an identical raw ID. These are stable
pseudonyms, **not secret or authenticated identities**. Authorized callers can
assert arbitrary valid labels; this header grants no usage, replay or account
rights and is not an ancestry proof.

Each new tagged row stores this object in existing `context_info.correlation`:

```json
{"version":1,"source":"client_asserted","client":"pi","conversation":"<hash>","purpose":"agent","parent":"<optional hash>","root":"<optional hash>","operation":"<optional hash>"}
```

Only telemetry grouping `session_id` is replaced with the conversation hash.
Execution sessions, prompt/cache keys, native-cache comparisons, provider account
and slot affinity, replay receipts and signed reasoning bindings are unchanged.
The header is not forwarded upstream and is not injected into request bodies.
Valid signed-chain provenance remains independently recorded in `session_identity`;
`correlation.source` does not replace or upgrade that evidence.

No database schema, migration, backfill or historical regrouping is involved.
Request API objects expose the sanitized value at `request.context.correlation`.
Session and turn groups expose `correlation` (the latest tagged selected row,
otherwise `null`) and `purposes` (sorted distinct values among selected requests,
otherwise `[]`). For lazy details, use
`GET /api/routing/turns/{turn_id}?sessionId=<session.id>` to isolate a card's
requests when independent logical conversations share an execution turn ID. This
optional single ASCII token is limited to 128 characters; malformed or repeated
filters return HTTP 400 `invalid_session_id`. Omitting it retains the legacy
unfiltered endpoint. Execution turn IDs are not rewritten. Aggregation uses the existing bounded selected-row scan, not
additional history queries. Filters/turn limits therefore bound the observed
purpose set; it is not an exhaustive lifetime inventory.

The dashboard groups the bounded visible session set by asserted client/root;
requests with no root stay standalone. Parent/root navigation is available only
when that target is present in the visible set, otherwise the UI marks it as
outside the current view. Legacy identities are labeled rather than reclassified.
Observed purposes and usage are passive telemetry, not an exact inventory of
native attempts or an authenticated execution tree. Filtering and retention can
hide parents, descendants or requests; grouping does not fetch missing history.

## Native Pi source integration

The canonical `infra/pi/extensions/spartan-route-usage.ts` extension adds the
header in Pi's awaited `before_provider_headers` hook, scoped by the existing
`X-Spartan-Pi-Client: 1` transport marker. It uses the stable native
`ctx.sessionManager.getSessionId()`, including named subagent session IDs, and
labels native calls `purpose: other`: this generic native hook does not expose a
reliable request-purpose discriminator and may also serve cache warming or other
auxiliary work. It must not guess `agent`, `summary`, or `compaction` from prompts
or session IDs. It does not infer parent/root,
intercept payloads, add an RPC, or require a successful usage registration.
Existing usage-origin headers and cache preparation remain independent.

Source tests load the real installed Pi extension dispatcher and native Pi-AI
Responses implementation, run an inert tool against loopback Route/synthetic
upstream, and resume from a saved native session with the same grouping ID.
They also exercise receipt tampering and account-affinity denial with correlation
present. These tests use no live provider inference or private agent data.
Source verification is not installation or live activation; installed helpers
must not be edited as part of this contract.
