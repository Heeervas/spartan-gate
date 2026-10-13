const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const vm = require('node:vm');

const patch = path.join(__dirname, 'patch-fonts.js');
const original = `const options = {
        addons: addonPaths,
        proxy: launchProxy,
        exclude_addons: disableDefaultAddons ? ['UBO'] : undefined,
};`;

function fixture(source, check) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'camofox-fonts-'));
  try {
    const file = path.join(cwd, 'server.js');
    fs.writeFileSync(file, source);
    check(() => spawnSync(process.execPath, [patch], { cwd, encoding: 'utf8' }),
      () => fs.readFileSync(file, 'utf8'));
  } finally {
    fs.rmSync(cwd, { recursive: true, force: true });
  }
}

test('launch exposes Onest and preserves proxy and default add-on options; reapplication is identical', () => {
  fixture(original, (run, read) => {
    assert.equal(run().status, 0);
    const patched = read();
    const proxy = { server: 'http://proxy:8888' };
    for (const disableDefaultAddons of [false, true]) {
      const addonPaths = ['/readonly/custom-addon'];
      const options = vm.runInNewContext(patched + '\noptions', {
        addonPaths, launchProxy: proxy, disableDefaultAddons,
      });
      assert.equal(options.fonts.join(','), 'Onest');
      assert.equal(options.proxy, proxy);
      assert.equal(options.addons, addonPaths);
      assert.equal(options.exclude_addons?.join(','), disableDefaultAddons ? 'UBO' : undefined);
      assert.equal(options.custom_fonts_only, undefined);
    }
    assert.equal(run().status, 0);
    assert.equal(read(), patched);
  });
});

for (const [label, source] of [
  ['missing anchor', 'const options = {};'],
  ['duplicate anchor', original + '\n' + original],
  ['existing fonts', original.replace('        proxy:', "        fonts: ['Other'],\n        proxy:")],
  ['conflicting fonts after patched anchor', original.replace(
    '        addons: addonPaths,', "        addons: addonPaths,\n        fonts: ['Onest'],\n        fonts: ['Other'],")],
]) {
  test(`${label} fails without writing`, () => {
    fixture(source, (run, read) => {
      assert.notEqual(run().status, 0);
      assert.equal(read(), source);
    });
  });
}
