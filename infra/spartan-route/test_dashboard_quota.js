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
  h.run("quotaRing(account, 'weekly', 'Weekly usage'); quotaRing(account, 'fiveHour', 'Five-hour usage')");
  const weekly = new Element(); weekly.dataset = { action: 'limit-weekly', account: key }; weekly.value = '70'; weekly.closest = () => weekly;
  const fiveHour = new Element(); fiveHour.dataset = { action: 'limit-fiveHour', account: key }; fiveHour.value = '50'; fiveHour.closest = () => fiveHour;
  const accounts = h.elements.get('accounts');
  accounts.listeners.input({ target: weekly });
  accounts.listeners.input({ target: fiveHour });
  h.context.account.quotaLimits = { weekly: 10, fiveHour: 10 };
  assert.match(h.run("quotaRing(account, 'weekly', 'Weekly usage')"), /value="70"/);
  assert.match(h.run("quotaRing(account, 'fiveHour', 'Five-hour usage')"), /value="50"/);
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
  assert.match(h.run("quotaRing(account, 'fiveHour', 'Five-hour usage')"), /value="50"/);
  const saved = accounts.listeners.click({ target: button });
  h.respond(h.requests[1], { quotaLimits: { weekly: 70, fiveHour: 50 } });
  await flush();
  h.respond(h.requests[2], {});
  await saved;
  assert.equal(error.textContent, '');
  assert.equal(h.run(`limitDrafts.get('${key}').dirty`), false);
  assert.equal(fiveHour.disabled, undefined);
});
