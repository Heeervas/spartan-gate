# Reader Markdown and bounded Pi admission — 30 October 2026

Status: applied and live-verified for Reader, the necessary tool-access gateway
and installed host Pi helpers. Already-running Pi processes retain imported
helper code until restarted; a fresh installed-client process passed live checks.

Reader keeps its checked public GET transport, `/fetch` plain UTF-8 responses,
complete untrusted markers and trust header. The candidate negotiates publisher
Markdown, preserves native source/Markdown/XML whitespace and extracts static
HTML locally with pinned Trafilatura. It retains headings, links, tables and
preformatted code. GitHub embedded file lines and repository listing links are
supported without guessing slash-containing branch/ref boundaries.

Extraction uses one bounded child at a time, with a three-second budget inside
the original fetch deadline. Timeout kills and reaps the child before release;
late extraction cannot publish success. No JavaScript, external-resource loading,
third-party extraction API, browser fallback or egress-policy expansion is added.
Recognized empty/JS-only shells fail explicitly as `needs_render`; this remains
heuristic static extraction, not a bot-wall or all-sites solution.

The Pi Reader client queues bounded bursts: two active, sixteen pending, with a
30-second absolute deadline from enqueue through body cleanup. Queued stop/abort
prevents dispatch; overflow is explicit. Separate processes and browser/search
operations can still encounter authoritative gateway capacity limits. No
operation is automatically retried. Remote HTTP 429 is a remote error, never
misreported as local admission capacity. Remote bodies/exceptions are not exposed.

Hermes's unchanged direct Reader provider remains compatible. Output truncation
is identified in response headers; not every existing consumer displays that
metadata. Code/source is exact only within the declared output limit and the
representation actually supplied by the publisher.

Live acceptance: six simultaneous installed-client reads through authenticated
managed ingress completed successfully in about two seconds, including the
original GitHub source and directory cases. The source contents and indentation
match the raw endpoint; GitHub's embedded line representation omits the terminal
newline, so byte-identical original files should use the raw endpoint. Missing
files return explicit upstream HTTP 404 errors; unauthenticated ingress is denied.
Reader/gateway installed source identities were read back. Only those two
containers changed; all neighboring container identities/start times were retained.
Hermes compatibility was verified with its unchanged consumer fixtures, not a
new native Hermes startup. No browser, edge, Hermes or Route recreation occurred.

Previous Reader/gateway images and matched helper/configuration recovery bytes
are retained privately. Roll back only those affected versions after checking
for later edits; never reset profiles, expand policy or delete volumes. Restart
an already-open Pi process to load the new queue/error helpers. See
`docs/reader.md` and `docs/shared-tool-access.md` for the public contract and
`.agents/validation.md` for source gates.
