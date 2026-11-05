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
LEGACY_NEW = MARKER + '''
if [ "${SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY:-0}" = 1 ]; then
    if as_hermes "$INSTALL_DIR/.venv/bin/python" -B "$INSTALL_DIR/patches/validate_image_dependencies.py"; then
        :
    else
''' + FAILURE + '''    fi
else
''' + OLD + '''fi
'''


EMERGENCY = '''# Spartan Gate recovery: passive selected-generation admission; no sync or GC.
if as_hermes "$INSTALL_DIR/.venv/bin/python" -B - <<'SPARTAN_SELECTED'
from pathlib import Path
import os
from pm.environments import selected_venv, install_state_dir
from pm.recovery import validate_environment
from pm.install import venv_is_current
root = Path('/opt/hermes')
state = install_state_dir(root)
for marker in (state / 'publication.json', state / '.repair-incomplete',
               root / '.update-incomplete', root / '.lazy-refresh-incomplete'):
    if marker.exists() or marker.is_symlink():
        raise SystemExit('[stage2] FATAL: pending dependency transaction; refusing passive recovery')
python = selected_venv(root) / 'bin/python'
validate_environment(python, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), cwd=root)
if not venv_is_current(project_root=root):
    raise SystemExit('[stage2] FATAL: selected generation is not current; no changes made')
print('[stage2] selected dependency environment validated; no sync or collection')
SPARTAN_SELECTED
then
    :
else
''' + FAILURE + '''fi
'''
EMERGENCY_NEW = LEGACY_NEW.replace(OLD, EMERGENCY)
NEW = MARKER + '''
if [ "${SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY-0}" != 0 ] ||
   [ "${SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY-0}" != 0 ]; then
    if as_hermes "$INSTALL_DIR/.venv/bin/python" -B "$INSTALL_DIR/patches/validate_image_dependencies.py"; then
        :
    else
''' + FAILURE + '''    fi
else
''' + OLD + '''fi
'''


def patch_text(source):
    if MARKER in source:
        for known in (NEW, LEGACY_NEW, EMERGENCY_NEW):
            if source.count(known) == 1:
                return source.replace(known, NEW)
        raise ValueError('Incompatible dependency startup guard')
    if source.count(OLD) != 1:
        raise ValueError('Upstream dependency refresh block missing or ambiguous')
    return source.replace(OLD, NEW)


def legacy_runtime_replacement():
    return f'''    {RUNTIME_MARKER}
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


EARLY_ANCHOR = '    root = Path(project_root).resolve()\n    # sys.argv[0]'
EARLY = '''    root = Path(project_root).resolve()
    # Spartan Gate: validate managed selection BEFORE native recovery or sealed return.
    if _spartan_dependency_admission(root, argv):
        return None
    # sys.argv[0]'''
HELPER = '''

def _spartan_dependency_admission(project_root, argv):
    if (os.environ.get('SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY', '0') == '0'
            and os.environ.get('SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY', '0') == '0'):
        return False
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            '_spartan_dependency_admission', Path(project_root) / 'patches/validate_image_dependencies.py')
        admission = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(admission)
        return admission.startup_admission(project_root, argv)
    except Exception:
        import sys
        print('FATAL: protected dependency admission could not run', file=sys.stderr)
        raise SystemExit(78) from None
'''
ACTIVATION = '''    # Spartan Gate: preserve PM tool diagnostics, not the mutable generation verdict.
    import sys
    if _spartan_dependency_admission(project_root, sys.argv[1:]):
        problems = pm.activate(allow_incomplete=True)
    else:
        problems = pm.activate()
'''


def patch_runtime_source(source):
    if HELPER in source and EARLY in source and ACTIVATION in source:
        return source
    if '_spartan_dependency_admission' in source:
        raise ValueError('Partial dependency runtime admission')
    if RUNTIME_MARKER in source:
        legacy = legacy_runtime_replacement()
        if source.count(legacy) != 1:
            raise ValueError('Incompatible image-only runtime admission')
        source = source.replace(legacy, '    problems = pm.activate()\n')
    original = '    problems = pm.activate()\n'
    if source.count(original) != 1 or source.count(EARLY_ANCHOR) != 1:
        raise ValueError('Upstream dependency runtime anchors missing or ambiguous')
    result = source.replace(original, ACTIVATION).replace(EARLY_ANCHOR, EARLY) + HELPER
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
