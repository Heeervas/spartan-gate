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
override and much of the pre-1.12 lifecycle patching. The 1.15 wrapper was built
and trialled on September 12 after disk headroom became available, then rolled
back because immediate post-recovery tracker protection failed. Healthy 1.14
remains active; **do not activate this candidate yet**.

## Patch decisions

| Surface | Decision and evidence |
| --- | --- |
| Fixed-proxy timeout guard | Remove the local guard: upstream `handleRouteError` now requires a rotatable proxy before destroying the user session. Keep Spartan's immediate retirement of the affected tab and suppression of duplicate route-level timeout accounting; upstream deliberately leaves that tab alive. |
| Periodic profile cleanup | Retain suppression. Upstream protects paths belonging to an established browser, but `browser` can be null during a launch. The outer launch timeout can settle before the underlying launch, so checking only `browserLaunchPromise` is insufficient. Startup and post-close cleanup remain upstream-owned. |
| OS/VNC/yt-dlp installation | Remove the wrapper's duplicate apt upgrade/install layer. Keep assertions for upstream tools and noVNC, checksum-verified Onest and offline UBO. GHCR's PyInstaller yt-dlp cannot run under the existing noexec `/tmp`: reuse upstream's installer with its pinned, checksum-verified portable Python asset instead. Do not weaken the mount policy. |
| VNC and display ownership | Use upstream's exclusive provider registry and VNC plugin. A conflicting display provider must fail startup. Set `CAMOFOX_INTERACTIVE=off` in the image and all tiers; noVNC remains controlled by `CAMOFOX_ENABLE_VNC`. |
| Operator lease | Adapt to upstream's `SESSION_TIMEOUT_MS > 0` condition, preserving disabled expiry, pending-create fences, renewable leases, and ordinary-close protection. |
| Persistence | Retain private-mode, validated, fsynced atomic writes and fail-closed pre-close checkpoints. Publish the checkpoint callback through a core-owned holder: 1.15 gives plugins prototype-scoped contexts, so assigning a new service object in the plugin hides it from the core. Reuse upstream serialization and exact supplied snapshots. IndexedDB remains opt-in; identity hashes and filenames are unchanged. |
| Lifecycle, readiness and pressure | Retain bounded admission, late-completion fences, generation-bound deep health, cgroup limits, and the PID-1 recovery ledger. Health probes must enter the reserved priority queue even while an owner is active; map admission failures to bounded cached-busy semantics. An early owner check starved probes during sustained traffic. Upstream page leases, per-user recovery, and owned process cleanup remain in use. |
| Integration | Retain verified viewport behavior, authenticated capabilities/checkpoints, operator isolation, URL-log redaction, and Obsidian staging. Move the inline geo-IP edit into the tested patch chain. Hermes tab isolation stays unchanged. |

No plugin extraction is needed for this release. Upstream's display registry
does not provide enforcement hooks for our core admission or durability rules.
Public shared memory remains 1 GiB and the public service retains its 2 GiB
memory ceiling. Existing private resource overrides are unchanged.

## Reproducible source validation

Extract the exact commit into a scratch directory and set
`CAMOFOX_UPSTREAM_SOURCE` to that directory. Do not point it at a patched tree
or a browser profile. The harness verifies release hashes, applies all patchers
to a temporary copy, syntax-checks output, reapplies the entire chain, and
compares bytes. It also exercises transformed timeout isolation and native
display ownership, and rejects missing/duplicate anchors without partial writes.

September 12 validation: 46 Camofox JavaScript tests, 24 supervisor tests,
3 Hermes isolation tests, and the full 345-test scripts suite pass (one optional
Realtime probe test skips without aiohttp). The scripts suite renders all 13
Compose shapes. Independent review found no remaining P0–P2 code issues.
`scripts/doctor.sh` stops at pre-existing public-path findings in workstation
and Route files outside this change. The exact-source harness also exercises
the real upstream plugin loader and generated deep-health queue integration.

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

The initial disk blocker was cleared on September 12. Free space remained above
5 GB during the Camofox-only build and activation (about 6.9 GB afterwards).
Do not prune active or rollback images or inspect profile contents to make space.

The observed healthy rollback image is `spartan-gate-camofox:1.14.0`, image ID
`sha256:55f32a94be0be17a0c814631144adc4937836cc86fa7abc68d25622f3306aa82`.
It is retained as `spartan-gate-camofox:rollback-1.14-20260912`.
The private Camofox base/wrapper pins were restored to 1.14 after testing.
The tested 1.15 candidate image is
`sha256:ac44808e1ac3ef8f96a83ffab8a38d6ced6b1e52b49d177b2845435030066bb0`.
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

Runtime checks that passed on the built candidate:

- Persistence and VNC plugins actually loaded; scratch checkpoints survive
  session and container recreation; forced checkpoint failure retains a
  retryable live context.
- Operator lease, authenticated noVNC status/reattachment, sequential and
  concurrent tabs, and `sg-camofox-viewport-check 390 844`.
- Deep-health recovery and a changed browser generation, without changing
  neighboring services; never reset the durable recovery ledger for testing.
- Synthetic cookie/localStorage state survived 630 seconds of continuous checks
  and a final durable checkpoint.
- On bundled Camoufox 152.0.4 beta.28, default UBO blocks controlled GA/GTM/Meta
  page-script requests and exact `true`/`1` analytics mode permits them through
  the existing proxy/DNS policy. Direct egress and a non-allowlisted proxy
  destination remain denied. Uppercase `TRUE` remains protected. Keep custom
  add-ons denied in protected mode until their coexistence with UBO is proven;
  test read-only custom add-ons separately in analytics mode.

Tracker probes must run as ordinary page scripts, reporting results through a
DOM attribute. On this engine, direct automation `evaluate(fetch(...))` can
bypass the extension's request interception; successful fetches also include
UBO's local replacements for known tracking scripts. Neither is a reliable
page-protection test. Use inert GA/GTM paths and an unparameterized Meta endpoint,
paired with the positive analytics-mode control, without sending tracking events.
Run the durable check inside each candidate container so it inherits that
container's real addon mode and access key:

```sh
source scripts/aliases.sh
sg exec -T camofox node --input-type=module - \
  < scripts/camofox-addon-acceptance.mjs
```

The script uses a unique synthetic user, closes its session, and removes its
persisted storage state before returning.

The deliberate frozen-browser test recovered through PID-1's child replacement
with no container restart. The recovery ledger was not reset. Checkpoint failure
injection and custom add-on tests used a disposable container with synthetic
tmpfs profiles; real profile contents were not inspected or deleted.

## Remaining activation blocker

After authenticated recovery advanced browser generation 1→2, deep health and
noVNC reattachment passed, but the immediately following ordinary page-script
probe allowed all three inert tracker requests. Later probes blocked all three.
This is distinct from the automation-evaluation test artifact described above.
Camoufox's per-window temporary-addon installation is asynchronous, and UBO's
filter initialization is also asynchronous: browser/page readiness does not
prove that filtering is ready. Steady-state protection is not sufficient.

Keep the 1.14 service and rollback image. A future activation needs an upstream
fix or an independently reviewed readiness gate, followed by cold-start and
immediate post-recovery protection tests. No experimental engine archive patch
is shipped in this change. Real profile contents were not read or deleted, and
all eight neighboring containers, images and start times remained unchanged.

After an accepted activation, review logs over 24 hours and retain rollback for
seven days. Browserless retirement and builds of other services are separate work.
