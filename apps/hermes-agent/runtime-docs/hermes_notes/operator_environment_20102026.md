# Operator environment — portable verification guidance

Status: pending operational recommendations, not a record of any particular
host's installed services, configuration or migration state.

- Validate aliases and environment resolution in a fresh shell; interactive and
  automation environments can differ. Configuration parsing alone does not
  establish account authentication.
- Docker-group membership grants root-equivalent host access. Never expose the
  Docker socket or management credentials to agent workloads for convenience.
- Verify resource changes against both effective runtime metadata and persisted
  configuration, retaining bounded limits and independent rollback evidence.
- Diagnose remote connectivity by separating listener availability, forwarding,
  source policy and trust. Do not replace SSH keys to bypass an unexplained error.
- Paused synchronization with zero counters is not proof of convergence. Resuming
  shares, overriding conflicts or changing encryption requires explicit authority.
- A stopped or deferred workflow remains stopped until reauthorized; a general
  environment repair does not implicitly resume it.

Keep host inventories, identifiers, measurements and migration receipts private.
