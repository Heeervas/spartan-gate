# Camofox 1.15 refresh

The source upgrade targets upstream [v1.15.0](https://github.com/jo-inc/camofox-browser/releases/tag/v1.15.0),
released September 10, 2026, commit
`771b610a7b5994759c138b912741de58b0edd588`. The annotated tag and commit are
unsigned according to GitHub. The GHCR multi-architecture index was retrieved
and its SHA-256 independently recomputed:

```text
ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae
```

This follows the 1.14 refresh, which removed the old VNC override and much of
the pre-1.12 lifecycle patching. The first 1.15 trial exposed an upstream
temporary-addon startup race. The accepted wrapper closes that race before
publication and was activated on September 12 after independent review and
cold-start, recovery, protected-mode, analytics-mode, and VNC acceptance.

## Patch decisions

| Surface | Decision and evidence |
| --- | --- |
| Fixed-proxy timeout guard | Remove the local guard: upstream `handleRouteError` now requires a rotatable proxy before destroying the user session. Keep Spartan's immediate retirement of the affected tab and suppression of duplicate route-level timeout accounting; upstream deliberately leaves that tab alive. |
| Periodic profile cleanup | Retain suppression. Upstream protects paths belonging to an established browser, but `browser` can be null during a launch. The outer launch timeout can settle before the underlying launch, so checking only `browserLaunchPromise` is insufficient. Startup and post-close cleanup remain upstream-owned. |
| OS/VNC/yt-dlp installation | Remove the wrapper's duplicate apt upgrade/install layer. Keep assertions for upstream tools and noVNC, checksum-verified Onest and offline UBO. GHCR's PyInstaller yt-dlp cannot run under the existing noexec `/tmp`: reuse upstream's installer with its pinned, checksum-verified portable Python asset instead. Do not weaken the mount policy. |
| VNC and display ownership | Use upstream's exclusive provider registry and VNC plugin. A conflicting display provider must fail startup. Set `CAMOFOX_INTERACTIVE=off` in the image and all tiers; noVNC remains controlled by `CAMOFOX_ENABLE_VNC`. |
| Add-on admission | Retain upstream temporary-addon installation, but claim it once per launch profile in the pinned browser archive. Before publishing a protected browser, require ordinary page-world GA/GTM/Meta requests to fail with `NS_ERROR_ABORT`. Exact `true`/`1` analytics mode bypasses this gate. All launch steps share one deadline; late resources are disposed, and cleanup failure degrades lifecycle for PID-1 recovery. |
| VNC admission | Start noVNC only after browser acceptance. Bind the watcher to the exact server, root browser and Xvfb PID/start-time/display identities. Stop and reap websockify before x11vnc before session checkpointing or recovery; identity mismatch, zombies and teardown failure fail closed. |
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

September 12 validation: 46 Camofox JavaScript tests, 26 Python tests,
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
5 GB during Camofox-only builds and activation. The accepted local 1.15 image is
`sha256:dce311d851e7759a7ecd5107238ed9d2a5494dfa466eca2ebfa802ff9ad2a9c5`.
After production acceptance, superseded local Camofox images, including 1.14,
were pruned as requested. Rebuild coordinates remain commit `fb6e8f7` plus
upstream 1.14 digest
`sha256:86c79eed8a6b3a78859f73bc70d6003c5566b85e969354ec454524b28197ffce`.
Use the main-stack helpers to build and recreate only Camofox, with no
dependency recreation. To build while leaving the active private pins intact:

```sh
source scripts/aliases.sh
CAMOFOX_BASE_IMAGE=ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae \
CAMOFOX_IMAGE=spartan-gate-camofox:1.15.0 sg build camofox
# After updating the two private pins to the successfully built image:
sg up -d --no-deps --no-build camofox
```

A rollback rebuilds the immutable 1.14 coordinates above, selects that image,
and recreates only Camofox with `--no-build`; it does not restore, reset or
delete profiles.

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

## Accepted readiness gate

The wrapper patches one hash-pinned `browser-init.js` entry inside `omni.ja` so
temporary add-ons start once per launch profile, then delays publication until
the page-world protection probe passes. The archive and VNC transformations are
strictly hash-checked and idempotent. VNC transport is identity-bound and absent
before acceptance. An open-WebSocket recovery test advanced generation 1→2,
reaped all generation-1 VNC processes, created new identities, and the immediate
ordinary page-script probe blocked all three inert tracker requests.

Independent closure review found no remaining P0/P1 production blocker. Real
profile contents were not read or deleted, and neighboring services were not
recreated. Review Camofox logs over the next 24 hours. Browserless retirement
and builds of other services remain separate work.
