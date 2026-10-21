# Route account removal — applied change

Two operator-selected Codex accounts were removed from the active private Route auth-path list and their credential mounts. Only Spartan Route was recreated; no image rebuild, credential deletion or telemetry deletion occurred. Other service container identities remained unchanged.

Validation: effective Compose configuration passed; the recreated Route is healthy; authenticated balancer and dashboard usage APIs expose only the remaining configured accounts.

Operational guidance: removing a bootstrap credential mount alone does not remove a configured slot, because Route can retain managed OAuth state. Remove the slot from the active `OPENAI_CODEX_AUTH_PATHS` value, remove its mount, validate effective Compose, and recreate only Route. Preserve credential stores and historical telemetry unless deletion is separately authorized. Reload any already-open dashboard after changing the pool.
