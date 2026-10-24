# Standard Responses source candidate (2026-10-24)

Status: **revision-two source review PASS, accepted by parent; Route deployed and
Pi task-owned files installed with bounded live evidence**. Source implementation
is locally committed/integrated as `cc460db9baa0e0d62ba22b327f3b0d3883f8fbeb`.
The first source review returned BLOCKED_NEEDS_FIXES for two major Pi local-state
defects; the repaired frozen revision received independent PASS before closeout
and separately authorized operational execution. Parent retains final acceptance.
No push occurred.

## Promise and design authority

An unmodified client using the documented stateless Responses subset must complete
first/function-tool/encrypted continuation and fresh-process resume without
Spartan-specific signing. Parent accepted the independent plan PASS_WITH_NOTES on
plan SHA-256 `79453823fc02ba9374220ede7ae3d826908413772727b4ecb58c823353ab33eb`.
The design now has a tracked contract in `docs/pi-native-responses.md`; the ignored
plan is not the only explanation of source behavior.

The clean task branch started at `9aef64fae9f20e601f762f74ac520278f0057fba`, tree
`0538c11939ddd2c0e6f550ab7a329969eb85b0a1`. Accepted pre-commit manifest SHA-256 is
`2cd8170ed5d3eb32f15025e549686c0c21b7014f5d75c0ffb2ec48d3588e70aa`; full patch
SHA-256 is `172b883413bf16c222808ffb3cc335031927b00c32b650410315310e27327da1`.
All 27 staged/committed/integrated files were compared to that manifest. The
original exact dirty/untracked snapshot and full patch remain in task evidence:
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
Source approval alone granted no operational permission. The parent subsequently
explicitly authorized the following Route-only deployment and task-owned Pi
installation; no other-service mutation or push was performed.

## Applied operational closeout (2026-10-24)

Private owner: `private/maintenance/route-standard-responses-20261024/`. It retains
`source-evidence/`, `source-inputs/`, `source-integration.json`, exact prechange
Compose/container/image snapshots, `pi-before/`, `pi-install-manifest.json`,
`pi-installed.json`, `image-canary.log`, `activation-summary.json`, stage-specific
live logs, `live-budget.json`, `closeout-summary.json` and `ROLLBACK.md`. Private
receipts are not publication artifacts and contain no credentials in public docs.

`sg build spartan-route` built the exact integrated source; an isolated nonroot,
read-only, no-network/no-production-data image import and synthetic receipt canary
passed. Candidate/live image is
`sha256:96bf7faa073f52138a4c43dcc8b0d02e68bf3474be99c260cf49b076e0fb2464`.
Only Route was recreated with the scoped no-dependencies/no-build/no-pull command.
Container `4a27d247140e04c0a56facca65d38103183003366b10e11d5b226fbdd7e62dc7`
is healthy, all 26 Route module hashes match source, and protected inference
without authentication returns 401. `/v1/models` remains intentionally public
under the existing discovery contract. Environment key/value mappings and runtime
security settings are unchanged; Docker reordered the environment array, not its
values. All nine neighboring IDs, images and start times remain unchanged.
Disk capacity remained far above the 5-GB floor; no prune or data migration ran.

The installed provider entry and three changed helpers match source. Exact
four-file installation-manifest SHA-256 is
`2233d87f9e55e49f9a6364fb190804f86655dcc0af274ad5fe78b58fd9d25a38`.
Settings, usage/cache entries, authentication and unrelated extensions are
unchanged. A fresh process loaded the actual installed entry successfully; no
current parent was reloaded/terminated and existing processes need a full restart.

Actual-provider acceptance consumed **eight physical upstream attempts total**,
including failures/retries; cap was twelve, now closed. Every call used the
allowed Sol model and requested 4096 output tokens, which Codex does not enforce.
All eight attempts have usage. Seven client operations completed successfully:
standard streamed first/inert-tool/continuation/fresh-process resume; native Pi
named first/resume; and legacy raw named first/migration-resume using preserved
old-client bytes then current installed helpers. Encrypted content was positively
observed and replayed, not inferred from zero-reasoning text. No real external
tool/channel message ran and no existing session was rewritten.

One counted client probe failed: SDK 7.19's higher-level `responses.stream()`
accumulator does not recognize upstream `response.metadata`. The server completed
that request with usage. The documented unmodified SDK
`responses.create({stream:true})` iterator then completed the required journey;
no SDK/server source adaptation was made. Do not claim the higher-level
accumulator, every SDK convenience API or the unrelated intermittent incident
is fixed. The full native Pi CLI/RPC orchestrator was not newly exercised here:
native installed loader, transport/helpers and real persisted SessionManager were.

Previous image is retained as
`spartan-gate-spartan-route:pre-standard-responses-20261024`, immutable ID
`sha256:fd6d8d5b5fb2e4305be3aace82af7d4e4a4da5f1984a6dee55755b7962f8ff93`.
Exact scoped restore instructions and installed-file backups are in `ROLLBACK.md`.
Old-server rollback still requires visible-history/Chat recovery for receipt-bearing
sessions; stable key/account availability and shared-root-secret limits remain.
The clean task worktree is retained until parent confirms writer cessation; its
ignored evidence/inputs have already been copied into the private closeout owner.
