import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_stage2_install_chown_guard.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_stage2_install_chown_guard',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Stage2InstallChownGuardPatchTests(unittest.TestCase):
    def test_replaces_unconditional_install_tree_chown_with_immutable_install_tree(self):
        module = load_module()
        source = '''before
if [ "$needs_chown" = true ]; then
    echo "data"
    for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing; do
        echo "$sub"
    done
as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }
# --- Fix ownership of build trees under $INSTALL_DIR ---
# Hermes-owned trees under $INSTALL_DIR must be re-chowned when the UID
# is remapped — otherwise:
if [ -n "$venv_owner" ]; then
    chown -R hermes:hermes \\
        "$INSTALL_DIR/.venv" \\
        "$INSTALL_DIR/ui-tui" \\
        "$INSTALL_DIR/node_modules" \\
        2>/dev/null || \\
        echo "[stage2] Warning: chown of build trees failed (rootless container?) — continuing"
fi

# Always reset ownership
after

mkdir -p "$HERMES_HOME"

validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

# --- Sync bundled skills ---
sync

for f in \\
    auth.json auth.lock .env \\
    state.db state.db-shm state.db-wal \\
    hermes_state.db \\
    response_store.db response_store.db-shm response_store.db-wal \\
    gateway.pid gateway.lock gateway_state.json processes.json \\
    active_profile; do
    if [ -e "$HERMES_HOME/$f" ]; then
        chown hermes:hermes "$HERMES_HOME/$f" 2>/dev/null || true
    fi
done

if [ -f "$HERMES_HOME/config.yaml" ]; then
    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py" \\
        || echo "[stage2] Warning: docker_config_migrate.py failed; continuing"
fi

# Use direct `mkdir -p` invocation
'''

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn('keeps Hermes install trees immutable', patched)
        self.assertNotIn('chown -R hermes:hermes \\\n            "$INSTALL_DIR/.venv"', patched)
        self.assertIn('\n# Always reset ownership', patched)
        self.assertIn('# Spartan Gate patch: immutable install tree', patched)
        self.assertIn('# Always reset ownership', patched)
        self.assertNotIn('    # Hermes-owned trees under $INSTALL_DIR', patched)
        self.assertIn(module.MIGRATION_MARKER, patched)
        self.assertIn(module.ROOT_FILE_MARKER, patched)
        self.assertIn(module.GATEWAY_LOCK_MARKER, patched)
        self.assertIn(module.RUNTIME_IDENTITY_MARKER, patched)
        self.assertIn('    .install_method; do', patched)
        self.assertIn('mkdir -p "$HERMES_HOME/.local/state/hermes/gateway-locks"', patched)
        self.assertIn('platforms/pairing kanban .local/state/hermes; do', patched)
        self.assertIn(module.AS_HERMES_MARKER, patched)
        self.assertIn('SPARTAN_HERMES_RUN_UID="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}"', patched)
        self.assertIn('SPARTAN_HERMES_RUN_GID="${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}"', patched)
        self.assertIn('SPARTAN_HERMES_RUNTIME_OWNER="$SPARTAN_HERMES_RUN_UID:$SPARTAN_HERMES_RUN_GID"', patched)
        self.assertIn('HERMES_UID=""', patched)
        self.assertIn('PUID=""', patched)
        self.assertIn('/command/s6-applyuidgid -u "$SPARTAN_HERMES_RUN_UID" -g "$SPARTAN_HERMES_RUN_GID" "$@"', patched)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME/$f"', patched)
        self.assertIn('as_hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py"', patched)
        self.assertNotIn('s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py"', patched)
        self.assertNotIn('groupmod -o -g "$SPARTAN_HERMES_RUN_GID" hermes', patched)
        self.assertNotIn('usermod -u "$SPARTAN_HERMES_RUN_UID"', patched)
        self.assertIn('FATAL: invalid SPARTAN_HERMES_RUN_UID', patched)
        self.assertIn('FATAL: invalid SPARTAN_HERMES_RUN_GID', patched)
        self.assertIn('exit 1', patched)
        self.assertNotIn('SPARTAN_HERMES_DATA_GID', patched)
        self.assertNotIn('SPARTAN_HERMES_DATA_GROUP_REPAIR', patched)
        self.assertNotIn('-G "$SPARTAN_HERMES_DATA_GID"', patched)
        self.assertNotIn('chgrp -R', patched)
        self.assertNotIn('chmod -R g+rwX "$HERMES_HOME"', patched)
        self.assertNotIn('setfacl', patched)
        self.assertNotIn('find "$HERMES_HOME" -type d -exec chmod g+s {} +', patched)
        self.assertIn(
            'as_hermes "$INSTALL_DIR/.venv/bin/python" \\\n'
            '        "$INSTALL_DIR/patches/patch_clawroute_named_provider.py"',
            patched,
        )

    def test_idempotent_when_marker_present(self):
        module = load_module()
        source = (
            f'before\n{module.MARKER}\n{module.MIGRATION_MARKER}\n{module.ROOT_FILE_MARKER}\n'
            f'{module.GATEWAY_LOCK_MARKER}\n{module.RUNTIME_IDENTITY_MARKER}\n{module.AS_HERMES_MARKER}\n'
            'SPARTAN_HERMES_RUN_UID="${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}"\n'
            'for sub in cron sessions logs hooks memories skills skins plans workspace home profiles '
            'pairing platforms/pairing kanban .local/state/hermes; do\n'
            'after\n'
        )

        self.assertEqual(module.patch_source(source), source)

    def test_adds_missing_migration_hook_to_previously_guarded_source(self):
        module = load_module()
        source = f'''before
{module.MARKER}
guarded block
as_hermes() {{ [ "$(id -u)" = 0 ] || {{ "$@"; return; }}; s6-setuidgid hermes "$@"; }}
for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing; do
    echo "$sub"
done

# Always reset ownership
after ownership

mkdir -p "$HERMES_HOME"

validate_uid_gid() {{
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}}

for f in \\
    auth.json auth.lock .env \\
    state.db state.db-shm state.db-wal \\
    hermes_state.db \\
    response_store.db response_store.db-shm response_store.db-wal \\
    gateway.pid gateway.lock gateway_state.json processes.json \\
    active_profile; do
    if [ -e "$HERMES_HOME/$f" ]; then
        chown hermes:hermes "$HERMES_HOME/$f" 2>/dev/null || true
    fi
done

# Use direct `mkdir -p` invocation

# --- Sync bundled skills ---
after
'''

        patched = module.patch_source(source)

        self.assertIn(module.MIGRATION_MARKER, patched)
        self.assertLess(patched.index(module.MIGRATION_MARKER), patched.index('# --- Sync bundled skills ---'))

    def test_accepts_upstream_immutable_install_tree_block(self):
        module = load_module()
        source = '''before
if [ "$needs_chown" = true ]; then
    for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing; do
        echo "$sub"
    done
fi
as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }
mkdir -p "$HERMES_HOME"

validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

# --- Immutable install tree ---
# Upstream no longer chowns runtime code back to hermes.

# Always reset ownership of $HERMES_HOME/profiles to hermes on every
if [ -d "$HERMES_HOME/profiles" ]; then
    chown -R hermes:hermes "$HERMES_HOME/profiles" 2>/dev/null || true
fi

for f in \\
    auth.json auth.lock .env \\
    state.db state.db-shm state.db-wal \\
    hermes_state.db \\
    response_store.db response_store.db-shm response_store.db-wal \\
    gateway.pid gateway.lock gateway_state.json processes.json \\
    active_profile; do
    if [ -e "$HERMES_HOME/$f" ]; then
        chown hermes:hermes "$HERMES_HOME/$f" 2>/dev/null || true
    fi
done

if [ -f "$HERMES_HOME/config.yaml" ]; then
    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py" \\
        || echo "[stage2] Warning: docker_config_migrate.py failed; continuing"
fi

# Use direct `mkdir -p` invocation

# --- Sync bundled skills ---
sync
'''

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn('keeps Hermes install trees immutable', patched)
        self.assertNotIn('# --- Immutable install tree ---', patched)
        self.assertIn('\n# Always reset ownership of $HERMES_HOME/profiles', patched)
        self.assertIn(module.MIGRATION_MARKER, patched)
        self.assertIn(module.AS_HERMES_MARKER, patched)
        self.assertIn('chown -R "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME/profiles"', patched)

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
