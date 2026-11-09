# Hermes branch navigation (terminal CLI, Desktop and Discord)

This native image patch adds conversational branches with a way back to their
immediate parent. It reuses Hermes sessions, transcript persistence, compression
lineage, terminal `/resume`, Discord thread creation and the gateway's FIFO queue.
It requires an image containing the patch; source tests do not activate a running
installation. Desktop additionally requires the rebuilt native webapp renderer in
that same image; a backend-only patch leaves the served UI stale. Other TUI
surfaces and remote agent proxies are not covered by this navigation contract.

## Commands

| Command | Effect |
| --- | --- |
| `/branch [--here] [name]`, `/start-branch [--here] [name]` | Enter a copied child conversation in the terminal; on Discord prefer a new sibling thread, leaving the original conversation live. Native `/fork`, `--here`, DM and thread-failure in-place behavior remain supported. |
| `/start-fresh [name]` | Open a linked child without inherited conversational messages. System instructions, configured memory, skills and permissions are not erased. |
| `/return` | Summarize only work added in this branch, send one synthetic summary turn to its immediate parent, and request a brief acknowledgment with tools disabled for that turn. |
| `/cancel` | Import nothing. Terminal restores the immediate parent; Discord retains both threads. This does **not** undo tool effects or filesystem changes. |

Discord **navigation** requires a separate child thread in a server channel with
thread permissions. Native `/branch`, `/fork` and the `/start-branch` alias still
switch the current conversation to an ordinary in-place child with `--here`, in
DMs, or when thread creation fails. Those in-place children do not acquire
navigation metadata: `/return` and navigation `/cancel` refuse there; use native
`/resume` to switch back. `/start-fresh` refuses if it cannot open a separate
Discord child thread. Other platforms retain native `/branch` behavior; the new
fresh navigation contract is terminal/Discord only. Desktop uses its existing
`/branch` (also `/fork` and `/start-branch`) or branch controls, then `/return` or
`/cancel` without arguments. Desktop `/start-fresh` is not supported.

## Typical journeys

**Terminal:** discuss the main task, run `/start-branch investigate`, explore an
alternative, then `/return`. Hermes restores the parent using native `/resume`
(including its cwd, model and approval state), imports the summary once and
acknowledges it. A following ordinary parent turn retains normal tools.
Use `/cancel` instead to restore the parent without importing a summary.

**Discord:** run `/start-branch investigate` in conversation A. Continue work in
its new thread B; A remains independently usable. `/return` in B first posts a
queued notice there. Hermes sends the summary to A without a human follow-up;
if A is busy, the summary waits in its native FIFO rather than interrupting it.
B receives a completion or failure notice from that acknowledgment's own outcome,
not from an unrelated parent turn. Completion means the acknowledgment generated
successfully, not confirmed delivery of its final Discord bubble. Failure to send
the child completion notice is recorded separately in navigation metadata.

**Desktop:** branch using `/branch` or the native branch control, do child work,
then `/return`. The child's owning connection and profile handle the request;
the server summarizes only added child work and admits one normal parent turn.
Only a successful acknowledgment instructs the UI to resume the immediate
stored parent. `/cancel` navigates without summary/import. A refused request,
summary failure or failed acknowledgment stays in the child with an error;
there is no fallback through the CLI slash worker. Switching views while a
return is pending does not let its late response steal the new view.

Desktop's authenticated session authority is the primary path. The legacy
sidecar has a separate compatibility adapter using its native lookup, resume and
prompt execution, offloaded through the existing bounded long-handler pool so
summary/acknowledgment waits do not block its serial RPC reader. An idle child
and idle parent are required; queued, running,
unknown or conflicting ownership is refused, not interrupted. Actual parent
profile/principal checks, subscriptions, admission and native execution fences
remain in force. Legacy isolated compute workers refuse this operation rather
than silently losing the acknowledgment restriction. Authority managed workers
carry the trusted restriction in the existing per-turn bootstrap and restore
ordinary tools afterward.

Nested branches return to the conversation from which they were created, not the
outermost ancestor. Both parent and branch can compress normally. Creation-time
copied-message identities delimit inherited context; full native display lineage
retains branch work across compression without summarizing copied parent context
or replaying compression summaries as new work. Parent advancement is allowed.
A deliberately replaced parent conversation is refused at execution time, including
when a queued return bypasses ordinary ingress. The old conversation is never
silently rebound to the original thread.

## Failure and recovery

- Old Desktop branches without creation-time navigation metadata refuse; no
  guessed watermark, retroactive transcript copy or implicit migration is made.
  Creation records the actual copied message identities after successful seed
  persistence (atomically with canonical branch commit). Lazy legacy fallback
  records the seed before accepting the first added prompt; a failed seed blocks
  that prompt rather than marking new work as inherited.
- Summary failure, including truncated/empty output, imports nothing and leaves
  the branch open and usable. Retry `/return` after correcting the provider issue.
- Ordinary repeated `/return` while preparing, queued, running or completed does
  not import again. Return state lives in the original child session's namespaced
  `model_config`, merged without replacing unrelated native metadata.
- Pending state survives restart, but automatic replay is deliberately absent.
  Interrupted or ambiguous effects require inspection of both histories before
  manual recovery. This is **not** crash-safe exactly-once delivery. Do not reset
  the navigation status blindly or assume a queued item survived a gateway restart.
- Failed/rejected acknowledgment retains history and visible status; it is not
  automatically retried. After cancellation or return the branch history remains
  available through native session listing/resume; no thread or transcript is deleted.
- The summary is reported context, not proof that all branch assertions are true.
  The parent acknowledgment must not continue the task. Tool definitions are empty,
  native pin/MCP restoration cannot re-enable them, model-emitted tool dispatch is
  denied, and the cached tools and durable normal pin remain available afterward.

## Implementation and verification boundary

`spartan_branch_navigation.py` owns navigation metadata, branch-only native
auxiliary summaries, terminal restoration and the temporary acknowledgment
restriction. `spartan_branch_gateway.py` owns Discord injection, parent identity
rechecks and child outcome notices. `spartan_branch_desktop.py` adapts the same
protocol to the authenticated authority and, separately, the legacy sidecar.
`patch_branch_navigation.py` integrates these at the pinned native command,
session, queue, turn, worker-bootstrap, tool and renderer-action seams. It
preflights all anchors and Python compilation before writing any target and is
idempotent; real native renderer tests, TypeScript checking and the frozen
headless build validate the TypeScript edits. The Dockerfile's disposable renderer
stage copies only rebuilt `dist-webapp` into the final image; it downloads no
Electron or browser binary.

The regression fixture executes pinned native SessionDB/session-store/command,
FIFO-recursion, turn-inner completion and approval-scoped methods with a synthetic
agent and Discord-shaped adapter. Terminal command methods are AST-loaded without
initializing the full Rich/UI application. Native auxiliary requests are intercepted
before provider execution; no model or real Discord messages are used. These are
source/native-collaborator checks, not a live end-to-end Discord or complete AIAgent
provider-wire certification. Desktop checks additionally exercise native branch
mutation/receipts, durable admission, execution scope, actual TurnRunner and
legacy invocation, seed fallback/refusal and managed-bootstrap producer/consumer
with a synthetic provider. The legacy journey substitutes application/worker
assembly, not branch persistence or the invocation guard. Real renderer hooks
and the real session router run over a synthetic gateway transport: they prove
owner-bound action dispatch, parent resume intent, failure retention and late
view-switch behavior, not an authenticated browser E2E. The managed-worker check
round-trips native bootstrap producers/consumers but does not launch a worker
process. Full image readback, runtime health and bounded live
operator acceptance remain separate. See `.agents/validation.md` for exact source
checks and the existing independently reviewed build/activation gates.
