# ClawRoute compatibility names after retirement

The legacy TypeScript ClawRoute application, dashboard, tests and Docker source
under `packages/clawroute/` are retired. It is not an installable lower-tier
addon. `--with clawroute` and persisted addon selections fail before writes;
review the old selection, clear `SPARTAN_ADDONS` explicitly, then select L3/L4
for Spartan Route. No automatic legacy database conversion is performed.

The following are **not retired**:

- `config/clawroute/`: publishable read-only routing defaults consumed by Route.
- `CLAWROUTE_*`: compatibility environment keys still consumed by Route.
- `clawroute/auto` and model aliases: supported router request identifiers.
- Hermes `patch_clawroute*` provider adapters: still used by Spartan Route.

Do not rename or delete those shared contracts as an application cleanup.
Runtime databases, credentials and Browserless profiles remain untouched.
See [layers.md](layers.md) for supported shapes and migration boundaries and
[spartan-route.md](spartan-route.md) for the surviving implementation.
