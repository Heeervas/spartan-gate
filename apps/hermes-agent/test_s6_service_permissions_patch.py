import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_s6_service_permissions.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_s6_service_permissions',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class S6ServicePermissionsPatchTests(unittest.TestCase):
    def test_reconcile_runs_container_boot_as_root_without_chowning_scandir(self):
        module = load_module()
        source = f'''before
{module.RECONCILE_OLD}
after
'''

        patched = module.patch_reconcile_source(source)

        self.assertIn(module.RECONCILE_MARKER, patched)
        self.assertIn('exec /opt/hermes/.venv/bin/python -m hermes_cli.container_boot', patched)
        self.assertNotIn('chown hermes:hermes /run/service', patched)
        self.assertNotIn('/run/service/.s6-svscan/$entry', patched)
        self.assertNotIn('exec s6-setuidgid hermes /opt/hermes/.venv/bin/python -m hermes_cli.container_boot', patched)

    def test_reconcile_patch_is_idempotent(self):
        module = load_module()
        source = f'before\n{module.RECONCILE_MARKER}\nafter\n'

        self.assertEqual(module.patch_reconcile_source(source), source)

    def test_service_manager_disables_non_root_runtime_registration(self):
        module = load_module()
        source = f'''class S6ServiceManager:
{module.SERVICE_MANAGER_OLD}
'''

        patched = module.patch_service_manager_source(source)

        self.assertIn(module.SERVICE_MANAGER_MARKER, patched)
        self.assertIn('return os.geteuid() == 0', patched)
        self.assertNotIn('return True', patched)

    def test_service_manager_patch_is_idempotent(self):
        module = load_module()
        source = f'before\n{module.SERVICE_MANAGER_MARKER}\nafter\n'

        self.assertEqual(module.patch_service_manager_source(source), source)

    def test_raises_when_anchor_missing(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.patch_reconcile_source('before\nafter\n')
        with self.assertRaises(ValueError):
            module.patch_service_manager_source('before\nafter\n')


if __name__ == '__main__':
    unittest.main()
