import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { mkdtemp, chmod, rm, mkdir, rename, readFile } from 'node:fs/promises';
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

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'browser-sdk-')); await chmod(directory, 0o700);
  const calls = []; let override;
  const http = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const record = { path: req.url, method: req.method, headers: req.headers,
      body: chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined };
    calls.push(record);
    if (override && await override(record, res)) return;
    if (req.url === '/v1/workflows') res.end(JSON.stringify(req.method === 'POST' ? { workflowToken: WORKFLOW } : { ok: true }));
    else if (req.url.endsWith('/create')) res.end(JSON.stringify({ tabId: 'tab-1', private: TOKEN }));
    else if (req.url.endsWith('/tabs')) res.end(JSON.stringify({ tabs: [{ tabId: 'tab-1', private: TOKEN }] }));
    else if (req.url.endsWith('/snapshot')) res.end(JSON.stringify({ url: 'https://example.com', snapshot: 'untrusted fixture', refsCount: 1, truncated: false, hasMore: false, nextOffset: null }));
    else if (req.url.endsWith('/viewport')) res.end(JSON.stringify({ ok: true, strategy: 'pageViewport', requested: { width: 390, height: 844 }, effective: { innerWidth: 390, innerHeight: 844 } }));
    else if (req.url.endsWith('/screenshot')) {
      res.setHeader('Content-Type', 'image/png'); res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64'));
    } else if (req.url.endsWith('/file_stage')) res.end(JSON.stringify({ file_id: 'c'.repeat(32), name: 'photo.jpg', size: 17, sha256: 'd'.repeat(64), internalPath: '/private' }));
    else res.end('{"ok":true}');
  });
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const binding = { baseUrl: `http://127.0.0.1:${http.address().port}`, host: 'tool-edge:18792', token: TOKEN, statePath: join(directory, 'state.json') };
  let server, client;
  const connect = async () => {
    server = await createBrowserToolsServer(binding); client = new Client({ name: 'native-browser-fixture', version: '1' });
    const [up, down] = InMemoryTransport.createLinkedPair(); await server.connect(up); await client.connect(down);
  };
  await connect();
  const f = { calls, binding, directory, get client() { return client; }, setHandler: fn => { override = fn; },
    reconnect: async () => { await server.close(); await client.close(); await connect(); } };
  try { await run(f); }
  finally { await server.close(); await client.close(); http.closeAllConnections(); await new Promise(resolve => http.close(resolve)); await rm(directory, { recursive: true, force: true }); }
}

async function session(f) { return decode(await call(f, 'session_create', {}, true)).session_id; }

test('official SDK exposes explicit session/file/viewport schemas, with no discovery effects', () => fixture(async f => {
  const tools = (await f.client.listTools()).tools;
  for (const name of ['browser_session_create', 'browser_viewport', 'browser_upload', 'browser_snapshot']) {
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

test('actual stdio process refuses missing protected binding without leaking diagnostics', async () => {
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [new URL('./browser-tools.mjs', import.meta.url).pathname], env: { SPARTAN_BROWSER_TOOLS_BINDING: '/nonexistent' }, stderr: 'pipe' });
  let stderr = ''; transport.stderr.on('data', chunk => { stderr += chunk; });
  const client = new Client({ name: 'stdio-check', version: '1' });
  try { await assert.rejects(client.connect(transport)); }
  finally { await client.close(); }
  assert.match(stderr, /binding_or_state_unavailable/); assert.ok(!stderr.includes('ENOENT'));
});
