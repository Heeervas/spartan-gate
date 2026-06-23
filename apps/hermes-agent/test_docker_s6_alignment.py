import pathlib
import unittest


APP_DIR = pathlib.Path(__file__).parent


class DockerS6AlignmentTests(unittest.TestCase):
    def test_entrypoint_wrapper_does_not_start_unsupervised_profile_gateways(self):
        source = (APP_DIR / 'entrypoint-wrapper.sh').read_text(encoding='utf-8')

        self.assertNotIn('HERMES_AUTOSTART_PROFILES:-}" ]; then', source)
        self.assertNotIn('hermes -p "${profile_name}" gateway run', source)
        self.assertNotIn('[profile:${profile_name}]', source)
        self.assertIn('exec /opt/hermes/docker/main-wrapper.sh "$@"', source)

    def test_entrypoint_wrapper_does_not_background_cdp_broker(self):
        source = (APP_DIR / 'entrypoint-wrapper.sh').read_text(encoding='utf-8')

        self.assertNotIn('browserless-cdp-broker.py" \\', source)
        self.assertNotIn('broker_pid=$!', source)
        self.assertNotIn('browserless-cdp-broker.log', source)

    def test_dockerfile_uses_s6_safe_patches(self):
        source = (APP_DIR / 'Dockerfile').read_text(encoding='utf-8')

        self.assertNotIn('patch_api_server_port_autoincrement.py', source)
        self.assertIn('patch_s6_service_permissions.py', source)


if __name__ == '__main__':
    unittest.main()
