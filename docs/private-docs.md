# Private Documentation

Spartan Gate splits documentation by audience.

## Public Docs

Files under `docs/` are public. They should be safe to publish and share with another operator.

Public docs may include:

- architecture
- setup flows
- generic cutover steps
- generic profile rules
- public env variable names
- validation commands
- redacted examples

Public docs must not include:

- real host paths
- exact private ports
- Tailscale machine names or real Tailscale IPs
- real token ownership notes
- private profile inventories
- migration notes tied to a specific old stack
- local operational decisions that only apply to one machine

## Private Docs

Files under `private/docs/` are local runbooks. They are ignored by Git.

Use private docs for:

- actual cutover plan for one machine
- old-stack path and rollback commands
- local port map
- profile inventory
- token ownership notes
- Tailscale exposure notes
- decisions about what data to migrate
- local troubleshooting notes
- personal whitelist entries and why they exist
- GogCLI, Google MCP, profile and auth runbooks

## Template

`private.example/docs/README.md` is the public template for creating private docs after cloning.

Common private runbooks are:

- `private/docs/hermes-profiles-local.md`
- `private/docs/gogcli-local.md`
- `private/docs/docker-cleanup-local.md`
- `private/docs/cutover-real-stack-local.md`
