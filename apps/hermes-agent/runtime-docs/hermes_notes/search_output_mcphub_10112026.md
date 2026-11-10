# Compact MCP search output — 10 November 2026

**Applied correction.** The `spartan-web-tools` backend now enforces `num_results`
locally instead of relying on SearXNG's query parameter. It returns only title,
URL and snippet, in source order, with explicit count, omission, truncation and
partial-engine metadata. The complete untrusted-content frame is limited to
16 KiB. Long URLs are omitted rather than cut; title/snippet UTF-8 limits preserve
valid text. Reader and browser/native integrations are unchanged.

Diagnosis reproduced 30 results and 23,863 bytes of raw search JSON despite a
request for two. The same acceptance query after deployment returned exactly two
compact citations in 760 bytes including the trust frame, with no warnings or
truncation. The tools were also exercised directly from the reconnected native
agent conversation; public Reader success and private-URL refusal still passed.

The protected operator binding selects Bing, DuckDuckGo, Google CSE and Wikipedia
for this backend, after an explicit comparison returned no engine failures. It
no longer queries the observed blocked/rate-limited Google/Brave defaults on this
path. This does not repair or bypass those upstream restrictions, and selected
engines can fail later. Normalized warnings remain visible; complete engine
failure returns an MCP error rather than an apparently successful empty search.
Shared SearXNG defaults and other consumers were not changed.

Only this MCP backend was reloaded. No Docker container, browser workflow, token,
permission, network policy or shared search configuration was restarted/changed.
All three client catalogs remain at 103 tools. Existing request/body deadlines,
admission bounds and Reader checks are preserved. Twelve SDK/stdio regressions,
30 existing REST/browser tests and the TOP quality gate passed; independent source
review accepted the exact deployed bridge.

The generic contract and validation remain in `docs/mcphub.md`. Scoped acceptance
and recovery owner: [task record](../../../../../tasks/archive/2026/web-search-output/task.md).
This is an operator archive note, not a newly published runtime guide.
