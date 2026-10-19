# Codex model eligibility and bounded recovery

This describes the source-level Route repair prepared on 2026-10-19. Deployment
is separate; committing the change does not update a running Route image.

## Availability versus eligibility

An operational Codex account may still lack a validated catalog offering the
requested model or reasoning level. General slot availability is not proof that
the account can serve a particular request. Model-specific selection failures
return `codex_model_unavailable`, with operational and model-eligible counts
kept distinct. Quota and transient cooldown failures retain their existing
status and retry behavior.

Selection diagnostics record only bounded safe metadata: slot index, exclusion
reason, catalog revision/age, requested model and reasoning level, and recovery
outcome/duration. They do not include credentials, account identities, or local
paths. Diagnostics also accompany request telemetry without a database schema
migration. Missing historical diagnostics cannot reconstruct an old catalog.

Both Chat Completions and native Responses can recover catalog-related
admission or selection failure. When an otherwise operational account is
blocked by model eligibility, Route shares one bounded catalog recovery across
concurrent requests, then retries the same admission/selection once. Recovery
starts at most once per minute and has a 20-second controller deadline,
including lock waiting and publication. Request cancellation does not cancel
the shared controller. Reset and shutdown invalidate obsolete publication and
cancel owned work.

Recovery fetches at most four account catalogs concurrently. Successful,
validated results and still-valid previous snapshots can be published after
slow workers are cancelled and awaited. Failed, malformed, expired, or
identity-mismatched catalogs do not grant eligibility. A discovered model that
disappears remains catalog-gated; its removal cannot make it available on every
account. Normal background discovery retains its startup/hourly schedule.

Recovery preserves disabled slots, quota/transient cooldowns, account and slot
exclusions, model, reasoning level, session/cache identity, and provider realm.
It does not generate inference probes, switch provider credentials, downgrade
reasoning, or replay a committed native response. Experimental probes keep
their separately constrained selection and admission path.

## Operator response

Read the model-specific diagnostic before treating all accounts as unavailable.
A missing or outdated offering may recover through catalog discovery. A valid
negative offering remains a denial after recovery; do not bypass it. A failed
catalog refresh may indicate upstream or network trouble, while an identity
mismatch requires investigating the intended credential generation.

Use independent local login only for slots reporting a terminal
`reauth_required` condition. Do not copy a refresh grant between slots. Preserve
genuine usage-limit cooldowns; restarting Route is not quota recovery. Existing
safe OAuth lifecycle details remain documented in [Spartan Route](spartan-route.md).

The incident motivating this change had one operational account while the
requested model was not selected. Another model succeeded on that account.
The historical catalog was not retained, so this establishes inconsistent
diagnostics and a recovery gap, rather than proving the exact upstream catalog
change that caused that incident.

## Validation and activation

Focused tests cover model-specific denial and recovery, vanished models,
reasoning restrictions, admission before forwarding, shared controller
cancellation/deadline behavior, and unchanged routing restrictions. Run the
full Route Python suite before activation.

Preparation validation: all 425 Route Python tests passed in a disposable,
network-isolated container using the existing image, read-only task sources,
and bounded memory-backed test storage. Independent adversarial review found
no remaining blockers in the combined implementation. These checks exercise
mock upstreams and do not establish live upstream model availability.

This task authorizes source edits and bounded tests under a temporary disk
exception only. No image build, service recreation, reauthentication, quota
mutation, or live inference is part of this repair. A later activation should
restore the normal disk margin, retain the current Route image for rollback,
build/recreate only Route through the main-stack helpers, and inspect safe
health plus real request diagnostics. Hermes deployment is independent.
