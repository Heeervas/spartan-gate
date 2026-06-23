# Contributing To ClawRoute

ClawRoute is maintained inside Spartan Gate. Use the root
[CONTRIBUTING.md](../../CONTRIBUTING.md) and [SECURITY.md](../../SECURITY.md)
for project-wide process and disclosure rules.

## Local Development

```sh
git clone https://github.com/Heeervas/spartan-gate.git
cd spartan-gate
npm --prefix packages/clawroute ci
npm run clawroute:build
npm run clawroute:test
```

Keep changes compatible with the OpenAI-compatible proxy surface and add focused
tests for routing, executor, configuration, or dashboard behavior changes.
