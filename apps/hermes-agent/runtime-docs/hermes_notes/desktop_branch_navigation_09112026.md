# Desktop branch return/cancel — applied, interactive acceptance pending

Status: applied to the existing Hermes deployment. The operator owns the real
Desktop branch → return and branch → cancel walkthrough after recreation.
This is an operator archive note, not a runtime-published guide.

Desktop now routes `/return` and `/cancel` through the owning session authority,
with separate compatibility for legacy session dispatch. `/return` summarizes
only added branch work and imports one ordinary parent turn, enforcing a brief
no-tools acknowledgment before navigating to that parent. `/cancel` imports
nothing. Neither operation deletes the child history or undoes filesystem changes.
Existing branches without the required navigation metadata refuse; do not promise
retroactive return or infer the copied-message boundary from transcript length.

The focused automated checks used the actual Hermes image interpreter with
separate temporary profiles/databases and a synthetic provider. Desktop and
CLI/Discord native cases passed, as did the native-source patch suite, renderer
hook/owner tests, TypeScript, headless renderer build and existing TOP quality
gates. Independent source review found no blocking defects. These checks do not
certify real model-summary quality, live Discord delivery or an interactive
browser journey.

The exact existing upstream revision and Camofox-only base were retained. A
renderer build initially exposed Docker overlayfs's lower-layer directory rename
restriction. Only the disposable renderer stage now removes its inherited output
before the unchanged native publisher builds the replacement. Its regression and
the repaired image build passed; the live application payload was not patched.

Only Hermes was recreated through the main-stack lifecycle. Readback verified
healthy startup with zero additional restarts, installed helper/patch identities,
all 1,204 renderer file identities and the seven declared assets served over the
existing authenticated HTTPS edge. Native secure cookies and managed dependency
validation passed; the selected dependency generation remained unchanged.
Persistent mounts, security/network controls and all thirteen neighboring
containers retained their identities. The obsolete image-pinned dependency code
overlay was retired in favor of the same protected adaptation baked in the image;
managed validate-only behavior remains enabled, with no automatic install,
fallback or generation collection.

No live chat/model probe, session-database copy, credential change or second
gateway was used. Scoped private configuration beforeimages and the old image
remain available for operator recovery; no database/schema rollback is implied.
Refresh the Desktop page before testing with a newly created branch. Pending or
ambiguous returns still require history inspection, not blind replay.
