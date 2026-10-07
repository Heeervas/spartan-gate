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
5 GB throughout; any operator-authorized exception must still retain the
5 GB floor. Free space just above that floor is insufficient. The changed
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
# Capture the resulting immutable local image ID; use that ID below.
docker image inspect --format '{{.Id}}' spartan-hermes-upstream:e8c97320-camofox-only

docker build --progress=plain \
  --build-arg HERMES_BASE_IMAGE=sha256:REVIEWED_UPSTREAM_VARIANT_ID \
  --tag spartan-hermes:camofox-only apps/hermes-agent
docker image inspect --format '{{.Id}}' spartan-hermes:camofox-only
```

Use an approved clean downstream checkout for the final build: building a dirty
checkout can silently include unrelated patches. Record installed package
inventories and source manifests; any differences beyond browser payload and
variant guards require review. Keep measured candidate image size, physical
storage cost and comparison to the previously deployed image distinct.

## Explicit selection

`infra/compose/compose.hermes-camofox-only.yml` is optional and requires explicit
`HERMES_CAMOFOX_ONLY_BASE_IMAGE` and `HERMES_CAMOFOX_ONLY_IMAGE` references. Prefer
immutable local image IDs or registry digests. Include it after the base Compose
file and before private overrides; inspect the final render because later
overrides can change selection. For configuration validation only:

```sh
HERMES_CAMOFOX_ONLY_BASE_IMAGE=sha256:REVIEWED_UPSTREAM_VARIANT_ID \
HERMES_CAMOFOX_ONLY_IMAGE=sha256:REVIEWED_FINAL_VARIANT_ID \
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
