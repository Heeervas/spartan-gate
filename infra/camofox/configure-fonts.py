"""Give the Linux renderer absolute paths to its bundled fonts and Onest."""
from pathlib import Path
from xml.dom import minidom

browser = Path('/root/.cache/camoufox')
source = browser / 'fontconfig/linux/fonts.conf'
# Retain the complete document, including upstream's copyright/permission
# comments and doctype, as well as its rendering rules.
document = minidom.parse(str(source))
root = document.documentElement
directories = [node for node in root.childNodes
               if node.nodeType == node.ELEMENT_NODE and node.tagName == 'dir']
if (root.tagName != 'fontconfig' or len(directories) != 1
        or directories[0].firstChild is None
        or directories[0].firstChild.nodeValue != 'fonts'):
    raise RuntimeError('unexpected Camoufox bundled Fontconfig directories')
bundled = browser / 'fonts/linux'
onest = Path('/usr/local/share/fonts/onest')
if not bundled.is_dir() or len(list(onest.glob('*.ttf'))) != 7:
    raise RuntimeError('required bundled fonts or Onest missing')
directory = directories[0]
for name in list(directory.attributes.keys()):
    directory.removeAttribute(name)
for child in list(directory.childNodes):
    directory.removeChild(child)
directory.appendChild(document.createTextNode(str(bundled)))
onest_directory = document.createElement('dir')
onest_directory.appendChild(document.createTextNode(str(onest)))
root.appendChild(onest_directory)
Path('/etc/spartan-camofox-fonts.conf').write_bytes(document.toxml(encoding='utf-8'))
