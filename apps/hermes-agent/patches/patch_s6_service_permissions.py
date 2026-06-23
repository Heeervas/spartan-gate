#!/usr/bin/env python3
"""Build-time patcher: keep s6 service definitions root-owned."""

from __future__ import annotations

import sys
from pathlib import Path


RECONCILE_TARGET = Path("/etc/cont-init.d/02-reconcile-profiles")
SERVICE_MANAGER_TARGET = Path("/opt/hermes/hermes_cli/service_manager.py")

RECONCILE_MARKER = "# Spartan Gate patch: root-owned dynamic service definitions"
SERVICE_MANAGER_MARKER = "# Spartan Gate patch: runtime service registration disabled"

RECONCILE_OLD = '''# Make the dynamic scandir hermes-writable. The directory itself
# starts root-owned by s6-overlay.
chown hermes:hermes /run/service 2>/dev/null || true

# Make the svscan control FIFO hermes-writable so s6-svscanctl -a
# / -an work for the hermes user. The FIFO is created by s6-svscan
# at PID-1 startup, so by the time this cont-init.d script runs it
# already exists. Both ``control`` and ``lock`` need to be writable
# for the various svscanctl operations; the directory itself stays
# root-owned (we only need to touch the two FIFOs/locks inside).
if [ -d /run/service/.s6-svscan ]; then
    for entry in control lock; do
        if [ -e "/run/service/.s6-svscan/$entry" ]; then
            chown hermes:hermes "/run/service/.s6-svscan/$entry" 2>/dev/null || true
        fi
    done
fi

# Skip the drop when already non-root.
[ "$(id -u)" = 0 ] || exec /opt/hermes/.venv/bin/python -m hermes_cli.container_boot
exec s6-setuidgid hermes /opt/hermes/.venv/bin/python -m hermes_cli.container_boot
'''

RECONCILE_NEW = f'''{RECONCILE_MARKER}
# Spartan Gate keeps /run/service and service run scripts root-owned. The boot
# reconciler runs as root, creates fixed service definitions, and each service
# run script drops to hermes before starting application code.
[ "$(id -u)" = 0 ] || exec /opt/hermes/.venv/bin/python -m hermes_cli.container_boot
exec /opt/hermes/.venv/bin/python -m hermes_cli.container_boot
'''

SERVICE_MANAGER_OLD = '''    def supports_runtime_registration(self) -> bool:
        return True
'''

SERVICE_MANAGER_NEW = f'''    def supports_runtime_registration(self) -> bool:
        {SERVICE_MANAGER_MARKER}
        # Registering or unregistering dynamic s6 services requires mutating
        # /run/service. Spartan Gate allows that only during root-owned
        # container boot reconciliation; runtime Hermes processes should use
        # existing service slots and persist desired state for the next recreate.
        import os
        return os.geteuid() == 0
'''


def patch_reconcile_source(source: str) -> str:
    if RECONCILE_MARKER in source:
        return source
    if RECONCILE_OLD not in source:
        raise ValueError("reconcile permission block not found")
    return source.replace(RECONCILE_OLD, RECONCILE_NEW, 1)


def patch_service_manager_source(source: str) -> str:
    if SERVICE_MANAGER_MARKER in source:
        return source
    if SERVICE_MANAGER_OLD not in source:
        raise ValueError("service manager registration method not found")
    return source.replace(SERVICE_MANAGER_OLD, SERVICE_MANAGER_NEW, 1)


def patch_file(path: Path, patcher, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    source = path.read_text(encoding="utf-8")
    patched = patcher(source)
    if patched == source:
        return f"SKIP: {label} already patched"
    path.write_text(patched, encoding="utf-8")
    return f"Patched: {label}"


def main() -> None:
    try:
        print(patch_file(RECONCILE_TARGET, patch_reconcile_source, str(RECONCILE_TARGET)))
        print(patch_file(SERVICE_MANAGER_TARGET, patch_service_manager_source, str(SERVICE_MANAGER_TARGET)))
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
