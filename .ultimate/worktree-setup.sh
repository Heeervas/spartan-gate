#!/bin/sh
# Source-only preparation: never read or copy the source checkout's local data.
set -eu

fail() { printf '%s\n' "Worktree setup: $*" >&2; exit 1; }

# Finish local preflight before creating or installing anything.
: "${ULTIMATE_SOURCE:?Ultimate must provide the source checkout path}"
source_root=$(cd "$ULTIMATE_SOURCE" && pwd -P) || fail 'source checkout path is unavailable'
[ "$(pwd -P)" != "$source_root" ] || fail 'run in a worktree, not the source checkout'
[ "$(git rev-parse --show-toplevel)" = "$(pwd -P)" ] || fail 'run at the worktree root'
[ -f package-lock.json ] && [ -f infra/spartan-route/requirements.txt ] && [ -f .agents/test-requirements.txt ] || fail 'missing tracked dependency inputs'
for path in .agents .venv .venv/test node_modules; do
    [ ! -L "$path" ] || fail "refusing shared/symlinked $path"
done
node22=$(command -v node-22 || command -v node || true)
[ -n "$node22" ] || fail 'install Node 22 before preparing worktrees'
[ "$("$node22" -p 'process.versions.node.split(".")[0]')" = 22 ] || fail 'Node 22 is required (node-22 or node on PATH)'
command -v npm >/dev/null || fail 'npm is required'
python3 -c 'import sys, venv, ensurepip; assert sys.version_info[:2] == (3, 14), "Python 3.14 is required"'
if [ -e .venv/test ]; then
    [ -x .venv/test/bin/python ] || fail 'existing test environment is incomplete; remove it explicitly before retrying'
    .venv/test/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 14), "existing test environment must use Python 3.14"'
fi

# Each checkout owns its writable dependencies. Only the Node executable is linked.
if [ ! -d .venv/test ]; then
    python3 -m venv .venv/test
fi
ln -sf "$node22" .venv/test/bin/node
export PATH="$PWD/.venv/test/bin:$PATH"
npm ci --ignore-scripts --no-audit --no-fund
python -m pip install --disable-pip-version-check -r .agents/test-requirements.txt
printf '%s\n' 'Source-only setup ready. Use sh .ultimate/run.sh <command> for Node 22 and isolated Python.'
