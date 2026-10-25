#!/bin/sh
# Setup exports do not survive into later Ultimate tool calls.
set -eu
[ -x .agents/test-venv/bin/python ] && [ -x .agents/test-venv/bin/node ] || {
    printf '%s\n' 'Run the worktree setup first; see .ultimate/README.md.' >&2
    exit 1
}
export PATH="$PWD/.agents/test-venv/bin:$PATH"
exec "$@"
