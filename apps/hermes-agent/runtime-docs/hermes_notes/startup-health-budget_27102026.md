# Hermes startup health budget — pending operational activation

Status: source/configuration correction prepared; not an applied runtime change.
This operator archive is not a runtime guide and must not be mounted into Hermes.

Observed startup can require roughly 55 seconds before the local API listens and
64 seconds before native profile connections finish. With a 30-second Docker
startup grace, expected API refusals can count toward the same failure streak as
an isolated later dependency blip. A 75-second controller observation window can
also expire before the next 15-second health-probe opportunity. Running with
Docker health `unhealthy` is not itself a process crash or proof of bad Discord
authentication.

The prepared correction uses a 75-second Hermes startup grace and a bounded
90-second activation health observation. Keep the existing local-API, proxy TCP
and Spartan Route checks, 3-second socket limits, 5-second outer command timeout,
15-second interval and three retries. Accept only an actual healthy observation
and separately verified native profile connections; never accept `starting` or
persistent dependency failure. Auth, ownership, CAS, protected-process and
unknown-operation safeguards remain unchanged.

Use the current approved image; no image rebuild/pull is required. Source alone
does not update an existing container. Activation requires a reviewed effective
Compose/CAS packet and a separately announced quiet window. Preserve exact
before-state rollback, including the previous startup grace, if the candidate is
reverted. Retain available health timing and native connection metadata before
removal; do not persist credentials, account inventories or user content.

The cause of an individual Route read timeout remains unproven. This change does
not fix tokens/intents, increase individual request timeouts, relax steady-state
dependency readiness or authorize another activation attempt.
