# Codex replay portability — pending activation

Status: source candidate only, pending independent candidate review and separately
authorized activation. No service restart, deployment, credential/config change,
conversation rewrite or database operation was applied by this task.

Standard Responses Codex replay receipts continue to authenticate ciphertext,
version, canonical encoding, size and subscription realm. The last receipt's
issuer becomes an advisory scheduling preference, not an account authorization
pin. Valid mixed-issuer histories can continue on the ordinary eligible pool when
the preferred issuer is disabled, cooled, unauthenticated, model/effort-ineligible
or excluded by safe retry policy. Nothing overrides these gates; committed output
and ambiguous sends remain non-replayable. Exact ciphertext and tool pairings are
preserved, and new output receipts identify the actual attempt.

Signed legacy Hermes raw reasoning and its chain/realm binding remain unchanged;
no plugin or history migration is required. Unknown-account credential replacement
loses preference rather than invalidating authentic receipts. Keep the existing
signing secret stable: rotation still invalidates receipts. Shared-secret trust
and hostile-client isolation limits are unchanged.

Cross-account synthetic completion evidence does not prove lossless hidden-
reasoning continuity or cache reuse; foreign reasoning counted zero input tokens
in the bounded prior probe. Treat these properties as uncertain and do not claim
that the pending policy is active. See `docs/pi-native-responses.md` and
`docs/reviews/codex-replay-portability-20261025.md` for scope and evidence limits.
