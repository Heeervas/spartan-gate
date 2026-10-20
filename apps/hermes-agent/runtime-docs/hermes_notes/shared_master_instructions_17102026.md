# Shared MASTER instructions in Hermes

Status: applied and independently checked on 2026-10-17; operator gateway restart and fresh-session acceptance remain pending.

The `shared-master` Hermes plugin loads the operator-maintained `/opt/data/MASTER.md`
into eligible profile prompts. Keep one shared source; do not copy common rules
into profile-local files. The plugin registers a native system-prompt section
and adds the full text at request time if that section is absent. It logs and
includes an advisory above 4,000 characters without truncating the instructions.
A missing or unreadable MASTER produces a visible diagnostic.

The plugin reads MASTER when it loads. After changing MASTER or plugin code,
the operator runs `sg-hermes restart` on the host, then starts a new session.
Existing stored prompts are retained; request fallback can supply the shared
instructions when absent, but an older session is not fresh-session acceptance.

Completion checks passed the focused plugin suite and isolated loading through
the installed Hermes PluginManager, including eligible and excluded profile
contexts, native section formatting, and request fallback. These checks made no
model calls and did not restart the running gateway. They do not establish that
the existing gateway has loaded the new code.

Profile creation requires the shared plugin link, enabled plugin key, and a
fresh-session prompt check, subject to the local exclusions. Follow the private
profile-creation authority and Hermes skills for installation details. The
private migration record contains verified safecopy paths and a guarded restore
helper; restore refuses later edits rather than silently overwriting them.
