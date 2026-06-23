## Summary

- 

## Validation

- [ ] `npm run clawroute:build`
- [ ] `npm run clawroute:test`
- [ ] `python3 -m unittest discover -s apps/hermes-agent -p "test_*.py"`
- [ ] `node --test apps/hermes-agent/test_*.js`
- [ ] `docker compose -f infra/compose/compose.yml config --quiet`
- [ ] `scripts/doctor.sh`

## Security Checklist

- [ ] No secrets, private host paths, browser profiles, runtime data, or local domains were committed.
- [ ] Public edge bindings remain localhost-only.
- [ ] Hermes remains off the external network.
- [ ] Invalid or missing policy/auth data fails closed where relevant.
