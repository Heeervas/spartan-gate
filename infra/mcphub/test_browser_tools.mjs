import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { mkdtemp, chmod, rm, mkdir, rename, readFile, writeFile } from 'node:fs/promises';
import vm from 'node:vm';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { createBrowserToolsServer } from './browser-tools.mjs';
const require = createRequire('/app/package.json');
const { Client } = require('@modelcontextprotocol/sdk-v1/client/index.js');
const { InMemoryTransport } = require('@modelcontextprotocol/sdk-v1/inMemory.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk-v1/client/stdio.js');
const TOKEN = 'a'.repeat(64); const WORKFLOW = 'b'.repeat(64);
const decode = result => JSON.parse(result.content[0].text);
const code = result => decode(result).error?.code;
const call = (f, action, args = {}, operation = false) => f.client.callTool({ name: 'browser_' + action,
  arguments: { ...args, ...(operation ? { operation_id: operation === true ? randomUUID() : operation } : {}) } });

async function nativePeer(record, res, page) {
  const path = new URL(record.path, 'http://fixture').pathname;
  if (!path.startsWith('/tabs') && path !== '/spartan/capabilities') return false;
  assert.equal(record.headers.authorization, 'Bearer native-fixture-secret-key');
  if (path === '/tabs') { res.end(JSON.stringify({ tabs: [
    { tabId: 'native-tab', listItemId: 'native-group', url: 'https://example.com/form?secret=NO' },
    { tabId: 'foreign-tab', listItemId: 'foreign-group', url: 'https://private.example' },
  ] })); return true; }
  if (path.endsWith('/stats')) { res.end(JSON.stringify({ tabId: path.split('/')[2],
    sessionKey: path.includes('foreign-tab') ? 'foreign-group' : path.includes('native-tab') ? 'native-group' : 'managed-group',
    url: 'https://example.com/form?token=SECRET', toolCalls: 2, refsCount: 1, downloadsCount: 1 })); return true; }
  if (path.startsWith('/tabs/') && path.endsWith('/evaluate')) {
    const result = await vm.runInContext(record.body.expression, page);
    res.end(JSON.stringify({ ok: true, result })); return true;
  }
  if (path.endsWith('/downloads')) { res.end(JSON.stringify({ downloads: [{ id: 'd1', suggestedFilename: 'synthetic.txt', bytes: 10, path: '/PRIVATE', dataBase64: 'SECRET_BYTES' }] })); return true; }
  if (path === '/spartan/capabilities') { res.end('{"singleAttemptBrowserUpload":true}'); return true; }
  return false;
}

async function fixture(run, { native = false, profileWide = false } = {}) {
  let owned = !native;
  const page = vm.createContext({ console: Object.fromEntries(['log', 'info', 'warn', 'error', 'debug'].map(key => [key, () => {}])),
    URL, TextEncoder, Request, location: { href: 'https://example.com/form' },
    fetch: () => Promise.resolve({ status: 200 }), setTimeout: () => 1, clearTimeout: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, document: { querySelector: selector => selector === '#ready' ? {} : null } });
  const directory = await mkdtemp(join(tmpdir(), 'browser-sdk-')); await chmod(directory, 0o700);
  const calls = []; let override;
  const http = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const record = { path: req.url, method: req.method, headers: req.headers,
      body: chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined };
    calls.push(record);
    if (override && await override(record, res)) return;
    if (await nativePeer(record, res, page)) return;
    if (req.url === '/v1/workflows') res.end(JSON.stringify(req.method === 'POST' ? { workflowToken: WORKFLOW } : { ok: true }));
    else if (req.url.endsWith('/create')) { owned = true; res.end(JSON.stringify({ tabId: 'tab-1', private: TOKEN })); }
    else if (req.url.endsWith('/tabs')) res.end(JSON.stringify({ tabs: owned ? [{ tabId: 'tab-1', private: TOKEN }] : [] }));
    else if (req.url.endsWith('/snapshot')) res.end(JSON.stringify({ url: 'https://example.com', snapshot: 'untrusted fixture', refsCount: 1, truncated: false, hasMore: false, nextOffset: null }));
    else if (req.url.endsWith('/viewport')) res.end(JSON.stringify({ ok: true, strategy: 'pageViewport', requested: { width: 390, height: 844 }, effective: { innerWidth: 390, innerHeight: 844 } }));
    else if (req.url.endsWith('/screenshot')) {
      res.setHeader('Content-Type', 'image/png'); res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64'));
    } else if (req.url.endsWith('/file_stage')) res.end(JSON.stringify({ file_id: 'c'.repeat(32), name: 'photo.jpg', size: 17, sha256: 'd'.repeat(64), internalPath: '/private' }));
    else res.end('{"ok":true}');
  });
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const binding = { baseUrl: `http://127.0.0.1:${http.address().port}`, host: 'tool-edge:18792', token: TOKEN, statePath: join(directory, 'state.json') };
  const nativeBindingPath = native ? join(directory, 'native.json') : undefined;
  if (native) await writeFile(nativeBindingPath, JSON.stringify({ baseUrl: binding.baseUrl, token: 'native-fixture-secret-key',
    managedUserId: 'managed-scratch', profiles: { main: profileWide ? { userId: 'native-profile', groupAccess: 'profile' }
      : { userId: 'native-profile', sessionKeys: ['native-group'] } }, uploadRoot: '/browser/uploads' }), { mode: 0o600 });
  let server, client;
  const connect = async () => {
    server = await createBrowserToolsServer(binding, { nativeBindingPath }); client = new Client({ name: 'native-browser-fixture', version: '1' });
    const [up, down] = InMemoryTransport.createLinkedPair(); await server.connect(up); await client.connect(down);
  };
  await connect();
  const f = { calls, binding, directory, page, get client() { return client; }, setHandler: fn => { override = fn; },
    reconnect: async () => { await server.close(); await client.close(); await connect(); } };
  try { await run(f); }
  finally { await server.close(); await client.close(); http.closeAllConnections(); await new Promise(resolve => http.close(resolve)); await rm(directory, { recursive: true, force: true }); }
}

async function session(f) { return decode(await call(f, 'session_create', {}, true)).session_id; }

test('official SDK exposes explicit session/file/viewport schemas, with no discovery effects', () => fixture(async f => {
  const tools = (await f.client.listTools()).tools;
  for (const name of ['browser_session_create', 'browser_viewport', 'browser_upload', 'browser_snapshot',
    'browser_evaluate', 'browser_wait_for', 'browser_stats', 'browser_downloads', 'browser_attach',
    'browser_attachable_tabs', 'browser_debug_start', 'browser_debug_read', 'browser_debug_stop']) {
    const tool = tools.find(value => value.name === name); assert.ok(tool); assert.equal(tool.inputSchema.additionalProperties, false);
  }
  assert.equal(f.calls.length, 0);
  for (const [action, args] of [['viewport', { session_id: randomUUID(), tab_id: 't', width: 390, height: 844, relaunch: true }],
    ['upload', { session_id: randomUUID(), tab_id: 't', file_ids: ['c'.repeat(32)], selector: '#cv', userId: 'foreign' }],
    ['session_create', { session_id: 'chosen-by-model' }]]) {
    assert.equal(code(await call(f, action, args, true)), 'invalid_request');
  }
  assert.equal(f.calls.length, 0);
}));

test('explicit session creation is idempotent, resources recover after reconnect, and no workflow token leaks', () => fixture(async f => {
  const op = randomUUID(); const first = await call(f, 'session_create', {}, op);
  assert.equal(code(first), undefined); assert.deepEqual(await call(f, 'session_create', {}, op), first);
  const id = decode(first).session_id;
  assert.equal(f.calls.length, 1); assert.ok(!JSON.stringify(first).includes(WORKFLOW));
  const tab = await call(f, 'create_tab', { session_id: id, url: 'https://example.com' }, true);
  assert.deepEqual(decode(tab), { tab_id: 'tab-1' });
  assert.equal(f.calls[1].headers.authorization, 'Bearer ' + TOKEN);
  assert.equal(f.calls[1].headers['x-spartan-workflow'], WORKFLOW);
  await f.reconnect();
  assert.deepEqual(decode(await call(f, 'list_tabs', { session_id: id })), { tabs: [{ tab_id: 'tab-1' }] });
  const before = f.calls.length;
  assert.equal(code(await call(f, 'list_tabs', { session_id: randomUUID() })), 'stale_session');
  assert.equal(f.calls.length, before);
}));

test('snapshot trust frame, real PNG block, viewport and general-file staging retain their contracts', () => fixture(async f => {
  const id = await session(f); const args = { session_id: id, tab_id: 'tab-1' };
  const snapshot = await call(f, 'snapshot', { ...args, offset: 12 });
  assert.match(snapshot.content[0].text, /^BEGIN_UNTRUSTED_WEB_CONTENT\n/);
  assert.match(snapshot.content[0].text, /\nEND_UNTRUSTED_WEB_CONTENT\n$/);
  assert.equal(f.calls.at(-1).body.offset, 12);
  const image = await call(f, 'screenshot', args);
  assert.equal(image.content[0].type, 'image'); assert.equal(image.content[0].mimeType, 'image/png');
  assert.equal(Buffer.from(image.content[0].data, 'base64').readUInt32BE(16), 1);
  const viewport = await call(f, 'viewport', { ...args, width: 390, height: 844 }, true);
  assert.equal(decode(viewport).effective.innerWidth, 390);
  const file = await call(f, 'file_stage', { session_id: id, path: 'photo.jpg' }, true);
  assert.equal(decode(file).name, 'photo.jpg'); assert.ok(!JSON.stringify(file).includes('internalPath'));
  const upload = await call(f, 'upload', { ...args, file_ids: [decode(file).file_id], selector: '#cv' }, true);
  assert.deepEqual(decode(upload), { ok: true });
}));

test('same operation ID cannot change arguments or repeat a successful mutation', () => fixture(async f => {
  const id = await session(f); const op = randomUUID();
  const args = { session_id: id, tab_id: 'tab-1', selector: '#submit' };
  const first = await call(f, 'click', args, op); assert.equal(first.isError, undefined);
  assert.deepEqual(await call(f, 'click', args, op), first);
  assert.equal(code(await call(f, 'click', { ...args, selector: '#other' }, op)), 'operation_conflict');
  assert.equal(f.calls.filter(row => row.path.endsWith('/click')).length, 1);
}));

test('lost response after dispatch stays unknown across restart, with zero redispatch', () => fixture(async f => {
  const id = await session(f); const op = randomUUID();
  f.setHandler((record, res) => { if (record.path.endsWith('/upload')) { res.destroy(); return true; } return false; });
  const args = { session_id: id, tab_id: 'tab-1', file_ids: ['c'.repeat(32)], selector: '#file' };
  assert.equal(code(await call(f, 'upload', args, op)), 'remote_outcome_unknown');
  await f.reconnect();
  assert.equal(code(await call(f, 'upload', args, op)), 'remote_outcome_unknown');
  assert.equal(f.calls.filter(row => row.path.endsWith('/upload')).length, 1);
}));

test('failed final write poisons the state and never acknowledges remote success', () => fixture(async f => {
  const id = await session(f); const op = randomUUID();
  f.setHandler(async record => {
    if (!record.path.endsWith('/click')) return false;
    await rename(f.binding.statePath, f.binding.statePath + '.pending');
    await mkdir(f.binding.statePath, { mode: 0o700 });
    return false;
  });
  const args = { session_id: id, tab_id: 'tab-1', ref: 'e1' };
  assert.equal(code(await call(f, 'click', args, op)), 'state_unavailable');
  assert.equal(code(await call(f, 'click', args, op)), 'state_unavailable');
  const count = f.calls.length;
  assert.equal(code(await call(f, 'click', args, true)), 'state_unavailable');
  assert.equal(f.calls.length, count);
  const pending = JSON.parse(await readFile(f.binding.statePath + '.pending', 'utf8'));
  assert.equal(pending.operations[op].status, 'pending');
}));

test('closed-session cleanup returns cached historical result, never creates a replacement', () => fixture(async f => {
  const id = await session(f); const op = randomUUID();
  const result = await call(f, 'session_close', { session_id: id }, op);
  assert.equal(result.isError, undefined);
  assert.deepEqual(await call(f, 'session_close', { session_id: id }, op), result);
  assert.equal(code(await call(f, 'snapshot', { session_id: id, tab_id: 'tab-1' })), 'stale_session');
  assert.equal(f.calls.filter(row => row.path === '/v1/workflows').length, 2);
}));

const pageValue = result => JSON.parse(result.content[0].text.replace(/^BEGIN_UNTRUSTED_WEB_CONTENT\n/, '').replace(/\nEND_UNTRUSTED_WEB_CONTENT\n$/, ''));
const attach = async f => {
  const session_id = await session(f);
  const result = await call(f, 'attach', { session_id, tab_id: 'native-tab', profile: 'main', session_key: 'native-group' }, true);
  assert.equal(result.isError, undefined); return { session_id, tab_id: 'native-tab' };
};

test('missing native binding preserves old backend and denies new operations before REST/native effects', () => fixture(async f => {
  const session_id = await session(f); const before = f.calls.length;
  assert.equal(code(await call(f, 'evaluate', { session_id, tab_id: 'tab-1', expression: '7' }, true)), 'unsupported_backend');
  assert.equal(f.calls.length, before);
}));

test('official SDK borrows only exact configured native groups; utility results omit credentials, paths and bytes', () => fixture(async f => {
  const session_id = await session(f);
  const list = pageValue(await call(f, 'attachable_tabs', { session_id, profile: 'main' }));
  assert.deepEqual(list, { tabs: [{ tab_id: 'native-tab', session_key: 'native-group', url: 'https://example.com/form' }] });
  assert.equal(code(await call(f, 'attach', { session_id, tab_id: 'foreign-tab', profile: 'main', session_key: 'native-group' }, true)), 'forbidden');
  assert.equal(code(await call(f, 'attach', { session_id, tab_id: 'native-tab', profile: 'unknown', session_key: 'native-group' }, true)), 'forbidden');
  assert.equal(code(await call(f, 'attach', { session_id, tab_id: 'native-tab', profile: 'main', session_key: 'other' }, true)), 'forbidden');
  const args = await attach(f);
  assert.deepEqual(pageValue(await call(f, 'wait_for', { ...args, selector: '#ready' }, true)), { found: true });
  assert.deepEqual(pageValue(await call(f, 'wait_for', { ...args, selector: '#missing', timeout_ms: 0 }, true)), { found: false });
  assert.deepEqual(pageValue(await call(f, 'evaluate', { ...args, expression: '({answer:42})' }, true)), { result: { answer: 42 } });
  assert.deepEqual(pageValue(await call(f, 'evaluate', { ...args, expression: 'void 0' }, true)), { result: null });
  const stats = await call(f, 'stats', args); assert.equal(pageValue(stats).url, 'https://example.com/form');
  const downloads = await call(f, 'downloads', args);
  assert.deepEqual(pageValue(downloads), { downloads: [{ id: 'd1', suggestedFilename: 'synthetic.txt', bytes: 10, failed: false }] });
  const request = f.calls.find(row => row.path.includes('/downloads?'));
  assert.match(request.path, /includeData=false/); assert.match(request.path, /consume=false/);
  for (const value of [stats, downloads]) for (const secret of ['SECRET', '/PRIVATE', 'native-fixture-secret-key', WORKFLOW]) assert.ok(!JSON.stringify(value).includes(secret));
  f.setHandler((record, res) => {
    if (!record.path.includes('/downloads?')) return false;
    res.end('{"downloads":[{"id":"d2","suggestedFilename":"failed.txt","bytes":null,"mimeType":null,"failure":"/PRIVATE: secret failure"}]}'); return true;
  });
  assert.deepEqual(pageValue(await call(f, 'downloads', args)), { downloads: [{ id: 'd2', suggestedFilename: 'failed.txt', bytes: null, mimeType: null, failed: true }] });
  await f.reconnect(); assert.equal(pageValue(await call(f, 'stats', args)).toolCalls, 2);
}, { native: true }));

test('profile-wide binding accepts future task groups but never substitutes identity or mismatched tab group', () => fixture(async f => {
  const session_id = await session(f);
  f.setHandler((record, res) => {
    if (!record.path.startsWith('/tabs')) return false;
    assert.equal(new URL(record.path, 'http://fixture').searchParams.get('userId'), 'native-profile');
    if (record.path.includes('/stats?')) res.end('{"tabId":"future-tab","sessionKey":"new-conversation"}');
    else res.end('{"tabs":[{"tabId":"future-tab","listItemId":"new-conversation"}]}');
    return true;
  });
  const tabs = pageValue(await call(f, 'attachable_tabs', { session_id, profile: 'main' }));
  assert.equal(tabs.tabs[0].session_key, 'new-conversation');
  const input = { session_id, profile: 'main', tab_id: 'future-tab', session_key: 'wrong-group' };
  assert.equal(code(await call(f, 'attach', input, true)), 'forbidden');
  assert.equal(code(await call(f, 'attach', { ...input, profile: 'other' }, true)), 'forbidden');
  assert.equal(code(await call(f, 'attach', { ...input, userId: 'other' }, true)), 'invalid_request');
  assert.equal((await call(f, 'attach', { ...input, session_key: 'new-conversation' }, true)).isError, undefined);
  await f.reconnect();
  assert.equal(decode(await call(f, 'list_tabs', { session_id })).tabs[0].tab_id, 'future-tab');
}, { native: true, profileWide: true }));

test('one native borrower and historical denial replay survive reconnect', () => fixture(async f => {
  const args = await attach(f); const second = await session(f); const operation = randomUUID();
  const input = { session_id: second, tab_id: args.tab_id, profile: 'main', session_key: 'native-group' };
  const denied = await call(f, 'attach', input, operation); assert.equal(code(denied), 'workflow_busy');
  await f.reconnect(); assert.deepEqual(await call(f, 'attach', input, operation), denied);
  assert.equal(decode(await call(f, 'close_tab', args, true)).detached, true);
  assert.equal((await call(f, 'attach', input, true)).isError, undefined);
}, { native: true }));

test('native utilities validate the exact REST-created tab rather than adopting arbitrary IDs', () => fixture(async f => {
  const session_id = await session(f);
  await call(f, 'create_tab', { session_id, url: 'https://example.com' }, true);
  assert.equal(code(await call(f, 'evaluate', { session_id, tab_id: 'native-tab', expression: '7' }, true)), 'forbidden');
  assert.deepEqual(pageValue(await call(f, 'evaluate', { session_id, tab_id: 'tab-1', expression: '7' }, true)), { result: 7 });
  assert.equal(f.calls.find(row => row.path.endsWith('/evaluate')).body.userId, 'managed-scratch');
}, { native: true }));

for (const [label, size, streamed] of [['journal limit', 3000, false], ['transport header limit', 140000, false], ['transport stream limit', 140001, true]]) {
  test(`native evaluate oversized receipt: ${label}, completed durably without redispatch`, () => fixture(async f => {
    const args = await attach(f); const operation = randomUUID();
    if (streamed) f.setHandler((record, res) => {
      if (!record.path.endsWith('/evaluate')) return false;
      const payload = JSON.stringify({ ok: true, result: 'x'.repeat(size) });
      res.writeHead(200); res.write(payload.slice(0, 70000)); res.end(payload.slice(70000)); return true;
    });
    const input = { ...args, expression: `'x'.repeat(${size})` };
    const first = await call(f, 'evaluate', input, operation);
    assert.equal(code(first), 'response_too_large');
    const disk = JSON.parse(await readFile(f.binding.statePath, 'utf8'));
    assert.equal(disk.operations[operation].status, 'done');
    assert.ok(Buffer.byteLength(JSON.stringify(disk.operations[operation].result)) <= 2048);
    await f.reconnect(); assert.deepEqual(await call(f, 'evaluate', input, operation), first);
    assert.equal(f.calls.filter(row => row.path.endsWith('/evaluate')).length, 1);
  }, { native: true }));
}

for (const outcome of ['lost', 'oversized', 'malformed']) test(`borrowed upload ${outcome} response retains claims across reconnect`, () => fixture(async f => {
  const args = await attach(f);
  assert.equal(code(await call(f, 'upload', { ...args, file_ids: ['e'.repeat(32)], selector: '#cv' }, true)), 'forbidden');
  const staged = decode(await call(f, 'file_stage', { session_id: args.session_id, path: 'photo.jpg' }, true));
  const input = { ...args, file_ids: [staged.file_id], selector: '#cv' }; const operation = randomUUID();
  f.setHandler((record, res) => {
    if (!record.path.startsWith('/tabs/') || !record.path.endsWith('/upload')) return false;
    assert.deepEqual(record.body.path, ['/browser/uploads/' + staged.file_id + '/photo.jpg']);
    assert.equal(record.body.singleAttempt, true); assert.equal(record.body.userId, 'native-profile');
    if (outcome === 'oversized') res.end(JSON.stringify({ ok: true, extra: 'x'.repeat(140000) }));
    else if (outcome === 'malformed') res.end('{broken');
    else res.destroy();
    return true;
  });
  assert.equal(code(await call(f, 'upload', input, operation)), 'remote_outcome_unknown');
  await f.reconnect(); assert.equal(code(await call(f, 'upload', input, operation)), 'remote_outcome_unknown');
  assert.equal(code(await call(f, 'upload', input, true)), 'file_in_use');
  assert.equal(code(await call(f, 'file_remove', { session_id: args.session_id, file_id: staged.file_id }, true)), 'file_in_use');
  assert.equal(code(await call(f, 'close_tab', args, true)), 'file_in_use');
  assert.equal(code(await call(f, 'session_close', { session_id: args.session_id }, true)), 'file_in_use');
  assert.equal(f.calls.filter(row => row.path.startsWith('/tabs/') && row.path.endsWith('/upload')).length, 1);
  assert.equal(f.calls.filter(row => row.method === 'DELETE').length, 0);
  const disk = JSON.parse(await readFile(f.binding.statePath, 'utf8'));
  assert.equal(disk.version, 2); assert.equal(disk.sessions[args.session_id].files[staged.file_id].claim, operation);
  assert.equal(disk.operations[operation].status, 'pending');
}, { native: true }));

test('confirmed native upload releases only its own claims, enabling staging cleanup without closing native tab', () => fixture(async f => {
  const args = await attach(f);
  const staged = decode(await call(f, 'file_stage', { session_id: args.session_id, path: 'photo.jpg' }, true));
  assert.deepEqual(decode(await call(f, 'upload', { ...args, file_ids: [staged.file_id], selector: '#cv' }, true)), { ok: true });
  assert.equal((await call(f, 'file_remove', { session_id: args.session_id, file_id: staged.file_id }, true)).isError, undefined);
  const closed = decode(await call(f, 'session_close', { session_id: args.session_id }, true));
  assert.equal(closed.detached, true);
  assert.ok(f.calls.every(row => row.method !== 'DELETE' || row.path === '/v1/workflows'));
}, { native: true }));

test('active capture detach and session-close never evaluate cleanup or delete native resources, even with unresponsive page', () => fixture(async f => {
  const args = await attach(f); const operation = randomUUID();
  assert.equal(code(await call(f, 'debug_read', { ...args, capture_id: operation })), 'capture_missing');
  const start = await call(f, 'debug_start', { ...args, ttl_ms: 1000 }, operation);
  assert.equal(pageValue(start).capture_id, operation);
  assert.equal(code(await call(f, 'debug_start', args, true)), 'capture_busy');
  f.page.console.warn('synthetic warning'); await f.page.fetch('/upload?token=PRIVATE');
  const read = pageValue(await call(f, 'debug_read', { ...args, capture_id: operation }));
  assert.ok(read.events.some(row => row.kind === 'console')); assert.match(read.coverage, /NOT|not|no prehistory/);
  const before = f.calls.filter(row => row.path.startsWith('/tabs/')).length;
  f.setHandler(record => { if (record.path.startsWith('/tabs/')) assert.fail('detach must not touch unresponsive native page'); return false; });
  const detached = decode(await call(f, 'close_tab', args, true));
  assert.equal(detached.detached, true); assert.match(detached.capture_restoration, /not_confirmed/);
  await call(f, 'session_close', { session_id: args.session_id }, true);
  assert.equal(f.calls.filter(row => row.path.startsWith('/tabs/')).length, before);
  assert.ok(f.calls.every(row => row.method !== 'DELETE' || row.path === '/v1/workflows'));
}, { native: true }));

test('fresh REST authority and native group are checked before every evaluation; no automatic stale-tab adoption', () => fixture(async f => {
  const args = await attach(f);
  f.setHandler((record, res) => {
    if (record.path !== '/v1/browser/tabs') return false;
    res.writeHead(409); res.end('{"error":{"code":"stale_workflow"}}'); return true;
  });
  assert.equal(code(await call(f, 'evaluate', { ...args, expression: '7' }, true)), 'stale_workflow');
  assert.equal(f.calls.filter(row => row.path.endsWith('/evaluate')).length, 0);
  f.setHandler((record, res) => {
    if (!record.path.includes('/stats?')) return false;
    res.end('{"tabId":"native-tab","sessionKey":"foreign-group"}'); return true;
  });
  assert.equal(code(await call(f, 'evaluate', { ...args, expression: '7' }, true)), 'forbidden');
  assert.equal(f.calls.filter(row => row.path.endsWith('/evaluate')).length, 0);
}, { native: true }));

test('actual stdio process refuses missing protected binding without leaking diagnostics', async () => {
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [new URL('./browser-tools.mjs', import.meta.url).pathname], env: { SPARTAN_BROWSER_TOOLS_BINDING: '/nonexistent' }, stderr: 'pipe' });
  let stderr = ''; transport.stderr.on('data', chunk => { stderr += chunk; });
  const client = new Client({ name: 'stdio-check', version: '1' });
  try { await assert.rejects(client.connect(transport)); }
  finally { await client.close(); }
  assert.match(stderr, /binding_or_state_unavailable/); assert.ok(!stderr.includes('ENOENT'));
});
