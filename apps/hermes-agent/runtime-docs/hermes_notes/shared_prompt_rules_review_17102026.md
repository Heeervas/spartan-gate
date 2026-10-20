# Shared preferences and rule review

Date: 2026-10-17 (Europe/Madrid). Status: **pending recommendation; not an applied Hermes policy**.

A private review now compares current instruction snapshots with a moderate candidate for five profiles. The review groups substantive rules, records their source and destination spans, and distinguishes dependencies from parallel instructions. Per-rule size deltas can overlap when several rules map to one section; only file-level totals are additive. Current USER and MEMORY files are preserved in the candidates.

The shared-preference proposal uses a small canonical MASTER file and Hermes' native system-prompt-section plugin API. MASTER is not a filename Hermes loads automatically. Profile activation, section limits, failure handling, persisted prompts and delegation coverage still require validation before adoption. No plugin, profile, live instruction, skill, memory or Hermes service was changed by this review.
