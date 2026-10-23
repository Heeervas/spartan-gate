# Hermes process instruction correction

Status: applied on 2026-10-23. Instruction loading verified; efficiency gains remain unmeasured.

The operator-approved correction changes the shared MASTER tool-policy rule,
the default profile's transport wording, and the canonical orchestration core
and Hermes adapter. It distinguishes unavailable execution surfaces from denied
operations, requires an observable checkpoint and early consequential boundary,
and separates execution, result-contract and artifact-acceptance claims. Existing
role contracts, personas, approval controls and profile skill disables remain.

The two orchestration files remain in the single shared collection. Only their
hash and byte entries in its existing manifest changed. No Hermes core/plugin
patch or model/configuration change was made by this deployment.

These instruction sources are bind-mounted. The existing graceful gateway
restart helper drained work and refreshed the cached MASTER; no image build or
container recreation was needed. Hermes remained healthy and the pre-existing
reviewer completed normally during the drain.

Fresh-process checks as the runtime user exercised the installed native plugin
manager, context loader and skill reader for default, Builder and Reviewer.
The updated MASTER was present in native prompt sections, the default context
contained the corrected transport rule, and eligible shared-skill reads returned
the approved bytes. Reviewer's orchestration disable remained effective. These
checks made no model calls and do not prove future workflow compliance or savings.

Persisted conversations retain their previous system prompts across restart.
An existing controller must recover current MASTER, profile instructions,
required enabled skill supports and project state before continuing. Do not
rewrite session databases or reset goals to obtain apparent adoption. A gateway
refresh is not acceptance of an unfinished project or certification of unrelated
plugin changes already present on disk. Check its actual candidate and loaded
contract under that project's existing gates.

Evaluate comparable authorized work across the whole campaign. If an original
process failure repeats despite verified instruction delivery, reassess that
boundary before an equivalent affected dispatch; do not repeat an unchanged
prose remedy. Continue unrelated authorized work and preserve writer-cessation
and remaining-budget obligations. Finding a defect through the intended early
check is useful evidence rather than failure of the process correction.
