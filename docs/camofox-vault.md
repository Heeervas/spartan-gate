# Hermes credential vault with Camofox

Spartan Gate adapts Hermes's native `browser_vault` tools to the selected
Camofox browser. Credentials stay in the Hermes vault or its configured
password-manager backend. Camofox receives only the native fill expression
for the current operation; it does not mount or manage the vault.

## Operator workflow

Add credentials with Hermes's native `hermes vault add` command in the intended
Hermes profile. The agent navigates to the matching site, obtains an opaque
handle through `browser_vault_list`, and calls `browser_vault_fill`. Hermes
fills the password; the agent can type the identifier and submit the form using
the normal browser tools. The adapter does not submit forms itself, although
sites can react to input events, including automatic OTP submission.

Login passwords, payment cards and addresses use Hermes's existing classifiers
and fill scripts. Payment cards retain native human consent. Saving new logins,
unlocking an external manager, and requesting a one-time code retain the
channel's native secure prompts; channels without the required prompt refuse
the operation. A stored TOTP can be used only on an exact origin authorized by
its login item. The adapter does not add password collection in chat.

The current native classifier inspects the main document. This integration
does not add iframe autofill, passkeys, CAPTCHA solving or new SSO flows.

## Browser and secret boundary

Vault page operations use the same durable Hermes session as ordinary Camofox
tools and pin an existing tab for the operation, including human prompt waits.
They do not create a tab, search other session groups, or adopt the reserved
operator login tab. Missing or retired tabs require an explicit
`browser_navigate` before another attempt.

The adapter requires authenticated `GET /spartan/capabilities` to advertise
`vaultEvaluateVersion: 1`. Secret expressions use
`POST /tabs/:tabId/spartan/vault-evaluate` with `userId`, `sessionKey` and
`expression`. This route requires the configured access bearer, checks the
tab's owner and group, rejects tracing, and returns only a validated field
count or an origin-change refusal. Hermes retains its exact-origin check and
per-inspection DOM markers. Ordinary `/evaluate` is never the fallback for a
secret expression.

The secret route parses at most 100 KiB after authentication and returns fixed
errors. Expressions and original exceptions are excluded from plugin events,
logs and error reporting. Hermes uses direct authenticated HTTP without proxy
inheritance or redirects. Credentials do not travel in subprocess arguments.

Queue timeouts do not mutate tabs. An execution timeout retires only the
affected tab and does not automatically retry a possibly completed write.
The result may be indeterminate; navigate explicitly before retrying.

Hermes's existing exact-value redactor protects textual browser results,
including short snapshots and cached full snapshots. **Screenshots and vision
retain Hermes's native limitation:** visible payment details or OTP values can
appear in image pixels and reach the vision model. Text redaction does not
redact pixels; this integration does not disable vision or promise image
confidentiality.

## Deployment and recovery

The change is baked into both images. Preserve the selected Hermes variant;
the local Camofox-only variant requires its separately prepared upstream base
before the downstream image build. See [the build procedure](hermes-camofox-only-build.md).
Fingerprint-pinned code overlays must be retired only after their adaptations
are incorporated in the reviewed image source.

Build images sequentially, validate the images, and then activate Camofox before
Hermes using the main-stack `sg` commands. A server lacking the capability fails
closed; Hermes does not fall back to CDP or a different browser. Neither a
build nor rollback requires deleting browser profiles, vaults or volumes.

If old local images were explicitly removed to recover space, rollback
requires rebuilding the recorded source and base coordinates. A local image
digest alone does not establish that the custom image can be pulled from a
registry.
