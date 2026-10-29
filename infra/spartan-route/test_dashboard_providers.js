const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(`${__dirname}/web/assets/dashboard-config.js`, 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.classNames = new Set();
    this.classList = { toggle: (name, force) => force ? this.classNames.add(name) : this.classNames.delete(name) };
    this.value = '';
    this.textContent = '';
    this.disabled = false;
    this.checked = false;
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  prepend(child) { this.children.unshift(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  focus() { this.focused = true; }
  querySelectorAll(selector) {
    const tags = selector.split(',').map(value => value.trim());
    return this.children.flatMap(child => [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) {
    if (selector.startsWith('#')) {
      const find = node => {
        for (const child of node.children) {
          if (child.id === selector.slice(1)) return child;
          const nested = find(child);
          if (nested) return nested;
        }
        return null;
      };
      return find(this);
    }
    return this.querySelectorAll(selector)[0] || null;
  }
}

const routing = { effective: {}, editableTiers: [], fileDefaults: {}, sources: {}, overrides: {}, models: [], enabled: true };
const variants = { variants: [], models: [], configured: false };
const providers = {
  providers: {
    openai: {
      enabled: true, rejectEndpoints: [], rejectModels: [],
      supportedEndpoints: ['/v1/chat/completions', '/v1/responses'],
      models: ['openai/gpt-6-luna', 'openai/gpt-6-astra'], credentialStatus: 'configured',
    },
    codex: {
      enabled: true, rejectEndpoints: ['/v1/images/generations'], rejectModels: ['codex/old-model'],
      supportedEndpoints: ['/v1/responses', '/v1/images/generations'],
      models: ['codex/gpt-6-luna'], credentialStatus: 'missing_credentials',
    },
  }, configured: false, updatedAt: null,
};

function harness() {
  const elements = new Map();
  const requests = [];
  const events = {};
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    AbortController,
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
      createElement(tag) { return new Element(tag); },
      createTextNode(text) { const node = new Element('#text'); node.textContent = text; return node; },
    },
    window: { addEventListener(type, handler) { events[type] = handler; } },
    sessionStorage: { getItem() { return 'test-token'; }, setItem() {} },
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(path, options) { return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })); },
  });
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  const respond = (request, payload, status = 200) => request.resolve({
    ok: status >= 200 && status < 300, status,
    json: async () => payload,
    headers: { get: name => name.toLowerCase() === 'etag' ? payload.etag || 'etag-next' : null },
  });
  return { run, requests, elements, events, timers, respond };
}

async function loaded() {
  const h = harness();
  assert.deepEqual(h.requests.map(item => item.path), ['/api/config/routing', '/api/config/model-variants', '/api/config/providers']);
  h.respond(h.requests[0], routing);
  h.respond(h.requests[1], variants);
  h.respond(h.requests[2], providers);
  await flush();
  return h;
}

function providerInputs(h) { return h.elements.get('providers').querySelectorAll('input'); }

 test('provider screen displays credential readiness and leaves supported endpoints enabled by default', async () => {
  const h = await loaded();
  const root = h.elements.get('providers');
  assert.equal(root.children.length, 2);
  const codex = root.children[0];
  const openai = root.children[1];
  assert.match(codex.children[0].children[0].children[1].textContent, /Credential: missing credentials/);
  assert.match(openai.children[0].children[0].children[1].textContent, /Credential: configured/);
  assert.deepEqual(providerInputs(h).filter(input => input.type === 'checkbox').map(input => input.checked), [true, false, true, true, true, true]);
  assert.deepEqual(providerInputs(h).filter(input => input.type === 'text').map(input => input.placeholder), ['Exact model ID', 'Exact model ID']);
  assert.equal(h.elements.get('provider-save').disabled, true);
});

test('provider edits save only editable fields with If-Match and leave sibling config untouched', async () => {
  const h = await loaded();
  const checkboxes = providerInputs(h).filter(input => input.type === 'checkbox');
  checkboxes[3].checked = false;
  checkboxes[3].listeners.change();
  checkboxes[4].checked = false;
  checkboxes[4].listeners.change();
  assert.equal(h.elements.get('provider-save').disabled, false);
  h.elements.get('provider-save').onclick();
  const write = h.requests[3];
  assert.equal(write.path, '/api/config/providers');
  assert.equal(write.options.method, 'PUT');
  assert.equal(write.options.headers['If-Match'], 'etag-next');
  const body = JSON.parse(write.options.body);
  assert.deepEqual(body.providers.openai, {
    enabled: false, rejectEndpoints: ['/v1/chat/completions'], rejectModels: [],
  });
  assert.deepEqual(body.providers.codex, {
    enabled: true, rejectEndpoints: ['/v1/images/generations'], rejectModels: ['codex/old-model'],
  });
  assert.deepEqual(Object.keys(body.providers.openai).sort(), ['enabled', 'rejectEndpoints', 'rejectModels']);
  assert.equal(h.run('variantDirty'), false);
  h.respond(write, { ...providers, configured: true, etag: 'etag-saved' });
  await flush();
  assert.equal(h.elements.get('provider-save').disabled, true);
  assert.equal(h.elements.get('provider-success').textContent, 'Provider settings saved.');
});

test('catalog suggestions and valid manual IDs add exact rejects; malformed and duplicate IDs are blocked', async () => {
  const h = await loaded();
  const input = h.elements.get('providers').querySelector('#provider-model-0');
  assert.equal(input.attributes.list, 'provider-models-0');
  const datalist = h.elements.get('providers').querySelectorAll('datalist')[0];
  assert.deepEqual(datalist.children.map(item => item.value), ['codex/gpt-6-luna']);
  const add = h.elements.get('providers').children[0].children[2].children[2].children[1];
  input.value = 'gpt-local-v2';
  add.listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(h.run('providerDraft.codex.rejectModels'))), ['codex/old-model', 'gpt-local-v2']);
  assert.equal(h.elements.get('provider-dirty').textContent, 'Unsaved changes');
  input.value = 'bad model';
  add.listeners.click();
  assert.match(h.elements.get('provider-error').textContent, /valid exact model ID/);
  assert.deepEqual(JSON.parse(JSON.stringify(h.run('providerDraft.codex.rejectModels'))), ['codex/old-model', 'gpt-local-v2']);
  input.value = 'gpt-local-v2';
  add.listeners.click();
  assert.match(h.elements.get('provider-error').textContent, /already rejected/);
});

test('unsubmitted model text survives add and remove actions in another provider', async () => {
  const h = await loaded();
  const openaiInput = h.elements.get('providers').querySelector('#provider-model-1');
  openaiInput.value = 'openai/gpt-local-next';
  openaiInput.listeners.input();

  const codexAdd = h.elements.get('providers').children[0].children[2].children[2].children[1];
  const codexInput = h.elements.get('providers').querySelector('#provider-model-0');
  codexInput.value = 'codex/new-model';
  codexAdd.listeners.click();
  assert.equal(h.elements.get('providers').querySelector('#provider-model-1').value, 'openai/gpt-local-next');

  h.elements.get('providers').querySelectorAll('button')[0].listeners.click();
  assert.equal(h.elements.get('providers').querySelector('#provider-model-1').value, 'openai/gpt-local-next');
});

test('remove model updates the draft and focuses the add control', async () => {
  const h = await loaded();
  const remove = h.elements.get('providers').querySelectorAll('button')[0];
  remove.listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(h.run('providerDraft.codex.rejectModels'))), []);
  assert.equal(h.elements.get('providers').querySelector('#provider-model-0').focused, true);
});

test('write failures and ETag conflicts preserve drafts and prevent blind retry', async () => {
  for (const status of [503, 412]) {
    const h = await loaded();
    const toggle = providerInputs(h).filter(input => input.type === 'checkbox')[3];
    toggle.checked = false;
    toggle.listeners.change();
    h.elements.get('provider-save').onclick();
    const write = h.requests[3];
    h.respond(write, { error: { message: status === 412 ? 'conflict' : 'unavailable' } }, status);
    await flush();
    assert.equal(h.run('providerDraft.openai.enabled'), false);
    assert.equal(h.run('providerDirty'), true);
    assert.equal(h.requests.length, 4);
    assert.match(h.elements.get('provider-error').textContent, status === 412 ? /another tab/ : /unavailable/);
    if (status === 412) {
      assert.equal(h.elements.get('provider-save').disabled, true);
      h.elements.get('provider-save').onclick();
      assert.equal(h.requests.length, 4);
    } else {
      assert.equal(h.elements.get('provider-save').disabled, false);
    }
  }
});

test('failed provider reload preserves unsaved edits and protects navigation', async () => {
  const h = await loaded();
  const enabled = providerInputs(h).filter(input => input.type === 'checkbox')[3];
  enabled.checked = false;
  enabled.listeners.change();
  const beforeunload = { preventDefault() { this.prevented = true; } };
  h.events.beforeunload(beforeunload);
  assert.equal(beforeunload.prevented, true);
  h.elements.get('reload').onclick();
  h.respond(h.requests[3], { error: { message: 'unauthorized' } }, 401);
  h.respond(h.requests[4], { error: { message: 'unauthorized' } }, 401);
  h.respond(h.requests[5], { error: { message: 'unauthorized' } }, 401);
  await flush();
  assert.equal(h.run('providerDraft.openai.enabled'), false);
  assert.equal(h.run('providerDirty'), true);
  assert.equal(h.elements.get('provider-save').disabled, true);
  assert.match(h.elements.get('provider-error').textContent, /unauthorized/);
});

test('Reload and Use token load providers through the shared authentication flow', async () => {
  const h = await loaded();
  h.elements.get('token').value = 'new-token';
  h.elements.get('token').listeners.input();
  h.elements.get('use-token').onclick();
  assert.deepEqual(h.requests.slice(3).map(item => item.path), ['/api/config/routing', '/api/config/model-variants', '/api/config/providers']);
  assert.equal(h.requests[5].options.headers.Authorization, 'Bearer new-token');
  h.respond(h.requests[3], { error: { message: 'unauthorized' } }, 401);
  h.respond(h.requests[4], { error: { message: 'unauthorized' } }, 401);
  h.respond(h.requests[5], { error: { message: 'unauthorized' } }, 401);
  await flush();
  assert.equal(h.elements.get('provider-status').textContent, 'Reload required');
  assert.match(h.elements.get('provider-error').textContent, /unauthorized/);
});
