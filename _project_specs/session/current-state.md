# Current State

Date: 2026-06-28

## Latest Update

Spartan Route Codex-auth image fallback now accepts valid `gpt-image-2`
non-square sizes instead of preflight-rejecting them. The image payload follows
the Codex built-in `image_generation` tool shape, preserves requested aspect
ratio, and returns `codex_image_size_mismatch` when upstream returns the wrong
aspect instead of silently handing Hermes a square image. Live probes after
rebuild confirmed `1536x1024` returns a real `1536x1024` PNG, and `1024x1024`
returns a square `1254x1254` PNG with `actual_size` recorded.

## Active Task

None recorded.

## Blockers

None recorded.
