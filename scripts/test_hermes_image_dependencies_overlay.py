import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

SPEC = importlib.util.spec_from_file_location('dependency_overlay', Path(__file__).with_name('hermes-image-dependencies-overlay.py'))
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


class DependencyOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = 'sha256:' + '1' * 64
        files = {n: b'# fixture\n' for n in (*overlay.validator.CODE_FILES, *overlay.validator.CODE_ANCHORS)}
        for n, value in files.items():
            path = self.root / n
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            path.chmod(0o444)
        manifest = {'schema': 1, 'image': self.image,
                    'files': {n: overlay.digest(files[n]) for n in overlay.validator.CODE_FILES},
                    'anchors': {n: overlay.digest(files[n]) for n in overlay.validator.CODE_ANCHORS},
                    'originals': {n: overlay.digest(files[n]) for n in overlay.validator.CODE_FILES}}
        for n in overlay.validator.CODE_FILES:
            path = self.root / 'original' / n
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[n])
        self.manifest = self.root / 'manifest.json'
        self.raw = json.dumps(manifest).encode()
        self.manifest.write_bytes(self.raw)
        config = {'services': {'hermes': {'image': self.image, 'environment': {
            'SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE': self.image,
            'SPARTAN_HERMES_DEPENDENCY_CODE_MANIFEST': overlay.validator.CODE_MANIFEST,
            'SPARTAN_HERMES_DEPENDENCY_CODE_SHA256': overlay.digest(self.raw)}, 'volumes': [
                {'type': 'bind', 'source': str(self.root / n), 'target': '/opt/hermes/' + n,
                 'read_only': True, 'bind': {'create_host_path': False}} for n in overlay.validator.CODE_FILES] + [
                {'type': 'bind', 'source': str(self.manifest), 'target': overlay.validator.CODE_MANIFEST,
                 'read_only': True, 'bind': {'create_host_path': False}}]}}}
        self.path = self.root / 'compose.yml'
        self.path.write_text(yaml.safe_dump(config))

    def test_image_code_anchors_and_rollback_are_fingerprinted(self):
        with patch.object(overlay, 'OVERLAY', self.path), patch.object(overlay, 'run', return_value=self.image.encode()):
            overlay.check(self.image)
            for name in (*overlay.validator.CODE_FILES, *overlay.validator.CODE_ANCHORS,
                         'original/' + overlay.validator.CODE_FILES[0]):
                path = self.root / name
                previous = path.read_bytes()
                path.chmod(0o644)
                path.write_bytes(b'changed\n')
                path.chmod(0o444)
                with self.assertRaises((ValueError, RuntimeError)):
                    overlay.check(self.image)
                path.chmod(0o644)
                path.write_bytes(previous)
                path.chmod(0o444)
        with patch.object(overlay, 'OVERLAY', self.path), patch.object(overlay, 'run', return_value=b'sha256:' + b'2' * 64):
            with self.assertRaises(ValueError):
                overlay.check(self.image)

    def test_writable_or_automatic_code_mount_is_rejected(self):
        config = yaml.safe_load(self.path.read_text())
        config['services']['hermes']['volumes'][0]['read_only'] = False
        self.path.write_text(yaml.safe_dump(config))
        with patch.object(overlay, 'OVERLAY', self.path), self.assertRaises(ValueError):
            overlay.overlay_data()

    def test_check_uses_effective_compose_image_without_its_own_pin(self):
        def run(args):
            if args[:2] == ['docker', 'compose']:
                self.assertNotIn(str(self.path), args)
                return json.dumps({'services': {'hermes': {'image': 'operator-selected-image:current'}}}).encode()
            self.assertEqual(args[-1], 'operator-selected-image:current')
            return self.image.encode()
        with patch.object(overlay, 'OVERLAY', self.path), patch.object(overlay, 'ROOT', self.root), \
                patch.object(overlay, 'run', side_effect=run):
            overlay.check()

    def test_recreated_container_aborts_before_overlay_publication(self):
        root = self.root / 'repository'
        (root / 'private').mkdir(parents=True)
        source = root / 'apps/hermes-agent/patches/validate_image_dependencies.py'
        source.parent.mkdir(parents=True)
        source.write_text('# fixture validator\n')
        destination = root / 'private/compose.hermes-image-dependencies.yml'
        inspections = 0
        def run(args):
            nonlocal inspections
            if args[:2] == ['docker', 'inspect']:
                inspections += 1
                return f'container-{inspections} {self.image} started\n'.encode()
            if args[:2] == ['docker', 'exec']:
                self.assertEqual(args[2], 'container-1')
                if args[-1].endswith('hermes_cli/venv_sync.py'):
                    return b'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n'
                return b'# fixture\n'
            return b''
        with patch.object(overlay, 'ROOT', root), patch.object(overlay, 'OVERLAY', destination), \
                patch.object(overlay, 'run', side_effect=run), \
                patch.object(overlay.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 6_000_000_000})()):
            with self.assertRaisesRegex(ValueError, 'container changed'):
                overlay.prepare('running-container')
        self.assertFalse(destination.exists())
        self.assertFalse((root / 'private/runtime').exists())

    def test_alias_build_guard_includes_build_flags_and_leaves_other_services_alone(self):
        source = Path(__file__).with_name('aliases.sh').read_text()
        start = source.index('sg_hermes_image_dependencies_preflight() {')
        function = source[start:source.index('\nsg_route_compose()', start)]
        for arguments, expected in [('build', 1), ('up --build', 1), ('up --build --scale hermes=2', 1),
                                    ('build hermes', 1), ('build spartan-route', 0),
                                    ('--tier L2 up hermes', 1), ('--tier L2 build spartan-route', 0)]:
            result = subprocess.run(['bash', '-c', function + '\nsg_hermes_image_dependencies_preflight ' + arguments], capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == '__main__':
    unittest.main()
