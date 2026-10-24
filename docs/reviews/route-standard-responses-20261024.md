# Standard Responses source candidate (2026-10-24)

Status: **READY_FOR_REVIEW**, not independently accepted, installed or deployed.
No implementation commit has been made. Parent owns independent review and any
later scoped commit/installation/Route-only activation. The first source review
returned BLOCKED_NEEDS_FIXES for two major Pi local-state defects; this revision
addresses them and requires renewed review, not acceptance by the implementer.

## Promise and design authority

An unmodified client using the documented stateless Responses subset must complete
first/function-tool/encrypted continuation and fresh-process resume without
Spartan-specific signing. Parent accepted the independent plan PASS_WITH_NOTES on
plan SHA-256 `79453823fc02ba9374220ede7ae3d826908413772727b4ecb58c823353ab33eb`.
The design now has a tracked contract in `docs/pi-native-responses.md`; the ignored
plan is not the only explanation of source behavior.

The clean task branch started at `9aef64fae9f20e601f762f74ac520278f0057fba`, tree
`0538c11939ddd2c0e6f550ab7a329969eb85b0a1`. Candidate identity is the exact dirty/
untracked snapshot and full patch under `.agents/quality-receipts/route-standard/`:
`final-candidate.json`, `candidate.patch` and `candidate-source.tar`. A base commit
alone does not identify these uncommitted bytes. That receipt also identifies
program inputs, scoped file hashes, raw command results and artifact hashes.
The original repository's unrelated dirty work is outside this worktree/candidate.

## Owned behavior and preserved boundaries

- Route's new `reasoning_replay.py` owns opaque receipt encoding/verification,
  legacy-chain validation, request-local scope and public-output transformation.
  The native Codex path captures raw provider observations and transforms copies
  for clients, including duplicate/late terminal representations and nonstream
  reconstruction. No native transport or scheduler copy was added.
- Existing account selection now enforces verified issuing scope before dispatch,
  retaining policy/disable/cooldown/auth/model gates. Unavailable matching accounts
  fail explicitly, never by forwarding ciphertext to another account. OAuth refresh
  and slot moves for a known account preserve scope; unknown-account tokens do not
  acquire guessed portability.
- Signed legacy Hermes/Pi raw traffic retains its wire/output/store behavior.
  Recognizable receipts remain validated even with a valid legacy binding.
  OpenAI API-key raw passthrough remains, with foreign Codex receipt rejection.
- New Pi sessions use local realm/provenance state and no binding RPC. Old raw
  histories use a lazy cached binding; session files/native named IDs and the
  shared UUIDv8 mapping are unchanged. Provider message/tool item IDs are removed
  on full-history replay, including copied zero-reasoning histories; call/result
  relationships and reasoning IDs are retained.
- Concurrency ownership is reserved provisionally before asynchronous legacy
  validation. Only successful validation/preparation commits the native durable
  realm lock. Wrong-realm legacy failure releases the reservation without poisoning
  correct-realm continuation/reopen, including when native conversion removed old
  reasoning before the payload callback.
- Native append failure after in-memory mutation latches the retained manager
  unavailable through replacement extension modules/fences. A process-global
  manager-keyed WeakMap keeps that poison and provisional ownership across reload;
  it does not persist secrets or add a session store. Fresh-process disk recovery
  is a distinct passing control; it cannot rehabilitate poisoned retained memory.
  Binding errors expose only allowlisted categories, not private body/stderr.
- Auth, egress, Compose topology, provider credentials, databases, Hermes/plugins,
  active Pi installation and services were not changed. No dependency installation,
  image build, real inference, paid request or service recreation occurred.

### Necessary fixture compatibility scope

`test_audit_regressions.py` and `test_model_variants.py` now provision a scoped
synthetic signing secret so their existing unsigned first-request tests reach the
behavior they actually protect. No assertions/gates were removed or relaxed.
`test_app.py` uses the same synthetic admission prerequisite, with class-scoped
cleanup and no unused server field. These are within owned Route tests.

The new `infra/pi/responses_fixture.py` owns disposable loopback Route/upstream
HTTP and synthetic account-policy controls, clearing inherited provider environment.
`native_replay_fixture.mjs` is the fresh-process native named-session producer/
consumer. `pi_test_inputs.mjs` resolves actual import-only Pi AI program files in
both hoisted 1.3 and bundled 1.4 layouts. These are necessary owned Pi test fixtures,
not production fallback frameworks or changes to installed dependencies.

## Evidence and reproducibility

Revised affected gates passed: **24 focused provider/SDK JavaScript tests on each
of real Pi 1.3.0 and 1.4.0**, with zero skips/cancellations. The prior complete Pi
results (5 Python + 48 JavaScript per version) remain supporting evidence for
unchanged suites, not claims of a fresh full-suite run. Byte-matched Route results
remain **492 Python + 41 JavaScript** and the unchanged Hermes lineage consumer
remains **29 tests**. The Route suite also includes the unchanged Hermes plugin signing/
stamping/raw-replay bridge against the new HTTP handler. Python 3.14 emits
resource warnings in the full-suite fixtures; they are retained in raw logs, not hidden
or treated as proof of resource cleanup. New owned subprocess fixtures are reaped.

The command owner is `.agents/validation.md`. Raw task evidence is ignored and
local under `.agents/quality-receipts/route-standard/`:

- `sdk-base-final-red.log`: the final decisive SDK cases against a pristine base
  Route source snapshot. First turns succeed; both encrypted continuations fail
  specifically with `reasoning_binding_required`, not missing dependencies.
- `route-final.log`: full Route Python/API/transport/telemetry and dashboard JS.
- `pi13-final.log`, `pi14-final.log`: complete Pi suites, including native loader,
  native save/resume, append-throw/retry, copied zero-reasoning, concurrent realm
  locks and real HTTP unmodified-SDK/actual native Pi process-resume journeys.
- `legacy-hermes-final.log`: unchanged Hermes lineage consumer regressions.
- `reviewer-fixes-red.log`: both added native-manager regressions fail on the
  first source candidate. `reviewer-fixes-green.log` records the affected focused
  green gate; `revision2-pi13-focused.log` and `revision2-pi14-focused.log` record
  the revised final affected gates, including the stricter SDK tool-pairing
  negative/positive control. `revision1/` retains superseded source/
  evidence bytes; only the top-level final candidate identifies this revision.
- `quality-final.log`, `quality-final.json`, `metrics-final.json`: fresh all-scope
  TOP measurement with immutable baseline and frozen stronger policy.
- `checks-final.log`: syntax/import, whitespace and source/input reconciliation.

Since the prior review, only Pi fence/provider behavior, its tests/HTTP fixture,
affected docs/state and the requested pre-review CHANGELOG have changed. Route
production/tests are byte-identical to the previously tested 492-Python/41-JS
candidate, so those raw results and the unchanged 29-test Hermes consumer result
remain valid. The stricter upstream fixture now requires preceding matching
`call_id` relationships, rejects wrong/reordered/duplicate result IDs even with
correct text, and includes a legitimate successful pairing control.

The SDK is real OpenAI 7.19.0, without custom fetch/payload/signing adapters. Route
and upstream use real loopback HTTP; only remote provider auth/transport/content
are synthetic. An inert tool actually computes its result, which the upstream
fixture requires. Tests record raw ciphertext and account at dispatch, deny
modified receipts/mixed accounts/foreign realms before submission, exercise actual
slot policy disabling, and restart both client and Route state. Fresh native Pi
processes persist/reopen a named session using the real 1.3/1.4 normalization and
session manager. This is not real upstream ciphertext or deployed acceptance.

Program inputs: Node 22, existing Python venv with Route's aiohttp 3.14.4, explicit
real Pi 1.3.0 and 1.4.0 packages, their pinned SDK, and Oxlint 1.80.0. No global
package changes. The unchanged Hermes plugin is exercised directly with synthetic scope/session
inputs, not an active installation or private profile.

Quality floor is TOP. The immutable base remains
`75651cc0c5baa1db5a6523b790564988698f2a43`. Stronger inherited policy bytes are
frozen at SHA-256 `18b4582cb69dec698867cff5cb6d2837e9a08531438ba28e75bb4d84545da5db`;
no policy/exception/baseline weakening or source-policy commit. Final status and
exact measurement snapshot are in the canonical JSON. Existing bounded exceptions
remain debt, not release acceptance. An interim new policy-function finding was
resolved by simplifying the same loop; an app-test fixture growth finding was
resolved by scoped environment decoration and eliminating unused server state,
without dropping tests or changing thresholds. Raw preflight/interim receipts are
retained separately from final evidence.

## Independent review focus and remaining limits

Review the frozen actual sources and raw evidence, not just this summary. Check
receipt MAC/canonical parsing/size bounds and scope enforcement, context lifetime
across stream consumption, raw observation ordering, legacy signing compatibility,
local append-failure retry safety, migration and installed/old-server rollback.
Direct source/caller inspection was used; no complete repository graph tool was
available. No required failed/missing source gate should be read as acceptance.

Purpose separation is **not exclusive server trust**: Hermes holds the same root
secret. Integrity assumes uncompromised authorized secret holders; secret holders
can forge receipts. Gateway access plus a valid receipt is a replay bearer
capability, not hostile tenant/profile isolation. Legacy chain MACs do not attest
ciphertext. API-key raw replay keeps provider-owned guarantees.

A stable root secret and an available compatible issuing account are necessary.
Rotation/key loss invalidates receipts; no key ring is added. An old Route image
cannot understand new receipts: full rollback needs the retained Chat/visible-
history path or a reviewed new visible-only session, preserving original records.
Actual Codex optional item-ID omission, encrypted replay, native installed bytes
and operational availability still require parent-owned image/live acceptance.
The intermittent incident has not been diagnosed or declared fixed.

The later live gate permits **at most 12 physical upstream requests total**,
Sol/Luna only, inert tools, requesting 4096 output tokens per request, counting
failures/retries. Codex discards `max_output_tokens`, so this is not an enforceable
output cap. A zero-reasoning response or partial text is not encrypted acceptance.
Source approval grants no implicit installation, deployment, push or other-service
mutation.
