# Route Providers and Decisions — activated

Providers controls and native OpenAI Decisions are implemented, merged and
activated in Spartan Route. Only Route was rebuilt/recreated; all 11 neighboring
containers retained their identities and start times. Non-root image checks,
health, authentication and exact deployed program/served Config assets passed.
The normal OpenAI key was injected without displaying its value.

Clients keep their existing Route endpoint and normal Bearer.
Use `codex/<model>` for subscription and `openai/<model>` for OpenAI API.
Providers in Config are enabled by default with empty endpoint/model reject lists;
there is no allowlist or provider-policy environment variable. Speed parameters
remain subject to upstream support. Decisions uses `/v1/decisions` with
`openai/gpt-6-luna` or its native ID and the normal OpenAI API key.

The source candidate passed Route Python/JavaScript tests, the existing TOP
quality gate and independent review. An authenticated invalid Decisions request
confirmed local endpoint registration without an upstream model call. Real
provider inference was not exercised. Providers remain enabled with empty reject
lists; introducing the key did not change auto-routing assignments.

The activation check caught restrictive host source permissions before deployment.
Public program read permissions were normalized, the image was rebuilt, and its
non-root canary passed. No source bytes or private credential permissions changed.
The previous image and restricted env/database backups remain available for rollback.
The public behavior contract is in `docs/spartan-route.md`.

This is an unserved operator archive note, not a runtime guide publication.
