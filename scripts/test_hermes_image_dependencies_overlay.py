import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import zlib
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
            path.chmod(0o555 if n == 'docker/stage2-hook.sh' else 0o444)
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
                mode = path.stat().st_mode & 0o777
                path.chmod(0o644)
                path.write_bytes(b'changed\n')
                path.chmod(mode)
                with self.assertRaises((ValueError, RuntimeError)):
                    overlay.check(self.image)
                path.chmod(0o644)
                path.write_bytes(previous)
                path.chmod(mode)
        with patch.object(overlay, 'OVERLAY', self.path), patch.object(overlay, 'run', return_value=b'sha256:' + b'2' * 64):
            with self.assertRaises(ValueError):
                overlay.check(self.image)

    def test_nonexecutable_stage2_is_refused_by_check_and_bundle_reuse(self):
        stage2 = self.root / 'docker/stage2-hook.sh'
        with patch.object(overlay, 'OVERLAY', self.path), patch.object(overlay, 'run', return_value=self.image.encode()):
            overlay.check(self.image)
            overlay.publish_bundle(self.root, {'docker/stage2-hook.sh': stage2.read_bytes()})
            stage2.chmod(0o444)
            with self.subTest(boundary='check'), self.assertRaisesRegex(ValueError, 'executable'):
                overlay.check(self.image)
            with self.subTest(boundary='reuse'), self.assertRaisesRegex(ValueError, 'executable'):
                overlay.publish_bundle(self.root, {'docker/stage2-hook.sh': stage2.read_bytes()})

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
        (root / 'infra/compose').mkdir(parents=True)
        (root / 'infra/compose/compose.yml').write_text('services: {}\n')
        source = root / 'apps/hermes-agent/patches/validate_image_dependencies.py'
        source.parent.mkdir(parents=True)
        source.write_bytes((overlay.ROOT / 'apps/hermes-agent/patches/validate_image_dependencies.py').read_bytes())
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
                    return ('def prepare_launch(project_root, argv):\n' + overlay.patcher.EARLY_ANCHOR + '\n    return None\n'
                            'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n').encode()
                if args[-1].endswith('docker/stage2-hook.sh'):
                    return overlay.patcher.OLD.encode()
                if args[-1].endswith('patches/validate_image_dependencies.py'):
                    return source.read_bytes()
                return b'# fixture\n'
            if args[-2:] == ['--format', 'json']:
                return json.dumps(yaml.safe_load(Path(args[args.index('config') - 1]).read_bytes())).encode()
            return b''
        with patch.object(overlay, 'ROOT', root), patch.object(overlay, 'OVERLAY', destination), \
                patch.object(overlay, 'run', side_effect=run), \
                patch.object(overlay.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 6_000_000_000})()):
            with self.assertRaisesRegex(ValueError, 'container changed'):
                overlay.prepare('running-container')
        self.assertFalse(destination.exists())
        self.assertFalse((root / 'private/runtime').exists())

    def test_known_stage2_migrations_and_unknown_inputs(self):
        for source in (overlay.patcher.OLD, overlay.patcher.LEGACY_NEW,
                       overlay.patcher.EMERGENCY_NEW, overlay.patcher.NEW):
            self.assertEqual(overlay.patcher.patch_text(source), overlay.patcher.NEW)
        with self.assertRaises(ValueError):
            overlay.patcher.patch_text(overlay.patcher.EMERGENCY_NEW.replace('venv_is_current', 'changed'))
        runtime = ('def prepare_launch(project_root, argv):\n' + overlay.patcher.EARLY_ANCHOR + '\n    return None\n'
                   'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n')
        old = runtime.replace('    problems = pm.activate()\n', overlay.patcher.legacy_runtime_replacement())
        self.assertEqual(overlay.patcher.patch_runtime_source(runtime), overlay.patcher.patch_runtime_source(old))
        with self.assertRaises(ValueError):
            overlay.transform_sources({n: b'unknown' for n in overlay.validator.CODE_FILES})

    def test_effective_stage2_replacement_must_be_unique_and_readonly(self):
        config = yaml.safe_load(self.path.read_bytes())
        service = config['services']['hermes']
        service['environment'].update(SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY='0',
                                      SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY='1')
        overlay.verify_effective(config, self.root, 'managed')
        service['volumes'].append(service['volumes'][2].copy())
        with self.assertRaises(ValueError):
            overlay.verify_effective(config, self.root, 'managed')

    def test_alias_build_guard_includes_build_flags_and_leaves_other_services_alone(self):
        source = Path(__file__).with_name('aliases.sh').read_text()
        start = source.index('sg_hermes_image_dependencies_preflight() {')
        function = source[start:source.index('\nsg_route_compose()', start)]
        for arguments, expected in [('build', 1), ('up --build', 1), ('up --build --scale hermes=2', 1),
                                    ('build hermes', 1), ('build spartan-route', 0),
                                    ('--tier L2 up hermes', 1), ('--tier L2 build spartan-route', 0)]:
            result = subprocess.run(['bash', '-c', function + '\nsg_hermes_image_dependencies_preflight ' + arguments], capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)


# Exact published validator from 1f7e47ba62e2bd98505dce80543845344f42d4ee.
# Compressed to keep the historical compatibility input self-contained without
# requiring Git history, a private installation, or a network fetch at test time.
LEGACY_VALIDATOR = (
    b'eJy1Wl9v3DiSf/en4HiAlTSQZSe7O7frQw+QTZydAIknsHMPC8MQ2FKpm2M1qSOpjnuCfPdDFUmJUnc73tk5v1hNkcVi/f1VUd9/'
    b'd94bfb4U8hzklnU7u1byzyenp6c3wOszJdsd4/VGGCOUZI3SDB67VlTCtju2VMoKuWJ2DUxs+AoSw5b8AWpWQweyBlkJMMXp6elJ'
    b'o9WGlWXT215DWTKx6ZS2jEupLLdCSXNy4sfcv1Ysiw1YXnPLGTcsPIdZa27WrViGn78aJcOzMm67jlucEvb6yO06TDGW2+G5X3Za'
    b'VWDMMLIzJyevf3lzVb599/7qli1YmqxBb8CUVSvOtyC3pdnJquh2Sc6SjttqDeZ8y1tRcwslyaKcyKDbJZkj+er69c+/3Dii3eZc'
    b'SGN52wZSG1SD0EpuQFrjR6O9N1xIP7riFj7z3bnuZUT+w6vrd2+vbj+xBUvw1bnpuLZcnhFTZzFTZ5Wq4XzDpWjA2AJFmJycnNTQ'
    b'sC1o0exKnFCGCalWyl6SGHMWBsNvon7JjNU5WghUFuoSdURjGTv7iV0rCZcnjLGgEA3uVzNQK4QpzW7TCvmQZkxpJpWdvGxEC+7N'
    b'MIqqTLPC2NKI34D9xF5cvPwL+4H+ue3wT3NhgN300ooNXGmtdJq8k6Sx0Vh3DA88kE6yE7f0M1uM+2ngdbncWTCpe08WuiALLFrF'
    b'a5Nq/jkLR0vxCBqKpm/bDRpKqhOz5i//+uPl3cXZ3/lZc//lx798TbwIs4Fj/FM62Hnh1hDpYg2PtVihSjL23WIq7/l6AzZFDmnm'
    b'l8RUa9hwNB/nsDlLUKYGH7is1krTo9JiJSRvTfJ1TtDuOiCKd4HYfcaEIVUJaXHK9C1u/GIcdvvSqDvx00p6c0Q5TNQgrbA7Vq25'
    b'XEHttYUhqhHQ1jmTfAOGCcnu0uGQo1dnOUujI8e+Sa9GEUxW3Y/sVmuoHky/MWzhzkb73g/vg/aFISeXFaTDkpzVorJZ0NAwTmrC'
    b'EWJ+ag1eAVzu5mS3ufMy7zEzc5vZ2TYjIW1RMsO+xZa3PZp0FunjuTpphFyB7rSQ1jCugQlZqU3XgoUki8VBAsITRtKdboec4dHz'
    b'gbUpm8LCxqQzHj31matQvGLnRC6buO2eBwX6+2R/hwgig3TBtPTRVG1Bt3wXxdFZXHQ6YAt2p0zh80CxAps+wC5nSeIU9wA7lEk6'
    b'8Jrcfnx18+nVdfnz1c2Hq9vyzdXHq+s3V9ev/1VOUgI69jemvvvw6p9XSf5s0rc/v3r51x+T7D6EOzQ/NFB3lNi3wfZahmnu9d0F'
    b'hYEJk/9ZyGatqghOeLs7msdykn868JHlgacX98Pjy/tBhZ1W1gVZn+WVnuTDYfSgYlFtiEZIb257b5wBPCQRiUhqQjaKLWht0bpM'
    b'F0mU14jP/OlUL62fyhaLkRrjskak1qbKULLcNibFWVnRlE3LV+xPTJni9lN58+aX6/f/mjgsbTxPyilyhQm3F+TMF7SFCzx7LNGK'
    b'sGCDuvoTu1AXL19+O8y8w/QQq3o806Y3li2BpHhWKWm1atshB3g7xMMWt+W725urf6bD2ggwIDvfSD/P4IEzDau+5Zh3Whi83oFA'
    b'koaxXNu+mzs+KsXtTnC7a3kFhKVXIEGTGaMBY6JgDog3FjRrNBhEXEa13ELtNhpBenFCJG93xsLm6lFYVkMrlkgQ2h0DU/EODO0j'
    b'uRVbYBVvW9AmYUsw9gyaBsHZ1WMFHbJA1NZc1jinYNeKNbzycZ7XqkMWMItZvumgzskWPn5gxoq29YGVCWuYVao1RTgu/SdzWLBZ'
    b'sJvHHIpJY+R5d3Vbop1iNLsYFe5oLXBsHnTYW94ahzWt3l3G9k2rMB+9SJ5hjz74iLlNIJUo0ZFrL1yAwWdMPka1W4icdzQjP7Ek'
    b'bFuWhyaLxtH8LnLrouMaS4S7F/fkkyGeFCthk6yAR2HsXqJ8loWTTDT8by80ONUZ4C0amitV4viKf0djYxzSxtkHkuH4luq2sdwp'
    b'DJY4uh4rBl6Xno2SLG66stsUcfkUlhloPX8gtyfDEiLAFgeozrgSjZvrzLMWxmqx7J0YyHpqVT2ATgY/cBP7DqvBD4B4QJiNnwuP'
    b'FrTk7XMM7mnVBErtDvMfX0HN3jhGRtZdOb6I7ANlkByxspRM0exM0WloxGM0DXl3xPCQwwx0Ofy15AZKN3QIs040MHeKgfR/bq0Y'
    b'1eARqp76Eo5fIS0iM7ATwWgwfYtuOpb/he5lOuHgzlidOirnLMEmiWuQJFnOkrN3GILO/pEQ+B4TTHafT4hUn+uFc4eKd9T+UL3t'
    b'erv4pHvIGR5O9Xbx9wuPeRcUrkYa0zhAbBcusGGW/R0ii/o5XIxZEyjij4Efuy7wWI0bfM9er0VbM8f+eaVkI1YEEQyruGSYh7mQ'
    b'rNNiyy1QTVSwGyAfxLKRslgx0CO0nDbJ21efXr2/dGHVdZwOMqvhV7Ihln6hGhQeq6woS4T3Zfk1S3LKvwu0RmNr0FHQcWIZU2L6'
    b'X3/LXMRAjBb3JFq1Wgm5oiH/jM78Xq1WoNOwW1YgookQ+EzGAkyIfVD/N6bDKKXHpQLB5a5rBdlpLALKKSej/n0qQ5vxAGMUUilk'
    b'11szAaS10FBZpQUYAhu271oY0MbbiIe5xGuoWu54Nbn3XDIHWbNOKxQyc7ovQiIPwbdr+5WQBoOohSDSiM+12oA5sKKM95wEe+dv'
    b'8fvJ8kPRXjvrLwmmlB1J40AGIF7Q/5W2UKdzLtPMQwsSLVuwLyGCguTLFuqzBjh6s3GNsxHR99uiVdVDNNLtOq3QeAurNm2SzyLk'
    b'PsMuQubshxR5QRJe4Du+aRMqJ+iFkO4Y2dehzAh6pxoxMoK4psATFS47pUeknA50soJ6J94K1WeqT+/3yhovRkc8m4AsKiIOA5K4'
    b'vpmVN2G7gneomTTFIEt1i4u3tGbIIVk+FBk1bMcfQqq5uGfbUyFA8kdzn/UQ/CZx6yD0DbKRVWjNPA4fYRzDTTZx6JQcM93wLqXm'
    b'qdNnlrPZeOzP2fRISP/JBDtQQ7ayUKBoqJSuoS7h0Wpu0lpVPTrSpW9Nnf3EWmEs5kDf9DrQ0AqLon6WaxD6cYeEfCswixuF875i'
    b'WHGgcXhkT0e749UDX4HBnEw8fKui228hkOOF/iGvEBWM3Az0791+Dj8Febif/kwHV+1VI3f3x4SJe0eCPPDWixMxZpJNm374+s6/'
    b'up8L9ygpp3ukhbre6z4f6DXSiunWNOQvh/BJSM+Np37/XJ0Ek4yVMySgSOhR2E/CbshM4k2+LikCh1fPKPFd0vWF+ognfYXc8LZd'
    b'8uphuIVin4VdM6mGxBIl+GTi31NBDK5H9EPRAXVqOqhMzn5A9Y/lxSLsV8Sjs+bS6enpa8SNbMP1A+hzJCUaAfrc7cqqVhlMVMS0'
    b'6jGzopQQIVNvhnhAoBPSeeifIE8HO3j+eg1NHNf540yy8M04dmhJb0U7zK24VFJUvBW/ASEsl6ENgGSuG+5vWqBxEJnElTNXAvv4'
    b'tUiTJJ8Y2sgBW8T80OoZqh5eFk6KQ0sLPWD/dQHYIsT8+cXpNrl0tv81m/rBhMV5fTMK9AAfvW6fie2ntlvRnarvfDLOHMLy+ePs'
    b'f27eR84V1UOTzgixE2wTo2FkfRNpUG99zIOughiM9qMLgtfKvlW9rInnvari2NE+er7HSCAM40uD6iRjwlrPV3h0peQRPTxWsUhd'
    b'W3LkePCOwpcrJh1OWmxBY6mRs06Dhha4AUNl2jMq031+PbWQ8wy3wjSI9Je7feYH+tjeX+y7xL7YQ1L3yCt+781tYuP+2gCd6kkz'
    b'xAkFr2u8dIgaM5ihI1XI0T6KIVpiH2omKeeu40q8WUty9sNRbnEj1BHxOg1Ao+sHCBNqrONXKr4Xh/9mLY/jjShM6X7N2DEJgfHp'
    b'DglOjUa+1Sp5dmLyB0Vroraz7lEBcytiW6Ftz1sW58fsDyiYYgqh7AmrBzvwQW4yGRP6FrAYcZOHTyQiPv7IevCz0g+mwz66X+SL'
    b'tTLQFdqcBFV+syk11ebvUZaGpjdgGB+BQuRFe5ghAvlsMXXvAwdJzYPoSuGwU2mgUrLmeuf6R9m8fj3cLpgWFgMCxgXHytIhFaNC'
    b'0mmpR3MnF0b/1m2y25osHCMmXa54StOL5AB4zbFGtwNfw/cOl+wFfajjQfkl+/J1/LDBT44+4HCkyeAsPNoUsNUm5GqR9LY5+9uZ'
    b'EavEy3cJjdLgu8hpgH4exi72iiw3ffDJ3wjhRDunT3cZsoipbMDDe3UEEZ59YuAG9z4D8cj08Gcebs3xDz3m76li+1bt4dcESPyH'
    b'Vx4z+s+tPTbAESWHUOrlzoTcgsQ+iDfA79k/lF1jI2wJhvUG4iA8dpqjz+RyJmGLiPJQseC6oYfCYkppaNp2zjGyL1Ct6XhpluVD'
    b'i9mxOJxksRecw70MyG3uP/Yr4REWB/bKxjBp00By+DxlT8pBI/gyGH1WCGP65YTA08q4CZ7jSxe8ZDyInYYz7oOo53XBogwSroCe'
    b'7ILF0SeaUYRLmBnsUdIK2cOxVUH07tRFUvyqhBybPQgTPHossUd1d/nn+2d9meNB6IDIuPTf4nArlq3/SpN9pN0DPp1ciszr0pjp'
    b'uMwLznCtmGv2ftbCgsmxJh6q5a5ftsJ9j0EvoiZ4hff11egBdLVd/n/kv5Cn6JbJBesJlp1mwz0+aJ2b8uwP1XzO9d8BsbrXWGuL'
    b'QwjBC99dhSR3xvIVvLyPQUIUEi7HS4UJ7Et5b9WGW1Ex/CyVatdRwFi9kewy+jZBNCxcY9B9eVniN6Vl6e8jJ3Wgb1s/cUcdXUOP'
    b'FQRCXvpwRBrQNr3wDVtHLKpKBvgeXj3vHircGw3SevICKUJj7rrrkn2Bx+rrv3Vf9CK+Lvo/Bzq8NA=='
)


class DependencyOverlayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = 'sha256:' + 'a' * 64
        self.fail_preflight = False
        self.real_run = overlay.run
        current = (overlay.ROOT / 'apps/hermes-agent/patches/validate_image_dependencies.py').read_bytes()
        self.legacy_validator = zlib.decompress(base64.b64decode(LEGACY_VALIDATOR))
        self.assertEqual(overlay.digest(self.legacy_validator), 'cb251ebf7b11f31a6a3844ef1d492ca9d807df47cbac67d629633ec6a3ffa2ff')
        self.native_runtime = ('def prepare_launch(project_root, argv):\n' + overlay.patcher.EARLY_ANCHOR + '\n    return None\n'
                               'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n').encode()
        native_source = os.environ.get('HERMES_UPSTREAM_SOURCE')
        if native_source:
            self.native_runtime = (Path(native_source) / 'hermes_cli/venv_sync.py').read_bytes()
        runtime = self.native_runtime.decode().replace('    problems = pm.activate()\n', overlay.patcher.legacy_runtime_replacement()).encode()
        if native_source:
            # Exact installed image-only runtime captured during bounded recovery.
            self.assertEqual(overlay.digest(runtime), 'a002487faf38c73894cc5213b790574730dcee863661d8cb95d650a9696afcd3')
        emergency = overlay.patcher.EMERGENCY_NEW.encode()
        self.assertEqual(overlay.digest(emergency), '2bd2632c9fb639464e25a2cb3b3ca00dfd0c3c6f905400ecaaa473aa573cf9c5')
        self.originals = dict(zip(overlay.validator.CODE_FILES, (runtime, self.legacy_validator, emergency)))
        self.anchors = {name: ('# immutable fixture anchor ' + name + '\n').encode() for name in overlay.validator.CODE_ANCHORS}
        self.installed = {**self.originals, **self.anchors}
        source = self.root / 'apps/hermes-agent/patches/validate_image_dependencies.py'
        source.parent.mkdir(parents=True)
        source.write_bytes(current)
        self.emergency = self.root / 'private/runtime/emergency/stage2-hook.sh'
        self.emergency.parent.mkdir(parents=True)
        self.emergency.write_bytes(emergency)
        self.emergency.chmod(0o555)
        self.base = self.root / 'infra/compose/compose.yml'
        self.base.parent.mkdir(parents=True)
        self.base.write_text('services:\n  hermes:\n    image: fixture-hermes:current\n    command: ["fixture"]\n')
        self.foreign = self.root / 'private/foreign-owner.py'
        self.foreign.write_text('# retained unrelated code owner\n')
        self.local = self.root / 'private/compose.local.yml'
        self.local.write_text(yaml.safe_dump({'services': {'hermes': {'environment': {
            'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '0'}, 'volumes': [
                self.mount(self.emergency, '/opt/hermes/docker/stage2-hook.sh'),
                self.mount(self.foreign, '/opt/hermes/bootstrap/foreign-owner.py')]}}}))
        self.local_before = self.local.read_bytes()
        self.destination = self.root / 'private/compose.hermes-image-dependencies.yml'
        for patcher in (patch.object(overlay, 'ROOT', self.root), patch.object(overlay, 'OVERLAY', self.destination),
                        patch.object(overlay, 'run', side_effect=self.run_fixture),
                        patch.object(overlay.shutil, 'disk_usage', return_value=SimpleNamespace(free=6_000_000_000)),
                        patch.dict(os.environ, {'COMPOSE_DISABLE_ENV_FILE': '1', 'COMPOSE_ENV_FILES': '',
                                                'COMPOSE_PROJECT_NAME': 'admission-fixture'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def mount(source, target):
        return {'type': 'bind', 'source': str(source), 'target': target,
                'read_only': True, 'bind': {'create_host_path': False}}

    def run_fixture(self, args):
        if args[:2] == ['docker', 'inspect']:
            self.assertEqual(args[-1], 'fixture-container')
            return f'fixture-id {self.image} fixture-start\n'.encode()
        if args[:3] == ['docker', 'image', 'inspect']:
            self.assertIn(args[-1], (self.image, 'fixture-hermes:current'))
            return (self.image + '\n').encode()
        if args[:2] == ['docker', 'exec']:
            self.assertEqual(args[2:4], ['fixture-id', 'cat'])
            return self.installed[args[4].removeprefix('/opt/hermes/')]
        if args[:2] == ['docker', 'compose']:
            # Only real, daemon-free config rendering over disposable synthetic inputs.
            self.assertIn('config', args)
            self.assertIn(args[-1], ('--quiet', 'json'))
            if self.fail_preflight:
                raise RuntimeError('synthetic Compose preflight failure')
            return self.real_run(args)
        self.assertEqual(args[:3], ['sh', '-n', '-c'])
        return self.real_run(args)

    def effective(self):
        return json.loads(overlay.run(overlay.compose_args() + ['-f', str(self.destination), 'config', '--format', 'json']))

    def activate_fixture(self):
        mounts = {m['target']: Path(m['source']) for m in self.effective()['services']['hermes']['volumes']}
        for name in overlay.validator.CODE_FILES:
            self.installed[name] = mounts['/opt/hermes/' + name].read_bytes()

    def assert_managed_bundle(self, expected_originals):
        service = self.effective()['services']['hermes']
        self.assertEqual(service['image'], self.image)
        env = service['environment']
        self.assertEqual(env['SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY'], '0')
        self.assertEqual(env['SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY'], '1')
        self.assertEqual(env['SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE'], self.image)
        targets = ['/opt/hermes/hermes_cli/venv_sync.py', '/opt/hermes/patches/validate_image_dependencies.py',
                   '/opt/hermes/docker/stage2-hook.sh', '/run/spartan-image-dependencies-code/manifest.json',
                   '/opt/hermes/bootstrap/foreign-owner.py']
        mounts = service['volumes']
        self.assertCountEqual([m['target'] for m in mounts], targets)
        for mount in mounts:
            self.assertEqual(mount['type'], 'bind')
            self.assertIs(mount['read_only'], True)
            self.assertIs(mount['bind']['create_host_path'], False)
            self.assertTrue(Path(mount['source']).is_file())
        sources = {m['target']: Path(m['source']) for m in mounts}
        stage2 = sources['/opt/hermes/docker/stage2-hook.sh']
        self.assertEqual(stage2.stat().st_mode & 0o777, 0o555)
        self.assertIn(overlay.patcher.NEW, stage2.read_text())
        self.assertNotIn('SPARTAN_SELECTED', stage2.read_text())
        manifest = sources['/run/spartan-image-dependencies-code/manifest.json']
        self.assertEqual(overlay.digest(manifest.read_bytes()), env['SPARTAN_HERMES_DEPENDENCY_CODE_SHA256'])
        bundle = manifest.parent
        for name, expected in expected_originals.items():
            self.assertEqual((bundle / 'original' / name).read_bytes(), expected)
        self.assertEqual((bundle / 'rollback/compose.local.yml').read_bytes(), self.local_before)
        self.assertEqual(self.emergency.read_bytes(), self.originals['docker/stage2-hook.sh'])
        self.assertEqual(self.local.read_bytes(), self.local_before)
        self.assertEqual(sources['/opt/hermes/bootstrap/foreign-owner.py'], self.foreign)
        self.assertEqual(self.foreign.read_text(), '# retained unrelated code owner\n')
        overlay.check()
        return bundle

    def test_emergency_prepare_check_repeat_and_current_candidate_reuse(self):
        overlay.prepare('fixture-container', 'managed')
        bundle = self.assert_managed_bundle(self.originals)
        first = self.destination.read_bytes()
        code = {name: (bundle / name).read_bytes() for name in overlay.validator.CODE_FILES}
        overlay.prepare('fixture-container', 'managed')  # Not activated: installed emergency remains.
        self.assertEqual(self.destination.read_bytes(), first)
        self.activate_fixture()  # Model the readback of mounts, not a real container operation.
        overlay.prepare('fixture-container', 'managed')
        self.assertEqual(self.destination.read_bytes(), first)
        self.assertEqual({name: (bundle / name).read_bytes() for name in code}, code)
        self.assertEqual(self.assert_managed_bundle(self.originals), bundle)

    def legacy_image_overlay(self):
        bundle = self.root / 'private/runtime/legacy-image'
        files = {name: self.originals[name] for name in overlay.validator.CODE_FILES[:2]}
        originals = {**files, 'hermes_cli/venv_sync.py': self.native_runtime}
        manifest = {'schema': 1, 'image': self.image, 'files': {n: overlay.digest(v) for n, v in files.items()},
                    'anchors': {n: overlay.digest(v) for n, v in self.anchors.items()},
                    'originals': {n: overlay.digest(v) for n, v in originals.items()}}
        raw = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
        for name, value in {**files, **self.anchors, **{'original/' + n: v for n, v in originals.items()}, 'manifest.json': raw}.items():
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            path.chmod(0o444)
        config = {'services': {'hermes': {'image': self.image, 'environment': {
            'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '1', 'SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY': '0',
            'SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE': self.image,
            'SPARTAN_HERMES_DEPENDENCY_CODE_MANIFEST': overlay.validator.CODE_MANIFEST,
            'SPARTAN_HERMES_DEPENDENCY_CODE_SHA256': overlay.digest(raw)}, 'volumes': [
                self.mount(bundle / name, '/opt/hermes/' + name) for name in files] + [
                self.mount(bundle / 'manifest.json', overlay.validator.CODE_MANIFEST)]}}}
        self.destination.write_text(yaml.safe_dump(config))
        return bundle, originals

    def test_legacy_image_overlay_migration_preserves_original_provenance(self):
        legacy_bundle, originals = self.legacy_image_overlay()
        overlay.check()
        previous = self.destination.read_bytes()
        legacy_before = {str(p.relative_to(legacy_bundle)): p.read_bytes() for p in legacy_bundle.rglob('*') if p.is_file()}
        expected = {**originals, 'docker/stage2-hook.sh': self.originals['docker/stage2-hook.sh']}
        overlay.prepare('fixture-container', 'managed')
        bundle = self.assert_managed_bundle(expected)
        self.assertEqual((bundle / 'previous-compose.yml').read_bytes(), previous)
        first = self.destination.read_bytes()
        self.activate_fixture()
        overlay.prepare('fixture-container', 'managed')
        self.assertEqual(self.destination.read_bytes(), first)
        self.assertEqual((bundle / 'previous-compose.yml').read_bytes(), previous)
        self.assertEqual(self.assert_managed_bundle(expected), bundle)
        self.assertEqual({str(p.relative_to(legacy_bundle)): p.read_bytes() for p in legacy_bundle.rglob('*') if p.is_file()}, legacy_before)

    def test_failed_preflight_preserves_active_overlay_and_bundles(self):
        overlay.prepare('fixture-container', 'managed')
        self.activate_fixture()
        before = self.destination.read_bytes()
        runtime = self.root / 'private/runtime'
        inventory = {str(p.relative_to(runtime)): (p.read_bytes(), p.stat().st_mode) for p in runtime.rglob('*') if p.is_file()}
        self.fail_preflight = True
        with self.assertRaisesRegex(RuntimeError, 'Compose preflight failure'):
            overlay.prepare('fixture-container', 'managed')
        self.assertEqual(self.destination.read_bytes(), before)
        self.assertEqual({str(p.relative_to(runtime)): (p.read_bytes(), p.stat().st_mode) for p in runtime.rglob('*') if p.is_file()}, inventory)
        self.fail_preflight = False
        overlay.check()


if __name__ == '__main__':
    unittest.main()
