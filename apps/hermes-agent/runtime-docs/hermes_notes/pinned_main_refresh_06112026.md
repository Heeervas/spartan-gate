# Pinned Hermes main refresh — applied

Status: exact-source Camofox-only image built, reviewed, deployed and verified.
The scoped interruption was explicitly authorized before activation.
This is an operator archive note, not a runtime-published guide.

The applied update fixes upstream at `90d6b98fd86d20981ee06ddd5b7d93e4320f26a4`.
The inspected moving registry image did not identify this commit, so an exact
source build was used. Do not overlay only new Python source onto a different
sealed package-manager runtime. Preserve the existing Camofox-only deployment;
no browser-server or neighboring service upgrade is part of this change.

Upstream now separates root-owned static s6 supervision from the unprivileged
dynamic gateway tree. The obsolete permission/registration workaround is
removed; configured numeric ownership still applies to the dynamic scanner,
reconciler, gateway/logger slots and root-issued control commands.

Camofox moved out of Hermes core. The image bakes the official plugin at the
catalog pin `1138f25c8b7b5a8fb882b3161e248f29abbd1a46/camofox` as a protected
bundled backend, with native explicit disables still authoritative. Existing
user plugins retain writable ownership and their own sources. Gate adds only
the missing durable routing, non-replayed stale actions, managed tab-only close,
viewport route and protected pinned vault page transport. Native origin/OTP
checks, script exception wrapping, redaction and per-platform migration remain
upstream-owned. Do not restore deleted browser core modules or introduce a
fallback browser.

The only demonstrated installed-plugin incompatibility was the governor's old
prompt anchor. Its canonical shared source now supports the current native
locked-skill contract while retaining read-before-write, external ownership,
staged-only learning, native memory routing and approval. Its focused native
checks and independent slice review passed. LCM's actual hook registration and
native post-turn dispatch were observed without changing its implementation.

Integrated source and governor reviews passed with notes. The built image runs
Python 3.14.9 and passed image-only dependency admission, all twelve selected
plugin registrations, actual LCM hook dispatch and the image plugin's PNG
normalization under the security-pinned Pillow 13. Its raw older plugin Pillow
constraint still warns during native discovery; no downgrade was introduced.
Disposable native s6 registration, numeric-UID execution, stop and unregister
passed while the static scanner remained root-owned. Boot testing found and
fixed a reconciler dependency on a prior hook's unexported shell variable. The
restart alias now recognizes the native dynamic service tree, preserving the
legacy layout only when that scanner is absent. Cache lock permissions are
sealed before the immutable-install audit.

The narrow build/runtime delta review passed with notes. The old recorded
generation referenced an interpreter absent from the new image; it was
re-resolved through explicit native PM management and passed strict managed
admission. Facts were not cleared or manually redirected. Obsolete local core
mounts were retired and the current image-pinned admission bundle was prepared
through the existing helper. Guarded main-stack controls activated Hermes alone,
with no build, pull or neighboring-service recreation.

Initial live boot refused an inherited root-owned reconciliation log. Only
`logs/container-boot.log` received an exact-file ownership repair to the configured
runtime owner, preserving its inode, contents and mode; no recursive permission
change was made. Subsequent startup is healthy. Native source/provenance and
Python 3.14.9, managed admission, installed plugin registration, Camofox health,
viewport/vault registration, disabled alternate browser execution, s6 ownership
and configured platform connectivity were read back. Persistent mounts,
security/network boundaries and all neighboring containers remained unchanged.
No live model inference, browser-tab operation or vault-secret operation was
needed for this readback. The codebase-memory connector and unavailable Opus
codec warnings also predated this update; no unrelated repair was bundled.

No session database was copied or backed up. Native automatic migration remains
the sole schema-migration route. Retain the old image and scoped configuration
recovery coordinates, but do not promise image rollback will reverse a schema
migration. Keep the existing 24-hour observation and seven-day rollback follow-ups
manual; no scheduler was added.
