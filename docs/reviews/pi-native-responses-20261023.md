# Pi native Responses review (2026-10-23)

Base: `91faa84`. Scope: the versioned Pi Route provider, authenticated Pi
reasoning bindings, catalog protocol/effort metadata and native Pi attempt
accounting. Existing Hermes transport and signing validation remain authoritative.

An independent reviewer inspected the server authentication, Pi-specific HMAC
namespace, durable realm locking, request-local replay provenance, private-field
stripping, native Pi dispatch, cancellation, session resume/fork boundaries and
attempt cleanup. Verdict: PASS_WITH_RESIDUAL_RISK for source, subject to the
quality gate and separate installed/deployed acceptance.

Review found that native Pi implicitly sends reasoning effort `none` when
thinking is off. Some Codex models do not advertise that effort. A real native
wire regression now verifies its omission for those models, and preservation
where supported. Catalog variants inherit base-model efforts; auto aliases use
the intersection of all active choices. The reviewer accepted these fixes.

Validation: Route passed 483 Python tests and 41 JavaScript tests. Pi passed its
four Python gateway tests and all three JavaScript test files, including ten
new native installed-Pi tests. These exercise actual normalization, opaque
reasoning persistence, tools, save/resume, fork/profile reset, concurrency,
fail-closed binding, cancellation, dynamic discovery and the real loader.
TOP assurance returned QUALITY_PASS_WITH_EXCEPTION with no evidence gaps.

The independent reviewer approved two narrowly scoped exceptions on
`execute_codex_responses`: cyclomatic complexity maximum 75 and function logical
lines maximum 250, expiring 2026-11-20. The existing function owns admission,
retry and stream cleanup; the observer must share that lifecycle. PiAttempt
retains storage and idempotence. Broader transport extraction would enlarge this
protocol change. The immutable baseline remains
`75651cc0c5baa1db5a6523b790564988698f2a43`; the five previous exceptions and all
other limits remain unchanged. This is measured debt, not repository-wide or
deployment acceptance.

Installed and deployed acceptance passed after source review. The candidate
Route image preserves the deployed base and changes only four owning Python
modules; isolated read-only/network-disabled import acceptance passed. The
running image is healthy and its four source hashes match the reviewed source.
The installed Pi entry and three helper files also match their owning source.
Only Route was recreated; all nine other stack containers retained their IDs
and remained healthy. Previous image and original installed/config bytes are
retained outside extension discovery for rollback.

A real Pi 1.3.0 RPC canary used six synthetic calls through the authenticated
gateway. Native streaming, one read-tool call, continuation and process restart
passed. The low-effort marker prompts returned zero reasoning tokens; one
high-effort counting turn and another resumed continuation additionally proved
encrypted replay. The saved session contains eight opaque reasoning items with
one signed chain. All six assistant calls completed without error; the final
resumed reply matched its predecessor. Route recorded six attempts, all with
usage, and no incomplete or missing-usage attempts. Acceptance observes
`agent_settled`, not just prompt acknowledgments. Private receipts are retained
in the task's dated admin directory. Existing Pi processes require a restart;
no session migration is necessary. No push occurred.

## Named subagent regression correction

The initial live acceptance covered ordinary UUID sessions and missed mjakl's
named IDs (`subagent.<16 hex>`). Real named calls failed locally with
`Route session binding unavailable` before provider submission. Usage also
assumed UUIDs and could not register those children. Source fix `adc8e6b` adds
one shared, versioned native-ID-to-UUIDv8 mapping for binding and usage, preserving
existing UUIDs, native session files and cache identities. Invalid IDs still fail
closed; server authentication, signing and realm contracts remain unchanged.
Usage keeps raw and canonical identities separate across headers, settled
updates, resume and disposal.

The independent reviewer approved the source and tests. Named save/reopen and
usage registration failed before the fix and pass afterward. The focused suites
pass 11 native provider, 20 usage and three identity tests, alongside the full Pi
command (four Python tests and four JavaScript files). TOP still returns
QUALITY_PASS_WITH_EXCEPTION with no gaps and no new exceptions.

Installed acceptance used the actual mjakl `subagent` tool with an isolated
read-only builder fixture and the reported model
`spartan-route/codex/gpt-6.1-sol-282kcontext`. The installed failure was reproduced,
then the same named session was successfully retried and resumed after updating
the three Pi helper modules. The parent process stayed alive across installation.
A separate named child and an ephemeral child also completed: four real provider
calls total. The two persisted named sessions retain their original IDs; their
separate Route usage ledgers record two and one successful attempts respectively,
with no missing or incomplete usage. Encrypted named replay is covered by the
native synthetic save/reopen regression; these simple live prompts establish
the actual named launch, binding, continuation and accounting path.

Route required no change or restart. Existing child sessions and unrelated work
were preserved. Subsequent child launches read the new helpers, so retrying the
same calls requires no parent restart. Previous helper bytes are retained outside
discovery; private receipts are in the dated subagent-binding admin directory.
