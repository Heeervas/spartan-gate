# Audit remediation and retired source — 22 October 2026

Status: source changes implemented and locally validated; runtime activation is
pending. This note does not assert that the running stack has changed.

The supported source now uses Camofox and Spartan Route. Browserless and the
legacy ClawRoute application, debugger and CDP-only operator workflows are
retired. Existing browser profiles and databases have not been deleted or
converted. Camofox Meet caption transcription remains; realtime/CDP audio is not
claimed as a replacement capability. Shared Route configuration names remain
compatible.

Route fixes require terminal completion before tool publication, fence delayed
credential outcomes and improve resource cleanup/policy isolation. Reader has
bounded inbound request handling; this does not make blocked outbound DNS work
cancellable. Doctor diagnostics use private scratch; proxy audit logs minimize
URLs. Native patch checks use fixed upstream source inputs.

Signed Caddy sessions now require a dedicated internal verifier, separate keys
and supported listener templates. Existing private overrides must be migrated
and validated before a separately authorized activation. Do not start, rebuild,
remove or migrate runtime services or profiles solely because this note exists.

Durable source plan: `docs/reviews/2026-10-22-remediation-plan.md`. Operator
commands and prerequisites are maintained in `.agents/validation.md` and public
operations documentation. No performance improvement is claimed from the new
file-backed benchmark; equivalent outputs were verified under a synthetic load.
