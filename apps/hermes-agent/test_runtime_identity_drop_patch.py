import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_runtime_identity_drop.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_runtime_identity_drop',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RuntimeIdentityDropPatchTests(unittest.TestCase):
    def test_patches_main_wrapper_drop_to_numeric_runtime_identity(self):
        module = load_module()
        source = f'before\n{module.MAIN_DROP_OLD}after\n'

        patched = module.patch_main_wrapper_source(source)

        self.assertIn(module.MAIN_MARKER, patched)
        self.assertIn('SPARTAN_HERMES_RUN_UID', patched)
        self.assertIn('/command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid"', patched)
        self.assertIn('/command/s6-setuidgid hermes "$@"', patched)
        self.assertNotIn('set -- s6-setuidgid hermes "$@"', patched)

    def test_patches_dashboard_drop_to_numeric_runtime_identity(self):
        module = load_module()
        source = f'before\n{module.DASHBOARD_DROP_OLD}after\n'

        patched = module.patch_dashboard_source(source)

        self.assertIn(module.DASHBOARD_MARKER, patched)
        self.assertIn('SPARTAN_HERMES_RUN_GID', patched)
        self.assertIn('/command/s6-applyuidgid -u "$_spartan_run_uid" -g "$_spartan_run_gid" hermes dashboard', patched)
        self.assertIn('/command/s6-setuidgid hermes hermes dashboard', patched)
        self.assertNotIn('exec s6-setuidgid hermes hermes dashboard', patched)

    def test_idempotent_when_markers_present(self):
        module = load_module()

        self.assertEqual(
            module.patch_main_wrapper_source(f'before\n{module.MAIN_MARKER}\nafter\n'),
            f'before\n{module.MAIN_MARKER}\nafter\n',
        )
        self.assertEqual(
            module.patch_dashboard_source(f'before\n{module.DASHBOARD_MARKER}\nafter\n'),
            f'before\n{module.DASHBOARD_MARKER}\nafter\n',
        )

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_main_wrapper_source('before\nafter\n')
        with self.assertRaises(ValueError):
            module.patch_dashboard_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
