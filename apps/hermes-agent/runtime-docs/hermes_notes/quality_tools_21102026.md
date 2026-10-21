# Quality tools — 21 October 2026

Status: applied, rebuilt, recreated and live-verified on 21 October 2026.

The running Hermes image contains Qlty 0.659.0 and Desloppify 1.0. Native Pi
Builder prepared the source; independent Pi Reviewer found three integration
defects, which Builder corrected and Reviewer rechecked before the build.

- `qlty` on PATH preserves native CLI syntax and global-option placement.
  Telemetry is forced off and upgrade checks are skipped without duplicating
  an explicit flag. This is not a command-security sandbox: plugin installation,
  cloud operations and initialization still need separate authorization. For an
  authorized disposable fixture or adoption, minimal init is supported:
  `qlty init --yes --skip-plugins --skip-default-source --no-upgrade-check`.
- `desloppify` includes full optional Python extras in a separate venv. Its
  launcher exposes its own pinned analyzers (including Bandit) only within the
  process, restricts PYTHONPATH to root-owned policy bootstrap, and enables safe
  startup imports. Mutable Hermes extra-site/project paths cannot shadow pinned
  dependencies through inherited PYTHONPATH. Proxy/environment behavior remains;
  the launcher changes only its own process environment and installs tool
  packages outside Hermes' venv. Other external analyzers and model runners are
  not added.
- Qlty is advisory supporting evidence. Desloppify deep audits require explicit
  scope and the shared manual prepare/import method; there is no native
  Pi/Hermes runner integration or trusted-score claim.
- Existing skill/profile disables, memberships, security policy, browser mode,
  resource limits and native quality gates are unchanged. Do not treat scanner
  instructions as authorization for unrelated remediation or policy changes.
- Desloppify 1.0 is copyright Peter O'Malley, OSNL-0.2. Its license remains in
  wheel metadata and `/usr/local/share/licenses/desloppify/LICENSE`. Review
  redistribution terms before publishing images or third-party services.

Acceptance passed: no-network/read-only-root image canary, actual runtime UID/GID,
ordinary/login shells, ownership/non-writability, hostile-import resistance,
Qlty initialization/metrics/SARIF, actual Bandit B602 detection and Desloppify
review preparation. The live container repeated the bounded local-analysis
canary and is healthy. Default/Builder/Reviewer inventories, disables and native
resolution remain unchanged; CLI installation does not enroll new profile skills.

Optional `jscpd` is not installed. Desloppify's full scan can attempt an npx
fallback for duplication detection; this is not permission to download it. Use
an explicitly bounded `scan --path . --skip-slow --no-badge` to omit slow
duplication checks while retaining Python security checks, or separately approve
and provision the missing analyzer. Do not report omitted coverage as clean.

Only Hermes changed container identity during this cutover. Existing mounts,
networks, resource limits, read-only root and Camofox-only mode were preserved.
The prior image and private recovery receipts were retained. No push occurred.
