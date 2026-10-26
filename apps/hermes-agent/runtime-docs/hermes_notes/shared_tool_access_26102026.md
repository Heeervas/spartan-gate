# Shared tool access — pending operational recommendation

Date: 26 October 2026. Status: **source candidate only; not applied**.

Native host Pi and a restricted HTTP container can share search, Reader and the
managed browser through a dedicated opt-in authenticated edge. This does not
move Hermes, change its model routing, instructions or skills, or authorize a
Hermes rebuild/restart. Existing Hermes browser/default click behavior remains;
new clients require an advertised opt-in single-attempt click capability.

The isolated candidate used synthetic credentials, fresh scratch browser users,
actual browser/PNG and native Pi SDK processes with saved resume/fork. Browser
used test-only analytics/addons-disabled mode, not default-UBO runtime proof;
Reader used a declared test-only global-address transport; model replies were
synthetic. No production account/profile was used for these journeys.

Shared capacity is finite. Ordinary tool clients cannot reset profiles/storage,
administer leases, relaunch the browser, import cookies, evaluate arbitrary code
or silently fall back to another browser. Creates reject rather than recycle.
Stale/ambiguous operations fail without replay; service restart invalidates
workflow authority and may leave orphaned remote tabs. Cleanup should precede
planned revocation/rotation; revocation alone does not delete browser resources.

Before any deployment, obtain the single integration/activation approval in
`docs/reviews/shared-tool-access-20261026.md`. It declares scoped Reader/Camofox
build/recreation, dedicated access services, scratch identities, exact private
client config/backups and safe canaries. Coordinate a browser-work pause rather
than disrupting active Hermes/operator tabs. No model change, account sharing,
profile/volume reset, paid inference or global restart follows by implication.

Validation exposed inherited Caddy default-XDG writes and an unsealed Route
suite making public TCP connections through a possible ambient-auth startup path.
Consequences remain partly unknown; fixtures are now isolated, but a later green
does not erase those effects. Private cleanup/credential assessment requires
separate owner authorization. The public review retains the limitations without
publishing personal paths or credentials.

This file is an unserved operator archive note, not a new runtime guide. Do not
mount it or republish it into Hermes automatically. Existing three-guide runtime
publication boundary is unchanged.
