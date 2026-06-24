# Agent Readability

This plan makes Spartan Gate easier for LLM coding agents to read without
publishing local agent packs or private operator instructions.

## Choice

Use a root `llms.txt` first. It gives agents one stable public entry point with
the repo map, safety rules, and validation commands. A generated LLM wiki can
come later if the documentation grows beyond what a single index can cover.

## Why Not A Wiki First

- A wiki adds another artifact that can drift from tracked docs.
- Generated summaries can accidentally flatten security caveats or ownership
  boundaries.
- The repo already has strong docs; the missing piece is discoverability, not a
  second explanation of every subsystem.
- `llms.txt` is reviewable in normal PRs and works for both humans and agents.

## Rollout

1. Keep `llms.txt` as the public agent entry point.
2. Link it from `README.md` and keep it scoped to orientation, not duplicated
   detailed docs.
3. When adding a new service, tier, helper, or security boundary, update:
   `llms.txt`, the owning doc under `docs/`, and `CHANGELOG.md` when the change
   is operator-visible.
4. Add a lightweight CI check later if drift becomes visible. A first version
   can verify that every path linked from `llms.txt` exists and that required
   sections such as `Start Here`, `Safety Rules For Agents`, and `Validation
   Shortcuts` are present.
5. Consider a generated LLM wiki only after two conditions are true:
   documentation is too large for a single entry map, and the generation path is
   deterministic, reviewed, and excludes private/ignored paths.

## Wiki Criteria

If a wiki is added later, it should be generated from tracked docs only and
published as an artifact or tracked `docs/llm-wiki/` snapshot. It must not read
or include:

- `AGENTS.md`
- `.agents/`
- `.github/agents/`, `.github/hooks/`, `.github/prompts/`, `.github/skills/`
- `private/`
- `runtime/`
- `.env`
- browser profiles, OAuth files, tokens, certificates, or local databases

The wiki should preserve the same ownership map used in `llms.txt`: Hermes
consumes services, ClawRoute owns model routing, Tinyproxy/DNS own egress,
Reader owns bounded GET retrieval, Browserless/Camofox own browser modes, and
Caddy owns edge exposure.
