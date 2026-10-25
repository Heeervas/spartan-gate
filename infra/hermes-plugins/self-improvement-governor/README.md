# Native skill-review prompt addition

Prompt-only replacement for the historical governor. The installed source owns
`_SKILL_REVIEW_PROMPT` and `_COMBINED_REVIEW_PROMPT` in
`agent/background_review.py`; `run_agent.AIAgent` exposes their overridable class
attributes. Native `spawn_background_review_thread` selects those attributes.

Registration appends one instruction block to both skill-bearing prompts. It
requires full optimizer/refiner and mandatory Hermes-support reads, and separates
generic additions/removals in `SKILL.md` from topical detail in `references/`,
with routing. It does not replace native text, wrap spawning, register middleware,
change permissions, tools, triggers, write approval, memory routing or delivery.
These are model instructions, not a new mechanical enforcement guarantee.

The required canonical skill packages/supports must be available through their
separately reviewed adoption gate. This package neither installs nor adopts them.
No configuration, enablement, image rebuild or runtime activation is included.

One focused check in the pinned Hermes environment:

```sh
python -B infra/hermes-plugins/test_self_improvement_governor.py
```

The check imports native program modules and composes prompts without constructing
an agent, running the review target, starting threads or calling a provider.
