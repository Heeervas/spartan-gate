# Shared MASTER instructions in Hermes

The `shared-master` Hermes plugin transports the operator-maintained `MASTER.md`
from Hermes' native default root (`/opt/data/MASTER.md` in the standard container)
into eligible profile requests. The native root helper also handles custom roots
and active named profiles; profile-local copies are not selected. Keep one common
source. Native project/gateway `AGENTS.md` loading remains separate and unchanged.

The native system-prompt section retains its 4,000-character cap. Larger common
instructions travel complete through request middleware, without truncation or
a size advisory. A missing, empty or unreadable source produces a diagnostic.
The source is snapshotted at plugin registration; edits require plugin/gateway
reload. Follow the operator's authorized reload procedure, then verify a fresh
session. Source tests alone do not establish that a running gateway adopted it.

At request time, valid length-framed owned native sections and middleware
policy envelopes are reconciled to one current common policy. Only trusted
instruction fields are rewritten: top-level instructions/system, system/developer
messages, and system/developer Responses input items. User/tool evidence and
function outputs are preserved, as are foreign plugin frames and nontext blocks.
Owned-only text blocks/items removed during reconciliation leave no invalid empty
placeholder; retained blocks keep their metadata. Persisted session prompts are
never edited.

An exact whole trusted text body can satisfy common-policy inclusion. A substring,
quote or label such as `Project instructions:` is not evidence of inclusion: that
prose stays intact and an explicit owned envelope supplies the common policy.
Retries do not add another envelope. Malformed frames and unowned old fallback
text are not guessed away; a fresh session may be needed for those older prompts.
Unsupported request shapes log a delivery gap and remain unchanged. Middleware is
not a security sandbox or proof of final payload composition with other plugins.

Profile creation requires the shared plugin link, enabled plugin key, and a
fresh-session prompt check, subject to the local exclusions. Follow the private
profile-creation authority and Hermes skills for installation details. No common
policy contents belong in public docs or profile-local copies. Registration and
synthetic transport tests make no model calls and do not authorize activation.
