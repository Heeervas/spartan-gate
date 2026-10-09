#!/usr/bin/env python3
"""Opt out of boot-time dependency provisioning only after baked-image admission."""
from pathlib import Path

TARGET = Path('/opt/hermes/docker/stage2-hook.sh')
RUNTIME = Path('/opt/hermes/hermes_cli/venv_sync.py')
RUNTIME_MARKER = '# Spartan Gate: admit sealed image dependencies before omitting the PM generation verdict'
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


def patch_runtime_source(source):
    original = '    problems = pm.activate()\n'
    replacement = f'''    {RUNTIME_MARKER}
    if os.environ.get('SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY', '0') == '0':
        problems = pm.activate()
    else:
        try:
            import importlib.util
            _admission_spec = importlib.util.spec_from_file_location(
                '_spartan_image_admission', Path(project_root) / 'patches/validate_image_dependencies.py')
            _admission = importlib.util.module_from_spec(_admission_spec)
            _admission_spec.loader.exec_module(_admission)
            if not _admission.image_only_startup(project_root):
                raise RuntimeError('Requested image admission was not performed')
        except Exception:
            import sys
            print('FATAL: protected image dependency admission could not run', file=sys.stderr)
            raise SystemExit(78) from None
        problems = pm.activate(allow_incomplete=True)
'''
    if RUNTIME_MARKER in source:
        if source.count(replacement) != 1:
            raise ValueError('Incompatible image-only runtime admission')
        return source
    if source.count(original) != 1:
        raise ValueError('Upstream PM runtime activation anchor missing or ambiguous')
    result = source.replace(original, replacement, 1)
    compile(result, str(RUNTIME), 'exec')
    return result


def main():
    # Complete both preflights before the first write.
    updates = [(TARGET, TARGET.read_text()), (RUNTIME, RUNTIME.read_text())]
    results = [patch_text(updates[0][1]), patch_runtime_source(updates[1][1])]
    for (path, source), result in zip(updates, results):
        if result != source:
            path.write_text(result)


if __name__ == '__main__':
    main()
