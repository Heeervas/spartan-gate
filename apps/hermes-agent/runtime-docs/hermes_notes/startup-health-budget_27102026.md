# Hermes startup health budget — applied with execution-control qualification

Status: the runtime startup grace is applied. Current healthy state, native
profile connections and intended primary credential/configuration binding were
verified, independently audited and accepted with the qualifications below.
This operator archive is not a runtime guide and must not be mounted into Hermes.

Observed startup can require roughly 55 seconds before the local API listens and
64 seconds before native profile connections finish. With a 30-second Docker
startup grace, expected API refusals can count toward the same failure streak as
an isolated later dependency blip. A 75-second controller observation window can
also expire before the next 15-second health-probe opportunity. Running with
Docker health `unhealthy` is not itself a process crash or proof of bad Discord
authentication.

The correction uses a 75-second Hermes startup grace and a configured, bounded
90-second activation health observation. Keep the existing local-API, proxy TCP
and Spartan Route checks, 3-second socket limits, 5-second outer command timeout,
15-second interval and three retries. Accept only an actual healthy observation
and separately verified native profile connections; never accept `starting` or
persistent dependency failure. Auth, ownership, CAS, protected-process and
unknown-operation safeguards remain unchanged.

The existing approved image was retained without a rebuild or pull. The applied
packet changed only startup grace and the reviewed primary credential bindings;
no plugin/helper enrollment, mounts or unrelated profile settings were changed.
Source alone never updates an existing container: activation requires a reviewed
effective Compose/CAS packet and a separately announced quiet window. Preserve
exact before-state rollback, including the previous startup grace, if a future
candidate is reverted. Retain available health timing and native connection
metadata before removal; never persist credentials, inventories or user content.

Execution qualification: the outer orchestration deadline interrupted the
activation before its normal terminal receipt. A separate read-only
reconciliation established current healthy state, unchanged image, runtime grace,
exact staged configuration and native connection/process guards. That current
state was accepted; the missing original terminal record was not backfilled.
Healthy observation within the initial 90-second window remains unobserved, and
direct in-memory adapter bot identity was not observed. Do not label this a clean
closed-execution PASS or recreate a healthy service merely to repair bookkeeping.
Future long operations must use a top-level command or an adequate explicit outer
deadline and retain owned process metadata. No replay is authorized by this note.

The cause of an individual Route read timeout remains unproven. The startup-budget change does
not establish a token/intents fault, increase individual request timeouts, relax
steady-state dependency readiness or authorize another activation attempt.
Credential-withdrawal/edge-negative acceptance and separate agent feature
activation remain outside this applied change.
