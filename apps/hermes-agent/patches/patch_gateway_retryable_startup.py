#!/usr/bin/env python3
"""Runtime patcher: keep Hermes alive when startup failures are retryable.

If the only configured messaging platforms fail during startup for retryable
reasons such as transient network, DNS, or proxy outages, upstream Hermes
returns False from GatewayRunner.start(). The outer runner treats that as a
fatal startup failure and exits the whole gateway process, even though the
platforms were already queued for background reconnect.

This patch keeps the gateway process alive in that retryable-only case so the
API server, healthcheck, and reconnect watcher stay up until connectivity
returns.
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path('/opt/hermes/gateway/run.py')

MARKER = '# Spartan Gate patch: keep gateway alive for retryable startup outages'
UPSTREAM_ABSORBED_MARKERS = (
    'Gateway started with no connected platforms',
    'queued for retry',
    'write_runtime_status(',
    'gateway_state="degraded"',
)

OLD_BLOCK = '''        if connected_count == 0:
            if startup_nonretryable_errors:
                reason = "; ".join(startup_nonretryable_errors)
                logger.error("Gateway hit a non-retryable startup conflict: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                self._request_clean_exit(reason)
                return True
            if enabled_platform_count > 0:
                reason = "; ".join(startup_retryable_errors) or "all configured messaging platforms failed to connect"
                logger.error("Gateway failed to connect any configured messaging platform: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                return False
            logger.warning("No messaging platforms enabled.")
            logger.info("Gateway will continue running for cron job execution.")'''

NEW_BLOCK = '''        if connected_count == 0:
            if startup_nonretryable_errors:
                reason = "; ".join(startup_nonretryable_errors)
                logger.error("Gateway hit a non-retryable startup conflict: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                self._request_clean_exit(reason)
                return True
            if enabled_platform_count > 0:
                reason = "; ".join(startup_retryable_errors) or "all configured messaging platforms failed to connect"
                logger.error("Gateway failed to connect any configured messaging platform: %s", reason)
                if self._failed_platforms:
                    # Spartan Gate patch: keep gateway alive for retryable startup outages
                    logger.warning(
                        "All startup platform failures are retryable. "
                        "Gateway will stay up and retry in background. "
                        "Likely transient network, DNS, or proxy outage."
                    )
                else:
                    try:
                        from gateway.status import write_runtime_status
                        write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                    except Exception:
                        pass
                    return False
            else:
                logger.warning("No messaging platforms enabled.")
                logger.info("Gateway will continue running for cron job execution.")'''


def patch_source(source: str) -> str:
    if MARKER in source:
        return source
    if all(marker in source for marker in UPSTREAM_ABSORBED_MARKERS):
        return source
    if OLD_BLOCK not in source:
        raise ValueError(f'anchor block not found in {TARGET}')
    return source.replace(OLD_BLOCK, NEW_BLOCK, 1)


def main() -> None:
    if not TARGET.exists():
        print(f'FATAL: {TARGET} not found', file=sys.stderr)
        sys.exit(1)

    source = TARGET.read_text(encoding='utf-8')

    try:
        patched = patch_source(source)
    except ValueError as exc:
        print(f'FATAL: {exc} — upstream may have changed', file=sys.stderr)
        sys.exit(1)

    if patched == source:
        print(f'SKIP: {TARGET} already keeps retryable startup outages alive')
        return

    TARGET.write_text(patched, encoding='utf-8')
    print(f'Patched: {TARGET} — retryable startup outages no longer stop the gateway')


if __name__ == '__main__':
    main()
