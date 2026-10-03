#!/command/with-contenv sh
set -eu

# Native stage2 must stop after an interrupted cont-init transaction. Its
# failure flag is captured before this hook runs, so do not repair it here.
if [ "$(/command/printcontenv S6_BEHAVIOUR_IF_STAGE2_FAILS 2>/dev/null || true)" != 2 ]; then
    echo '[browser-policy] FATAL: s6 stage2 failure policy must be 2' >&2
    exec /command/s6-pause
fi

# Verify the immediate caller before permitting initialization. Unsupported
# or manual invocation holds here and must be interrupted by the operator.
if ! /opt/hermes/.venv/bin/python -B - "$PPID" <<'PYTHON'
import sys
sys.path.insert(0, '/opt/hermes/bootstrap')
from spartan_browser_policy import validate_init_runner
try:
    validate_init_runner(int(sys.argv[1]))
except Exception:
    raise SystemExit(1) from None
PYTHON
then
    echo '[browser-policy] FATAL: native s6 cont-init runner required' >&2
    exec /command/s6-pause
fi

if /opt/hermes/.venv/bin/python -B /opt/hermes/bootstrap/spartan_browser_policy.py; then
    exit 0
fi

# cont-init normally continues after failures. Interrupt only its verified
# runner so s6-rc returns failure to stage2, whose native haltwith preserves a
# nonzero container exit. Requesting shutdown while holding this transaction
# deadlocks s6's shutdown dependency on the active startup transaction.
if /opt/hermes/.venv/bin/python -B - "$PPID" <<'PYTHON'
import os
import signal
import sys
sys.path.insert(0, '/opt/hermes/bootstrap')
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
