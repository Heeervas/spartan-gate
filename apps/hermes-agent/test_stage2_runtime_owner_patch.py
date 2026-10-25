import importlib.util
import os
import pathlib
import subprocess
import tempfile
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


def _identity_script(module) -> str:
    return """set -eu
validate_uid_gid() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) [ "$1" -ge 1 ] && [ "$1" -le 65534 ] ;;
    esac
}
""" + module.RUNTIME_IDENTITY_BLOCK.replace(
        '"$INSTALL_DIR/.venv/bin/python"', "python3"
    )


def _preflight_script(module) -> str:
    return "set -eu\nINSTALL_DIR=/opt/hermes\n" + module.OWNERSHIP_PREFLIGHT_BLOCK.replace(
        '"$INSTALL_DIR/.venv/bin/python"', "python3"
    )


def upstream_shape() -> str:
    return r"""#!/bin/sh
as_hermes() { [ "$(id -u)" = 0 ] || { "$@"; return; }; s6-setuidgid hermes "$@"; }

# --- Bootstrap HERMES_HOME as root ---
# Create the directory (and any missing parents) while we still have root
# privileges so the chown checks below see real metadata and the later
# `s6-setuidgid hermes mkdir -p` block doesn't EACCES on root-owned
# ancestors. Without this, custom HERMES_HOME paths whose parents only
# root can create (e.g. `HERMES_HOME=/ho""" + r"""me/hermes/.hermes` in a Compose
# file, or any path under a fresh / not pre-populated by the image)
# fail on first boot with `mkdir: cannot create directory '/...': Permission
# denied` and the cont-init hook exits non-zero. Idempotent — `mkdir -p`
# is a no-op if the dir already exists. (#18482, salvages #18488)
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

chown_hermes_tree() {
    target="$1"
    chown -R hermes:hermes "$target"
}

tree_has_non_hermes_owner() {
    target="$1"
    find "$target" \( ! -user hermes -o ! -group hermes \) -print -quit 2>/dev/null | grep -q .
}

needs_chown=false
if [ "$needs_chown" = true ]; then
    echo "[stage2] Fixing ownership of $HERMES_HOME (targeted) to hermes ($actual_hermes_uid)"
    chown hermes:hermes "$HERMES_HOME"
    for sub in cron sessions logs hooks memories skills skins plans workspace home profiles pairing platforms/pairing lazy-packages; do
        chown_hermes_tree "$HERMES_HOME/$sub"
    done
fi

chown hermes:hermes "$HERMES_HOME/logs/gateways"

# --- Immutable install tree ---
# Upstream keeps /opt/hermes root-owned.

for f in __BS__
    auth.json auth.lock .env __BS__
    state.db state.db-shm state.db-wal __BS__
    hermes_state.db __BS__
    response_store.db response_store.db-shm response_store.db-wal __BS__
    gateway.pid gateway.lock gateway_state.json processes.json __BS__
    active_profile; do
    chown hermes:hermes "$HERMES_HOME/$f"
done
chown hermes:hermes "$HERMES_HOME/config.yaml"
chown hermes:hermes "$HERMES_HOME/.env"
chown hermes:hermes "$HERMES_HOME/auth.json"
chown hermes:hermes "$HERMES_HOME/gateway_state.json"

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
        self.assertIn(module.OWNERSHIP_PREFLIGHT_MARKER, patched)
        self.assertIn('actual_hermes_uid="$SPARTAN_HERMES_RUN_UID"', patched)
        self.assertIn('export SPARTAN_HERMES_RUN_UID SPARTAN_HERMES_RUN_GID', patched)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME"', patched)
        self.assertIn(module.PHYSICAL_CHOWN_REPLACEMENT, patched)
        self.assertNotIn('chown -hR "$SPARTAN_HERMES_RUNTIME_OWNER" "$target"', patched)
        self.assertIn('! -uid "$SPARTAN_HERMES_RUN_UID"', patched)
        self.assertIn('! -gid "$SPARTAN_HERMES_RUN_GID"', patched)
        self.assertIn(
            'chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME/logs/gateways"',
            patched,
        )
        self.assertIn('"$HERMES_HOME/kanban"', patched)
        self.assertIn('"$HERMES_HOME/.local/state/hermes/gateway-locks"', patched)
        self.assertIn("platforms/pairing lazy-packages kanban .local/state/hermes; do", patched)
        self.assertLess(patched.index(module.OWNERSHIP_PREFLIGHT_MARKER), patched.index('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$HERMES_HOME"'))
        self.assertIn("nested mount in ownership target", patched)
        self.assertIn("chown -h uses physical traversal", patched)
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

    def test_unknown_existing_guard_or_traversal_fails_closed(self):
        module = load_module()
        patched = module.patch_source(upstream_shape())
        for corrupted in (
            patched.replace("# Validate all targets", "# Unknown guard"),
            patched.replace(module.OWNERSHIP_PREFLIGHT_MARKER, "# Missing guard marker"),
            patched.replace(module.PHYSICAL_CHOWN_REPLACEMENT, 'chown -R "$target"'),
            patched.replace(module.PRUNED_TREE_OWNER_REPLACEMENT, 'find "$target" -print'),
        ):
            with self.subTest(source=corrupted[-50:]):
                with self.assertRaisesRegex(ValueError, "guard|traversal anchor"):
                    module.patch_source(corrupted)

    def test_managed_dir_allowlist_rejects_unknown_upstream_entries(self):
        module = load_module()
        source = (
            "for sub in cron sessions logs hooks profiles pairing "
            "platforms/pairing future-dir; do\n"
        )
        with self.assertRaisesRegex(ValueError, "allowlist anchor"):
            module.patch_managed_dir_allowlist(source)

    def test_managed_file_allowlist_rejects_upstream_drift(self):
        module = load_module()
        source = upstream_shape().replace("auth.json auth.lock .env", "auth.json future.db .env")
        with self.assertRaisesRegex(ValueError, "top-level file allowlist changed"):
            module.patch_source(source)

    def test_incomplete_pair_fails_before_home_creation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "home"
            result = subprocess.run(
                ["sh", "-c", _identity_script(module)],
                env={
                    **os.environ,
                    "HERMES_HOME": str(home),
                    "SPARTAN_HERMES_RUN_UID": "1000",
                    "SPARTAN_HERMES_RUN_GID": "",
                    "HERMES_UID": "",
                    "HERMES_GID": "",
                    "PUID": "",
                    "PGID": "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(home.exists())

    def test_conflicting_explicit_pairs_fail_before_home_creation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "home"
            result = subprocess.run(
                ["sh", "-c", _identity_script(module)],
                env={
                    **os.environ,
                    "HERMES_HOME": str(home),
                    "SPARTAN_HERMES_RUN_UID": "1000",
                    "SPARTAN_HERMES_RUN_GID": "1000",
                    "HERMES_UID": "2000",
                    "HERMES_GID": "2000",
                    "PUID": "",
                    "PGID": "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(home.exists())

    def test_symlinked_home_parent_fails_before_creation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "link"
            link.symlink_to(outside, target_is_directory=True)
            home = link / "home"
            result = subprocess.run(
                ["sh", "-c", _identity_script(module)],
                env={
                    **os.environ,
                    "HERMES_HOME": str(home),
                    "SPARTAN_HERMES_RUN_UID": "1000",
                    "SPARTAN_HERMES_RUN_GID": "1000",
                    "HERMES_UID": "",
                    "HERMES_GID": "",
                    "PUID": "",
                    "PGID": "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink in HERMES_HOME path", result.stderr)
            self.assertFalse(home.exists())

    def test_recursive_preflight_does_not_follow_descendant_links(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "data"
            target = root / "state" / ".claude" / "skills" / "safe"
            target.mkdir(parents=True)
            skills = root / "skills"
            skills.mkdir()
            (skills / "safe").symlink_to(pathlib.Path("../state/.claude/skills/safe"))
            (skills / "python").symlink_to(pathlib.Path("/usr/bin/python3"))
            (skills / "broken").symlink_to(pathlib.Path("missing-target"))

            result = subprocess.run(
                ["sh", "-c", _preflight_script(module)],
                env={**os.environ, "HERMES_HOME": str(root)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_recursive_preflight_rejects_symlinked_managed_root(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = base / "data"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (root / "skills").symlink_to(outside, target_is_directory=True)

            result = subprocess.run(
                ["sh", "-c", _preflight_script(module)],
                env={**os.environ, "HERMES_HOME": str(root)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ownership target is a symlink", result.stderr)

    def test_raises_when_required_anchors_are_missing(self):
        module = load_module()
        with self.assertRaises(ValueError):
            module.patch_source("before\nafter\n")


@unittest.skipUnless(os.environ.get("HERMES_STAGE2_TEST_IMAGE"), "explicit isolated Docker fixture image required")
class Stage2BindBoundaryTests(unittest.TestCase):
    """Real mountinfo/stat and physical ownership repair, never native bootstrap."""

    def run_fixture(self, variant="canonical", home_spelling="/opt/data", mount_options="Z", nested_parent="/opt/data/skills/shared"):
        module = load_module()
        source_path = os.environ.get("HERMES_STAGE2_TEST_SOURCE")
        if source_path:
            source = pathlib.Path(source_path).read_text(encoding="utf-8")
            if not os.environ.get("HERMES_STAGE2_TEST_OLD_GUARD"):
                source = module.patch_source(source)
            start = source.index("path_has_symlink_component() {")
            ownership = source[start:source.index("needs_chown=false\n", start)]
        else:
            source = module.patch_source(upstream_shape())
            start = source.index("chown_hermes_tree() {")
            ownership = source[start:source.index("needs_chown=false\n", start)]
        script = '''set -eu
INSTALL_DIR=/opt/hermes
HERMES_HOME=/opt/data
SPARTAN_HERMES_RUN_UID=12345
SPARTAN_HERMES_RUN_GID=12346
SPARTAN_HERMES_RUNTIME_OWNER=12345:12346
''' + ownership + '''
# A successful all-target preflight must precede even this synthetic write.
touch "$HERMES_HOME/write-started"
if tree_has_non_hermes_owner "$HERMES_HOME/skills"; then
    chown_hermes_tree "$HERMES_HOME/skills"
fi
'''
        script = script.replace("HERMES_HOME=/opt/data\n", f"HERMES_HOME={home_spelling}\n", 1)
        with tempfile.TemporaryDirectory(dir=os.environ.get("HERMES_STAGE2_TEST_TMPDIR")) as directory:
            base = pathlib.Path(directory).resolve()
            home, shared, other = (base / name for name in ("data", "shared", "other"))
            for path in (home / "skills" / "shared", shared / "nested", other):
                path.mkdir(parents=True)
            # Repair changes this synthetic directory's owner; keep it removable
            # by the unprivileged host test runner without a privileged cleanup.
            (home / "skills").chmod(0o777)
            (home / "skills" / "ordinary").write_bytes(b"managed")
            (shared / "nested" / "payload").write_bytes(b"canonical must not change")
            (home / "skills" / "external").symlink_to("/opt/shared-skills/nested/payload")
            (home / "skills" / "broken").symlink_to("missing-target")
            if variant == "unbound":
                (home / "skills" / "shared" / "payload").write_bytes(b"ordinary unbound skills")
                (home / "skills" / "shared").chmod(0o777)
            if variant == "symlink":
                (home / "profiles").symlink_to("/opt/shared-skills")
            if variant == "nested":
                (shared / "unexpected").mkdir()
            before = self.snapshot(shared)
            command = ["docker", "run", "--rm", "-i", "--network", "none", "--user", "0:0", "--entrypoint", "/bin/sh"]
            command += ["--volume", f"{home}:/opt/data:Z"]
            if variant != "unbound":
                command += ["--volume", f"{shared}:/opt/data/skills/shared:{mount_options}"]
            if variant not in ("partial", "unbound"):
                alias = other if variant == "wrong-alias" else shared
                command += ["--volume", f"{alias}:/opt/shared-skills:Z"]
            if variant == "nested":
                command += ["--volume", f"{other}:{nested_parent}/unexpected:Z"]
            command += [os.environ["HERMES_STAGE2_TEST_IMAGE"], "-s"]
            result = subprocess.run(command, input=script, capture_output=True, text=True, check=False)
            self.assertEqual(self.snapshot(shared), before, result.stderr)
            if variant in ("canonical", "unbound"):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((home / "write-started").exists())
                for name in ("ordinary", "external", "broken"):
                    metadata = (home / "skills" / name).lstat()
                    self.assertEqual((metadata.st_uid, metadata.st_gid), (12345, 12346), name)
                if variant == "unbound":
                    metadata = (home / "skills" / "shared" / "payload").stat()
                    self.assertEqual((metadata.st_uid, metadata.st_gid), (12345, 12346))
                # Once ordinary ownership is repaired, detection must prune the
                # differently-owned canonical branch rather than report it.
                probe = script[:script.index('touch "$HERMES_HOME/write-started"')] + '''
if tree_has_non_hermes_owner "$HERMES_HOME/skills"; then exit 42; fi
'''
                repeated = subprocess.run(command, input=probe, capture_output=True, text=True, check=False)
                self.assertEqual(repeated.returncode, 0, repeated.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("FATAL", result.stderr)
                self.assertFalse((home / "write-started").exists())
                self.assertEqual((home / "skills" / "ordinary").stat().st_uid, os.getuid())

    @staticmethod
    def snapshot(root):
        return {
            str(path.relative_to(root)): (
                path.lstat().st_mode, path.lstat().st_uid, path.lstat().st_gid,
                path.read_bytes() if path.is_file() else None,
            )
            for path in (root, *root.rglob("*"))
        }

    def test_actual_same_source_bind_is_pruned_during_forced_repair(self):
        self.run_fixture()

    def test_unbound_shared_is_still_owned(self):
        self.run_fixture("unbound")

    def test_bound_home_requires_exact_shell_path_spelling(self):
        for spelling in ("/opt/data/", "/opt//data", "/opt/./data"):
            with self.subTest(spelling=spelling):
                # A defective guard must not corrupt even the synthetic
                # collection during RED probes that reach physical repair.
                self.run_fixture("spelling", spelling, mount_options="ro,Z")

    def test_unbound_home_preserves_alternate_path_spellings(self):
        for spelling in ("/opt/data/", "/opt//data", "/opt/./data"):
            with self.subTest(spelling=spelling):
                self.run_fixture("unbound", spelling)

    def test_alias_only_nested_mount_refuses_before_any_write(self):
        self.run_fixture("nested", nested_parent="/opt/shared-skills")

    def test_unsafe_boundaries_refuse_before_any_write(self):
        for variant in ("wrong-alias", "partial", "nested", "symlink"):
            with self.subTest(variant=variant):
                self.run_fixture(variant)


if __name__ == "__main__":
    unittest.main()
