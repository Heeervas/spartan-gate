# Ultimate in Spartan Gate

Start `ultimate` in this repository. When onboarding asks whether to use the
project's `.ultimate/` configuration, only the operator may approve it.

## Source-only worktrees

Ultimate prepares new worktrees with `worktree-setup.sh`: Node 22, locked root
npm dependencies (install scripts disabled), and a checkout-local Python 3.14
venv using the tracked `.agents/test-requirements.txt` pins (including Route's
requirements). Provision Node 22
(`node-22`, or `node` on PATH), npm and Python 3.14 with venv/ensurepip first.
Package registry access or populated package caches are needed. Setup is safe
to repeat and refuses shared dependency-directory symlinks. It never copies
`.env`, private configuration, databases, credentials or browser profiles from
`ULTIMATE_SOURCE`, and does not start the stack or call model providers.

Setup's environment does not persist into tool calls. From the worktree root,
run commands through the wrapper to select Node 22 and isolated Python:

```sh
sh .ultimate/run.sh npm run test:route
sh .ultimate/run.sh python3 -m unittest discover -s infra/reader -p 'test_*.py'
```

For a manual retry in a worktree, set `ULTIMATE_SOURCE` to the original checkout
and run `sh -e .ultimate/worktree-setup.sh`. Dependencies live in ignored
`node_modules/` and `.agents/test-venv/`, not shared writable source directories.
No automatic checks, push or deployment are configured on landing.

## Explicit remaining inputs

This is minimal host setup, not full native acceptance. Pi's real package,
pristine pinned Hermes/Camofox sources and their native dependencies, Caddy and
the external quality package must be provisioned explicitly when needed. Do not
discover them in private profiles, treat skips as acceptance, or copy ignored
operator-local `.agents/` files into worktrees automatically. Follow
`docs/development.md` and `.agents/validation.md`; the tracked requirements file
is the dependency authority if the prose setup examples lag its pins.
Model selection stays in personal Ultimate settings, outside this public setup.
