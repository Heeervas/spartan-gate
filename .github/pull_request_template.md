## Summary

-

## Validation

Use the isolated prerequisites and gate matrix in `docs/development.md`.
Report exact results and skips, not just checked boxes.

- [ ] `npm run test:route` (Python API and JS dashboard)
- [ ] `npm run test:hermes`
- [ ] `npm run test:pi` (provisioned pinned Pi loader)
- [ ] `npm run test:camofox`
- [ ] `npm run test:scripts` (includes public Compose matrix)
- [ ] `npm run test:reader`
- [ ] Mandatory pristine-source lanes: `npm run test:hermes:native` and `npm run test:camofox:native`, or explicit INCOMPLETE with missing inputs
- [ ] Applicable TOP assurance evidence and independent review (external measurement package must be explicitly provisioned; not enforced by portable CI)
- [ ] `docker compose -f infra/compose/compose.yml config --quiet`
- [ ] `git diff --check`

Doctor's confidentiality regressions run with synthetic fixtures. Run real
`scripts/doctor.sh` only in an explicitly authorized local operator environment;
it reads local configuration and Docker metadata.

## Security Checklist

- [ ] No secrets, private host paths, browser profiles, runtime data, or local domains were committed.
- [ ] Public edge bindings remain localhost-only.
- [ ] Hermes remains off the external network in gated tiers.
- [ ] Invalid or missing policy/auth remains fail closed.
- [ ] No image build, deployment or runtime mutation is implied by these checks.
