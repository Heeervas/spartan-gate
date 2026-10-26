import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const upstream = process.env.CAMOFOX_UPSTREAM_SOURCE;
const patchedServer = process.env.CAMOFOX_PATCHED_SERVER;
const routeStart = "app.post('/tabs/:tabId/click', async (req, res) => {";
const routeEnd = '// --- /tabs/:tabId/upload timeouts (ms) ---';

function patch(source) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-single-click-'));
  try {
    fs.writeFileSync(path.join(temp, 'server.js'), source);
    const result = spawnSync(process.execPath, [path.join(here, 'patch-server.js')], {
      cwd: temp, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    return fs.readFileSync(path.join(temp, 'server.js'), 'utf8');
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

// Execute the actual patched HTTP handler, substituting only browser/resources.
async function clickRoute(source, { singleAttempt, google = false, error, ref = false } = {}) {
  const calls = { clicks: [], mouse: [], guards: 0, locks: [], users: [] };
  const locator = {
    count: async () => 1,
    click: async (options) => {
      calls.clicks.push({ ...options });
      if (error) throw new Error(error);
    },
    boundingBox: async () => ({ x: 0, y: 0, width: 20, height: 20 }),
  };
  const page = {
    locator: () => locator,
    url: () => google ? 'https://www.google.com/search?q=fixture' : 'https://example.com/',
    mouse: Object.fromEntries(['move', 'down', 'up'].map((name) => [name, async () => calls.mouse.push(name)])),
    waitForTimeout: async () => {},
    waitForLoadState: async () => {},
    isClosed: () => false,
  };
  const tabState = { page, toolCalls: 0, refs: new Map([['e1', {}]]), visitedUrls: new Set() };
  const session = {};
  let handler;
  const start = source.indexOf(routeStart);
  const end = source.indexOf(routeEnd, start);
  assert.ok(start >= 0 && end > start, 'native click route boundaries');
  vm.runInNewContext(source.slice(start, end), {
    app: { post: (_route, fn) => { handler = fn; } },
    sessions: new Map([['fixture-user', session]]),
    normalizeUserId: (id) => id,
    findTab: () => ({ tabState }),
    selectorValidationError: () => null,
    visibleSelectorCandidate: () => null,
    refToLocator: () => locator,
    withUserLimit: async (id, fn) => { calls.users.push(id); return fn(); },
    withTabLock: async (id, fn, timeout, onTimeout) => {
      calls.locks.push({ id, timeout, onTimeout: typeof onTimeout });
      return fn();
    },
    clickWithDownloadGuard: async (tab, fn) => {
      assert.equal(tab, tabState);
      calls.guards++;
      return fn();
    },
    isGoogleSerp: () => google,
    HANDLER_TIMEOUT_MS: 30000,
    withTimeout: async (promise) => promise,
    refreshTabRefs: async () => new Map(),
    safePageUrl: (value) => value.url(),
    log: () => {},
    pluginEvents: { emit: () => {} },
    handleRouteError: (err, _req, res) => res.status(500).json({ error: err.message }),
  });
  const body = { userId: 'fixture-user', ...(ref ? { ref: 'e1' } : { selector: '#submit' }) };
  if (singleAttempt !== undefined) body.singleAttempt = singleAttempt;
  const response = {
    statusCode: 200,
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
  };
  await handler({ params: { tabId: 'fixture-tab' }, body }, response);
  return { calls, response: { statusCode: response.statusCode, body: JSON.parse(JSON.stringify(response.body)) } };
}

test('pinned native click single-attempt mode and default compatibility', {
  skip: upstream ? false : 'set CAMOFOX_UPSTREAM_SOURCE to pristine pinned v1.15.0 source',
}, async (t) => {
  const pristine = fs.readFileSync(path.join(upstream, 'server.js'), 'utf8');
  const patched = patch(pristine);

  await t.test('patch is byte-idempotent and capabilities execute with the implementation', () => {
    assert.equal(patch(patched), patched);
    const start = patched.indexOf("app.get('/spartan/capabilities'");
    const end = patched.indexOf("app.post('/tabs', async (req, res) => {", start);
    assert.ok(start >= 0 && end > start);
    let capability;
    vm.runInNewContext(patched.slice(start, end), {
      app: { get: (_route, _auth, fn) => fn({}, { json: (body) => { capability = body; } }) },
      authMiddleware: () => () => {},
    });
    assert.equal(capability.singleAttemptBrowserClick, true);
  });

  for (const google of [false, true]) {
    for (const error of ['Timeout 3000ms exceeded', 'element intercepts pointer events', 'click failed']) {
      await t.test(`singleAttempt rejects once: google=${google}, ${error}`, async () => {
        const { calls, response } = await clickRoute(patched, { singleAttempt: true, google, error });
        assert.equal(response.statusCode, 500);
        assert.equal(response.body.error, error);
        assert.equal(calls.clicks.length, 1);
        assert.equal(calls.clicks[0].force, undefined);
        assert.ok(calls.clicks[0].timeout > 0 && calls.clicks[0].timeout <= 3000);
        assert.deepEqual(calls.mouse, []);
        assert.equal(calls.guards, 1);
        assert.deepEqual(calls.users, ['fixture-user']);
        assert.deepEqual(calls.locks, [{ id: 'fixture-tab', timeout: 30000, onTimeout: 'function' }]);
      });
    }
    await t.test(`singleAttempt succeeds for ref: google=${google}`, async () => {
      const { calls, response } = await clickRoute(patched, { singleAttempt: true, google, ref: true });
      assert.equal(response.body.ok, true);
      assert.equal(calls.clicks.length, 1);
      assert.equal(calls.clicks[0].force, undefined);
      assert.deepEqual(calls.mouse, []);
    });
    for (const singleAttempt of [undefined, false]) {
      for (const error of [undefined, 'Timeout 3000ms exceeded', 'element intercepts pointer events']) {
        await t.test(`default matches native: flag=${singleAttempt}, google=${google}, error=${error}`, async () => {
          const options = { singleAttempt, google, error };
          const original = await clickRoute(pristine, options);
          const actual = await clickRoute(patched, options);
          assert.deepEqual(actual, original);
          assert.equal(actual.response.body.ok, true);
          if (error) assert.deepEqual(actual.calls.mouse, ['move', 'down', 'up']);
        });
      }
    }
  }
});

// The coordinator may provide copied, already-patched image program bytes.
// Never discover image inputs by traversing runtime/profile directories.
test('already-patched image program upgrades once without changing default clicks', {
  skip: patchedServer ? false : 'set CAMOFOX_PATCHED_SERVER to an explicit already-patched image server.js',
}, async () => {
  const original = fs.readFileSync(patchedServer, 'utf8');
  const upgraded = patch(original);
  assert.equal(patch(upgraded), upgraded);
  for (const google of [false, true]) {
    const options = { google, error: 'Timeout 3000ms exceeded' };
    const { calls } = await clickRoute(upgraded, { ...options, singleAttempt: true });
    assert.equal(calls.clicks.length, 1);
    assert.equal(calls.clicks[0].force, undefined);
    assert.deepEqual(calls.mouse, []);
    assert.deepEqual(await clickRoute(upgraded, options), await clickRoute(original, options));
  }
});
