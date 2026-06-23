import importlib.util
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_checkpoint_rollback_scan.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_checkpoint_rollback_scan',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class CheckpointRollbackScanPatchTests(unittest.TestCase):
    def test_appends_scope_helpers_and_missing_handlers(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cm = root / 'checkpoint_manager.py'
            gw = root / 'run.py'
            cli = root / 'cli.py'
            cm.write_text('class CheckpointManager:\n    pass\n', encoding='utf-8')
            gw.write_text(
                'class GatewayRunner:\n    pass\n'
                'async def route(self, event):\n'
                '    return await self._handle_rollback_command(event)\n',
                encoding='utf-8',
            )
            cli.write_text(
                'class HermesCLI:\n'
                '    def _resolve_checkpoint_ref(self, ref, checkpoints):\n'
                '        return ref\n'
                'def route(self, cmd_original):\n'
                '    self._handle_rollback_command(cmd_original)\n',
                encoding='utf-8',
            )
            module.CM_TARGET = cm
            module.GW_TARGET = gw
            module.CLI_TARGET = cli

            module.main()

            self.assertIn(module.CM_MARKER, cm.read_text(encoding='utf-8'))
            self.assertIn('def resolve_checkpoint_cwd', cm.read_text(encoding='utf-8'))
            self.assertIn(module.GW_MARKER, gw.read_text(encoding='utf-8'))
            self.assertIn(
                'GatewayRunner._handle_rollback_command = '
                '_spartan_gateway_handle_rollback_command',
                gw.read_text(encoding='utf-8'),
            )
            self.assertIn(module.CLI_MARKER, cli.read_text(encoding='utf-8'))
            self.assertIn(
                'HermesCLI._handle_rollback_command = '
                '_spartan_cli_handle_rollback_command',
                cli.read_text(encoding='utf-8'),
            )

    def test_patch_is_idempotent(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cm = root / 'checkpoint_manager.py'
            gw = root / 'run.py'
            cli = root / 'cli.py'
            cm.write_text('base\n', encoding='utf-8')
            gw.write_text(
                'return await self._handle_rollback_command(event)\n',
                encoding='utf-8',
            )
            cli.write_text(
                'self._handle_rollback_command(cmd_original)\n',
                encoding='utf-8',
            )
            module.CM_TARGET = cm
            module.GW_TARGET = gw
            module.CLI_TARGET = cli

            module.main()
            once = (cm.read_text(encoding='utf-8'), gw.read_text(encoding='utf-8'), cli.read_text(encoding='utf-8'))
            module.main()

            self.assertEqual(once[0], cm.read_text(encoding='utf-8'))
            self.assertEqual(once[1], gw.read_text(encoding='utf-8'))
            self.assertEqual(once[2], cli.read_text(encoding='utf-8'))

    def test_fails_when_callsite_is_missing(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cm = root / 'checkpoint_manager.py'
            gw = root / 'run.py'
            cli = root / 'cli.py'
            cm.write_text('base\n', encoding='utf-8')
            gw.write_text('class GatewayRunner:\n    pass\n', encoding='utf-8')
            cli.write_text('self._handle_rollback_command(cmd_original)\n', encoding='utf-8')
            module.CM_TARGET = cm
            module.GW_TARGET = gw
            module.CLI_TARGET = cli

            with self.assertRaises(SystemExit):
                module.main()


if __name__ == '__main__':
    unittest.main()
