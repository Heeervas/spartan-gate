const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the same page entry points and isolated IO used by the lifecycle suite.
const runtime = fs.readFileSync(`${__dirname}/test_dashboard_runtime.js`, 'utf8');
const fixture = { require, __dirname, setImmediate, AbortController, URLSearchParams, module: { exports: {} } };
vm.runInNewContext(runtime.slice(0, runtime.indexOf('\ntest(')) + '\nmodule.exports = { harness, Element, flush };', fixture);
const { harness, Element, flush } = fixture.module.exports;
const key = '0123456789abcdef';
const now = Date.parse('2030-01-08T12:00:00Z');
const at = minutes => new Date(now + minutes * 60000).toISOString();
function clock(h) { h.context.now = now; h.run('Date.now = () => now'); }

test('account glyph encodes raw windows, daily ratio, stop marks and weekly pace accessibly', () => {
  const h = harness('dashboard-codex'); clock(h);
  h.context.account = { weekly: { usedPercent: 42, resetAt: at(4 * 1440), windowMinutes: 10080 },
    fiveHour: { usedPercent: 68 }, quotaLimits: { weekly: 85, fiveHour: 60, daily: 15 },
    dailyBudget: { limit: 15, consumed: 6, status: 'ok' } };
  const html = h.run('accountGlyph(account)');
  assert.match(html, /viewBox="0 0 100 100" role="img"/);
  assert.match(html, /aria-label="Weekly 42% used, stop at 85%; today 6 of 15 points; 5-hour 68% used, stop at 60%"/);
  for (const [radius, percent, color] of [[44, 42, 'c-week'], [34, 40, 'c-day'], [24, 68, 'bad']]) {
    assert.match(html, new RegExp(`r="${radius}"[^>]*stroke="var\\(--${color}\\)"[^>]*pathLength="100" stroke-dasharray="${percent} 100"`));
  }
  assert.equal((html.match(/class="glyph-stop"/g) || []).length, 3);
  assert.equal((html.match(/class="glyph-pace"/g) || []).length, 1);
  assert.match(html, /class="glyph-center"[^>]*>42%<\/text>/);
  assert.match(h.run('paceVerdict(account)'), /1 pts under pace/);
  h.context.account.weekly.usedPercent = 49;
  assert.equal(h.run('paceVerdict(account)'), '+6 pts ahead of pace');
  h.context.account.stale = true;
  assert.equal(h.run('paceVerdict(account)'), 'pace unknown');
});

test('unknown rings are dashed without zero arcs; old servers omit Today, ratios clamp at 100', () => {
  const h = harness('dashboard-codex');
  const unknown = h.run('accountGlyph({weekly: null, fiveHour: {usedPercent: null}})');
  assert.equal((unknown.match(/glyph-unknown/g) || []).length, 2);
  assert.doesNotMatch(unknown, /glyph-arc|r="34"|0% used|NaN/);
  const missingDaily = h.run('accountGlyph({quotaLimits: {daily: 15}, dailyBudget: {status: "unknown", consumed: 0}, weekly: null, fiveHour: null})');
  assert.equal((missingDaily.match(/glyph-unknown/g) || []).length, 3);
  assert.match(missingDaily, /today unknown of 15 points/);
  assert.doesNotMatch(missingDaily, /glyph-arc/);
  const reached = h.run('accountGlyph({quotaLimits: {daily: 15}, dailyBudget: {status: "reached", consumed: 20}})');
  assert.match(reached, /r="34"[^>]*stroke="var\(--bad\)"[^>]*stroke-dasharray="100 100"/);
  const small = h.run('ringGlyph([{key: "weekly", value: null, label: "Unknown"}], 52, "hidden")');
  assert.doesNotMatch(small, /<text/);
});

test('short countdowns use a controlled clock and the existing ticker honors format and visibility', () => {
  const h = harness('dashboard-codex'); clock(h);
  for (const [minutes, expected] of [[3 * 1440 + 360, '3d6h'], [134, '2h14m'], [9, '9m'], [0.5, '<1m'], [-1, '<1m']]) {
    h.context.deadline = at(minutes);
    assert.equal(h.run('shortCountdown(deadline)'), expected);
  }
  assert.equal(h.run('shortCountdown("invalid")'), 'unknown');
  const short = new Element(); short.setAttribute('data-countdown', at(134)); short.setAttribute('data-countdown-format', 'short');
  const long = new Element(); long.setAttribute('data-countdown', at(134)); long.setAttribute('data-countdown-prefix', 'resets in ');
  h.document.querySelectorAll = selector => selector === '[data-countdown]' ? [short, long] : [];
  h.run('updateCountdowns()');
  assert.equal(short.textContent, '2h14m');
  assert.equal(long.textContent, 'resets in 2h 14m 0s');
  h.context.now += 60000;
  h.document.hidden = true; h.run('updateCountdowns()');
  assert.equal(short.textContent, '2h14m');
  h.document.hidden = false; h.run('updateCountdowns()');
  assert.equal(short.textContent, '2h13m');
});

test('glance totals are identity-deduplicated, mark unknown credits and link the earliest available expiry', () => {
  const h = harness('dashboard-codex'); clock(h);
  const paused = '1123456789abcdef'; const disabled = '2123456789abcdef'; const unknown = '3123456789abcdef';
  h.context.rows = [
    { accountKey: key, fiveHour: {resetAt: at(134)}, weekly: {resetAt: at(4 * 1440)}, resetCredits: {availableCount: 2, credits: [
      {status: 'available', expiresAt: at(10000)}, {status: 'redeemed', expiresAt: at(1)}, {status: 'available', expiresAt: at(9000)}]} },
    { accountKey: key, resetCredits: {availableCount: 2} },
    { accountKey: paused, fiveHour: {resetAt: at(200)}, resetCredits: {availableCount: 0, detailsAvailable: true} },
    { accountKey: disabled, resetCredits: null },
    { accountKey: unknown, resetCredits: {availableCount: 1, credits: []} },
  ];
  h.context.slots = [
    {accountKey: key, slotIndex: 0, routingEnabled: true, healthStatus: 'healthy'},
    {accountKey: paused, slotIndex: 1, routingEnabled: true, healthStatus: 'paused'},
    {accountKey: disabled, slotIndex: 2, routingEnabled: false, healthStatus: 'disabled'},
    {accountKey: unknown, slotIndex: 3, routingEnabled: true, healthStatus: 'unknown'},
  ];
  h.run('renderGlance(rows, slots)');
  const html = h.elements.get('glance').innerHTML;
  assert.match(html, /tile-value">3\+<\/div>/);
  assert.match(html, /some accounts unknown/);
  assert.match(html, /tile-value">1\/4<\/div>/);
  assert.match(html, /1 paused · 1 disabled/);
  assert.equal((html.match(new RegExp(`href="#acct-${key}"`, 'g')) || []).length, 1);
  assert.ok(html.includes(`…cdef ×2 · exp ${new Date(at(9000)).toLocaleDateString(undefined, {day: 'numeric', month: 'short'})}`));
  assert.match(html, /2h14m/);
  assert.match(html, /reset-kind">week/);
  assert.equal((html.match(/class="glance-tile /g) || []).length, 3);
  const order = ['tile-credits', 'tile-routing', 'tile-next'].map(name => html.indexOf(name));
  assert.ok(order.every((value, index) => index === 0 || value > order[index - 1]));
});

test('reset credits are outside disclosures; known zero omits chips and unknown never implies zero', () => {
  const h = harness('dashboard-codex');
  assert.equal(h.run('resetCreditStrip({availableCount: 0, detailsAvailable: true, credits: []})'), '');
  assert.equal(h.run('resetCreditStrip(null)'), '');
  assert.equal(h.run('resetCreditStrip({availableCount: 0, detailsAvailable: false})'), '');
  h.context.rows = [{accountKey: key, resetCredits: {availableCount: 5, credits: Array.from({length: 5}, () => ({status: 'available', resetType: '<weekly>', expiresAt: at(1000)}))}}];
  h.run('reconcileAccountCards = markup => { rendered = markup; }; renderAccountCards(rows)');
  const html = h.run('rendered');
  assert.match(html, new RegExp(`id="acct-${key}" data-account-key="${key}"`));
  assert.ok(html.indexOf('credit-row') < html.indexOf('class="account-controls"'));
  assert.equal((html.match(/class="credit-row/g) || []).length, 1);
  assert.match(html, /\+1 more/);
  assert.match(html, /&lt;weekly&gt;/);
  assert.doesNotMatch(html, /<weekly>/);
  assert.equal((html.match(/class="observation"/g) || []).length, 1);
  assert.ok(html.indexOf('class="observation"') > html.indexOf('class="account-controls"'));
  for (const [credits, label, value] of [[{availableCount: 0, detailsAvailable: true}, '0 reset credits', '0'], [null, 'Reset credits unknown', '?']]) {
    h.context.rows = [{accountKey: key, resetCredits: credits}];
    h.run('renderAccountCards(rows)');
    const rendered = h.run('rendered');
    assert.ok(rendered.includes(`class="credit-badge" aria-label="${label}" title="${label}">⟲ ${value}</span>`));
    assert.doesNotMatch(rendered, /class="credit-row|<span class="muted">Reset credits unknown/);
  }
});

test('compact window rows retain raw consumption, units and distinct stale sources', () => {
  const h = harness('dashboard-codex'); clock(h);
  h.context.account = { source: 'synthetic', weekly: {usedPercent: 42, usedUnits: 42, capacityUnits: 100, resetAt: at(3 * 1440 + 300), source: 'synthetic', updatedAt: at(-1)},
    fiveHour: {usedPercent: 68, usedUnits: 68, capacityUnits: 100, resetAt: at(134), source: 'other'},
    quotaLimits: {daily: 15}, dailyBudget: {consumed: 9.3, status: 'ok', sliceEnd: at(899)} };
  const weekly = h.run("balanceRing('Weekly usage', account.weekly, false, account)");
  assert.match(weekly, /title="42.0% raw consumed"/);
  assert.match(weekly, /class="window-value" title="42\.0% raw consumed">42%<\/strong>/);
  assert.ok(weekly.includes('42.0 / 100 units · 12 pts under pace'));
  assert.match(weekly, /data-countdown-format="short"[^>]*>3d5h/);
  assert.doesNotMatch(weekly, /class="observation"|>stale</);
  assert.match(h.run("balanceRing('Five-hour usage', account.fiveHour, true, account)"), /title="Observed · other[^"]*">stale/);
  const daily = h.run('dailyWindow(account)');
  assert.ok(daily.includes('9.3/15 pts'));
  assert.match(daily, /slice resets [\s\S]*14h59m/);
  assert.doesNotMatch(daily, /Weekly points|>reached</);
  h.context.account.dailyBudget.status = 'reached';
  assert.match(h.run('dailyWindow(account)'), /pill warn">reached/);
});

test('window bars warn at configured caps, not generic pressure thresholds', () => {
  const h = harness('dashboard-codex');
  for (const secondary of [false, true]) {
    for (const [usedPercent, cap, tone] of [[59, 60, secondary ? 'c-5h' : 'c-week'], [60, 60, 'bad'], [61, 60, 'bad'], [90, null, secondary ? 'c-5h' : 'c-week'], [null, 60, secondary ? 'c-5h' : 'c-week']]) {
      h.context.account = {quotaLimits: {[secondary ? 'fiveHour' : 'weekly']: cap}};
      h.context.row = {usedPercent}; h.context.secondary = secondary;
      assert.ok(h.run("balanceRing('Window', row, secondary, account)").includes(`--tone:var(--${tone})`));
    }
  }
  for (const [status, tone] of [['ok', 'c-day'], ['reached', 'bad'], ['unknown', 'c-day']]) {
    h.context.account = {quotaLimits: {daily: 15}, dailyBudget: {status, consumed: 15}};
    assert.ok(h.run('dailyWindow(account)').includes(`--tone:var(--${tone})`));
  }
});

test('Activity metric titles retain details hidden by the mobile quick-check layout', () => {
  const h = harness();
  assert.match(h.run('metric("Today errors", 2, \'3 missing usage & "partial"\')'), /class="metric" title="3 missing usage &amp; &quot;partial&quot;"/);
  h.context.payload = {version: 1, source: 'memory', historicalTelemetryEnabled: true, metrics: {requests: 2, cacheKnownRequests: 1, cacheHitPercent: 42}};
  h.run('window.SpartanRouteLive.render(liveMetricsEl, liveStatusEl, payload)');
  assert.match(h.elements.get('live-metrics').innerHTML, /class="metric" title="Token-weighted · 1\/2 with confirmed cache counters"/);
  assert.match(h.elements.get('live-metrics').innerHTML, /class="metric" title="Includes delivery; not generation speed"/);
});

test('daily validation rejects malformed drafts before writing; blanks submit all three nulls', async () => {
  for (const [value, badInput] of [['0', false], ['101', false], ['NaN', false], ['', true]]) {
    const h = harness('dashboard-codex');
    h.run(`quotaLimit({accountKey: '${key}'}, 'daily', 'Daily budget (pts of weekly)')`);
    const input = new Element(); input.dataset = {action: 'limit-daily', account: key}; input.value = value; input.validity = {badInput}; input.closest = () => input;
    h.elements.get('accounts').listeners.input({target: input});
    const button = new Element(); button.dataset = {action: 'limits', account: key}; button.closest = () => null;
    h.context.button = button;
    await assert.rejects(h.run('saveLimits(button, new AbortController().signal)'), /greater than 0 and at most 100/);
    assert.equal(h.requests.length, 0);
    assert.equal(h.run(`limitDrafts.get('${key}').daily`), value);
  }
  const h = harness('dashboard-codex');
  h.run(`quotaLimit({accountKey: '${key}'}, 'daily', 'Daily budget (pts of weekly)')`);
  const button = new Element(); button.dataset = {account: key}; button.closest = () => null; h.context.button = button;
  const pending = h.run('saveLimits(button, new AbortController().signal)');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {weekly: null, fiveHour: null, daily: null});
  h.respond(h.requests[0], {quotaLimits: {weekly: null, fiveHour: null, daily: null}}); await flush();
  h.respond(h.requests[1], {}); await pending;
  assert.equal(h.run(`limitDrafts.get('${key}').dirty`), false);
});

test('Activity cache reuse leads the band and never turns missing or saturated coverage into zero', () => {
  const h = harness();
  h.context.payload = {version: 1, source: 'memory', historicalTelemetryEnabled: false, metrics: {requests: 2, cacheKnownRequests: 0, cacheHitPercent: null}};
  h.run('window.SpartanRouteLive.render(liveMetricsEl, liveStatusEl, payload)');
  const metrics = h.elements.get('live-metrics');
  assert.ok(metrics.innerHTML.indexOf('Cache reuse') < metrics.innerHTML.indexOf('Requests · 15 min'));
  assert.match(metrics.innerHTML, /aria-label="Cache reuse unknown"/);
  assert.doesNotMatch(metrics.innerHTML, /glyph-arc/);
  h.context.payload.metrics.cacheHitPercent = 42;
  h.run('window.SpartanRouteLive.render(liveMetricsEl, liveStatusEl, payload)');
  assert.match(metrics.innerHTML, /stroke-dasharray="42 100"/);
  h.context.payload.saturated = true;
  h.run('window.SpartanRouteLive.render(liveMetricsEl, liveStatusEl, payload)');
  assert.match(metrics.innerHTML, /Cache reuse unknown/);
  assert.doesNotMatch(metrics.innerHTML, /glyph-arc/);
});

test('Activity summaries keep desktop facts in DOM and label only secondary facts for mobile hiding', () => {
  const h = harness();
  h.context.session = {id: 'example', metrics: {errors: 1}, models: ['codex/example'], turns: []};
  const html = h.run('sessionCard(session, 0, [session])');
  assert.match(html, /session-card has-errors/);
  assert.match(html, /class="muted session-meta"/);
  for (const label of ['Cache observations', 'Reasoning', 'Tools']) assert.match(html, new RegExp(`class="fact [^"]*fact-secondary"[^>]*><span class="fact-label">${label}`));
  assert.match(html, /class="fact speed fact-secondary"/);
  assert.match(html, /class="model-full">codex\/example<\/strong><strong class="model-short">example/);
  assert.match(html, /Est\. usage/);
  assert.match(html, /Status/);
});
