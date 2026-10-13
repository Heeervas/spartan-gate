# Whitelist helper lock recovery

Applied change: the whitelist helper now waits at most 30 seconds for its
policy lock, reports a busy-policy error without changing the policy, and
closes the lock descriptor before calling Docker to reload proxy/DNS.

A suspended helper or Docker reload client previously retained the lock and
blocked later whitelist commands indefinitely. Cancel or finish the original
command before retrying a busy-policy error. Do not delete the lock file to
bypass a lock, because that can permit concurrent policy transactions.

Policy validation, temporary expiry, atomic replacement, and the existing
fail-closed proxy/DNS refresh behavior are unchanged. No container rebuild is
needed for this host-side script change.
