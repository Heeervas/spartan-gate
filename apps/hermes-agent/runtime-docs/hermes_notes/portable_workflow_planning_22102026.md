# Portable workflow and native planning

Source implementation prepared on 2026-10-22. Runtime activation requires the normal separately controlled image build/recreation; this note does not certify a deployed image.

The native `/plan` patch keeps the current planning-only turn and short saved-path response. It resolves one plan owner, loads the enabled shared writing-plans method and Hermes adapter, and preserves a separate minimal native fallback when that method is unavailable. It never loads a denied method through another path. Fixed task durations, mandatory full code, universal TDD and per-task commits are removed from the native prompt.

Shared workflows use frame, inspect, plan, execute, verify and close stages, with explicit endpoints and evidence-based resume. Native skill/package visibility and current profile/platform controls remain distinct. Use native discovery and the active adapter; wrapper names do not restore denied capabilities.

Source tests validate prompt construction, unchanged function/task behavior, idempotence and drift refusal. Installation and a fresh runtime invocation require separate readback evidence.
