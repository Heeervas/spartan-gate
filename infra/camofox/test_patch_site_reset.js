const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { isIP } = require('node:net');
const { spawnSync } = require('node:child_process');
const { patchSiteReset, patchSiteResetPlugin, domainMatches } = require('./patch-site-reset.js');
const { patchPlugin } = require('./patch-persistence.js');
const prerequisites = [
  'SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1',
  'SPARTAN_PERSISTENCE_CLOSE_FENCE_V2',
  'SPARTAN_OPERATOR_LEASE_V2',
  'SPARTAN_OPERATOR_LEASE_API_V1',
  'SPARTAN_VAULT_EVALUATE_V1',
  'SPARTAN_VAULT_EVALUATE_END',
];
const fixture = `${prerequisites.map((marker) => '// ' + marker).join('\n')}
import express from 'express';
const capabilities = {
    vaultEvaluateVersion: 1,
};
const globalJsonParser = express.json({ limit: '100kb' });`;
const patched = patchSiteReset(fixture);
const route = patched.slice(
  patched.indexOf('function domainMatches'),
  patched.indexOf('// SPARTAN_SITE_RESET_END'),
);
const SECRET = 'SYNTHETIC_COOKIE_SECRET_SENTINEL';
const secretError = () => new Error(SECRET);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test('exact patch prerequisites, full revision validation, ordering and byte idempotence', () => {
  assert.equal(patchSiteReset(patched), patched);
  assert.ok(
    patched.indexOf("app.post('/tabs/:tabId/spartan/reset-site'") < patched.indexOf('const globalJsonParser'),
  );
  for (const marker of prerequisites)
    assert.throws(() => patchSiteReset(fixture.replace(marker, 'broken')), /prerequisite/);
  for (const anchor of [
    "import express from 'express';",
    '    vaultEvaluateVersion: 1,',
    "const globalJsonParser = express.json({ limit: '100kb' });",
  ]) {
    assert.throws(() => patchSiteReset(fixture.replace(anchor, 'changed')), /anchor/);
    assert.throws(() => patchSiteReset(fixture + '\n' + anchor), /anchor/);
  }
  for (const anchor of [
    '// SPARTAN_SITE_RESET_END',
    'const spartanSiteResetFences = new Map();',
    'siteResetVersion: 1,',
  ]) {
    assert.throws(() => patchSiteReset(patched.replace(anchor, 'incomplete')), /revision/);
  }
  assert.doesNotMatch(route, /handleRouteError|pluginEvents|captureException|console\.|\blog\(/);
});

test('CLI syntax failure leaves both inputs unchanged and removes staged candidates', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reset-patch-'));
  const server = path.join(dir, 'server.mjs');
  const plugin = path.join(dir, 'plugin.mjs');
  const pluginSource = `// SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V2
async function checkpointUser() {}
const ctx = { persistence: {} };
const fields = {
        logger,
        indexedDB,
};
  ctx.persistence.checkpointUser = checkpointUser;`;
  try {
    fs.writeFileSync(server, fixture + '\nconst broken = ;');
    fs.writeFileSync(plugin, pluginSource);
    const before = fs.readFileSync(server, 'utf8');
    const run = spawnSync(process.execPath, [path.join(__dirname, 'patch-site-reset.js'), server, plugin], {
      encoding: 'utf8',
    });
    assert.notEqual(run.status, 0);
    assert.equal(fs.readFileSync(server, 'utf8'), before);
    assert.equal(fs.readFileSync(plugin, 'utf8'), pluginSource);
    assert.deepEqual(fs.readdirSync(dir).sort(), ['plugin.mjs', 'server.mjs']);
    const transformed = patchSiteResetPlugin(pluginSource);
    assert.equal(patchSiteResetPlugin(transformed), transformed);
    assert.throws(() => patchSiteResetPlugin(pluginSource.replace('        logger,', 'logger,')), /anchor/);
    assert.throws(
      () => patchSiteResetPlugin(transformed.replace('checkpointUser.settled', 'broken')),
      /revision/,
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('one selector preserves host-only parents/siblings, all paths, label boundaries and exact IPs', () => {
  for (const [domain, host, expected] of [
    ['target.parent.test', 'target.parent.test', true],
    ['.target.parent.test', 'target.parent.test', true],
    ['.parent.test', 'target.parent.test', true],
    ['parent.test', 'target.parent.test', false],
    ['sibling.parent.test', 'target.parent.test', false],
    ['.parent.test', 'evilparent.test', false],
    ['.xn--bcher-kva.test', 'a.xn--bcher-kva.test', true],
    ['TARGET.TEST', 'target.test', true],
    ['127.0.0.1', '127.0.0.1', true],
    ['.127.0.0.1', '127.0.0.1', false],
    ['.0.0.1', '127.0.0.1', false],
    ['[::1]', '[::1]', true],
    ['.[::1]', '[::1]', false],
  ])
    assert.equal(domainMatches(domain, host), expected);
});

let express;
try {
  express = require(process.env.CAMOFOX_EXPRESS_MODULE || 'express');
} catch {}
const endpoint = (name, run) =>
  test(name, { skip: express ? false : 'requires actual Camofox Express input' }, run);
async function harness(t) {
  const { createSpartanBrowserLifecycleCoordinator } = await import('./spartan-lifecycle.mjs');
  const lifecycle = createSpartanBrowserLifecycleCoordinator();
  const app = express();
  const calls = {
    cookies: 0,
    writes: [],
    goto: [],
    routes: [],
    unroutes: [],
    logs: [],
    checkpoints: 0,
    order: [],
  };
  const state = {
    url: 'https://target.parent.test/deep?known=1#part',
    jar: [
      {
        name: 'login',
        value: SECRET,
        domain: 'target.parent.test',
        path: '/',
        secure: true,
        httpOnly: true,
        sameSite: 'Lax',
        expires: -1,
      },
      {
        name: '__Secure-login',
        value: SECRET,
        domain: '.parent.test',
        path: '/deep',
        secure: true,
        httpOnly: true,
        sameSite: 'None',
        expires: -1,
      },
      {
        name: 'sibling',
        value: SECRET,
        domain: 'sibling.parent.test',
        path: '/',
        secure: true,
        httpOnly: true,
        sameSite: 'Strict',
        expires: -1,
      },
    ],
    closed: false,
    mode: '',
    nth: 0,
    remaining: undefined,
  };
  const context = {
    async cookies(...args) {
      assert.equal(args.length, 0, 'inventory must not use a URL/path filter');
      calls.cookies++;
      if (state.mode === 'enumeration_fail' || (state.mode === 'verify_fail' && calls.cookies === 2))
        throw secretError();
      return structuredClone(state.jar);
    },
    async addCookies([cookie]) {
      calls.writes.push(cookie);
      if (state.nth && calls.writes.length === state.nth) throw secretError();
      if (state.mode !== 'residual')
        state.jar = state.jar.filter(
          (c) => !(c.name === cookie.name && c.domain === cookie.domain && c.path === cookie.path),
        );
      if (state.navigated) state.url = state.navigated;
    },
  };
  const page = {
    url: () => state.url,
    isClosed: () => state.closed,
    // An iframe's URL must never be asked for; only page.url identifies target.
    frames() {
      throw secretError();
    },
    async route(pattern, handler) {
      calls.routes.push([pattern, handler]);
      if (state.mode === 'route_fail') throw secretError();
    },
    async unroute(pattern, handler) {
      calls.unroutes.push([pattern, handler]);
      if (state.mode === 'unroute_fail') throw secretError();
    },
    async goto(url, options) {
      calls.goto.push([url, options]);
      if (state.mode === 'reload_fail') throw secretError();
      if (state.mode === 'reload_late') await state.remaining.promise;
      if (state.mode === 'fresh_cookie')
        state.jar.push({ name: 'new', value: SECRET, domain: 'target.parent.test', path: '/' });
    },
    reload() {
      throw new Error('POST must never be replayed');
    },
  };
  const tab = { page };
  const group = new Map([['tab', tab]]);
  const session = {
    context,
    tracePath: null,
    _spartanVaultUntraced: true,
    tabGroups: new Map([['group', group]]),
  };
  const sessions = new Map([['user', session]]);
  const findTab = (s, id) => {
    for (const [listItemId, g] of s.tabGroups)
      if (g.has(id)) return { listItemId, group: g, tabState: g.get(id) };
  };
  const checkpoint = (_user, reason) => {
    calls.checkpoints++;
    assert.equal(reason, 'site_reset');
    if (state.mode === 'checkpoint_fail') throw secretError();
    if (state.mode === 'checkpoint_timeout') return state.remaining.promise;
    if (state.mode === 'checkpoint_inFlight')
      return Promise.resolve({ persisted: false, inFlight: true, error: SECRET });
    if (state.mode === 'checkpoint_false') return Promise.resolve({ persisted: false, error: SECRET });
    return Promise.resolve({ persisted: true, secret: SECRET });
  };
  checkpoint.canCheckpointUser = (id, ctx) =>
    id === 'user' && ctx === context && state.mode !== 'persistence_inactive';
  checkpoint.settled = () => state.remaining?.promise || Promise.resolve();
  const pluginCtx = { persistence: { checkpointUser: checkpoint } };
  const CONFIG = { accessKey: 'synthetic-key' };
  const deps = {
    app,
    express,
    CONFIG,
    timingSafeCompare: (a, b) => a === b,
    isIP,
    sessions,
    normalizeUserId: String,
    findTab,
    SPARTAN_OPERATOR_GROUP: 'spartan-operator-login',
    pluginCtx,
    HANDLER_TIMEOUT_MS: 40,
    TAB_LOCK_TIMEOUT_MS: 40,
    spartanBrowserLifecycle: lifecycle,
    withUserLimit: async (_user, run) => {
      calls.order.push('user');
      return run();
    },
    getTabLock: () => ({
      acquire: async () => {
        calls.order.push('tab');
        if (state.mode === 'lock_fail') throw secretError();
      },
      release() {},
    }),
    log: (...args) => calls.logs.push(args),
    reporter: { captureException: (...args) => calls.logs.push(args) },
  };
  Function(...Object.keys(deps), route)(...Object.values(deps));
  app.use((_req, res) => {
    calls.logs.push('unexpected-global');
    res.status(500).end(SECRET);
  });
  app.use((error, _req, res, _next) => {
    calls.logs.push(error);
    res.status(500).end(SECRET);
  });
  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  t.after(async () => {
    state.remaining?.resolve({ persisted: true });
    await tick();
    await new Promise((resolve) => server.close(resolve));
    assert.deepEqual(calls.logs, [], 'protected request never reaches logs/reporters');
  });
  const post = async (body = {}, authorization = 'Bearer synthetic-key', raw = false) => {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/tabs/tab/spartan/reset-site`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization },
      body: raw
        ? body
        : JSON.stringify({ userId: 'user', sessionKey: 'group', operationId: randomUUID(), ...body }),
    });
    const text = await response.text();
    assert.ok(!text.includes(SECRET));
    const data = JSON.parse(text);
    assert.ok(
      Object.keys(data).every((k) =>
        ['ok', 'site', 'cookies', 'persisted', 'reload', 'code', 'residual_note'].includes(k),
      ),
    );
    assert.ok(['deleted', 'deleted_with_residual', 'none', 'partial', 'failed'].includes(data.cookies));
    if (data.cookies === 'deleted_with_residual') {
      assert.equal(data.residual_note, 'may_reappear_after_camofox_restart_repeat_reset');
    } else assert.equal(Object.hasOwn(data, 'residual_note'), false);
    assert.ok(['saved', 'unknown', 'not_attempted'].includes(data.persisted));
    assert.ok(['reloaded', 'skipped_navigated', 'not_attempted', 'failed'].includes(data.reload));
    await tick();
    return { status: response.status, data };
  };
  return { post, calls, state, page, tab, session, sessions, pluginCtx, CONFIG, lifecycle, group };
}

endpoint(
  'auth, strict shape/UUID, ownership, operator without lease, trace and persistence all precede enumeration',
  async (t) => {
    const h = await harness(t);
    for (const token of ['Bearer wrong', 'Bearer synthetic-key extra', 'synthetic-key', ''])
      assert.equal((await h.post({}, token)).status, 401);
    h.CONFIG.accessKey = '';
    assert.equal((await h.post()).status, 401);
    h.CONFIG.accessKey = 'synthetic-key';
    for (const body of [
      { unknown: SECRET },
      { operationId: 'not-a-uuid' },
      { operationId: '00000000-0000-1000-8000-000000000000' },
      { userId: '' },
      { sessionKey: '' },
    ])
      assert.equal((await h.post(body)).status, 400);
    assert.equal((await h.post('{"' + SECRET + '":', undefined, true)).status, 400);
    assert.equal((await h.post('"' + SECRET.repeat(300) + '"', undefined, true)).status, 413);
    assert.equal((await h.post({ userId: 'foreign' })).status, 403);
    assert.equal((await h.post({ sessionKey: 'foreign' })).status, 403);
    h.session.tabGroups = new Map([['spartan-operator-login', h.group]]);
    assert.equal((await h.post({ sessionKey: 'spartan-operator-login' })).status, 403);
    h.session.tabGroups = new Map([
      ['group', h.group],
      ['spartan-operator-login', new Map([['operator', { page: h.page }]])],
    ]);
    assert.equal((await h.post()).data.code, 'operator_denied'); // no lease field at all
    h.session.tabGroups.delete('spartan-operator-login');
    for (const tracePath of [SECRET, undefined]) {
      h.session.tracePath = tracePath;
      assert.equal((await h.post()).data.code, 'trace_denied');
    }
    h.session.tracePath = null;
    h.session._spartanVaultUntraced = undefined;
    assert.equal((await h.post()).data.code, 'trace_denied');
    h.session._spartanVaultUntraced = true;
    const service = h.pluginCtx.persistence.checkpointUser;
    h.pluginCtx.persistence.checkpointUser = null;
    assert.equal((await h.post()).data.code, 'persistence_unavailable');
    h.pluginCtx.persistence.checkpointUser = service;
    h.state.mode = 'persistence_inactive';
    assert.equal((await h.post()).data.code, 'persistence_unavailable');
    assert.equal(h.calls.cookies, 0);
    h.state.mode = '';
    assert.equal((await h.post()).data.ok, true);
    assert.deepEqual(h.calls.order.slice(-2), ['user', 'tab']);
  },
);

endpoint(
  'HTTP main-frame target expires all paths with exact attributes and reloads POST-backed page by GET goto only',
  async (t) => {
    const h = await harness(t);
    const original = structuredClone(h.state.jar);
    const response = await h.post();
    assert.deepEqual(response.data, {
      ok: true,
      site: 'target.parent.test',
      cookies: 'deleted',
      persisted: 'saved',
      reload: 'reloaded',
    });
    assert.equal(h.calls.cookies, 2);
    assert.deepEqual(
      h.calls.writes,
      original.slice(0, 2).map((c) => ({ ...c, value: '', expires: 1 })),
    );
    assert.deepEqual(h.state.jar, [original[2]]);
    assert.equal(h.calls.goto[0][0], 'https://target.parent.test/deep?known=1#part');
    assert.equal(h.calls.goto[0][1].waitUntil, 'load');
    assert.equal(h.calls.unroutes[0][1], h.calls.routes[0][1]);
    let fallback = 0;
    await h.calls.routes[0][1]({ fallback: async () => fallback++ });
    assert.equal(fallback, 1);
  },
);
for (const url of ['about:blank', 'file:///synthetic', 'data:text/html,synthetic', 'not-a-url']) {
  endpoint(`rejects non-HTTP main frame ${url} before inventory`, async (t) => {
    const h = await harness(t);
    h.state.url = url;
    assert.equal((await h.post()).data.code, 'invalid_site');
    assert.equal(h.calls.cookies, 0);
  });
}
for (const navigated of ['https://target.parent.test/changed?new=1', 'https://other.test/changed']) {
  endpoint(`full URL change skips reload: ${navigated}`, async (t) => {
    const h = await harness(t);
    h.state.navigated = navigated;
    const result = await h.post();
    assert.equal(result.data.site, 'target.parent.test');
    assert.equal(result.data.reload, 'skipped_navigated');
    assert.equal(h.calls.goto.length, 0);
    assert.equal(h.calls.checkpoints, 1);
  });
}
endpoint('replacement tab identity skips reload even at the same URL', async (t) => {
  const h = await harness(t);
  const original = h.page.goto;
  h.pluginCtx.persistence.checkpointUser.settled = () => {
    h.tab.page = { ...h.page, goto: original };
    return Promise.resolve();
  };
  assert.equal((await h.post()).data.reload, 'skipped_navigated');
  assert.equal(h.calls.goto.length, 0);
});
for (const [mode, cookies, persisted, reload] of [
  ['residual', 'deleted_with_residual', 'saved', 'reloaded'],
  ['enumeration_fail', 'failed', 'saved', 'not_attempted'],
  ['verify_fail', 'partial', 'saved', 'not_attempted'],
  ['checkpoint_fail', 'deleted', 'unknown', 'not_attempted'],
  ['checkpoint_false', 'deleted', 'unknown', 'not_attempted'],
  ['reload_fail', 'deleted', 'saved', 'failed'],
  ['route_fail', 'deleted', 'saved', 'failed'],
  ['unroute_fail', 'deleted', 'saved', 'failed'],
]) {
  endpoint(`fixed response and zero secret disclosure on ${mode}`, async (t) => {
    const h = await harness(t);
    h.state.mode = mode;
    const r = await h.post();
    assert.equal(r.data.ok, false);
    assert.equal(r.data.cookies, cookies);
    assert.equal(r.data.persisted, persisted);
    assert.equal(r.data.reload, reload);
    if (mode === 'residual') assert.equal(h.calls.writes.length, 2, 'second pass must not mutate');
    if (['reload_fail', 'route_fail', 'unroute_fail'].includes(mode))
      assert.equal(h.calls.routes[0][1], h.calls.unroutes[0][1]);
  });
}
endpoint('nth expiry failure checkpoints partial mutation once; no implicit retry', async (t) => {
  const h = await harness(t);
  h.state.nth = 2;
  const r = await h.post();
  assert.equal(r.data.cookies, 'partial');
  assert.equal(r.data.persisted, 'saved');
  assert.equal(h.calls.writes.length, 2);
  assert.equal(h.calls.checkpoints, 1);
  assert.equal(h.calls.goto.length, 0);
});
for (const mode of ['checkpoint_timeout', 'checkpoint_inFlight', 'reload_late']) {
  endpoint(
    `late underlying ${mode} holds busy after HTTP reply and through duplicate delivery`,
    async (t) => {
      const h = await harness(t);
      h.state.mode = mode;
      h.state.remaining = deferred();
      const operationId = randomUUID();
      const first = await h.post({ operationId });
      if (mode !== 'reload_late') {
        assert.equal(first.data.persisted, 'unknown');
        assert.equal(h.calls.goto.length, 0);
      } else assert.equal(first.data.reload, 'failed');
      assert.equal((await h.post()).data.code, 'busy');
      assert.deepEqual(await h.post({ operationId }), first);
      assert.equal(h.calls.writes.length, 2);
      h.state.remaining.resolve({ persisted: true });
      await tick();
      await tick();
      h.state.mode = '';
      h.state.remaining = undefined;
      assert.equal((await h.post()).data.ok, true);
    },
  );
}
endpoint('duplicate operation after reload never deletes a fresh cookie or navigates again', async (t) => {
  const h = await harness(t);
  h.state.mode = 'fresh_cookie';
  const operationId = randomUUID();
  const first = await h.post({ operationId });
  const duplicate = await h.post({ operationId });
  assert.equal(first.data.ok, true);
  assert.deepEqual(duplicate, first);
  assert.equal(h.calls.goto.length, 1);
  assert.equal(h.calls.writes.length, 2);
  assert.ok(h.state.jar.some((c) => c.name === 'new'));
});
endpoint('empty selection returns none, lock and lifecycle admission errors stay sanitized', async (t) => {
  const h = await harness(t);
  h.state.jar = [];
  assert.equal((await h.post()).data.cookies, 'none');
  h.state.mode = 'lock_fail';
  assert.equal((await h.post()).status, 503);
  h.state.mode = '';
  const entered = deferred(),
    release = deferred();
  const occupied = h.lifecycle.run('other', () => {
    entered.resolve();
    return release.promise;
  });
  await entered.promise;
  assert.equal((await h.post()).status, 503);
  release.resolve();
  await occupied;
});

test(
  'native persistence preflight, timeout waiter and separate settlement promise; reset errors never reach logger',
  {
    skip: process.env.CAMOFOX_UPSTREAM_SOURCE ? false : 'requires pinned Camofox program source',
  },
  async () => {
    let source = fs.readFileSync(
      path.join(process.env.CAMOFOX_UPSTREAM_SOURCE, 'plugins/persistence/index.js'),
      'utf8',
    );
    source = patchSiteResetPlugin(patchPlugin(source));
    assert.equal(patchSiteResetPlugin(source), source);
    source = source
      .replace(/^import[\s\S]*?from [^;]+;/gm, '')
      .replace('export async function register', 'async function register');
    const logs = [],
      listeners = new Map();
    let pending = deferred();
    const deps = {
      fs: {},
      getUserPersistencePaths() {},
      loadPersistedStorageState: async () => true,
      importBootstrapCookies() {},
      persistStorageState: async ({ logger }) => {
        logger.warn('failed', { error: SECRET, cookies: SECRET });
        return pending.promise;
      },
    };
    const register = Function(...Object.keys(deps), source + '\nreturn register;')(...Object.values(deps));
    const ctx = {
      persistence: { checkpointUser: null },
      config: { profileDir: '/synthetic' },
      events: { on: (name, handler) => listeners.set(name, handler) },
      log: (...args) => logs.push(args),
      auth: () => () => {},
    };
    await register({ post() {}, delete() {} }, ctx);
    const context = {};
    await listeners.get('session:created')({ userId: 'user', context });
    const checkpoint = ctx.persistence.checkpointUser;
    assert.equal(checkpoint.canCheckpointUser('user', context), true);
    assert.equal(checkpoint.canCheckpointUser('other', context), false);
    assert.equal(checkpoint.canCheckpointUser('user', {}), false);
    const waiter = checkpoint('user', 'site_reset', 20);
    const settled = checkpoint.settled('user');
    const keepalive = setTimeout(() => {}, 2000);
    try {
      assert.equal((await waiter).inFlight, true);
      let finished = false;
      settled.then(
        () => {
          finished = true;
        },
        () => {
          finished = true;
        },
      );
      await tick();
      assert.equal(finished, false);
      pending.resolve({ persisted: false, error: secretError() });
      await settled;
      assert.equal(finished, true);
      assert.ok(!JSON.stringify(logs).includes(SECRET));
      await listeners.get('session:destroyed')({ userId: 'user' });
      assert.equal(checkpoint.canCheckpointUser('user', context), false);
    } finally {
      clearTimeout(keepalive);
      pending.resolve({ persisted: false });
    }
  },
);
