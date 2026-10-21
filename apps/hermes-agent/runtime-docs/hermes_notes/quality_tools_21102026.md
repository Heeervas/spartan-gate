# Quality tools — 21 October 2026

Status: applied to image source; build, recreation and live acceptance pending.

Hermes image source now installs Qlty 0.659.0 and Desloppify 1.0. Availability
requires a reviewed image rebuild and Hermes recreation, not a gateway restart.

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
  global Hermes configuration and distributions are unchanged. Other external
  analyzers and model runners are not added.
- Qlty is advisory supporting evidence. Desloppify deep audits require explicit
  scope and the shared manual prepare/import method; there is no native
  Pi/Hermes runner integration or trusted-score claim.
- Existing skill/profile disables, memberships, security policy, browser mode,
  resource limits and native quality gates are unchanged. Do not treat scanner
  instructions as authorization for unrelated remediation or policy changes.
- Desloppify 1.0 is copyright Peter O'Malley, OSNL-0.2. Its license remains in
  wheel metadata and `/usr/local/share/licenses/desloppify/LICENSE`. Review
  redistribution terms before publishing images or third-party services.

Pending acceptance: check versions, actual runtime UIDs, ordinary/login shells,
root ownership and non-writability, isolated full-extra imports and a disposable
local analysis fixture. See the optional quality CLI section in operations docs.
