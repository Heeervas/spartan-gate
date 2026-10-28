# Codex subscription replay portability — 2026-10-25

## Authority and review handoff

Parent owns acceptance. Implementation follows the independently reviewed plan
(`PASS_WITH_RESIDUAL_RISK`) to retain unmodified standard Responses clients and
server-owned opaque receipts while restoring subscription pool failover. Independent
revision-1 source review returned `PASS_WITH_NOTES`, with no blocking source/evidence
issues, and parent accepted the source. The final revision-2 closeout subsequently
committed the accepted source as `bedef4627330663764738a5d1f60e593f4f554fc`.
This resolves the earlier pre-commit wording; it does not establish deployment.
No build, deployment, restart, push, new live inference, production config/DB
mutation or Pi production change is authorized by this closeout.

Starting source: `49b92d4c86b23412732e9fbad404e88f6040c514`. The independently
reviewed revision-1 patch, file hashes, environment and raw receipts are retained
unchanged in private task `codex-replay-portability`, under
`evidence/replay-portability/` after readable relocation.
Revision-1 patch SHA-256:
`d2d8923b6035eb4a8551be51cadbdbe0fb09cd323cde2f0f9e06f3e6f3f24fda`;
manifest SHA-256:
`0423d540f7ce2e584bcae5d760566c30974c08291017e0f416829bffdba6f286`.
The documentation-only revision's exact three-file delta, final scoped candidate
manifest/patch, unchanged production/test hashes and fresh TOP artifacts are under
`revision2/` in that directory. Unrelated dirty bytes, including the stronger TOP
policy, remain preserved and inventoried separately.

## Implementation and preserved properties

- `reasoning_replay.py` authenticates every receipt, accepts mixed issuers, derives
  preference from the last receipt in input order, and restores original ciphertext
  verbatim on a copy. Receipt format, derivation key, realm checks and legacy raw
  binding safeguards remain. The full encoded receipt ceiling is checked on input
  as well as output; no expanded-payload limit is substituted for it.
- `CodexRouter` retains all operational candidates. Model/effort eligibility comes
  before advisory issuer selection, then ordinary lease/load ordering. A valid lease
  outside the preferred subset neither overrides it nor becomes invalid. With no
  eligible matching issuer, selection uses the full eligible pool. Disable,
  cooldown, auth, catalog and slot/account retry exclusions are not bypassed.
- Ordinary unavailable/model/rate-limit codes, status and retry headers are retained;
  the misleading hard-pin unavailable remap is removed. Native transport, committed
  stream and ambiguous-send retry policy is untouched. Existing output transformation
  already binds new receipts to the actual selected attempt.
- The disposable HTTP fixture accepts only its bounded authentic synthetic
  ciphertext vocabulary independent of dispatch account, keeping exact ciphertext,
  ordered call/result IDs and tool-result checks. No provider/SDK/Pi patch or new
  dependency is added. Stored histories and legacy Hermes remain unchanged.

## Source verification

RED was run before production edits: mixed issuers denied, preference fallback
returned no candidate, conflicting leases were discarded, ordinary error codes
were remapped and issuer failure could not retry on another account. The full
receipt-size boundary also failed its new over-limit input assertion. SDK/native
journeys failed on account unavailability rather than startup/dependency errors.

GREEN: 14 focused receipt/scheduling/admission tests and 9 real SDK/native Pi 1.3
interoperability cases. Cases include conflicting lease/load, all operational,
model/effort and retry exclusions, mixed input order and invalid earlier receipt,
retryable A-to-B with exact raw history/tool pairings and B-scoped output,
JSON/SSE/helper full-history mixed-issuer fresh-client resume, native Pi resume
and legacy raw compatibility. This focused-green checkpoint was saved before the
full gates. The final exact test classes were also rerun against a disposable
starting-source snapshot: intended mixed-scope/lease/fallback/error/size failures,
not dependency or startup failures (`final-tests-baseline-red.log`).

Final gates, existing installed dependencies only (Node 22.23.1):

| Command | Result | Raw artifact in task receipt directory |
| --- | --- | --- |
| Focused `test_reasoning_replay.py` | 14 passed | `focused-green.log` |
| `npm run test:route` | 509 Python + 47 JS passed | `route-final.log` |
| `PI_CODING_AGENT_DIR=<real Pi 1.3 package> npm run test:pi` | 5 Python + 55 JS passed | `pi13-final.log` |
| `PI_CODING_AGENT_DIR=<real Pi 1.4 package> npm run test:pi` | 5 Python + 55 JS passed | `pi14-final.log` |
| Unchanged `test_spartan_cache_lineage.py -v` | 29 passed | `hermes-lineage-final.log` |
| `npm run pi:quality` (validate → collect → report) | `QUALITY_PASS_WITH_EXCEPTION`, TOP, seven existing exceptions, zero gaps | `final-quality.log`, `final-quality.json`, `final-oxlint-metrics.json` |
| `git diff --check` | passed | `diff-check.log` |

No skips/cancellations in the applicable suites. Focused and full counts overlap;
these are gate results, not a sum of unique tests. Both actual Pi dependency inputs
use SDK 7.19.0. Exact command/environment input paths are in `commands.txt` and
`environment.txt` in the ignored receipt directory, not public machine configuration.
The existing native no-replay-after-commit/ambiguous-send regressions pass unchanged
inside the full Route gate. No Hermes/Camofox production or Compose boundary changed,
so rebuild/native-image/browser/stack gates are not claimed by these source checks.

Final TOP uses immutable baseline `75651cc0c5baa1db5a6523b790564988698f2a43`,
unchanged policy SHA-256
`18b4582cb69dec698867cff5cb6d2837e9a08531438ba28e75bb4d84545da5db`.
No new exception or weaker policy is introduced. Self-review removed the hard
filter/remap rather than adding new state or retries; the changed selection method
remains below the strong complexity threshold. The independent revision-1 reviewer
returned `PASS_WITH_NOTES`, independently reran 14 focused plus 4 transport-boundary
tests and recomputed exact quality; parent accepted the source. The single note was
stale validation prose requiring mixed-issuer denial and prohibiting cross-account
failover. Parent authorized adding `.agents/validation.md` to owned scope and this
revision reconciles that prose without changing any executable command or weakening
any gate. Only that file, this review record and current-state changed since review.
Production/tests remain byte-identical, so their source behavioral receipts are
reused rather than rerunning full suites. Fresh TOP collection/report and diff
checks for this final documentation revision are retained under `revision2/`.

**Final documentation review is pending.** Parent should review this small delta
with revision-1 verdict/evidence reuse, then handle the authorized local commit.
No commit or activation has been performed by this closeout.

## Scope, consumers and limitations

Inspection follows the actual HTTP admission → replay context → native Responses
→ Codex selection/attempt → output transform path, plus model-catalog diagnostics,
lease publication, retry exclusions, native Pi/SDK producers and unchanged Hermes
lineage consumer. No graph analyzer was available in the tool surface; targeted
source/caller/test inspection is not a repository-wide reachability proof.

The historical review `route-standard-responses-20261024.md` describes the original
hard-pin candidate, not this source policy. Executable gate owner remains
`.agents/validation.md`; its contradictory mixed-account denial/wrong-account
prohibition prose is now reconciled with the reviewed portability contract. All
executable commands remain unchanged. The actual stronger policy bytes match its
documented frozen-policy hash and remain unchanged;
no narrower committed policy, baseline move or self-approved exception is used.

Parent-supplied prior live evidence used synthetic signed history only: unchanged
raw ciphertext replayed on a different enabled account completed without errors.
Foreign reasoning was attributed **zero input tokens**. This does not prove lossless
hidden-reasoning reuse, cache continuity, arbitrary upstream portability or full
live standard-client acceptance. This task performs no further inference. Existing
SDK convenience-output normalization and fresh-server-process live acceptance
limitations remain. Source tests restart Route within one OS process and tolerate
SDK-only extra input fields; green synthetic replay does not establish strict
upstream schema acceptance. Activation requires a separately authorized parent
journey; no deployment is implied by readiness for source review.
