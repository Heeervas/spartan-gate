import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_gateway_retryable_startup.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_gateway_retryable_startup',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class GatewayRetryableStartupPatchTests(unittest.TestCase):
    def test_patches_retryable_startup_exit(self):
        module = load_module()
        source = f'before\n{module.OLD_BLOCK}\nafter\n'

        patched = module.patch_source(source)

        self.assertIn(module.MARKER, patched)
        self.assertIn('Gateway will stay up and retry in background', patched)
        self.assertIn('if self._failed_platforms:', patched)
        self.assertIn('else:\n                    try:', patched)
        self.assertIn('return False', patched)

    def test_is_idempotent_when_marker_present(self):
        module = load_module()
        source = f'before\n{module.MARKER}\nafter\n'

        self.assertEqual(module.patch_source(source), source)

    def test_skips_when_upstream_already_absorbed_behavior(self):
        module = load_module()
        source = '''
                    logger.warning(
                        "Gateway started with no connected platforms — "
                        "%d platform(s) queued for retry: %s",
                    )
                    write_runtime_status(
                        gateway_state="degraded",
                        exit_reason=None,
                    )
'''

        self.assertEqual(module.patch_source(source), source)

    def test_raises_when_anchor_changes(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
