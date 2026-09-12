# Camofox 1.15 refresh

The source upgrade targets upstream [v1.15.0](https://github.com/jo-inc/camofox-browser/releases/tag/v1.15.0),
released September 10, 2026, commit
`771b610a7b5994759c138b912741de58b0edd588`. The annotated tag and commit are
unsigned according to GitHub. The GHCR multi-architecture index was retrieved
and its SHA-256 independently recomputed:

```text
ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae
```

This follows the already committed 1.14 refresh, which removed the old VNC
override and much of the pre-1.12 lifecycle patching. The 1.15 source changes
are not evidence of a deployed or runtime-validated image.

## Patch decisions

| Surface | Decision and evidence |
| --- | --- |
| Fixed-proxy timeout guard | Remove the local guard: upstream `handleRouteError` now requires a rotatable proxy before destroying the user session. Keep Spartan's immediate retirement of the affected tab and suppression of duplicate route-level timeout accounting; upstream deliberately leaves that tab alive. |
| Periodic profile cleanup | Retain suppression. Upstream protects paths belonging to an established browser, but `browser` can be null during a launch. The outer launch timeout can settle before the underlying launch, so checking only `browserLaunchPromise` is insufficient. Startup and post-close cleanup remain upstream-owned. |
| OS/VNC/yt-dlp installation | Remove the wrapper's duplicate apt upgrade/install layer. Upstream installs the browser requirements and plugin dependencies, and pins/checksums yt-dlp. Build assertions verify the required tools and noVNC asset. Keep checksum-verified Onest and the offline UBO cache. |
| VNC and display ownership | Use upstream's exclusive provider registry and VNC plugin. A conflicting display provider must fail startup. Set `CAMOFOX_INTERACTIVE=off` in the image and all tiers; noVNC remains controlled by `CAMOFOX_ENABLE_VNC`. |
| Operator lease | Adapt to upstream's `SESSION_TIMEOUT_MS > 0` condition, preserving disabled expiry, pending-create fences, renewable leases, and ordinary-close protection. |
| Persistence | Retain private-mode, validated, fsynced atomic writes and fail-closed pre-close checkpoints. Reuse upstream serialization and exact supplied storage snapshots. IndexedDB remains opt-in; stored identity hashes and filenames are unchanged. |
| Lifecycle, readiness and pressure | Retain bounded admission, late-completion fences, generation-bound deep health, cgroup limits, and the PID-1 recovery ledger. Upstream page leases, per-user recovery, and owned process cleanup remain in use. |
| Integration | Retain verified viewport behavior, authenticated capabilities/checkpoints, operator isolation, URL-log redaction, and Obsidian staging. Move the inline geo-IP edit into the tested patch chain. Hermes tab isolation stays unchanged. |

No plugin extraction is needed for this release. Upstream's display registry
does not provide enforcement hooks for our core admission or durability rules.
Shared memory remains 1 GiB and the service retains its 2 GiB memory ceiling.

## Reproducible source validation

Extract the exact commit into a scratch directory and set
`CAMOFOX_UPSTREAM_SOURCE` to that directory. Do not point it at a patched tree
or a browser profile. The harness verifies release hashes, applies all patchers
to a temporary copy, syntax-checks output, reapplies the entire chain, and
compares bytes. It also exercises transformed timeout isolation and native
display ownership, and rejects missing/duplicate anchors without partial writes.

September 12 validation: 46 Camofox JavaScript tests, 24 supervisor tests,
3 Hermes isolation tests, and the full 341-test scripts suite pass (one
unrelated Realtime probe test skips without aiohttp). The scripts suite renders
all 13 Compose shapes. Independent review found no remaining P0–P2 issues.
`scripts/doctor.sh` stops at pre-existing public-path findings in workstation
and Route files outside this change. No image build or live acceptance ran.

```sh
CAMOFOX_UPSTREAM_SOURCE=/path/to/pristine/source \
  node --test infra/camofox/test_*.js infra/camofox/test_*.mjs
python3 -m unittest discover -s infra/camofox -p 'test_*.py'
python3 -m unittest scripts.test_camofox_patch_server scripts.test_install \
  scripts.test_tooling_camofox scripts.test_compose_hardening_contract \
  scripts.test_camofox_acceptance scripts.test_camofox_deep_health_fault
python3 -m unittest discover -s apps/hermes-agent -p 'test_camofox_tab_isolation_patch.py'
```

## Deployment gate and rollback

On September 12, only about 1.5 GB was free. Building and deploying are pending:
keep at least 5 GB free throughout operations, with additional room for the
new image and retained rollback layers. Do not prune active or rollback images
or inspect profile contents to make space.

The observed healthy rollback image is `spartan-gate-camofox:1.14.0`, image ID
`sha256:55f32a94be0be17a0c814631144adc4937836cc86fa7abc68d25622f3306aa82`.
Before deployment, recheck its identity and retain it under a rollback tag.
Update only the private Camofox base/wrapper pins after the new image builds.
Use the main-stack helpers to build and recreate only Camofox, with no
dependency recreation. To build while leaving the active private pins intact:

```sh
source scripts/aliases.sh
CAMOFOX_BASE_IMAGE=ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae \
CAMOFOX_IMAGE=spartan-gate-camofox:1.15.0 sg build camofox
# After updating the two private pins to the successfully built image:
sg up -d --no-deps --no-build camofox
```

A rollback selects the retained 1.14 image and recreates
only Camofox with `--no-build`; it does not restore, reset or delete profiles.

Runtime acceptance must still establish:

- Persistence and VNC plugins actually loaded; scratch checkpoints survive
  session and container recreation; forced checkpoint failure retains a
  retryable live context.
- Operator lease, authenticated noVNC status/reattachment, sequential and
  concurrent tabs, and `sg-camofox-viewport-check 390 844`.
- Deep-health recovery and a changed browser generation, without changing
  neighboring services; never reset the durable recovery ledger for testing.
- Live scratch profile storage survives beyond ten minutes.
- On bundled Camoufox 152.0.4 beta.28, default UBO blocks controlled GA/GTM/Meta
  requests and exact `true`/`1` analytics mode permits them only through the
  existing proxy/DNS policy. Malformed values remain protected. Keep custom
  add-ons denied in protected mode until their coexistence with UBO is proven;
  test read-only custom add-ons separately in analytics mode.

After acceptance, review logs over 24 hours and retain the rollback image for
seven days. Browserless retirement and builds of other services are separate work.
