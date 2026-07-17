import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("patches").joinpath(
    "patch_stage2_runtime_owner.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("patch_stage2_runtime_owner", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def upstream_shape() -> str:
    return r"""#!/bin/sh
as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }
mkdir -p "$HERMES_HOME"

validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}

HERMES_UID="__DOLLAR__{HERMES_UID:-__DOLLAR__{PUID:-}}"
HERMES_GID="__DOLLAR__{HERMES_GID:-__DOLLAR__{PGID:-}}"
actual_hermes_uid=$(id -u hermes)
if [ "$needs_chown" = true ]; then
    echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to hermes ($actual_hermes_uid)"
    chown hermes:hermes "$HERMES_HOME"
    for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing lazy-packages; do
        chown -R hermes:hermes "$HERMES_HOME/$sub"
    done
fi

# --- Immutable install tree ---
# Upstream keeps /opt/hermes root-owned.

for f in auth.json active_profile; do
    chown hermes:hermes "$HERMES_HOME/$f"
done

as_hermes mkdir -p __BS__
    "$HERMES_HOME/backups" __BS__
    "$HERMES_HOME/lazy-packages"

if [ -f "$HERMES_HOME/config.yaml" ]; then
    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_config_migrate.py"
fi
if [ -f "$HERMES_HOME/auth.json" ]; then
    s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/docker_rebootstrap_nous_session.py"
fi

# --- Sync bundled skills ---
sync
""".replace("__DOLLAR__", chr(36)).replace("__BS__", chr(92))


class Stage2RuntimeOwnerPatchTests(unittest.TestCase):
    def test_keeps_only_numeric_identity_runtime_dirs_and_local_migration(self):
        module = load_module()
        patched = module.patch_source(upstream_shape())

        self.assertIn("# --- Immutable install tree ---", patched)
        self.assertNotIn("Spartan Gate patch: immutable install tree", patched)
        self.assertIn(module.RUNTIME_IDENTITY_MARKER, patched)
        self.assertIn(module.AS_HERMES_MARKER, patched)
        self.assertIn(module.RUNTIME_DIR_MARKER, patched)
        self.assertIn(module.MIGRATION_MARKER, patched)
        self.assertIn('actual_hermes_uid="$SPARTAN_HERMES_RUN_UID"', patched)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME"', patched)
        self.assertIn('chown -R "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME/$sub"', patched)
        self.assertIn('"$HERMES_HOME/kanban"', patched)
        self.assertIn('"$HERMES_HOME/.local/state/hermes/gateway-locks"', patched)
        self.assertIn("platforms/pairing lazy-packages kanban .local/state/hermes; do", patched)
        self.assertNotIn('s6-setuidgid hermes "$INSTALL_DIR/.venv/bin/python"', patched)
        self.assertEqual(patched.count('as_hermes "$INSTALL_DIR/.venv/bin/python"'), 3)
        self.assertNotIn(".install_method; do", patched)
        self.assertLess(
            patched.index("$INSTALL_DIR/scripts/docker_config_migrate.py"),
            patched.index(module.MIGRATION_MARKER),
        )
        self.assertLess(patched.index(module.MIGRATION_MARKER), patched.index(module.MIGRATION_ANCHOR))

    def test_is_idempotent(self):
        module = load_module()
        patched = module.patch_source(upstream_shape())
        self.assertEqual(module.patch_source(patched), patched)

    def test_managed_dir_allowlist_preserves_new_upstream_entries(self):
        module = load_module()
        source = (
            "for sub in cron sessions logs hooks profiles pairing "
            "platforms/pairing future-dir; do\n"
        )
        patched = module.patch_managed_dir_allowlist(source)
        self.assertIn("future-dir kanban .local/state/hermes; do", patched)
        self.assertEqual(module.patch_managed_dir_allowlist(patched), patched)

    def test_raises_when_required_anchors_are_missing(self):
        module = load_module()
        with self.assertRaises(ValueError):
            module.patch_source("before\nafter\n")


if __name__ == "__main__":
    unittest.main()
