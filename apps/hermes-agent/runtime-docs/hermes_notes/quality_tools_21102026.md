# Quality tools — 21 October 2026

Status: applied to image source; build, recreation and live acceptance pending.

Hermes image source now installs Qlty 0.659.0 and Desloppify 1.0. Availability
requires a reviewed image rebuild and Hermes recreation, not a gateway restart.

- `qlty` on PATH permits only local `metrics`, `smells`, help and version.
  Telemetry is forced off, upgrade checks are skipped, and commands that can
  install plugins or require cloud services are rejected. The underlying binary
  is retained for provenance, not as a supported bypass.
- `desloppify` includes the full optional Python extras in a separate venv.
  Its packages do not replace Hermes runtime distributions. External analyzers
  and model runners are not added by this installation.
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
