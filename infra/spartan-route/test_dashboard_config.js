const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/web/assets/dashboard-config.js`, 'utf8');
const script = html;
const helpers = {};
vm.runInNewContext(script.slice(script.indexOf('    function variantId('), script.indexOf('    function updateVariantControls(')), helpers);
const models = [{ id: 'base', eligible: true, maxContext: 1000000 }];

test('variant IDs use exact tokens or compact whole thousands', () => {
  assert.equal(helpers.variantId('base', 282000), 'base-282kcontext');
  assert.equal(helpers.variantId('base', 282001), 'base-282001context');
  assert.equal(helpers.variantId('base', ''), 'Enter a model and whole token count');
});

test('validate whole counts, model eligibility, limits and duplicate IDs', () => {
  const validate = rows => helpers.validateVariants(rows.map(row => ({id: 'alias', ...row})), models, []);
  assert.equal(validate([]), '');
  assert.equal(validate([{model: 'base', contextWindow: 1000000}]), '');
  for (const count of ['', '1.5', '1e3', 0, -1, 2000001, Infinity]) {
    assert.match(validate([{model: 'base', contextWindow: count}]), /whole token count/);
  }
  assert.match(validate([{model: 'base', contextWindow: 1000001}]), /maximum/);
  assert.match(validate([{model: 'gone', contextWindow: 1}]), /eligible/);
  assert.match(validate([{model: 'base', contextWindow: 1000}, {model: 'base', contextWindow: '1000'}]), /already exist/);
});

test('saved inactive pairs can be preserved or removed without selecting a new target', () => {
  const saved = [{id: 'gone-alias', model: 'gone', contextWindow: 282000}, {id: 'base-alias', model: 'base', contextWindow: 1500000}];
  assert.equal(helpers.validateVariants(saved, models, saved), '');
  assert.equal(helpers.validateVariants([], models, saved), '');
  assert.match(helpers.validateVariants([{id: 'gone-alias', model: 'gone', contextWindow: 1000}], models, saved), /eligible/);
});

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.value = '';
    this.disabled = false;
    this.listeners = {};
    this.classList = { toggle() {} };
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  prepend(child) { this.children.unshift(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(type, handler) { this.listeners[type] = handler; }
  focus() {}
  querySelectorAll(selector) {
    const tags = selector.split(',').map(tag => tag.trim());
    return this.children.flatMap(child => [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function harness() {
  const elements = new Map();
  const requests = [];
  const events = {};
  const context = vm.createContext({
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
      createElement(tag) { return new Element(tag); },
    },
    window: { addEventListener(type, handler) { events[type] = handler; } },
    sessionStorage: { getItem() { return 'old-token'; }, setItem() {} },
    fetch(path, options) { return new Promise(resolve => requests.push({path, options, resolve})); },
  });
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  const respond = (request, payload, status = 200) => request.resolve({ok: status < 400, status, json: async () => payload, headers: {get: () => payload.etag || 'etag'}});
  return { context, run, requests, elements, events, respond };
}
const routing = {effective: {simple: 'base'}, editableTiers: [], fileDefaults: {}, sources: {}, overrides: {}, models, enabled: true};
const variants = {variants: [{model: 'base', contextWindow: 282000, id: 'base-282kcontext', available: true}], models, configured: false, etag: 'variants-etag'};
const flush = () => new Promise(resolve => setImmediate(resolve));
async function loaded() {
  const h = harness();
  h.respond(h.requests[0], routing);
  h.respond(h.requests[1], variants);
  await flush();
  return h;
}

test('variant and routing saves preserve the other panel draft and ETag', async () => {
  const h = await loaded();
  h.run("draft.simple = 'other'; markDirty(true); variantDraft[0].contextWindow = 300000; updateVariantControls(); saveVariants();");
  assert.equal(h.requests[2].options.headers['If-Match'], 'variants-etag');
  h.respond(h.requests[2], {...variants, configured: true, variants: [{id: 'base-282kcontext', model: 'base', contextWindow: 300000}]});
  await flush();
  assert.equal(h.run('dirty'), true);
  assert.equal(h.run('draft.simple'), 'other');
  h.run('variantDraft[0].contextWindow = 400000; updateVariantControls(); save();');
  h.respond(h.requests[3], {...routing, effective: {simple: 'other'}});
  await flush();
  assert.equal(h.run('variantDirty'), true);
  assert.equal(h.run('variantDraft[0].contextWindow'), 400000);
});

test('412 and network errors retain edits; both drafts protect navigation', async () => {
  const h = await loaded();
  h.run('variantDraft[0].contextWindow = 300000; updateVariantControls(); saveVariants();');
  h.respond(h.requests[2], {error: {message: 'conflict'}}, 412);
  await flush();
  assert.equal(h.run('variantDraft[0].contextWindow'), 300000);
  assert.equal(h.run('variantEtag'), 'variants-etag');
  assert.equal(h.elements.get('variant-save').disabled, true);
  const event = {preventDefault() { this.prevented = true; }};
  h.events.beforeunload(event);
  assert.equal(event.prevented, true);
  h.run('variantsLoaded = true; saveVariants();');
  h.respond(h.requests[3], {error: {message: 'network down'}}, 503);
  await flush();
  assert.equal(h.run('variantDraft[0].contextWindow'), 300000);
  assert.equal(h.run('variantDirty'), true);
});

test('token edits and failed reloads prevent saving old account data', async () => {
  const h = await loaded();
  h.run("draft.simple = 'other'; markDirty(true); variantDraft[0].contextWindow = 300000; updateVariantControls(); tokenInput.value = 'new-token';");
  h.elements.get('token').listeners.input();
  h.run('save(); saveVariants();');
  assert.equal(h.requests.length, 2);
  h.run('load();');
  assert.equal(h.requests[2].options.headers.Authorization, 'Bearer new-token');
  h.respond(h.requests[2], {error: {message: 'unavailable'}}, 503);
  h.respond(h.requests[3], {error: {message: 'unavailable'}}, 503);
  await flush();
  h.run('save(); saveVariants(); reset();');
  assert.equal(h.requests.length, 4);
  assert.equal(h.run('dirty && variantDirty'), true);
});

test('late responses from previous credentials cannot replace current drafts', async () => {
  const h = harness();
  h.run("tokenInput.value = 'new-token'; load();");
  h.respond(h.requests[2], routing);
  h.respond(h.requests[3], variants);
  await flush();
  h.run('variantDraft[0].contextWindow = 300000; updateVariantControls();');
  h.respond(h.requests[0], routing);
  h.respond(h.requests[1], variants);
  await flush();
  assert.equal(h.run('variantDraft[0].contextWindow'), 300000);
});

test('typing context preserves ID and focused controls while save disables editing', async () => {
  const h = await loaded();
  const container = h.elements.get('variants');
  const input = container.querySelectorAll('input')[1];
  input.value = '333001';
  input.oninput();
  assert.equal(container.querySelectorAll('input')[1], input);
  assert.equal(container.children[0].children[4].children[0].textContent, 'Model ID: base-282kcontext');
  assert.equal(h.run('variantDirty'), true);
  h.run('saveVariants();');
  for (const control of container.querySelectorAll('input, select, button')) assert.equal(control.disabled, true);
  assert.equal(JSON.parse(h.requests[2].options.body).variants[0].contextWindow, 333001);
});

test('removing every row submits an explicit empty managed list', async () => {
  const h = await loaded();
  h.elements.get('variants').querySelector('button').onclick();
  assert.equal(h.run('variantDraft.length'), 0);
  assert.equal(h.run('variantDirty'), true);
  h.run('saveVariants();');
  assert.deepEqual(JSON.parse(h.requests[2].options.body), {variants: []});
  h.respond(h.requests[2], {...variants, variants: [], configured: true});
  await flush();
  assert.equal(h.elements.get('variant-status').textContent, 'Source: managed');
  assert.equal(h.run('variantDirty'), false);
});

test('unchanged defaults can be adopted as a managed list without unsaved edits', async () => {
  const h = await loaded();
  assert.equal(h.run('variantDirty'), false);
  assert.equal(h.elements.get('variant-save').disabled, false);
  const event = {preventDefault() { this.prevented = true; }};
  h.events.beforeunload(event);
  assert.equal(event.prevented, undefined);
  h.run('saveVariants();');
  assert.deepEqual(JSON.parse(h.requests[2].options.body), {variants: [{id: 'base-282kcontext', model: 'base', contextWindow: 282000}]});
  h.respond(h.requests[2], {...variants, configured: true});
  await flush();
  assert.equal(h.elements.get('variant-save').disabled, true);
  assert.equal(h.elements.get('variant-status').textContent, 'Source: managed');
});

test('existing 282k name remains stable after changing context to 412k and reloading saved response', async () => {
  const h = await loaded();
  const input = h.elements.get('variants').querySelectorAll('input')[1];
  input.value = '412000';
  input.oninput();
  h.run('saveVariants();');
  assert.deepEqual(JSON.parse(h.requests[2].options.body), {variants: [{id: 'base-282kcontext', model: 'base', contextWindow: 412000}]});
  h.respond(h.requests[2], {...variants, configured: true, variants: [{id: 'base-282kcontext', model: 'base', contextWindow: 412000}]});
  await flush();
  assert.equal(h.elements.get('variants').querySelectorAll('input')[0].value, 'base-282kcontext');
  assert.equal(h.run('variantDraft[0].contextWindow'), 412000);
});

test('custom names are editable and persist independently from base model selection', async () => {
  const h = await loaded();
  h.run("variantState.models = [...variantState.models, {id: 'codex/sol', eligible: true, maxContext: 1000000}]; renderVariants();");
  const container = h.elements.get('variants');
  const name = container.querySelectorAll('input')[0];
  name.value = 'my-sol';
  name.oninput();
  assert.equal(container.querySelectorAll('input')[0], name);
  assert.equal(container.children[0].children[4].children[0].textContent, 'Model ID: my-sol');
  const select = container.querySelector('select');
  select.value = 'codex/sol';
  select.onchange();
  assert.equal(name.value, 'my-sol');
  h.run('saveVariants();');
  assert.deepEqual(JSON.parse(h.requests[2].options.body), {variants: [{id: 'my-sol', model: 'codex/sol', contextWindow: 282000}]});
});

test('distinct names may share one base model and context window', () => {
  const rows = [{id: 'first', model: 'base', contextWindow: 282000}, {id: 'second', model: 'base', contextWindow: 282000}];
  assert.equal(helpers.validateVariants(rows, models, []), '');
  assert.match(helpers.validateVariants([{...rows[0]}, {...rows[1], id: 'first'}], models, []), /already exists/);
});

test('names reject malformed IDs, wrappers, catalog collisions and reserved routes', () => {
  for (const id of ['', 'x'.repeat(273), 'my name', 'x#y', 'x?y', 'x\ny', 'é', '/name', 'name/', 'x//y', '.', '..', 'x/../y', 'x/./y']) {
    assert.match(helpers.validateVariants([{id, model: 'base', contextWindow: 282000}], models, []), /enter a model ID/, id);
  }
  for (const id of ['auto', 'clawroute/auto', 'custom-1/name', 'spartan-route/name', 'base', 'gpt-image-test', 'openai/gpt-realtime-test']) {
    assert.match(helpers.validateVariants([{id, model: 'base', contextWindow: 282000}], models, []), /reserved/, id);
  }
  for (const id of ['codex/sol-282kcontext', 'my-sol', 'a.b_c:d/e-f', 'x'.repeat(272)]) {
    assert.equal(helpers.validateVariants([{id, model: 'base', contextWindow: 282000}], models, []), '', id);
  }
});

test('inactive preservation requires the same name, base and context identity', () => {
  const saved = [{id: 'old-name', model: 'gone', contextWindow: 282000}];
  assert.equal(helpers.validateVariants(saved, models, saved), '');
  assert.match(helpers.validateVariants([{...saved[0], id: 'renamed'}], models, saved), /eligible/);
  assert.match(helpers.validateVariants([{...saved[0], contextWindow: 412000}], models, saved), /eligible/);
  const collision = [{id: 'base', model: 'gone', contextWindow: 282000, available: false}];
  assert.equal(helpers.validateVariants(collision, models, collision), '');
  assert.match(helpers.validateVariants([{...collision[0], contextWindow: 412000}], models, collision), /reserved/);
});

test('generated names are suggested only when creating a new row', async () => {
  const h = await loaded();
  h.elements.get('variant-add').onclick();
  assert.equal(h.run('variantDraft[1].id'), 'base-282kcontext');
  const container = h.elements.get('variants');
  const newRow = container.children[1];
  const name = newRow.querySelectorAll('input')[0];
  name.value = 'new-name';
  name.oninput();
  const context = newRow.querySelectorAll('input')[1];
  context.value = '412000';
  context.oninput();
  assert.equal(h.run('variantDraft[1].id'), 'new-name');
  assert.equal(h.run('variantDraft[0].id'), 'base-282kcontext');
});


test('long generated v1 alias names stay editable within the migration bound', async () => {
  const h = await loaded();
  const id = `${'b'.repeat(256)}-2000000context`;
  assert.equal(helpers.validateVariants([{id, model: 'base', contextWindow: 282000}], models, []), '');
  assert.equal(h.elements.get('variants').querySelectorAll('input')[0].maxLength, 272);
});
