const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Execute the real dashboard initialization and card renderer with inert DOM IO.
// No production exports or source slicing of individual helpers is required.
function harness() {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { dataset: {}, value: '', addEventListener() {} });
      return elements.get(id);
    },
    addEventListener() {},
  };
  const context = vm.createContext({
    document, window: {}, URLSearchParams,
    location: { search: '', pathname: '/dashboard' },
    sessionStorage: { getItem() { return ''; } },
  });
  const source = fs.readFileSync(`${__dirname}/web/assets/dashboard.js`, 'utf8');
  vm.runInContext(source.replace(/    load\(\);\s*$/, ''), context);
  return { context, run: code => vm.runInContext(code, context) };
}

test('session Speed distinguishes requests from provider-reported tiers with accessible lightning', () => {
  const h = harness();
  const render = speed => {
    h.context.session = { id: 'session', metrics: { speed }, turns: [] };
    return h.run('sessionCard(session, 0, [session])');
  };
  const speedFact = html => html.match(/<span class="fact speed"[\s\S]*?<\/strong><\/span>/)?.[0] || '';
  const downgraded = speedFact(render({ requested: { priority: 1 }, served: { default: 1 } }));
  assert.match(downgraded, /Speed/);
  assert.match(downgraded, /Requested:.*Fast · 1/);
  assert.match(downgraded, /Served:.*Normal · 1/);
  assert.equal((downgraded.match(/<path /g) || []).length, 1);
  assert.match(downgraded, /<svg[^>]*aria-hidden="true"[^>]*focusable="false"/);
  const ultra = speedFact(render({ requested: { ultrafast: 2 }, served: { ultrafast: 2 } }));
  assert.equal((ultra.match(/<path /g) || []).length, 4);
  assert.match(ultra, /Ultrafast · 2/);
  const mixed = speedFact(render({ requested: { priority: 1, ultrafast: 2, unspecified: 2 }, served: { priority: 1, ultrafast: 2, unknown: 2 } }));
  assert.match(mixed, /Requested: mixed/);
  assert.match(mixed, /Unspecified · 2/);
  assert.match(mixed, /Served: mixed/);
  assert.match(mixed, /Unknown · 2/);
  assert.match(mixed, /Ultrafast · 2/);
  assert.equal((mixed.match(/<path /g) || []).length, 6);
  for (const speed of [undefined, { requested: { unspecified: 1 }, served: { unknown: 1 } },
    { requested: { auto: 1 }, served: { default: 1 } }]) {
    const html = speedFact(render(speed));
    assert.match(html, /Speed/);
    assert.doesNotMatch(html, /<svg/);
  }
});
