# Native cache experiments

Cache diagnostics are passive. Experimental requests and prompt changes are off
unless an operator explicitly starts the helper below. The experiment targets
`codex/gpt-6-astra` through native `/v1/responses` only. Provider API support for
a cache option does not establish Codex subscription support or quota savings.

## Baseline and probe

Run the read-only report inside the current Route container:

```sh
sg exec -T spartan-route python -m spartan_route.cache_probe_cli report
```

The helper uses the existing private data mount and authenticated loopback
endpoint. It does not print credentials, use external HTTP proxies, follow
redirects, refresh models, or execute tools. An older Route image without v2
diagnostics is rejected before any experiment file is written.

Collect 24–48 hours in the current diagnostic epoch. A restart starts a new
baseline. A probe requires at least ten completed, non-overlapping requests
across two sessions that reuse the existing initial developer group while
changing the suffix under the same effective key, with observations on two
accounts. Missing cache counters are excluded. If the recurring pattern is
absent after 48 hours, this intervention is reported as inapplicable.

Once applicable, explicitly run:

```sh
sg exec -T spartan-route python -m spartan_route.cache_probe_cli probe
```

The helper selects two currently eligible accounts, one with higher and one
with lower observed reuse. The private `cache-canary.json` authority is mode
0600, bound to one process epoch, and expires within an hour. Route validates
the file, exact synthetic payloads, account and shared budget before egress.
The header `X-Spartan-Cache-Probe` references the run and case; it grants no
general account-selection capability.

The entire run permits **12 physical generation attempts and 100,000 estimated
input tokens**. The fixed English fixture uses a conservative character
estimate; this is not a provider tokenizer or an output-token ceiling. Each
account has two groups: implicit caching and an explicit breakpoint on the
last existing text block of the initial developer group. Each group sends a
cold request, exact replay, then replacement of the final user suffix. No tool
is offered. The request asks for `OK`; unexpected tool output stops the helper.

Replay/suffix requests can request provider comparison diagnostics only against
their own group's completed cold response. Those response IDs stay in memory;
they are never used for stateful continuation. A rejection, missing cache
reading, network failure, timeout or ambiguous abort stops the run. There are
no retries, replacement accounts or extra capability-generation calls.

Route reserves each case durably before sending. It rechecks authority after
awaited authentication/selection. Duplicates, concurrency, out-of-order cases,
changed files and exceeded budgets fail closed. Existing account health and
routing-disable controls still apply. The helper revokes the manifest before
writing its final report, including on failure. The result remains in private
`cache-probe-result.json`; an existing result prevents another helper run.
Do not delete it to reset an authorized budget.

## Opt-in cohort

An accepted marker alone is insufficient. Both accounts must show warm replays
and at least 10% less uncached input on the changed-suffix treatment before
the helper will create a cohort:

```sh
sg exec -T spartan-route python -m spartan_route.cache_probe_cli cohort
```

The helper writes private `cache-cohort.json` only after checking current-epoch
baseline and probe evidence. It selects at most 64 eligible pseudonymous
session scopes on the probed accounts, with at least ten assigned to each arm.
Assignment is deterministic per session/run. Normal account routing continues;
unobserved accounts, changed effective keys, developer prefixes, instructions,
tools or settings cause treatment to be skipped. Existing explicit cache
controls are preserved. Top-level instructions are never converted into an
input message. The only payload change is one breakpoint on a verified,
existing developer text block.

Every actual cohort attempt, including control and retries, has a durable
reservation before egress. If reservation storage fails, Route sends the
original request and disables further cohort treatment for the process.
Provider failures, incomplete delivery and cancellation also disable further
treatment. Ordinary native retry and encrypted-reasoning behavior are retained.

The policy expires after **seven days**, or becomes inactive on restart. It is
not automatically renewed or expanded. Disable both probe and cohort authority
at any time with:

```sh
sg exec -T spartan-route python -m spartan_route.cache_probe_cli off
```

Already-sent requests finish normally. Removing authority cannot unsend them.

## Evaluation and quota isolation

Use the run identifier printed by the helper:

```sh
sg exec -T spartan-route python -m spartan_route.cache_probe_cli report --run-id RUN_ID
```

The evaluator requires at least 100 known-cache completed requests and ten
sessions with completed responses per arm in shared account, input-size,
predecessor-gap and observed-chain-age strata. It reports excluded observations
and uses fixed pooled stratum weights. A deterministic 1,000-resample,
session-cluster bootstrap estimates the 95% interval for relative uncached
input. Require at least 10% improvement and an interval excluding no benefit.
Meaningful-provider-output p95 latency must not rise more than 10%; missing
timings remain inconclusive. Failed intermediate attempts, unresolved
reservations and unlogged experiment usage block a favorable decision even
when a later ordinary retry succeeds.

Quota corroboration requires model-and-arm-isolated signed weekly spans on
the same two or more accounts, at least two resolved spans per account/arm,
similar output share and mean input size, and treatment's conservative upper
rate below control's lower rate on each account. Bounds allow two percentage
points of endpoint uncertainty per span, scaled by account capacity. These
remain observational estimates: provider update lag and account use outside
Route are not identifiable here. API prices and cache-write multipliers are
never substituted for subscription quota evidence.

The ordinary quota report excludes **whole intervals** touched by experiments,
including intermediate accounts and durable reservations. It never removes
just experiment tokens while retaining the corresponding quota movement.
Unresolved sent reservations conservatively contaminate all later intervals
on that account; missing logs do not become zero usage. Reservation rows are
small audit records retained independently of rebuildable routing-log retention.
Do not delete unresolved records to obtain a more favorable estimate.

Even after numerical gates pass, the result is `review_required`, never an
automatic expansion. Review task quality, tool arguments, full replay and
terminal/stream integrity separately; routing counters cannot prove semantic
correctness. If any gate, including quota or quality, remains inconclusive at
seven days, the policy expires to off.

Normal cost calibration remains available through:

```sh
python3 scripts/spartan-route-quota-report.py --model codex/gpt-6-astra --since 2026-09-07T00:00:00Z
```

The report's tokens-per-unit rate includes input plus output; cached input is
already a subset of input. It is valid only as an estimate for comparable
workloads and does not establish separate token-category prices.
