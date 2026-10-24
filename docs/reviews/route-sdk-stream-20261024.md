# SDK 7.19 accumulator metadata follow-up — 2026-10-24

## Accepted source

Independent reviewer PASS and parent acceptance covered the exact eight-file
patch SHA-256 `a5f2a657de9060d58d185c1838ed2a9f2473ed9e2a453c1f803746f2c4e1d9aa`.
Source commit: `bb4949c5be29c8bdda0a8f3ce369914a8e5283be`. It was fast-forwarded
into the original repository while preserving unrelated dirty bytes and staging.

The fix excludes only the exact proprietary `response.metadata` event from the
Codex public-SSE encoder, after raw telemetry observation. Other events, unknowns,
terminal errors/usage, tools and replay/account policy retain their existing
behavior. No SDK/Pi patch, dependency upgrade or replay normalizer was added to
production. The original RED reproduced the real SDK accumulator's unsupported-
event error, with JSON/create-iterator controls passing.

Focused source evidence: 52 Route tests and 25 SDK/native-provider tests on each
real Pi 1.3/1.4 dependency input, no skips/cancellations. TOP passes with the same
seven pre-existing approved exceptions, immutable baseline and stronger frozen
policy. The source suite uses synthetic upstream HTTP; it does not enforce the
strict upstream input schema and restarts Route within one OS process. Its green
helper replay is not live/provider or fresh-server-process evidence.

## Authorized Route-only activation

The existing main-stack `sg` helpers selected the unchanged private configuration.
Only Route was built and recreated after an isolated nonroot, read-only,
no-network/no-production-data import/metadata smoke passed. No Pi install or
neighbor rebuild/recreation ran. Disk space stayed above the ordinary 5-GB floor.

- Deployed image: `sha256:6c1b624cdc17eb94c44fb9d960e8d92b156aac57952c576e7d3019652df304c6`.
- Route container: `a96b3adb8f00413c79b477c72eea756097d520e31b7d897aa60e4f420b6bdf3d`.
- Docker healthy; `/health` returns 200; unauthenticated `POST /v1/responses`
  returns 401.
- All 26 source module hashes match the accepted integrated source. Runtime
  environment, signing secret, mounts and network membership are unchanged.
- All nine neighboring IDs, image IDs and start times are unchanged.

## Bounded actual-provider result — partial

The finite follow-up authorized at most four physical upstream attempts, including
failures/retries. SDK retries were disabled; each submitted request reserved the
Route ceiling of three physical attempts before admission. Both requests used an
allowed Sol model, requested 4096 output tokens and used only an inert local
integer-verification tool. Codex does not enforce that requested token limit.

1. Unmodified SDK `responses.stream().finalResponse()` completed the first tool
   call with nonempty opaque reasoning and a matching inert function invocation.
   The unknown-event accumulator defect did not occur. One physical attempt,
   usage observed.
2. A fresh SDK process loaded the retained history/tool result and submitted the
   encrypted continuation. Upstream rejected it with `unknown_parameter` on one
   physical attempt, with no usage observed. No success or resumed output is
   claimed.

The accumulator adds SDK-only `parsed_arguments` to function output; the installed
SDK's documented `toResponseInputItems()` helper removes it while preserving
opaque reasoning and call IDs, as verified with synthetic data. That is a
source-supported explanation for the rejection, **not confirmation of the exact
upstream rejected parameter**, which was not retained. No live ciphertext or
account/credential values are included in this report.

The canary stopped at **two physical attempts of four**, no outstanding
reservation. Remaining capacity cannot safely admit another worst-case
three-attempt Route request. No extra call, retry-policy change or speculative
production patch was made. Full live helper tool/encrypted-continuation/resume
acceptance remains incomplete. A separately authorized bounded check should use
the official SDK history normalizer; the unrelated intermittent incident remains
outside this repair.

## Authorized normalized continuation follow-up

The parent authorized one additional request with a three-attempt reservation,
raising the total physical-attempt cap to five. A fresh SDK process used the
SDK's documented `toResponseInputItems()` to prepare the saved encrypted history
and inert tool result. `finalResponse()` returned without an unknown-event or
API error. Route recorded HTTP 200, terminal completion and usage on one physical
attempt, with no routing error. Total usage was **three physical attempts of
five**; the canary is closed and no reservation remains.

A subsequent local assertion failed without retaining its exact assertion
identity. Therefore successful SDK/provider completion is evidenced, but the
canary's complete content and new-opaque-output postconditions are not claimed.
Zero new reasoning tokens were recorded. No additional production change or
rebuild was needed. Private evidence includes `normalized-completion-accounting.json`
and `live-normalized-resume.log`. The original metadata-accumulator defect is
fixed and live-verified; the unrelated intermittent incident remains undiagnosed.

## Evidence and rollback

Private receipt owner: `private/maintenance/route-sdk-stream-20261024/`. It retains
the exact reviewed source diff/snapshot and quality inputs, raw source checks,
source integration, build/image canary, activation/readback, finite live budget,
sanitized event/shape/error-category evidence and the private canary state.

Prior image retained as `spartan-gate-spartan-route:pre-sdk-stream-20261024`,
immutable ID `sha256:96bf7faa073f52138a4c43dcc8b0d02e68bf3474be99c260cf49b076e0fb2464`.
`ROLLBACK.md` in the private receipt owner identifies the exact configured image
reference and Route-only restoration command. This rollback image supports the
existing receipt protocol but restores the accumulator metadata limitation. No
keys, databases, sessions or volumes need restoration. No push occurred.
