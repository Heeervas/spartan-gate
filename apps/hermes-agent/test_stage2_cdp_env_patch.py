import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_stage2_cdp_env.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_stage2_cdp_env',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Stage2CdpEnvPatchTests(unittest.TestCase):
    def test_injects_cdp_env_before_agent_browser_discovery(self):
        module = load_module()
        source = f'before\n{module.ANCHOR}\nafter\n'

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn('mkdir -p /run/s6/container_environment', patched)
        self.assertIn('browserless-cdp-url.js', patched)
        self.assertIn('browserless-cdp-broker.py', patched)
        self.assertIn('spartan-browserless-cdp-broker', patched)
        self.assertIn('/command/s6-applyuidgid', patched)
        self.assertIn('SPARTAN_HERMES_RUN_UID', patched)
        self.assertIn('SPARTAN_HERMES_RUN_GID', patched)
        self.assertIn('/command/s6-setuidgid hermes "$0" "$@"', patched)
        self.assertIn('chown "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}:${SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}" "$log_dir"', patched)
        self.assertIn('chown "$SPARTAN_HERMES_RUNTIME_OWNER" "$state_file"', patched)
        self.assertIn('/command/s6-setuidgid hermes s6-log', patched)
        self.assertNotIn('SPARTAN_HERMES_DATA_GID', patched)
        self.assertNotIn('-G "$SPARTAN_HERMES_DATA_GID"', patched)
        self.assertNotIn('chmod -R g+rwX "$log_dir"', patched)
        self.assertIn('BROWSERLESS_CDP_BROKER_RETRY_SECONDS', patched)
        self.assertIn('retrying in ${retry_seconds}s', patched)
        self.assertIn('seed_legacy_autostart_profile_state', patched)
        self.assertIn('seed_spartan_default_gateway_state', patched)
        self.assertIn('seed_spartan_supervise_skeleton', patched)
        self.assertIn('/opt/hermes/entrypoint-wrapper.sh gateway run', patched)
        self.assertIn('migrated_from":"spartan-container-cmd', patched)
        self.assertIn("sed 's/^[[:space:]]*//; s/[[:space:]]*$//'", patched)
        self.assertIn("profile 'default' is reserved", patched)
        self.assertIn('HERMES_AUTOSTART_PROFILES profile', patched)
        self.assertIn('migrated_from":"HERMES_AUTOSTART_PROFILES', patched)
        self.assertIn('mkfifo "$svc_dir/supervise/control"', patched)
        self.assertIn('/run/s6/container_environment/BROWSER_CDP_LAUNCH_URL', patched)
        self.assertIn('/run/s6/container_environment/BROWSER_CDP_MAIN_URL', patched)
        self.assertIn('/run/s6/container_environment/BROWSER_CDP_URL', patched)
        self.assertIn('/run/s6/container_environment/BROWSERLESS_CDP_BROKER_ENABLED', patched)
        self.assertIn('/run/s6/container_environment/BROWSERLESS_CDP_BROKER_PORT', patched)
        self.assertIn('BROWSER_CDP_MAIN_URL="ws://127.0.0.1:${BROWSERLESS_CDP_BROKER_PORT}"', patched)
        self.assertIn('camofox_browser_mode_enabled()', patched)
        self.assertIn('CAMOFOX_URL set; Browserless CDP env skipped for supervised services', patched)
        self.assertIn('! camofox_browser_mode_enabled && [ "${BROWSERLESS_CDP_BROKER_ENABLED:-true}" = "true" ]', patched)
        self.assertIn('! camofox_browser_mode_enabled && [ -n "${BROWSER_CDP_MAIN_URL:-}" ]', patched)
        self.assertIn('! camofox_browser_mode_enabled && [ -z "${BROWSER_CDP_URL:-}" ]', patched)
        self.assertIn('rm -f /run/s6/container_environment/BROWSER_CDP_URL', patched)
        self.assertIn('Browserless CDP env prepared for supervised services', patched)
        self.assertIn('Browserless CDP broker registered under s6', patched)
        self.assertLess(patched.index(module.MARKER), patched.index(module.ANCHOR))

    def test_idempotent_when_marker_present(self):
        module = load_module()
        source = f'before\n{module.MARKER}\nafter\n'

        self.assertEqual(module.patch_source(source), source)

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
