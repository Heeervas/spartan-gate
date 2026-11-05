# Proxy inheritance verification — 05/11/2026

## Status

Verification of an **already-applied persistent runtime configuration**, not a
new deployment. No environment, image, network policy, credentials or running
service was changed or restarted during this check.

## Observed configuration

- Docker supplies `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy`,
  `NO_PROXY` and `no_proxy` to Hermes.
- The existing read-only administrator-managed dotenv mount supplies the same
  six keys through Hermes' native managed scope, including routed profiles.
- The operator-side managed file has mode 0600 and its directory mode 0700.

## Verification

Executed the installed Hermes interpreter as the workload user, using a
throwaway home and synthetic secondary profile, not a real profile's secrets
or configuration. Native managed loading registered all six proxy keys.
The native primary-terminal and routed-secondary-terminal environment builders,
and the generic subprocess sanitizer, all retained their managed values.

A real anonymous child process, given only the six resulting proxy variables
and a minimal PATH, received **HTTP 200** from the public GitHub API using
Python urllib's automatic proxy discovery. No authentication was supplied.
The temporary test home was removed by its context manager; no task subprocess
or new runtime consumer remains.

## Interpretation and remaining limit

No missing persistent proxy setting was reproduced. Environment variables do
not make raw sockets or explicitly proxy-disabled clients use an HTTP proxy.
Direct egress remains blocked intentionally. The original failing command and
its affected session were not supplied, so their exact failure remains
unverified; do not infer a bad token or a missing route from that socket test.
The earlier suggestion that persistent inheritance still needed applying was
premature; it has now been tested through the native child-environment paths.

Public troubleshooting guidance was clarified in `docs/operations.md`.
This note is an operator archive record, not a published Hermes runtime guide.
