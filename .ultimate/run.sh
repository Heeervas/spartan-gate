#!/bin/sh
# Setup exports do not survive into later Ultimate tool calls.
set -eu
for path in .venv .venv/test; do
    [ ! -L "$path" ] || {
        printf '%s\n' "Refusing shared/symlinked $path; see .ultimate/README.md." >&2
        exit 1
    }
done
[ -x .venv/test/bin/python ] && [ -x .venv/test/bin/node ] || {
    printf '%s\n' 'Run the worktree setup first; see .ultimate/README.md.' >&2
    exit 1
}
export PATH="$PWD/.venv/test/bin:$PATH"
exec "$@"
