#!/usr/bin/env python3
"""Opt out of boot-time dependency provisioning only after baked-image admission."""
from pathlib import Path

TARGET = Path('/opt/hermes/docker/stage2-hook.sh')
MARKER = '# Spartan Gate: explicitly boot only validated image dependencies'
OLD = '''as_hermes "$INSTALL_DIR/.venv/bin/python" -c '
from pathlib import Path
from hermes_cli.runtime_state import collect_generations
from pm.environments import install_state_dir
from pm.recovery import refresh_dependencies
from pm.runtime import collect_runtime_generations
root = Path("'"$INSTALL_DIR"'")
print("[stage2] dependency environment:", refresh_dependencies(root))
removed = collect_generations(root) + collect_runtime_generations(install_state_dir(root) / "pm-runtime")
print("[stage2] collected", len(removed), "unused dependency generations")
' || echo "[stage2] Warning: dependency refresh failed; continuing"
'''
# A failed opt-in admission must stop the cont-init transaction. Only signal
# its verified native runner; an unsupported/manual invocation holds instead.
FAILURE = '''        echo '[stage2] FATAL: refusing image-only startup' >&2
        if "$INSTALL_DIR/.venv/bin/python" -B - "$PPID" <<'SPARTAN_FAILURE'
import os, signal, sys
sys.path.insert(0, '/opt/hermes/bootstrap')
from spartan_browser_policy import validate_init_runner
try:
    parent = int(sys.argv[1])
    validate_init_runner(parent)
    os.kill(parent, signal.SIGTERM)
except Exception:
    raise SystemExit(1) from None
SPARTAN_FAILURE
        then
            exit 1
        fi
        exec /command/s6-pause
'''
NEW = MARKER + '''
if [ "${SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY:-0}" = 1 ]; then
    if as_hermes "$INSTALL_DIR/.venv/bin/python" -B "$INSTALL_DIR/patches/validate_image_dependencies.py"; then
        :
    else
''' + FAILURE + '''    fi
else
''' + OLD + '''fi
'''


def patch_text(source):
    if MARKER in source:
        if source.count(NEW) != 1:
            raise ValueError('Incompatible image-only startup guard')
        return source
    if source.count(OLD) != 1:
        raise ValueError('Upstream dependency refresh block missing or ambiguous')
    return source.replace(OLD, NEW)


def main():
    source = TARGET.read_text()
    result = patch_text(source)
    if result != source:
        TARGET.write_text(result)


if __name__ == '__main__':
    main()
