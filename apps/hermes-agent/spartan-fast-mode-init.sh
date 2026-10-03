#!/command/with-contenv sh
set -eu

if [ "$(/command/printcontenv S6_BEHAVIOUR_IF_STAGE2_FAILS 2>/dev/null || true)" != 2 ]; then
    echo '[fast-mode] FATAL: s6 stage2 failure policy must be 2' >&2
    exec /command/s6-pause
fi

# Reuse the native-runner verification already shipped with Spartan Gate.
if ! /opt/hermes/.venv/bin/python -B - "$PPID" <<'PYTHON'
import sys
sys.path.insert(0, '/opt/hermes')
from spartan_browser_policy import validate_init_runner
try:
    validate_init_runner(int(sys.argv[1]))
except Exception:
    raise SystemExit(1) from None
PYTHON
then
    echo '[fast-mode] FATAL: native s6 cont-init runner required' >&2
    exec /command/s6-pause
fi

if /opt/hermes/.venv/bin/python -B /opt/hermes/bootstrap/spartan_fast_mode_guard.py; then
    exit 0
fi

if /opt/hermes/.venv/bin/python -B - "$PPID" <<'PYTHON'
import os
import signal
import sys
sys.path.insert(0, '/opt/hermes')
from spartan_browser_policy import validate_init_runner
try:
    parent = int(sys.argv[1])
    validate_init_runner(parent)
    os.kill(parent, signal.SIGTERM)
except Exception:
    raise SystemExit(1) from None
PYTHON
then
    exit 1
fi
exec /command/s6-pause
