# Optional Camofox-only Hermes image

The default Hermes image remains the pinned full upstream image. This optional
variant rebuilds upstream revision
`e8c97320ac8691d4de92af49f98459f9ef9ddb08` and omits only PM's bundled Chromium
installation, browser version probe and executable-path stamp from their
original creation layer. It preserves the Python/Node/FFmpeg toolchains,
Playwright client, GTK/browser libraries, compilers, toolbox and document tools.
Local Chromium-dependent operations, including direct Playwright Chromium
launch and Chromium HTML-to-PDF, are unsupported. Camofox does not imply an
equivalent PDF implementation.

The baked, root-owned read-only `/etc/spartan/hermes-browser-mode` marker selects
`camofox-only`. Browser policy and PM restrictions also apply to the resident
worker, whose source is patched before upstream stages and seals it. The
ordinary upstream source copy is patched again before application assembly.
Desktop builds are rejected because that option installs another Chromium.

## Prepare and review

Use a separate clean upstream Git checkout at the exact revision above. Obtain
that checkout separately; the helper neither downloads nor builds. It rejects a
wrong revision, dirty checkout or any change to the SHA256-pinned upstream
Dockerfile. It exports tracked source into a new context and never changes the
checkout. Repeating preparation is accepted only when the existing output
matches the complete expected file inventory. A modified output fails closed.

Run these commands from the approved Spartan Gate checkout after the runtime
variant patch and build changes have both been reviewed:

```sh
python3 apps/hermes-agent/build/prepare_camofox_only.py \
  --source /path/to/pinned-clean-hermes-checkout \
  --output /path/to/new-hermes-camofox-context
```

Review `spartan-variant-build.json`, the context's Dockerfile diff and its
`install-stamp.json`. The manifest records upstream revision, original image
digest, Dockerfile hashes, transformer hash and injected guard hashes. Preserve
this manifest alongside final image IDs and the approved downstream checkout
revision. Context preparation records source provenance; it does not guarantee
byte-identical dependencies from APT or other unlocked repositories.

## Capacity gate and build

Do not start a build until an explicit peak-space budget fits. The repository
normally requires more than 10 GB free before a Hermes rebuild and at least
5 GB throughout. Follow any explicit operator-authorized capacity exception
for that operation, retaining an agreed emergency margin. A small threshold
dip alone does not authorize cleanup. Free space just above the normal floor
is insufficient to assume that a full rebuild fits. The changed
upstream PM layer invalidates subsequent Python/frontend layers, so downloads,
intermediate stages, BuildKit cache and export can exceed the eventual Chromium
saving by several times. Image logical sizes do not measure physical coexistence
cost. Measure shared layers and reclaimable bytes before estimating a peak;
stop if a bounded build cannot fit. Cleanup and container/image removal are
separate operator decisions, never performed by the preparation helper.

Build only the reviewed candidate after that gate. Do not build two complete
comparators blindly. A controlled comparator needs the same approved downstream
source, dependency artifacts and inventories; otherwise report drift separately
from the Chromium delta. The pinned previous full upstream base is
`docker.io/nousresearch/hermes-agent:main@sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde`.
It remains a rebuild coordinate when the operator explicitly elects to remove
the current image to make space; such removal does not preserve an immediate
image rollback.

The following are build commands, not activation commands:

```sh
docker build --progress=plain \
  --tag spartan-hermes-upstream:e8c97320-camofox-only \
  /path/to/new-hermes-camofox-context
# Capture and review the resulting immutable local image ID.
# Keep the local tag pointing to that reviewed ID for the downstream build.
docker image inspect --format '{{.Id}}' spartan-hermes-upstream:e8c97320-camofox-only

test "$(docker image inspect --format '{{.Id}}' spartan-hermes-upstream:e8c97320-camofox-only)" \
  = sha256:REVIEWED_UPSTREAM_VARIANT_ID
docker build --progress=plain \
  --build-arg HERMES_BASE_IMAGE=spartan-hermes-upstream:e8c97320-camofox-only \
  --tag spartan-hermes:camofox-only apps/hermes-agent
docker image inspect --format '{{.Id}}' spartan-hermes:camofox-only
```

Before the downstream build, inspect the upstream tag again and require its ID
to match the reviewed ID. Use a registry digest when publishing the base. Local
image IDs are review coordinates; the Dockerfile `FROM` argument uses a local
tag or registry digest.

A local upstream tag is a required build input, not an archived artifact. If it
was removed after deployment to recover capacity, repeat upstream preparation
and build before running the downstream build (including `sg build hermes`).
Retain the pinned source bundle and preparation manifest separately. The running
final image does not require the upstream tag to remain installed.

Docker may retain both compressed content and unpacked layers. Record both when
comparing storage; the image inspect size alone can understate that total.
Changing export compression also changes the comparison: the first deployed
variant used Zstandard level 15, so its measured saving includes compression as
well as the omitted Chromium payload. Compression does not reduce the unpacked
application footprint or eliminate peak build-space requirements.

Use an approved clean downstream checkout for the final build: building a dirty
checkout can silently include unrelated patches. Record installed package
inventories and source manifests; any differences beyond browser payload and
variant guards require review. Keep measured candidate image size, physical
storage cost and comparison to the previously deployed image distinct.

## Explicit selection

`infra/compose/compose.hermes-camofox-only.yml` is optional and requires explicit
`HERMES_CAMOFOX_ONLY_BASE_IMAGE` and `HERMES_CAMOFOX_ONLY_IMAGE` references.
For the base build argument use the reviewed local tag (verify its immutable ID
before the build) or a registry digest. For the final image use the reviewed
local tag or registry digest and record its immutable ID. Include it after the
base Compose file and before private overrides; inspect the final render because later
overrides can change selection. For configuration validation only:

```sh
HERMES_CAMOFOX_ONLY_BASE_IMAGE=spartan-hermes-upstream:e8c97320-camofox-only \
HERMES_CAMOFOX_ONLY_IMAGE=spartan-hermes:camofox-only \
docker compose -f infra/compose/compose.yml \
  -f infra/compose/compose.hermes-camofox-only.yml config --quiet
```

Activation, live runtime canaries and actual savings measurement are separate
operator steps. Validate fresh and inherited synthetic PM state, prohibited
browser installations without network activity, resident-worker dispatch,
Camofox-only startup, retained non-browser tools and document workflows before
selecting this image for production. Browserless requires the full variant.
Removing this overlay restores the full default on a subsequent approved build;
changing a build argument alone does not replace an already running container.

## Boot Using The Validated Image Environment

`SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY=1` explicitly disables the native
startup dependency refresh and generation collection. The default remains the
upstream refresh behavior. This flag is useful when the image already carries
the required dependencies and rebuilding a persistent generation would exceed
the available storage.

Before enabling it, verify that native PM fallback metadata has no
`environment` or `resolved_lock` pointer. A selected generation causes startup
to fail; the flag never clears that selection. Back up dependency metadata and
use the native PM fallback recovery procedure separately when required. Keep
the recorded extras and original stamp; do not manufacture a current stamp.

Startup runs the baked `.venv/bin/python`, checks startup imports, compares its
actual optional-feature inventory with `enabled-features.json`, and requires
all recorded extras and enabled plugin Python requirements to be available in
that environment. Missing distributions, incompatible versions, unsatisfied
extras, direct URL requirements and incompatible plugin Python versions refuse
startup. The check does not install packages, change facts or delete generations.
Explicit operator dependency installation remains a separate action.
