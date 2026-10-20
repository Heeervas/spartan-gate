# Workstation mount preparation

Applied change: the host `sg` helpers now prepare the optional workstation broker
state directory before creating, starting, or restarting Hermes. This recovers a
missing temporary directory after reboot while retaining the read-only bind and
`create_host_path: false`.

Existing state must be operator-owned and owner-only. Unsafe paths fail closed;
preparation does not change existing permissions, replace sockets, start bridges,
or approve gateway trust. `SPARTAN_WORKSTATION_STATE_DIR` in the selected env file
or shell must match the Compose bind source and is also honored by
`sg-workstation`.

Operators must reload `scripts/aliases.sh` in existing shells to use the updated
helpers. Raw Docker Compose commands still require manual directory preparation.
Live stack recovery and health validation remain pending until the host meets
the repository's minimum 5 GB free-space requirement.
