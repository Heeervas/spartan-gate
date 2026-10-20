const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const asset = name => fs.readFileSync(`${__dirname}/web/assets/${name}.js`, 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor() {
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.value = '';
    this.textContent = '';
    this.isConnected = true;
    this.writes = 0;
    this._html = '';
  }
  set innerHTML(value) { this._html = value; this.writes++; }
  get innerHTML() { return this._html; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; this._html = ''; }
  querySelectorAll() { return []; }
  remove() { this.removed = true; }
}
function harness(name = 'dashboard') {
  const elements = new Map();
  const requests = [];
  const timers = new Map();
  const scripts = [];
  const events = {};
  let timerId = 0;
  const document = {
    hidden: false,
    head: { appendChild(script) { scripts.push(script); } },
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); },
    querySelectorAll() { return []; },
    addEventListener(type, handler) { events[type] = handler; },
  };
  const context = vm.createContext({
    document, window: {}, AbortController, URLSearchParams, TypeError,
    location: { search: '', pathname: '/dashboard' }, history: { replaceState() {} },
    sessionStorage: { getItem() { return 'token'; }, setItem() {} },
    setTimeout(callback, ms) { const id = ++timerId; timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() {},
    fetch(path, options) { return new Promise((resolve, reject) => requests.push({path, options, resolve, reject})); },
  });
  vm.runInContext(asset(name).replace(/    load\(\);\s*$/, ''), context);
  const run = code => vm.runInContext(code, context);
  const respond = (request, payload = {}, status = 200) => request.resolve({ ok: status < 400, status, json: async () => payload });
  return { context, run, requests, elements, document, events, timers, scripts, respond };
}

test('details script loads on first expansion only; reopening coalesces the request', async () => {
  const h = harness();
  assert.equal(h.scripts.length, 0);
  const item = new Element(); item.dataset = { turnId: 'turn', detailVersion: 'v1' };
  const target = new Element();
  h.context.item = item; h.context.target = target;
  h.run("first = loadTurn('turn', target, item); second = loadTurn('turn', target, item)");
  assert.equal(h.run('first === second'), true);
  assert.equal(h.scripts.length, 1);
  assert.equal(h.scripts[0].src, '/dashboard-assets/dashboard-details.js');
  assert.equal(h.requests.length, 1);
  vm.runInContext(asset('dashboard-details'), h.context);
  h.scripts[0].onload();
  h.respond(h.requests[0], { requests: [] });
  await h.run('first');
  assert.equal(item.dataset.loaded, '1');
  assert.equal(target.attributes['aria-busy'], 'false');
  assert.equal(h.run('turnLoads.size'), 0);
});

test('late turn response cannot publish after credentials or detail version changes', async () => {
  for (const change of ["tokenInput.value = 'new-token'", "item.dataset.detailVersion = 'v2'", 'target.isConnected = false']) {
    const h = harness();
    h.context.item = new Element(); h.context.item.dataset = { detailVersion: 'v1' };
    h.context.target = new Element();
    h.context.window.SpartanRouteDetails = { renderRequest: () => 'secret' };
    h.run("pending = loadTurn('turn', target, item)");
    h.run(change);
    h.respond(h.requests[0], { requests: [{}] });
    await h.run('pending');
    assert.doesNotMatch(h.context.target.innerHTML, /secret/);
    assert.equal(h.context.item.dataset.loaded, undefined);
  }
});

test('failed lazy script can retry; old loader error does not poison future expansions', async () => {
  const h = harness();
  const first = h.run('detailRenderer()');
  h.scripts[0].onerror();
  await assert.rejects(first, /Close and reopen/);
  const second = h.run('detailRenderer()');
  assert.equal(h.scripts.length, 2);
  h.context.window.SpartanRouteDetails = { renderRequest() {} };
  h.scripts[1].onload();
  await second;
});

test('unchanged markup performs zero additional DOM replacement', () => {
  for (const name of ['dashboard', 'dashboard-codex']) {
    const h = harness(name);
    h.context.element = new Element();
    h.run("stableHTML(element, '<p>same</p>'); stableHTML(element, '<p>same</p>')");
    assert.equal(h.context.element.writes, 1);
    h.run("stableHTML(element, '<p>changed</p>')");
    assert.equal(h.context.element.writes, 2);
  }
});

test('keyed turn reconciliation retains expanded request DOM while summary changes', () => {
  const h = harness();
  const old = new Element(); old.open = true; old.dataset = {turnId: 'id', detailVersion: 'v1', loaded: '1'};
  const summary = new Element(); summary.innerHTML = 'before';
  const request = new Element(); request.open = true;
  old.children = [summary, request];
  old.querySelector = () => summary;
  const next = new Element(); next.dataset = {...old.dataset};
  const newSummary = new Element(); newSummary.innerHTML = 'after';
  next.querySelector = () => newSummary;
  next.replaceWith = item => { next.replacement = item; };
  h.context.previous = { querySelectorAll: () => [old] };
  h.context.incoming = { querySelectorAll: () => [next] };
  h.run("retainDetails(incoming, previous, 'details.turn', 'turnId')");
  assert.equal(next.replacement, old);
  assert.equal(old.children[1], request);
  assert.equal(old.open, true);
  assert.equal(request.open, true);
  assert.equal(old.dataset.loaded, '1');
  assert.equal(summary.innerHTML, 'after');
});

test('Codex stale poll and mutation responses cannot overwrite a newer operation', async () => {
  const h = harness('dashboard-codex');
  const rendered = [];
  h.context.record = payload => rendered.push(payload.label);
  h.run('renderPayload = record; stale = load()');
  const button = new Element(); button.dataset = {action: 'usage', slot: '1'}; button.textContent = 'Check usage';
  const action = h.elements.get('accounts').listeners.click({target: {closest: () => button}});
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.equal(h.requests[1].options.method, 'POST');
  h.respond(h.requests[0], {label: 'stale'});
  await h.run('stale');
  assert.deepEqual(rendered, []);
  h.run("tokenInput.value = 'new-token'");
  h.elements.get('token').listeners.input();
  h.respond(h.requests[1], {label: 'old mutation'});
  await action;
  assert.deepEqual(rendered, []);
});

test('transient GET retries once; writes never retry and listeners are removed', async () => {
  const h = harness('dashboard-codex');
  h.run('pending = fetchJson("/read")');
  h.respond(h.requests[0], {error: {message: 'busy'}}, 503);
  await flush();
  assert.equal(h.requests.length, 2);
  h.respond(h.requests[1], {ok: true});
  await h.run('pending');
  const write = h.run('fetchJson("/write", {method: "PATCH"})');
  h.respond(h.requests[2], {error: {message: 'busy'}}, 503);
  await assert.rejects(write, /busy/);
  assert.equal(h.requests.length, 3);
  assert.equal(h.timers.size, 0);
  const controller = new AbortController();
  h.context.controller = controller;
  const bounded = h.run('fetchJson("/bounded", {signal: controller.signal})');
  h.respond(h.requests[3], {});
  await bounded;
  // An already completed request must not remain subscribed to the parent signal.
  controller.abort();
  assert.equal(h.requests[3].options.signal.aborted, false);
});

test('hidden pages suspend polling and visible pages resume with one read', () => {
  for (const name of ['dashboard', 'dashboard-codex']) {
    const h = harness(name);
    h.document.hidden = true;
    h.run('schedulePoll()');
    assert.equal(h.timers.size, 0);
    h.events.visibilitychange();
    assert.equal(h.requests.length, 0);
    h.document.hidden = false;
    h.events.visibilitychange();
    assert.equal(h.requests.length, name === 'dashboard' ? 2 : 1);
  }
});

test('raw percentages stay inside rings while weighted units remain alongside', () => {
  const h = harness('dashboard-codex');
  const html = h.run("balanceRing('Weekly balance', {usedPercent: 37.4, capacityMultiplier: 5, usedUnits: 187, capacityUnits: 500})");
  assert.match(html, /ring-value">37\.4%/);
  assert.match(html, /187 \/ 500 units/);
});


test('failed capacity writes restore the persisted plan without automatically retrying', async () => {
  const h = harness('dashboard-codex');
  const select = new Element(); select.dataset = {account: 'account', savedValue: '1'}; select.value = '5';
  const pending = h.elements.get('accounts').listeners.change({target: {closest: () => select}});
  h.respond(h.requests[0], {error: {message: 'failed'}}, 503);
  await pending;
  assert.equal(h.requests.length, 1);
  assert.equal(select.value, '1');
  assert.equal(select.disabled, false);
  assert.equal(h.run('accountSignature'), null);
});

test('targeted check allows its bounded server wait while ordinary GET remains five seconds', async () => {
  const h = harness('dashboard-codex');
  const pending = h.run('forceSlot(1)');
  assert.equal([...h.timers.values()][0].ms, 60000);
  assert.equal('timeoutMs' in h.requests[0].options, false);
  h.respond(h.requests[0], {});
  await pending;
  const read = h.run('fetchJson("/read")');
  assert.equal([...h.timers.values()][0].ms, 5000);
  h.respond(h.requests[1], {});
  await read;
});

test('initial Codex failure removes busy skeleton and credentials clear every data region', async () => {
  const h = harness('dashboard-codex');
  const pending = h.run('load()');
  h.respond(h.requests[0], {error: {message: 'unauthorized'}}, 401);
  await pending;
  assert.doesNotMatch(h.elements.get('accounts').innerHTML, /aria-busy/);
  assert.match(h.elements.get('accounts').innerHTML, /Balances unavailable/);
  for (const id of ['accounts', 'model-usage', 'efficiency', 'cache', 'leases']) {
    h.elements.get(id).innerHTML = 'previous credential content';
  }
  h.elements.get('token').listeners.input();
  for (const id of ['accounts', 'model-usage', 'efficiency', 'cache', 'leases']) {
    assert.equal(h.elements.get(id).innerHTML, '');
  }
});

test('refresh waits for completed progress and reliably restores both controls', async () => {
  const h = harness('dashboard-codex');
  h.run('renderPayload = () => {}; pending = load(true)');
  h.respond(h.requests[0], {refreshing: true});
  await flush();
  const tick = () => { const [id, timer] = [...h.timers].find(([,timer]) => timer.ms === 1000); h.timers.delete(id); timer.callback(); };
  tick(); await flush();
  h.respond(h.requests[1], {refreshing: false, refreshProgress: {status: 'running'}});
  await flush();
  tick(); await flush();
  h.respond(h.requests[2], {refreshing: false, refreshProgress: {status: 'complete'}});
  await h.run('pending');
  assert.equal(h.elements.get('force').disabled, false);
  assert.equal(h.elements.get('refresh').disabled, false);
  assert.equal(h.elements.get('force').textContent, 'Refresh Live');
});


test('changed routing reconciliation restores surviving focus without scrolling', () => {
  const h = harness();
  const calls = [];
  const element = {isConnected: true, focus(options) { calls.push(options.preventScroll); }};
  h.context.element = element;
  h.run('restoreFocus(element)');
  assert.deepEqual(calls, [true]);
  element.isConnected = false;
  h.run('restoreFocus(element)');
  assert.deepEqual(calls, [true]);
});


test('pending capacity mutation disables all controls and rejects a second unsaved selection', async () => {
  const h = harness('dashboard-codex');
  const first = new Element(); first.dataset = {account: 'one', savedValue: '1'}; first.value = '5'; first.disabled = false;
  const second = new Element(); second.dataset = {account: 'two', savedValue: '5'}; second.value = '5'; second.disabled = false;
  const action = new Element(); action.disabled = false;
  h.elements.get('accounts').querySelectorAll = () => [first, second, action];
  const handler = h.elements.get('accounts').listeners.change;
  const pending = handler({target: {closest: () => first}});
  assert.equal(second.disabled, true);
  assert.equal(action.disabled, true);
  second.value = '1';
  await handler({target: {closest: () => second}});
  assert.equal(second.value, '5');
  assert.equal(h.requests.length, 1);
  h.respond(h.requests[0], {error: {message: 'failed'}}, 503);
  await pending;
  assert.equal(first.disabled, false);
  assert.equal(second.disabled, false);
  assert.equal(action.disabled, false);
});


test('hiding during the refresh delay prevents the next poll dispatch', async () => {
  const h = harness('dashboard-codex');
  const pending = h.run('pollRefresh(loadGeneration, new AbortController().signal, Date.now() + 45000)');
  h.document.hidden = true;
  const [id, timer] = [...h.timers].find(([, timer]) => timer.ms === 1000);
  h.timers.delete(id); timer.callback();
  await pending;
  assert.equal(h.requests.length, 0);
});
