const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Reuse the existing isolated DOM/network fixture without registering its tests.
// Keeping this focused case separate also leaves the existing fixture unchanged.
const runtime = fs.readFileSync(`${__dirname}/test_dashboard_runtime.js`, 'utf8');
const fixture = { require, __dirname, setImmediate, AbortController, URLSearchParams, module: { exports: {} } };
vm.runInNewContext(runtime.slice(0, runtime.indexOf('\ntest(')) + '\nmodule.exports = { harness, Element, flush };', fixture);
const { harness, Element, flush } = fixture.module.exports;

test('quota limit drafts survive refreshed rings, save atomically and retain errors for retry', async () => {
  const h = harness('dashboard-codex');
  const key = '0123456789abcdef';
  h.context.account = { accountKey: key, quotaLimits: { weekly: null, fiveHour: null } };
  h.run("quotaLimit(account, 'weekly', 'Weekly usage'); quotaLimit(account, 'fiveHour', 'Five-hour usage')");
  const weekly = new Element(); weekly.dataset = { action: 'limit-weekly', account: key }; weekly.value = '70'; weekly.closest = () => weekly;
  const fiveHour = new Element(); fiveHour.dataset = { action: 'limit-fiveHour', account: key }; fiveHour.value = '50'; fiveHour.closest = () => fiveHour;
  const accounts = h.elements.get('accounts');
  accounts.listeners.input({ target: weekly });
  accounts.listeners.input({ target: fiveHour });
  h.context.account.quotaLimits = { weekly: 10, fiveHour: 10 };
  assert.match(h.run("quotaLimit(account, 'weekly', 'Weekly usage')"), /value="70"/);
  assert.match(h.run("quotaLimit(account, 'fiveHour', 'Five-hour usage')"), /value="50"/);
  const error = new Element();
  const card = { querySelector: () => error };
  const button = new Element(); button.dataset = { action: 'limits', account: key }; button.textContent = 'Save limits';
  button.closest = selector => selector === 'article.account' ? card : button;
  accounts.querySelectorAll = () => [button, weekly, fiveHour];
  h.run('renderPayload = () => {}');
  const failed = accounts.listeners.click({ target: button });
  assert.equal(weekly.disabled, true);
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { weekly: 70, fiveHour: 50 });
  h.respond(h.requests[0], { error: { message: 'Storage unavailable' } }, 503);
  await failed;
  assert.equal(h.requests.length, 1);
  assert.equal(error.textContent, 'Storage unavailable');
  assert.match(h.run('limitActions(account)'), /Storage unavailable/);
  assert.match(h.run("quotaLimit(account, 'fiveHour', 'Five-hour usage')"), /value="50"/);
  const saved = accounts.listeners.click({ target: button });
  h.respond(h.requests[1], { quotaLimits: { weekly: 70, fiveHour: 50 } });
  await flush();
  h.respond(h.requests[2], {});
  await saved;
  assert.equal(error.textContent, '');
  assert.equal(h.run(`limitDrafts.get('${key}').dirty`), false);
  assert.equal(fiveHour.disabled, undefined);
});

test('daily slot toggle commits its exact slot, reconciles on success, and failed writes never replay', async () => {
  const h = harness('dashboard-codex');
  const rendered = [];
  h.context.record = payload => rendered.push(payload);
  h.run('renderPayload = record');
  const button = new Element(); button.dataset = {action: 'toggle', slot: '7', next: 'false'};
  button.textContent = 'Disable'; button.closest = () => button;
  const handler = h.elements.get('accounts').listeners.click;
  const pending = handler({target: button});
  assert.equal(h.requests[0].path, '/api/codex/balancer/slots/7');
  assert.equal(h.requests[0].options.method, 'PATCH');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {routingEnabled: false});
  h.respond(h.requests[0], {}); await flush();
  assert.equal(h.requests[1].path, '/api/codex/usage');
  h.respond(h.requests[1], {slots: [{slotIndex: 7, routingEnabled: false}]});
  await pending;
  assert.equal(rendered[0].slots[0].routingEnabled, false);
  assert.equal(button.textContent, 'Disable');
  button.dataset.next = 'true';
  const failed = handler({target: button});
  h.respond(h.requests[2], {error: {message: 'Could not change slot'}}, 503);
  await failed;
  assert.equal(h.requests.length, 3);
  assert.equal(rendered.length, 1);
  assert.match(h.elements.get('errors').children.at(-1).textContent, /Could not change slot/);
  assert.equal(button.disabled, false);
});

test('daily usage check and capacity changes publish returned snapshots, not optimistic state', async () => {
  const h = harness('dashboard-codex');
  const rendered = [];
  h.context.record = payload => rendered.push(payload);
  h.run('renderPayload = record');
  const button = new Element(); button.dataset = {action: 'usage', slot: '2'}; button.closest = () => button;
  const check = h.elements.get('accounts').listeners.click({target: button});
  assert.equal(h.requests[0].path, '/api/codex/usage/slots/2');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.deepEqual(rendered, []);
  h.respond(h.requests[0], {accounts: [{accountKey: '0123456789abcdef', weekly: {usedPercent: 25}}]});
  await check;
  const select = new Element(); select.dataset = {action: 'capacity', account: '0123456789abcdef', savedValue: '1'}; select.value = '5';
  const change = h.elements.get('accounts').listeners.change({target: {closest: () => select}});
  assert.equal(h.requests[1].path, '/api/codex/balancer/accounts/0123456789abcdef/capacity');
  assert.deepEqual(JSON.parse(h.requests[1].options.body), {capacityMultiplier: 5});
  assert.equal(rendered.length, 1);
  h.respond(h.requests[1], {accounts: [{accountKey: '0123456789abcdef', capacityMultiplier: 5}]});
  await change;
  assert.equal(rendered[1].accounts[0].capacityMultiplier, 5);
  assert.equal(select.disabled, false);
});

test('slot-only identities retain direct controls, and missing health/windows never become healthy or zero', () => {
  const h = harness('dashboard-codex');
  const html = h.run(`(() => {
    const card = mergeAccountCards([], [{slotIndex: 3, accountKey: '0123456789abcdef', routingEnabled: false}])[0];
    return [accountHealth(card).label, renderSlotControls(card.slots[0]), quotaLimit(card, 'weekly', 'Weekly usage'), planControl(card), balanceRing('Weekly usage', card.weekly)];
  })()`);
  assert.equal(html[0], 'Unknown');
  assert.match(html[1], /Enable slot 3/);
  assert.match(html[1], /Check usage for slot 3/);
  assert.match(html[4], /Usage unavailable/);
  assert.doesNotMatch(html[4], /0.0%/);
  assert.match(html[2], /data-action="limit-weekly"/);
  assert.match(html[3], /data-action="capacity"/);
  for (const value of ['null', "'0'", 'NaN']) {
    assert.match(h.run(`balanceRing('Weekly usage', {usedPercent: ${value}})`), /Usage unavailable/);
  }
  assert.match(h.run("balanceRing('Weekly usage', {usedPercent: 0, updatedAt: 1000, source: 'persisted', stale: true})"), /0.0% raw consumed/);
  assert.match(h.run("balanceRing('Weekly usage', {usedPercent: 7, updatedAt: 1000}, false, {source: 'persisted', stale: true})"), /Stale · persisted/);
});

test('account renderer keeps one editable form per identity and dirty limits through changed polling and reorder', () => {
  const h = harness('dashboard-codex');
  const first = '0123456789abcdef';
  const second = 'fedcba9876543210';
  h.context.rows = [{accountKey: first, slotIndexes: [1], weekly: {usedPercent: 20}, quotaLimits: {weekly: 80}},
    {accountKey: second, slotIndexes: [2]}];
  h.run('reconcileAccountCards = markup => { rendered = markup; }; renderAccountCards(rows)');
  const input = new Element(); input.dataset = {action: 'limit-weekly', account: first}; input.value = '63'; input.closest = () => input;
  h.elements.get('accounts').listeners.input({target: input});
  h.context.rows[0].weekly.usedPercent = 21;
  h.context.rows[0].quotaLimits.weekly = 90;
  h.context.rows[0].slotIndexes = [4];
  h.run('renderAccountCards(rows, [{slotIndex: 3, routingEnabled: false}])');
  const markup = h.run('rendered');
  assert.ok(markup.indexOf(`data-account-key="${second}"`) < markup.indexOf(`data-account-key="${first}"`));
  assert.match(markup, new RegExp(`data-action="limit-weekly" data-account="${first}"\\s+value="63"`));
  assert.match(markup, /21.0% raw consumed/);
  assert.match(markup, /Enable slot 3/);
  assert.match(markup, /Usage unavailable/);
  for (const key of [first, second]) {
    for (const action of ['limit-weekly', 'limit-fiveHour', 'capacity', 'limits']) {
      const controls = markup.match(/<(?:input|select|button)\b[^>]*>/g) || [];
      assert.equal(controls.filter(tag => tag.includes(`data-action="${action}"`) && tag.includes(`data-account="${key}"`)).length, 1, `${key}: ${action}`);
    }
  }
  assert.equal((markup.match(/class="account-controls"/g) || []).length, 3);
  assert.equal(h.run(`limitDrafts.get('${first}').dirty`), true);
});

test('responsive gauges share truthful raw percentages, missing and stale text', () => {
  const h = harness('dashboard-codex');
  const html = h.run("balanceRing('Weekly usage', {usedPercent: 37.4, usedUnits: 187, capacityUnits: 500, stale: true, source: 'persisted'})");
  assert.match(html, /class="usage-bar"[^>]*aria-hidden="true"/);
  assert.match(html, /--used:37.4%/);
  assert.match(html, /37.4% raw consumed/);
  assert.match(html, /187 \/ 500 units/);
  assert.match(html, /Stale · persisted/);
  const missing = h.run("balanceRing('Five-hour usage', null)");
  assert.match(missing, /usage-bar bar-unavailable/);
  assert.match(missing, /Usage unavailable/);
  assert.doesNotMatch(missing, /0\.0%/);
});

test('changed account DOM restores disclosures before limit focus without scrolling', () => {
  const h = harness('dashboard-codex');
  const accounts = h.elements.get('accounts');
  const old = new Element(); old.dataset = {accountKey: 'key'};
  const next = new Element(); next.dataset = {accountKey: 'key'};
  const focused = new Element(); focused.dataset = {action: 'limit-weekly', account: 'key'}; focused.value = '70'; focused.closest = () => old;
  const replacement = new Element(); replacement.dataset = {...focused.dataset}; replacement.disabled = false;
  const details = new Element(); details.className = 'account-controls'; details.dataset = {accountKey: 'key'}; details.open = true;
  const diagnostics = new Element(); diagnostics.className = 'account-detail'; diagnostics.dataset = {accountKey: 'key'}; diagnostics.open = false;
  const nextDetails = new Element(); nextDetails.className = details.className; nextDetails.dataset = {...details.dataset}; nextDetails.open = false;
  const nextDiagnostics = new Element(); nextDiagnostics.className = diagnostics.className; nextDiagnostics.dataset = {...diagnostics.dataset}; nextDiagnostics.open = true;
  const calls = []; replacement.focus = options => { assert.equal(nextDetails.open, true); assert.equal(nextDiagnostics.open, false); calls.push(options.preventScroll); };
  next.querySelectorAll = () => [replacement];
  h.document.activeElement = focused;
  let replaced = false;
  accounts.querySelectorAll = selector => selector.includes('details') ? (replaced ? [nextDetails, nextDiagnostics] : [details, diagnostics]) : [replaced ? next : old];
  accounts.replaceChildren = () => { replaced = true; focused.isConnected = false; h.document.activeElement = null; };
  const template = {content: {childNodes: [next], querySelectorAll: () => [next]}};
  h.document.createElement = () => template;
  h.run("reconcileAccountCards('', [{accountKey: 'key'}], [])");
  assert.deepEqual(calls, [true]);
});

test('summary focus and open state follow account identity through changed-card reordering', () => {
  const h = harness('dashboard-codex');
  const accounts = h.elements.get('accounts');
  const old = new Element(); old.dataset = {accountKey: 'key'};
  const next = new Element(); next.dataset = {accountKey: 'key'};
  const unrelated = new Element(); unrelated.dataset = {accountKey: 'other'};
  const details = new Element(); details.className = 'account-controls'; details.dataset = {accountKey: 'key'}; details.open = true;
  const nextDetails = new Element(); nextDetails.className = details.className; nextDetails.dataset = {...details.dataset};
  const summary = new Element(); summary.closest = selector => selector === 'article.account' ? old : details;
  summary.matches = selector => selector === 'summary';
  const replacement = new Element(); let focused = false;
  replacement.focus = () => { assert.equal(nextDetails.open, true); focused = true; };
  nextDetails.querySelector = () => replacement;
  h.document.activeElement = summary;
  let replaced = false;
  accounts.querySelectorAll = selector => selector.includes('details') ? [replaced ? nextDetails : details] : (replaced ? [unrelated, next] : [old, unrelated]);
  accounts.replaceChildren = () => { replaced = true; summary.isConnected = false; h.document.activeElement = null; };
  h.document.createElement = () => ({content: {childNodes: [unrelated, next], querySelectorAll: () => [unrelated, next]}});
  h.run("reconcileAccountCards('', [{accountKey: 'other'}, {accountKey: 'key'}], [])");
  assert.equal(focused, true);
});

test('failed reads mark last-good Accounts stale and a successful read recovers freshness', async () => {
  const h = harness('dashboard-codex');
  // Only HTML parsing is replaced: exercise the real load/renderPayload/status lifecycle.
  h.run("renderAccountCards = rows => { stableHTML(accountsEl, rows[0]?.accountKey || 'empty'); accountsEl.children = [{}]; }; pending = load()");
  h.respond(h.requests[0], {accounts: [{accountKey: 'last-good-private'}]});
  await h.run('pending');
  h.run('pending = load()');
  h.respond(h.requests[1], {error: {message: 'temporarily unavailable'}}, 503);
  await flush();
  h.respond(h.requests[2], {error: {message: 'temporarily unavailable'}}, 503);
  await h.run('pending');
  assert.match(h.elements.get('accounts').innerHTML, /last-good-private/);
  assert.equal(h.elements.get('accounts').attributes['data-stale'], 'true');
  assert.match(h.elements.get('usage-status').textContent, /last-good readings are stale/);
  h.run('pending = load()');
  h.respond(h.requests[3], {accounts: [{accountKey: 'new-reading'}]});
  await h.run('pending');
  assert.match(h.elements.get('accounts').innerHTML, /new-reading/);
  assert.equal(h.elements.get('accounts').attributes['data-stale'], 'false');
});

test('contingency opens passively, does not refresh on focus or completed poll, and joins double clicks', async () => {
  const h = harness('dashboard-codex');
  h.run('renderAccountCards = () => {}; pending = load()');
  h.respond(h.requests[0], {optionalTelemetryEnabled: false, accounts: [], cacheDaily: null});
  await h.run('pending');
  assert.equal(h.requests[0].options.method, 'GET');
  assert.equal(h.timers.size, 0);
  assert.match(h.elements.get('cache').innerHTML, /analytics unavailable/);
  h.events.visibilitychange();
  assert.equal(h.requests.length, 1);
  h.run('pending = load(true); duplicate = load(true)');
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options.method, 'POST');
  assert.equal(h.requests[1].options.headers.Authorization, 'Bearer token');
  assert.equal(h.elements.get('force').disabled, true);
  h.respond(h.requests[1], {optionalTelemetryEnabled: false, refreshing: true, refreshProgress: {status: 'running'}});
  await flush();
  const poll = [...h.timers.values()].find(timer => timer.ms === 1000);
  assert.ok(poll);
  poll.callback();
  await flush();
  assert.equal(h.requests[2].path, '/api/codex/usage');
  assert.equal(h.requests[2].options.method, undefined);
  h.respond(h.requests[2], {optionalTelemetryEnabled: false, refreshing: false, refreshProgress: {status: 'complete'}});
  await h.run('pending');
  assert.equal(h.elements.get('force').disabled, false);
  assert.equal([...h.timers.values()].filter(timer => timer.ms === 15000).length, 0);
  h.events.visibilitychange();
  assert.equal(h.requests.length, 3);
});

test('uncalibrated normal-mode efficiency retains session/token averages and unknown quota percent', () => {
  const h = harness('dashboard-codex');
  h.context.usage = {available: false, unavailableReason: 'no_positive_weekly_usage', efficiency: [{
    model: 'codex/gpt-5.5', reasoning: 'high', sessions: 1, totalTokens: 1000000,
    averageMillionTokensPerSession: 1, percentPerMillionTokens: null,
  }]};
  h.run('renderEfficiency(usage)');
  const html = h.elements.get('efficiency').innerHTML;
  assert.match(html, /codex\/gpt-5.5/);
  assert.match(html, /<strong>1.000<\/strong>/);
  assert.match(html, /<td>1.00<\/td>/);
  assert.match(html, /<strong>unknown<\/strong>/);
  assert.doesNotMatch(html, /Historical analytics unavailable/);
  assert.match(h.elements.get('efficiency-meta').textContent, /Estimate unavailable/);
  h.run("renderEfficiency({available: false, unavailableReason: 'telemetry_unavailable', efficiency: []})");
  assert.match(h.elements.get('efficiency').innerHTML, /Historical analytics unavailable/);
  assert.doesNotMatch(h.elements.get('efficiency').innerHTML, /codex\/gpt-5.5|1.000/);
});

test('contingency reports unknown weighted capacity instead of silently choosing x1', () => {
  const h = harness('dashboard-codex');
  const html = h.run("planControl({accountKey: '0123456789abcdef', capacityMultiplier: null})");
  assert.match(html, /Weighted capacity unavailable/);
  assert.doesNotMatch(html, /selected|x1/);
});

test('access denial clears last-good account data and reveals token recovery', async () => {
  const h = harness('dashboard-codex');
  h.run("renderAccountCards = rows => { stableHTML(accountsEl, rows[0]?.accountKey || 'empty'); accountsEl.children = [{}]; }; pending = load()");
  h.respond(h.requests[0], {accounts: [{accountKey: 'private-before-denial'}]});
  await h.run('pending');
  assert.match(h.elements.get('accounts').innerHTML, /private-before-denial/);
  h.run('pending = load()');
  h.respond(h.requests[1], {error: {message: 'Access denied'}}, 401);
  await h.run('pending');
  assert.doesNotMatch(h.elements.get('accounts').innerHTML, /private-before-denial/);
  assert.equal(h.elements.get('dashboard-access').open, true);
  assert.match(h.elements.get('errors').children.at(-1).textContent, /Access denied/);
});
