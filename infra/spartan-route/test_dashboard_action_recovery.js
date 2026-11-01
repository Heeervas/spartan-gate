const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Existing Route VM/network fixture; no production exports or test-only flags.
const runtime = fs.readFileSync(`${__dirname}/test_dashboard_runtime.js`, 'utf8');
const fixture = {require, __dirname, setImmediate, AbortController, URLSearchParams, module: {exports: {}}};
vm.runInNewContext(runtime.slice(0, runtime.indexOf('\ntest(')) + '\nmodule.exports = {harness, Element, flush};', fixture);
const {harness, Element, flush} = fixture.module.exports;
const key = '0123456789abcdef';

async function loadedAccounts() {
  const h = harness('dashboard-codex');
  // Replace HTML parsing only. Real startup load, renderPayload, mutations,
  // generation ownership and error presentation all run unchanged.
  h.run("renderAccountCards = rows => { stableHTML(accountsEl, rows[0]?.accountKey || 'empty'); accountsEl.children = [{}]; }; pending = load()");
  h.respond(h.requests[0], {accounts: [{accountKey: 'private-before-action'}], source: 'live'});
  await h.run('pending');
  h.document.getElementById('dashboard-access').open = false;
  assert.match(h.elements.get('accounts').innerHTML, /private-before-action/);
  assert.equal(h.elements.get('accounts').attributes['data-stale'], 'false');
  assert.equal(h.elements.get('usage-status').textContent, 'live');
  return h;
}
function dailyAction(h, action) {
  const control = new Element();
  control.dataset = {action, slot: '2', next: 'false', account: key, savedValue: '1'};
  control.value = '5'; control.disabled = false; control.textContent = action;
  if (action === 'limits') {
    h.run(`quotaLimit({accountKey: '${key}', quotaLimits: {weekly: 60, fiveHour: null}}, 'weekly', 'Weekly usage')`);
    const input = new Element(); input.dataset = {action: 'limit-weekly', account: key}; input.value = '70'; input.closest = () => input;
    h.elements.get('accounts').listeners.input({target: input});
  }
  const error = new Element();
  const card = {querySelector: () => error};
  control.closest = selector => selector === 'article.account' ? card : control;
  const listener = h.elements.get('accounts').listeners[action === 'capacity' ? 'change' : 'click'];
  const pending = listener({target: control});
  return {pending, control, error};
}
async function acceptWrite(h, action) {
  h.respond(h.requests[1], action === 'limits' ? {quotaLimits: {weekly: 70, fiveHour: null}} : {});
  await flush();
  assert.equal(h.requests[2].path, '/api/codex/usage');
  assert.equal(h.requests[2].options.method, undefined);
}
function assertAccessRecovery(h) {
  assert.doesNotMatch(h.elements.get('accounts').innerHTML, /private-before-action/);
  assert.equal(h.elements.get('dashboard-access').open, true);
  assert.match(h.elements.get('usage-status').textContent, /Balances unavailable/);
}

test('direct daily-action 403 clears private readings and opens access recovery for every action owner', async t => {
  for (const action of ['toggle', 'usage', 'capacity', 'limits']) {
    await t.test(action, async () => {
      const h = await loadedAccounts();
      const {pending, control} = dailyAction(h, action);
      assert.equal(h.requests[1].options.method, action === 'usage' ? 'POST' : 'PATCH');
      h.respond(h.requests[1], {error: {message: 'Denied action'}}, 403);
      await pending;
      assertAccessRecovery(h);
      assert.equal(h.requests.length, 2, 'Denied writes/checks must not replay');
      assert.equal(control.disabled, false);
    });
  }
});

test('successful writes followed by failed reconciliation recover auth or mark retained readings stale', async t => {
  for (const action of ['toggle', 'limits']) {
    for (const status of [401, 503]) {
      await t.test(`${action} then GET ${status}`, async () => {
        const h = await loadedAccounts();
        const {pending} = dailyAction(h, action);
        await acceptWrite(h, action);
        h.respond(h.requests[2], {error: {message: 'Snapshot unavailable'}}, status);
        if (status === 503) {
          await flush();
          assert.equal(h.requests[3].path, '/api/codex/usage');
          h.respond(h.requests[3], {error: {message: 'Snapshot still unavailable'}}, 503);
        }
        await pending;
        if (status === 401) assertAccessRecovery(h);
        else {
          assert.match(h.elements.get('accounts').innerHTML, /private-before-action/);
          assert.equal(h.elements.get('accounts').attributes['data-stale'], 'true');
          assert.match(h.elements.get('usage-status').textContent, /last-good readings are stale/);
          assert.equal(h.elements.get('dashboard-access').open, false);
        }
        assert.equal(h.requests.filter(request => request.options.method === 'PATCH').length, 1);
      });
    }
  }
});

test('ordinary rejected writes retain current readings and limit drafts instead of falsely declaring a read failure', async t => {
  for (const action of ['limits', 'capacity']) {
    await t.test(action, async () => {
      const h = await loadedAccounts();
      const {pending, control, error} = dailyAction(h, action);
      h.respond(h.requests[1], {error: {message: 'Invalid policy'}}, 400);
      await pending;
      assert.match(h.elements.get('accounts').innerHTML, /private-before-action/);
      assert.equal(h.elements.get('accounts').attributes['data-stale'], 'false');
      assert.equal(h.elements.get('usage-status').textContent, 'live');
      assert.equal(h.elements.get('dashboard-access').open, false);
      assert.equal(h.requests.length, 2);
      if (action === 'limits') {
        assert.equal(h.run(`limitDrafts.get('${key}').weekly`), '70');
        assert.equal(h.run(`limitDrafts.get('${key}').dirty`), true);
        assert.equal(error.textContent, 'Invalid policy');
      } else assert.equal(control.value, '1');
    });
  }
});

test('retired-token action denials cannot publish recovery into the next credential context', async t => {
  for (const action of ['capacity', 'toggle']) {
    await t.test(action, async () => {
      const h = await loadedAccounts();
      const {pending} = dailyAction(h, action);
      if (action === 'toggle') await acceptWrite(h, action);
      const oldRequest = h.requests.at(-1);
      h.elements.get('token').value = 'new-token';
      h.elements.get('token').listeners.input();
      assert.equal(oldRequest.options.signal.aborted, true);
      h.respond(oldRequest, {error: {message: 'Old credential denied'}}, 403);
      await pending;
      assert.equal(h.elements.get('dashboard-access').open, false);
      assert.equal(h.elements.get('usage-status').textContent, 'Use token to load this account.');
      assert.equal(h.elements.get('errors').children.length, 0);
      h.run('pending = load()');
      assert.equal(h.requests.at(-1).options.headers.Authorization, 'Bearer new-token');
      h.respond(h.requests.at(-1), {accounts: [{accountKey: 'new-private-account'}], source: 'new live'});
      await h.run('pending');
      assert.match(h.elements.get('accounts').innerHTML, /new-private-account/);
      assert.equal(h.elements.get('accounts').attributes['data-stale'], 'false');
    });
  }
});
