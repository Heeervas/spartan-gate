import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_gateway_service_cdp_env.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_gateway_service_cdp_env',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class GatewayServiceCdpEnvPatchTests(unittest.TestCase):
    def test_imports_cdp_env_after_venv_activation(self):
        module = load_module()
        source = (
            f'before\n_HERMES_UID = 10000\n_HERMES_GID = 10000\n{module.ANCHOR}\n'
            '        lines.append(f"exec s6-setuidgid hermes {gateway_cmd}")\n'
            '            f\'chown hermes:hermes "$HERMES_HOME/logs/gateways" 2>/dev/null || true\\n\'\n'
            '            f\'chown -R hermes:hermes "$log_dir" 2>/dev/null || true\\n\'\n'
            '            f\'exec s6-setuidgid hermes s6-log 1 n10 s1000000 T "$log_dir"\\n\'\n'
            'after\n'
        )

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertNotIn('umask 0002', patched)
        self.assertIn('BROWSER_CDP_URL BROWSER_CDP_MAIN_URL BROWSER_CDP_LAUNCH_URL', patched)
        self.assertIn('BROWSERLESS_CDP_BROKER_ENABLED BROWSERLESS_CDP_BROKER_PORT', patched)
        self.assertIn('CAMOFOX_URL CAMOFOX_ACCESS_KEY CAMOFOX_USER_ID CAMOFOX_SESSION_KEY', patched)
        self.assertIn('/run/s6/container_environment/$_hermes_browser_key', patched)
        self.assertIn('export \\"$_hermes_browser_key=$(cat \\"$_hermes_browser_file\\")\\"', patched)
        self.assertIn('nc -z 127.0.0.1 \\"$BROWSERLESS_CDP_BROKER_PORT\\"', patched)
        self.assertIn('Browserless CDP broker not ready', patched)
        self.assertIn('export BROWSER_CDP_URL=\\"$BROWSER_CDP_LAUNCH_URL\\"', patched)
        self.assertIn('using launch CDP URL', patched)
        self.assertIn('_hermes_broker_attempt -lt 50', patched)
        self.assertIn(module.RUNTIME_OWNER_MARKER, patched)
        self.assertIn('_HERMES_UID = int(_spartan_os.environ.get("SPARTAN_HERMES_RUN_UID") or "10000")', patched)
        self.assertIn('_HERMES_GID = int(_spartan_os.environ.get("SPARTAN_HERMES_RUN_GID") or "10000")', patched)
        self.assertIn(module.RUNTIME_IDENTITY_MARKER, patched)
        self.assertIn('/command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid"', patched)
        self.assertIn('SPARTAN_HERMES_RUN_UID', patched)
        self.assertIn('SPARTAN_HERMES_RUN_GID', patched)
        self.assertIn('chown "${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}:${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}" "$HERMES_HOME/logs/gateways"', patched)
        self.assertIn('chown -R "${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}:${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}" "$log_dir"', patched)
        self.assertIn('        lines.append(f"exec /command/s6-setuidgid hermes {gateway_cmd}")', patched)
        self.assertNotIn('SPARTAN_HERMES_DATA_GID', patched)
        self.assertNotIn('-G "$SPARTAN_HERMES_DATA_GID"', patched)
        self.assertLess(patched.index(module.ANCHOR), patched.index(module.MARKER))

    def test_idempotent_when_marker_present(self):
        module = load_module()
        source = f'before\n{module.MARKER}\n{module.RUNTIME_OWNER_MARKER}\n{module.RUNTIME_IDENTITY_MARKER}\nafter\n'

        self.assertEqual(module.patch_source(source), source)

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
