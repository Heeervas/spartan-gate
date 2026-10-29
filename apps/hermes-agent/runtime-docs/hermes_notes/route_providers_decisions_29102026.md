# Route Providers and Decisions — source applied, activation pending

Providers controls and native OpenAI Decisions are implemented and merged in
Spartan Route source. This note does not claim that the running service has
changed. Route-only activation is pending the operator's provider-key input.

After activation, clients keep their existing Route endpoint and normal Bearer.
Use `codex/<model>` for subscription and `openai/<model>` for OpenAI API.
Providers in Config are enabled by default with empty endpoint/model reject lists;
there is no allowlist or provider-policy environment variable. Speed parameters
remain subject to upstream support. Decisions uses `/v1/decisions` with
`openai/gpt-6-luna` or its native ID and the normal OpenAI API key.

The source candidate passed Route Python/JavaScript tests, the existing TOP
quality gate and independent review. Real provider inference was not exercised.
The public behavior contract is in `docs/spartan-route.md`.

This is an unserved operator archive note, not a runtime guide publication.
