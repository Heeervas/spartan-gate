# Hermes Prompt Bloat Report

Use the safe all-profile report when Codex quota burn looks higher than a single
prompt should cause:

```sh
source scripts/aliases.sh
sg_hermes_exec_i python3 - < scripts/hermes-bloat-report.py
sg_hermes_exec_i python3 - --json < scripts/hermes-bloat-report.py
```

The helper inspects `/opt/data` plus every `/opt/data/profiles/*` profile when
run inside the Hermes container. For host-side inspection of a mounted data
directory, pass `--root /path/to/hermes-data`.

Each `state.db` is opened read-only, copied with SQLite's backup API into a
temporary database, and queried only from that copy. The report summarizes prompt
file sizes, profile config tool/MCP shape when parseable, recent session prompt
sizes, and stored message/tool output character totals.
