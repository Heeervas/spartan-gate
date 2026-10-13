import builtins
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from xml.dom import minidom
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).with_name('configure-fonts.py')
SOURCE = '''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- Copyright and permission notice retained from the bundled config. -->
<fontconfig>
  <dir prefix="cwd">fonts</dir>
  <match target="pattern"><edit name="hinting"><bool>true</bool></edit></match>
  <cachedir prefix="xdg">fontconfig</cachedir>
</fontconfig>
'''


class ConfigureFontsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.browser = self.root / 'browser'
        self.source = self.browser / 'fontconfig/linux/fonts.conf'
        self.source.parent.mkdir(parents=True)
        self.source.write_text(SOURCE)
        (self.browser / 'fonts/linux').mkdir(parents=True)
        self.onest = self.root / 'onest'
        self.onest.mkdir()
        for index in range(7):
            (self.onest / f'Onest-{index}.ttf').write_bytes(b'font fixture')
        self.target = self.root / 'fonts.conf'

    def run_script(self):
        mapping = {
            '/root/.cache/camoufox': self.browser,
            '/usr/local/share/fonts/onest': self.onest,
            '/etc/spartan-camofox-fonts.conf': self.target,
        }
        original_open = builtins.open

        def mapped_open(file, *args, **kwargs):
            return original_open(mapping.get(str(file), file), *args, **kwargs)

        with patch('pathlib.Path', side_effect=lambda value: mapping.get(str(value), Path(value))), \
                patch('builtins.open', side_effect=mapped_open):
            runpy.run_path(str(SCRIPT), run_name='__main__')

    def test_preserves_document_and_rules_with_absolute_font_directories_idempotently(self):
        self.run_script()
        first = self.target.read_bytes()
        self.assertIn(b'Copyright and permission notice', first)
        doctype = minidom.parseString(first).doctype
        self.assertEqual((doctype.name, doctype.systemId), ('fontconfig', 'fonts.dtd'))
        root = ET.fromstring(first)
        self.assertEqual([(node.text, node.attrib) for node in root.findall('dir')], [
            (str(self.browser / 'fonts/linux'), {}), (str(self.onest), {}),
        ])
        source = ET.fromstring(SOURCE)
        for tag in ['match', 'cachedir']:
            self.assertEqual(ET.tostring(root.find(tag)).strip(), ET.tostring(source.find(tag)).strip())
        self.assertEqual(self.source.read_text(), SOURCE)
        self.run_script()
        self.assertEqual(self.target.read_bytes(), first)

    def test_drift_or_missing_fonts_leaves_previous_output_unchanged(self):
        for label in ['missing dir', 'duplicate dir', 'changed dir', 'missing Onest', 'missing bundle']:
            with self.subTest(label=label):
                self.setUp()
                self.target.write_bytes(b'previous output')
                if label == 'missing dir':
                    self.source.write_text(SOURCE.replace('<dir prefix="cwd">fonts</dir>', ''))
                elif label == 'duplicate dir':
                    self.source.write_text(SOURCE.replace('</fontconfig>', '<dir>other</dir></fontconfig>'))
                elif label == 'changed dir':
                    self.source.write_text(SOURCE.replace('>fonts</dir>', '>other</dir>'))
                elif label == 'missing Onest':
                    next(self.onest.glob('*.ttf')).unlink()
                else:
                    (self.browser / 'fonts/linux').rmdir()
                with self.assertRaises(RuntimeError):
                    self.run_script()
                self.assertEqual(self.target.read_bytes(), b'previous output')


if __name__ == '__main__':
    unittest.main()
