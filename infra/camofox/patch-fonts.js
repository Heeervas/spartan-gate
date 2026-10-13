const fs = require('node:fs');

const file = 'server.js';
const source = fs.readFileSync(file, 'utf8');
// camoufox-js merges these families into its OS fingerprint font list.
// Installing TTFs in Fontconfig alone does not expose them to page content.
const anchor = '        addons: addonPaths,\n';
const replacement = anchor + "        fonts: ['Onest'],\n";
if (source.split(anchor).length !== 2) {
  throw new Error('expected exactly one Camofox launch fonts anchor');
}
const unpatched = source.includes(replacement) ? source.replace(replacement, anchor) : source;
if (/^\s*fonts\s*:/m.test(unpatched)) {
  throw new Error('unexpected existing Camofox fonts configuration');
}
if (source.includes(replacement)) process.exit(0);
fs.writeFileSync(file, source.replace(anchor, replacement));
