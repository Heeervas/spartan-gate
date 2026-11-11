/** Disposable integration: official MCP SDK -> real REST/files -> inert browser peer.
 * Does NOT exercise Firefox, a real site, native clients or production credentials.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer, get } from 'node:http';
import { mkdtemp, readFile, writeFile, readdir } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { createBrowserToolsServer } from './browser-tools.mjs';
const require = createRequire('/app/package.json');
const { Client } = require('@modelcontextprotocol/sdk-v1/client/index.js');
const { InMemoryTransport } = require('@modelcontextprotocol/sdk-v1/inMemory.js');
const bytes = Buffer.from([0, 255, 13, 10, 65, 66]);
let uploads = 0; let tabs = 0;
const peer = createServer(async (req, res) => {
  const chunks = []; for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : {};
  let result = { ok: true };
  if (req.url === '/health') result.browserGeneration = 1;
  if (req.url === '/spartan/capabilities') result = { nonDestructiveTabCreate: true, singleAttemptBrowserClick: true, singleAttemptBrowserUpload: true };
  if (req.url === '/tabs') { assert.equal(body.nonDestructive, true); result.tabId = 'tab-' + ++tabs; }
  if (req.url.startsWith('/tabs/native-tab/stats?')) result = { tabId: 'native-tab', sessionKey: 'native-group' };
  assert.notEqual(req.method + ' ' + req.url.split('?')[0], 'DELETE /tabs/native-tab', 'borrowed tab must survive');
  if (req.url.endsWith('/spartan/viewport')) {
    assert.equal(body.relaunch, false); assert.equal(body.reload, false);
    result = { ok: true, strategy: 'pageViewport', effective: { innerWidth: body.width, innerHeight: body.height } };
  }
  if (req.url.endsWith('/upload')) {
    assert.equal(body.singleAttempt, true); assert.equal(body.selector, '#second-file-input');
    assert.equal(body.path.length, 1); assert.deepEqual(await readFile(body.path[0]), bytes);
    await assert.rejects(writeFile(body.path[0], 'forbidden'), error => error.code === 'EROFS');
    uploads++;
  }
  res.end(JSON.stringify(result));
});
await new Promise(resolve => peer.listen(18081, '0.0.0.0', resolve));
const binding = { baseUrl: 'http://tool-access:9081', host: 'tool-edge:18792', token: 'a'.repeat(64), statePath: join(await mkdtemp('/tmp/browser-journal-'), 'state.json') };
const nativeBindingPath = join(binding.statePath, '..', 'native.json');
await writeFile(nativeBindingPath, JSON.stringify({ baseUrl: 'http://127.0.0.1:18081', token: 'synthetic-browser-key',
  managedUserId: 'fixture-browser', profiles: { main: { userId: 'native-fixture', sessionKeys: ['native-group'] } }, uploadRoot: '/browser/uploads' }), { mode: 0o600 });
const server = await createBrowserToolsServer(binding, { nativeBindingPath }); const client = new Client({ name: 'isolated-browser-acceptance', version: '1' });
const [up, down] = InMemoryTransport.createLinkedPair();
const call = (name, args = {}, op = true) => client.callTool({ name: 'browser_' + name, arguments: { ...args, ...(op ? { operation_id: op === true ? randomUUID() : op } : {}) } });
const value = result => { assert.notEqual(result.isError, true, result.content[0]?.text); return JSON.parse(result.content[0].text); };
try {
  // Readiness only: no mint or browser effect is used as a retry probe.
  let ready = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    ready = await new Promise(resolve => {
      const req = get('http://tool-access:9081/not-found', { headers: { Host: binding.host } }, res => { res.resume(); resolve(res.statusCode === 404); });
      req.setTimeout(1000, () => req.destroy()); req.on('error', () => resolve(false));
    });
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(ready, true, 'REST fixture startup');
  await server.connect(up); await client.connect(down);
  const session = value(await call('session_create')).session_id;
  const tab = value(await call('create_tab', { session_id: session, url: 'https://fixture.example/' })).tab_id;
  await writeFile('/incoming/ordinary.bin', bytes);
  const file = value(await call('file_stage', { session_id: session, path: 'ordinary.bin' }));
  assert.equal(file.sha256, createHash('sha256').update(bytes).digest('hex'));
  assert.equal(file.name, 'ordinary.bin');
  const op = randomUUID();
  const args = { session_id: session, tab_id: tab, file_ids: [file.file_id], selector: '#second-file-input' };
  value(await call('upload', args, op)); value(await call('upload', args, op));
  assert.equal(uploads, 1);
  value(await call('viewport', { session_id: session, tab_id: tab, width: 390, height: 844 }));
  const other = value(await call('session_create')).session_id;
  const otherTab = value(await call('create_tab', { session_id: other, url: 'https://fixture.example/' })).tab_id;
  const denied = await call('upload', { ...args, session_id: other, tab_id: otherTab });
  assert.equal(JSON.parse(denied.content[0].text).error.code, 'forbidden'); assert.equal(uploads, 1);
  value(await call('session_close', { session_id: session }));
  value(await call('session_close', { session_id: other }));
  assert.deepEqual(await readdir('/browser/uploads'), []);
  const borrowed = value(await call('session_create')).session_id;
  value(await call('attach', { session_id: borrowed, profile: 'main', session_key: 'native-group', tab_id: 'native-tab' }));
  const nativeFile = value(await call('file_stage', { session_id: borrowed, path: 'ordinary.bin' }));
  assert.equal(nativeFile.sha256, file.sha256);
  const nativeArgs = { session_id: borrowed, tab_id: 'native-tab', file_ids: [nativeFile.file_id], selector: '#second-file-input' };
  const nativeOperation = randomUUID();
  value(await call('upload', nativeArgs, nativeOperation)); value(await call('upload', nativeArgs, nativeOperation));
  assert.equal(uploads, 2, 'one old REST upload plus one direct native borrowed upload');
  assert.equal(value(await call('session_close', { session_id: borrowed })).detached, true);
  assert.deepEqual(await readdir('/browser/uploads'), []);
  assert.deepEqual(await readFile('/incoming/ordinary.bin'), bytes);
  console.log(JSON.stringify({ result: 'PASS', boundary: 'SDK-real-REST-real-files-inert-browser', uploads, borrowed_upload: true,
    duplicate_dispatches: 0, cross_session_denied: true, outgoing_read_only: true, source_preserved: true }));
} finally { await server.close(); await client.close(); peer.closeAllConnections(); await new Promise(resolve => peer.close(resolve)); }
