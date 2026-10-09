const assert = require('node:assert/strict');
const test = require('node:test');
const { patchVaultEvaluate } = require('./patch-vault-evaluate.js');
const fixture = "const globalJsonParser = express.json({ limit: '100kb' });\n    operatorTabIsolationVersion: 1,\nproxySessionId: sessionProxy?.sessionId || null, tracePath };";
const patched = patchVaultEvaluate(fixture);
const route = patched.slice(patched.indexOf('// SPARTAN_VAULT_EVALUATE_V1'), patched.indexOf('// SPARTAN_VAULT_EVALUATE_END'));

test('vault patch is idempotent and precedes every global parser/reporter', () => {
  assert.equal(patchVaultEvaluate(patched), patched);
  assert.ok(patched.indexOf("app.post('/tabs/:tabId/spartan/vault-evaluate'") < patched.indexOf('const globalJsonParser'));
  assert.match(patched, /vaultEvaluateVersion: 1/);
  assert.doesNotMatch(route, /pluginEvents|handleRouteError|\blog\(/);
  assert.throws(() => patchVaultEvaluate('changed upstream'), /anchor mismatch/);
});

let express;
try { express = require(process.env.CAMOFOX_EXPRESS_MODULE || 'express'); } catch {}
test('protected endpoint sanitizes parser/errors, scope, trace, results and timeout cleanup', {
  skip: express ? false : 'run with installed Camofox express (CAMOFOX_EXPRESS_MODULE)',
}, async () => {
  const { createSpartanBrowserLifecycleCoordinator } = await import(process.env.CAMOFOX_LIFECYCLE_MODULE || './spartan-lifecycle.mjs');
  const lifecycle = createSpartanBrowserLifecycleCoordinator();
  const app = express();
  const group = new Map();
  const session = { tracePath: null, _spartanVaultUntraced: true, tabGroups: new Map([['group', group]]) };
  const sessions = new Map([['user', session]]);
  let evaluated = 0, closed = 0, released = 0, globalTouched = 0;
  let mode = 'success', lockReject = false;
  let rejectEvaluation;
  const page = {
    isClosed: () => false,
    evaluate: async () => {
      evaluated++;
      if (mode === 'error') throw new Error('SECRET_SENTINEL');
      if (mode === 'timeout') return new Promise((_, reject) => { rejectEvaluation = reject; });
      if (mode === 'bad-result') return { filled: 1, secret: 'SECRET_SENTINEL' };
      // Match the native build_fill_js return representation, including its
      // diagnostic origin which must never cross the protected boundary.
      return mode === 'refuse' ? JSON.stringify({ refused: 'origin_changed', found: 'SECRET_SENTINEL' }) : JSON.stringify({ filled: 2 });
    },
    close: async () => { closed++; rejectEvaluation?.(new Error('SECRET_SENTINEL')); },
  };
  const lock = { acquire: async () => { if (lockReject) throw new Error('Tab lock queue timeout'); }, release: () => released++, drain() {} };
  const CONFIG = { accessKey: 'test-access-key' };
  const deps = {
    app, express, CONFIG, timingSafeCompare: (a, b) => a === b,
    withUserLimit: async (_user, run) => run(), getTabLock: () => lock,
    TAB_LOCK_TIMEOUT_MS: 20, HANDLER_TIMEOUT_MS: 20, PAGE_CLOSE_TIMEOUT_MS: 20,
    spartanBrowserLifecycle: lifecycle, sessions, normalizeUserId: String,
    findTab: (s, id) => {
      for (const [listItemId, g] of s.tabGroups) if (g.has(id)) return { listItemId, group: g, tabState: g.get(id) };
    },
    SPARTAN_OPERATOR_GROUP: 'spartan-operator-login',
    tabLocks: new Map([['tab', lock]]), refreshTabLockQueueDepth() {}, refreshActiveTabsGauge() {},
  };
  Function(...Object.keys(deps), route)(...Object.values(deps));
  app.use(express.json());
  app.use((_req, res) => { globalTouched++; res.status(500).end('GLOBAL_SECRET_SENTINEL'); });
  app.use((_error, _req, res, _next) => { globalTouched++; res.status(500).end('GLOBAL_SECRET_SENTINEL'); });
  const server = app.listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  const url = `http://127.0.0.1:${server.address().port}/tabs/tab/spartan/vault-evaluate`;
  const body = { userId: 'user', sessionKey: 'group', expression: 'SECRET_SENTINEL' };
  const post = async (payload = body, token = CONFIG.accessKey, raw = false) => {
    const response = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: raw ? payload : JSON.stringify(payload) });
    const text = await response.text();
    assert.doesNotMatch(text, /SECRET_SENTINEL/);
    return { status: response.status, data: JSON.parse(text) };
  };
  const reset = () => { session.tabGroups = new Map([['group', group]]); group.set('tab', { page }); session.tracePath = null; session._spartanVaultUntraced = true; };
  try {
    reset();
    assert.equal((await post('{"SECRET_SENTINEL":', undefined, true)).status, 400);
    assert.equal((await post('"' + 'SECRET_SENTINEL'.repeat(9000) + '"', undefined, true)).status, 413);
    assert.equal((await post(body, 'bad')).status, 401);
    CONFIG.accessKey = ''; assert.equal((await post()).status, 401); CONFIG.accessKey = 'test-access-key';
    assert.equal((await post({ ...body, userId: 'other' })).status, 403);
    assert.equal((await post({ ...body, sessionKey: 'other' })).status, 403);
    session.tabGroups = new Map([['spartan-operator-login', group]]);
    assert.equal((await post({ ...body, sessionKey: 'spartan-operator-login' })).status, 403); reset();
    for (const tracePath of ['active.zip', undefined]) { session.tracePath = tracePath; assert.equal((await post()).status, 409); }
    reset(); session._spartanVaultUntraced = undefined; assert.equal((await post()).status, 409);
    reset(); assert.equal(evaluated, 0);
    assert.deepEqual((await post()).data, { ok: true, result: { filled: 2 } });
    mode = 'refuse'; assert.deepEqual((await post()).data.result, { refused: 'origin_changed' });
    mode = 'error'; assert.equal((await post()).status, 500);
    mode = 'bad-result'; assert.equal((await post()).status, 500);
    const beforeQueue = evaluated;
    lockReject = true; assert.equal((await post()).status, 503); lockReject = false;
    let releaseOwner;
    const occupied = lifecycle.run('occupied', () => new Promise(resolve => { releaseOwner = resolve; }));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal((await post()).status, 503);
    assert.equal(evaluated, beforeQueue); assert.equal(closed, 0); assert.ok(group.has('tab'));
    releaseOwner(); await occupied;
    mode = 'timeout'; assert.equal((await post()).status, 410);
    assert.equal(closed, 1); assert.equal(group.has('tab'), false);
    assert.equal(lifecycle.snapshot().ownerKind, null); assert.ok(released > 0);
    reset(); mode = 'success'; assert.equal((await post()).status, 200);
    assert.equal(globalTouched, 0);
    // Unresponsive close cannot release an evaluation lease or hang the HTTP reply.
    let resolveClose;
    page.close = () => new Promise(resolve => { resolveClose = resolve; });
    mode = 'timeout'; assert.equal((await post()).status, 410);
    assert.equal(lifecycle.snapshot().degradedCode, 'vault_cleanup_failed');
    assert.equal(lifecycle.snapshot().ownerKind, 'vault_evaluate');
    assert.equal((await post()).status, 503);
    rejectEvaluation(new Error('SECRET_SENTINEL')); resolveClose();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(lifecycle.snapshot().ownerKind, null);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
