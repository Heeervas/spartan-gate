import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { patchManagedUpload } = require('./patch-managed-upload.js');
const input = process.env.CAMOFOX_PATCHED_SERVER;
const upstream = process.env.CAMOFOX_UPSTREAM_SOURCE;

function installedSource() {
  if (input) return fs.readFileSync(input, 'utf8');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-upload-source-'));
  try {
    fs.copyFileSync(path.join(upstream, 'server.js'), path.join(temp, 'server.js'));
    const script = path.join(path.dirname(fileURLToPath(import.meta.url)), 'patch-server.js');
    const result = spawnSync(process.execPath, [script], { cwd: temp, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    return fs.readFileSync(path.join(temp, 'server.js'), 'utf8');
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}

async function route(source, { strict = true, ref, count = 1, file = true, throws = false, missingFrame = false, emptyName = false } = {}) {
  const calls = []; const logs = [];
  const target = name => ({ count: async () => count, evaluate: async fn => fn({ tagName: file ? 'INPUT' : 'BUTTON', type: 'file' }),
    setInputFiles: async paths => { calls.push({ name, paths }); if (throws) throw new Error('ambiguous'); },
    nth() { return this; } });
  const first = target('first'); const second = target('second');
  const refName = emptyName ? '' : 'resume';
  const page = { locator: selector => selector === 'input[type="file"]' ? { first: () => first } : second,
    frames: () => [], getByRole: (_role, options) => options?.exact === true && options.name === refName ? second : first, waitForTimeout: async () => {} };
  const tabState = { page, refs: new Map([['e2', { role: 'button', name: refName, nth: 0, ...(missingFrame ? { frameUrl: 'https://frame.example/' } : {}) }]]), toolCalls: 0 };
  const start = source.indexOf("app.post('/tabs/:tabId/upload', async (req, res) => {");
  const end = source.indexOf("app.post('/tabs/:tabId/type', async (req, res) => {", start);
  const helperStart = source.indexOf('// SPARTAN_SINGLE_ATTEMPT_UPLOAD_V1');
  let handler;
  const context = { app: { post: (_path, fn) => { handler = fn; } },
    CONFIG: { uploadsDir: '/uploads' },
    sessions: new Map([['owner', {}]]), normalizeUserId: value => value,
    findTab: () => ({ tabState }), resolveUploadPaths: async () => ['/uploads/id/resume.docx'],
    withUserLimit: async (_owner, fn) => fn(), withTabLock: async (_id, fn) => fn(),
    selectorValidationError: () => null, refToLocator: () => second,
    UPLOAD_INPUT_TIMEOUT_MS: 100, UPLOAD_UI_TIMEOUT_MS: 100, UPLOAD_SETTLE_MS: 0,
    refreshTabRefs: async () => new Map(), log: (_level, _event, data) => logs.push(data),
    pluginEvents: { emit: () => {} }, handleRouteError: (err, _req, res) => res.status(err.statusCode || 500).json({ error: 'failed' }) };
  vm.runInNewContext((helperStart >= 0 ? source.slice(helperStart, start) : '') + source.slice(start, end), context);
  const response = { statusCode: 200, status(code) { this.statusCode = code; return this; }, json(body) { this.body = body; return this; } };
  await handler({ params: { tabId: 'tab' }, reqId: 'test', body: { userId: 'owner', path: '/uploads/id/resume.docx',
    ...(ref ? { ref: 'e2' } : { selector: '#second' }), ...(strict ? { singleAttempt: true } : {}) } }, response);
  return { calls, response, logs };
}

test('actual native-program upload handler targets once and keeps legacy behavior', { skip: input || upstream ? false : 'CAMOFOX_UPSTREAM_SOURCE or CAMOFOX_PATCHED_SERVER program input required' }, async t => {
  const original = installedSource(); const patched = patchManagedUpload(original);
  assert.equal(patchManagedUpload(patched), patched);
  await t.test('regression: legacy picks first; strict targets the selected second file input', async () => {
    assert.equal((await route(original, { strict: false })).calls[0].name, 'first');
    const result = await route(patched);
    assert.equal(result.response.body.ok, true);
    assert.deepEqual(result.calls.map(value => value.name), ['second']);
    assert.ok(!JSON.stringify(result.logs).includes('/uploads/'));
    assert.deepEqual((await route(patched, { strict: false })).calls, (await route(original, { strict: false })).calls);
  });
  await t.test('refs require exact accessible names, including unnamed inputs', async () => {
    for (const emptyName of [false, true]) {
      const result = await route(patched, { ref: true, emptyName });
      assert.equal(result.response.statusCode, 200);
      assert.deepEqual(result.calls.map(value => value.name), ['second']);
    }
  });
  for (const options of [{ count: 0 }, { count: 2 }, { file: false }, { ref: true, missingFrame: true }]) {
    await t.test('invalid/missing/nonfile/frame target causes zero attachments ' + JSON.stringify(options), async () => {
      const result = await route(patched, options);
      assert.equal(result.response.statusCode, 422);
      assert.equal(result.calls.length, 0);
    });
  }
  await t.test('ambiguous sole attempt has no chooser/click/fallback retry', async () => {
    const result = await route(patched, { throws: true });
    assert.equal(result.response.statusCode, 500); assert.equal(result.calls.length, 1);
  });
  await t.test('advertised capability and guarded anchors', () => {
    assert.match(patched, /singleAttemptBrowserUpload: true/);
    assert.throws(() => patchManagedUpload('unrelated'), /route missing/);
  });
});
