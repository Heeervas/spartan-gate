# Hermes branch navigation (terminal CLI and Discord)

This native image patch adds conversational branches with a way back to their
immediate parent. It reuses Hermes sessions, transcript persistence, compression
lineage, terminal `/resume`, Discord thread creation and the gateway's FIFO queue.
It requires an image containing the patch; source tests do not activate a running
installation. TUI/desktop parity and remote agent proxies are not supported.

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
return/fresh/cancel navigation contract is terminal/Discord only.

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

Nested branches return to the conversation from which they were created, not the
outermost ancestor. Both parent and branch can compress normally. Creation-time
copied-message identities delimit inherited context; full native display lineage
retains branch work across compression without summarizing copied parent context
or replaying compression summaries as new work. Parent advancement is allowed.
A deliberately replaced parent conversation is refused at execution time, including
when a queued return bypasses ordinary ingress. The old conversation is never
silently rebound to the original thread.

## Failure and recovery

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
rechecks and child outcome notices. `patch_branch_navigation.py` integrates these
at the pinned native command, session, queue, turn and tool seams. It preflights
all anchors and compilation before writing any target and is idempotent.

The regression fixture executes pinned native SessionDB/session-store/command,
FIFO-recursion, turn-inner completion and approval-scoped methods with a synthetic
agent and Discord-shaped adapter. Terminal command methods are AST-loaded without
initializing the full Rich/UI application. Native auxiliary requests are intercepted
before provider execution; no model or real Discord messages are used. These are
source/native-collaborator checks, not a live end-to-end Discord or complete AIAgent
provider-wire certification. Full image readback, runtime health and bounded live
operator acceptance remain separate. See `.agents/validation.md` for exact source
checks and the existing independently reviewed build/activation gates.
