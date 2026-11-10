import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer as httpServer } from 'node:http';
import { mkdtemp, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { createWebToolsServer } from './web-tools.mjs';

const require = createRequire('/app/package.json');
const { Client } = require('@modelcontextprotocol/sdk-v1/client/index.js');
const { InMemoryTransport } = require('@modelcontextprotocol/sdk-v1/inMemory.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk-v1/client/stdio.js');
const TOKEN = 'a'.repeat(64);
const FRAME = 'BEGIN_UNTRUSTED_WEB_CONTENT\nfixture\nEND_UNTRUSTED_WEB_CONTENT\n';

async function fixture(run) {
  const calls = [];
  let handler = (_req, res) => res.end(JSON.stringify({ results: [{ title: 'fixture', url: 'https://example.com', content: 'fixture snippet' }] }));
  const http = httpServer((req, res) => { calls.push({ path: req.url, headers: req.headers }); handler(req, res); });
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const binding = { baseUrl: `http://127.0.0.1:${http.address().port}`, host: 'tool-edge:18792', token: TOKEN };
  const server = createWebToolsServer(binding);
  const client = new Client({ name: 'native-fixture', version: '1' });
  const [up, down] = InMemoryTransport.createLinkedPair();
  await server.connect(up);
  await client.connect(down);
  const f = { calls, binding, client, setHandler: value => { handler = value; } };
  try { await run(f); } finally { await client.close(); await server.close(); http.closeAllConnections(); await new Promise(resolve => http.close(resolve)); }
}

const call = (f, name, args) => f.client.callTool({ name, arguments: args });
const code = result => JSON.parse(result.content[0].text).error.code;
const searchData = result => JSON.parse(result.content[0].text.slice('BEGIN_UNTRUSTED_WEB_CONTENT\n'.length, -'\nEND_UNTRUSTED_WEB_CONTENT\n'.length));

test('actual MCP SDK discovers exactly two read-only tools and constructs fixed search requests', () => fixture(async f => {
  const tools = (await f.client.listTools()).tools;
  assert.deepEqual(tools.map(t => t.name).sort(), ['read_page', 'web_search']);
  assert.ok(tools.every(t => t.annotations.readOnlyHint && t.inputSchema.additionalProperties === false));
  const result = await call(f, 'web_search', { query: 'a+b&c', num_results: 3 });
  assert.equal(result.isError, undefined);
  assert.match(result.content[0].text, /^BEGIN_UNTRUSTED_WEB_CONTENT\n/);
  assert.match(result.content[0].text, /fixture/);
  assert.match(result.content[0].text, /\nEND_UNTRUSTED_WEB_CONTENT\n$/);
  assert.equal(f.calls[0].path, '/search?q=a%2Bb%26c&format=json');
  assert.equal(f.calls[0].headers.host, 'tool-edge:18792');
  assert.equal(f.calls[0].headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(f.calls[0].headers['accept-encoding'], 'identity');
}));

test('requested count is enforced locally and only compact citation fields reach MCP', () => fixture(async f => {
  const rows = Array.from({ length: 30 }, (_, index) => ({ title: `Result ${index}`, url: `https://example.com/${index}`, content: 'useful snippet', thumbnail: 'unused image', parsed_url: ['internal'], score: 7 }));
  f.setHandler((_req, res) => res.end(JSON.stringify({ results: rows, infoboxes: ['unused box'], suggestions: ['unused suggestion'] })));
  for (const requested of [2, undefined, 10]) {
    const args = { query: 'bounded output' }; if (requested !== undefined) args.num_results = requested;
    const result = await call(f, 'web_search', args); const data = searchData(result);
    assert.equal(data.results.length, requested ?? 5); assert.equal(data.returnedResults, requested ?? 5);
    assert.equal(data.hasMore, true); assert.equal(data.partial, false);
    assert.deepEqual(data.results[0], { title: 'Result 0', url: 'https://example.com/0', snippet: 'useful snippet' });
    assert.ok(!result.content[0].text.includes('unused')); assert.ok(!result.content[0].text.includes('parsed_url'));
  }
}));

test('engine failures stay visible, and complete upstream failure is not empty success', () => fixture(async f => {
  let results = [{ title: 'available', url: 'https://example.com', content: 'one source' }];
  let failures = [['brave', 'too many requests'], ['google', 'access denied'], ['other', 'private raw diagnostic']];
  f.setHandler((_req, res) => res.end(JSON.stringify({ results, unresponsive_engines: failures })));
  let reply = await call(f, 'web_search', { query: 'partial' }); let data = searchData(reply);
  assert.equal(reply.isError, undefined); assert.equal(data.partial, true);
  assert.deepEqual(data.warnings, [{ engine: 'brave', code: 'rate_limited' }, { engine: 'google', code: 'access_denied' }, { engine: 'other', code: 'unavailable' }]);
  assert.ok(!reply.content[0].text.includes('private raw diagnostic'));
  results = []; reply = await call(f, 'web_search', { query: 'failed' });
  assert.equal(reply.isError, true); assert.equal(searchData(reply).error.code, 'search_unavailable');
  failures = []; reply = await call(f, 'web_search', { query: 'genuinely empty' });
  assert.equal(reply.isError, undefined); assert.equal(searchData(reply).partial, false); assert.equal(searchData(reply).returnedResults, 0);
}));

test('complete framed output stays within16KiB without cutting URLs or UTF8 text', () => fixture(async f => {
  const rows = Array.from({ length: 10 }, (_, index) => ({ title: '漢'.repeat(200), url: `https://example.com/${index}?q=${'x'.repeat(1800)}`, content: 'é'.repeat(1000) }));
  rows.unshift({ title: 'invalid link', url: 'https://user:secret@example.com', content: 'not a citation' });
  f.setHandler((_req, res) => res.end(JSON.stringify({ results: rows })));
  const reply = await call(f, 'web_search', { query: 'large', num_results: 10 }); const data = searchData(reply);
  assert.ok(Buffer.byteLength(reply.content[0].text) <= 16384); assert.equal(data.truncated, true); assert.equal(data.hasMore, true);
  assert.ok(data.results.length > 0 && data.results.length < 10);
  for (const result of data.results) {
    assert.ok(Buffer.byteLength(result.title) <= 200); assert.ok(Buffer.byteLength(result.snippet) <= 600);
    assert.ok(rows.some(row => row.url === result.url)); assert.ok(!JSON.stringify(result).includes('�'));
  }
  assert.ok(!reply.content[0].text.includes('user:secret'));
}));

test('operator-only engine selection is fixed in the binding, never model arguments', () => fixture(async f => {
  const server = createWebToolsServer({ ...f.binding, searchEngines: ['bing', 'duckduckgo', 'google cse', 'wikipedia'] });
  const client = new Client({ name: 'configured-fixture', version: '1' }); const [up, down] = InMemoryTransport.createLinkedPair();
  await server.connect(up); await client.connect(down);
  try {
    await client.callTool({ name: 'web_search', arguments: { query: 'fixed engines', num_results: 2 } });
    const query = new URL('http://fixture' + f.calls[0].path).searchParams;
    assert.equal(query.get('engines'), 'bing,duckduckgo,google cse,wikipedia');
    const denied = await client.callTool({ name: 'web_search', arguments: { query: 'override', engines: 'google' } });
    assert.equal(code(denied), 'invalid_request'); assert.equal(f.calls.length, 1);
  } finally { await client.close(); await server.close(); }
  for (const engines of [[], ['bing', 'bing'], ['bing,google'], ['https://evil'], [null], Array(9).fill('bing')]) {
    assert.throws(() => createWebToolsServer({ ...f.binding, searchEngines: engines }), /binding_unavailable/);
  }
}));

test('invalid, oversized and authority-override arguments never reach REST', () => fixture(async f => {
  for (const args of [{}, { query: '' }, { query: null }, { query: 7 }, { query: 'x'.repeat(4097) },
    { query: 'x', num_results: 0 }, { query: 'x', num_results: 11 }, { query: 'x', num_results: true },
    { query: 'x', headers: {} }, { query: 'x', principal: 'other' }]) {
    const result = await call(f, 'web_search', args);
    assert.equal(result.isError, true); assert.equal(code(result), 'invalid_request');
  }
  for (const args of [{}, { url: null }, { url: 'x'.repeat(8193) }, { url: 'https://example.com', proxy: '' }]) {
    assert.equal(code(await call(f, 'read_page', args)), 'invalid_request');
  }
  assert.equal(code(await call(f, 'browser', { action: 'tabs' })), 'not_found');
  assert.equal(f.calls.length, 0);
}));

test('Reader preserves complete trust framing and separate truncation notice', () => fixture(async f => {
  f.setHandler((_req, res) => { res.setHeader('X-Spartan-Content-Trust', 'untrusted-web-content'); res.setHeader('X-Spartan-Truncated', 'true'); res.end(FRAME); });
  const result = await call(f, 'read_page', { url: 'https://example.com/a?q=hello' });
  assert.equal(result.content[0].text, FRAME);
  assert.match(result.content[1].text, /truncated/i);
  assert.equal(f.calls[0].path, '/fetch?url=https%3A%2F%2Fexample.com%2Fa%3Fq%3Dhello');
  for (const [header, body] of [[false, FRAME], [true, FRAME.slice(0, -8)]]) {
    f.setHandler((_req, res) => { if (header) res.setHeader('X-Spartan-Content-Trust', 'untrusted-web-content'); res.end(body); });
    assert.equal(code(await call(f, 'read_page', { url: 'https://example.com' })), 'upstream_error');
  }
}));

test('remote denials and failures use fixed MCP errors without leaking bodies or credentials', () => fixture(async f => {
  for (const [status, errorCode] of [[401, 'unauthorized'], [403, 'forbidden'], [400, 'invalid_request'], [429, 'capacity'], [504, 'deadline'], [503, 'reader_overloaded']]) {
    f.setHandler((_req, res) => { res.statusCode = status; res.end(JSON.stringify({ error: { code: errorCode, detail: TOKEN } })); });
    const result = await call(f, 'web_search', { query: 'private-query' });
    assert.equal(result.isError, true); assert.equal(code(result), errorCode); assert.ok(!JSON.stringify(result).includes(TOKEN));
  }
  f.setHandler((_req, res) => { res.statusCode = 502; res.end(JSON.stringify({ error: { code: 'remote_http_error', upstreamStatus: 429, detail: TOKEN } })); });
  assert.deepEqual(JSON.parse((await call(f, 'read_page', { url: 'https://example.com' })).content[0].text), { error: { code: 'remote_http_error', upstreamStatus: 429 } });
  f.setHandler((_req, res) => { res.statusCode = 500; res.end(TOKEN); });
  assert.equal(code(await call(f, 'web_search', { query: 'x' })), 'upstream_error');
}));

test('redirects, unexpected encoding, malformed search and streaming overflow fail without retry', () => fixture(async f => {
  const modes = [res => { res.statusCode = 302; res.setHeader('Location', '/stolen'); res.end(); },
    res => { res.setHeader('Content-Encoding', 'gzip'); res.end('private'); },
    res => res.end('not JSON'), res => res.end('[]'),
    res => { res.write('x'.repeat(65536)); res.end('x'.repeat(65537)); }];
  for (const mode of modes) {
    f.setHandler((_req, res) => mode(res));
    assert.equal(code(await call(f, 'web_search', { query: 'x' })), 'upstream_error');
  }
  assert.equal(f.calls.length, modes.length);
}));

test('immediate capacity and real cancellation release resources for the next call', () => fixture(async f => {
  const held = [];
  let closeA;
  const disconnected = new Promise(resolve => { closeA = resolve; });
  f.setHandler((req, res) => { held.push(res); if (req.url.includes('q=a&')) res.on('close', closeA); });
  const abort = new AbortController();
  const a = f.client.callTool({ name: 'web_search', arguments: { query: 'a' } }, undefined, { signal: abort.signal });
  const b = call(f, 'web_search', { query: 'b' });
  const aRejected = assert.rejects(a, /abort|cancel/i);
  while (held.length < 2) await new Promise(resolve => setImmediate(resolve));
  assert.equal(code(await call(f, 'web_search', { query: 'c' })), 'capacity');
  abort.abort(); await aRejected; await disconnected;
  f.setHandler((_req, res) => res.end('{"results":[]}'));
  try {
    // B stays active: only release of cancelled A permits this third admission.
    assert.equal((await call(f, 'web_search', { query: 'recovered' })).isError, undefined);
    assert.equal(f.calls.length, 3);
  } finally { held[1].end('{"results":[]}'); await b; }
}));

test('the total deadline also stops a stalled response body and permits recovery', { timeout: 35000 }, () => fixture(async f => {
  let closed;
  const disconnected = new Promise(resolve => { closed = resolve; });
  f.setHandler((_req, res) => { res.on('close', closed); res.writeHead(200); res.write('{'); });
  const started = Date.now();
  const result = await call(f, 'web_search', { query: 'stalled-body' });
  assert.equal(code(result), 'deadline');
  assert.ok(Date.now() - started >= 29000 && Date.now() - started < 34000);
  await disconnected;
  f.setHandler((_req, res) => res.end('{"results":[]}'));
  assert.equal((await call(f, 'web_search', { query: 'after-deadline' })).isError, undefined);
}));

test('actual stdio entry loads only a protected binding and negotiates with native SDK', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'spartan-web-mcp-'));
  try {
    const path = join(directory, 'binding.json');
    await writeFile(path, JSON.stringify({ baseUrl: 'http://tool-access:9081', host: 'tool-edge:18792', token: TOKEN }), { mode: 0o600 });
    const transport = new StdioClientTransport({ command: process.execPath, args: [new URL('./web-tools.mjs', import.meta.url).pathname], env: { SPARTAN_WEB_TOOLS_BINDING: path }, stderr: 'pipe' });
    let stderr = ''; transport.stderr.on('data', chunk => { stderr += chunk; });
    const client = new Client({ name: 'stdio-fixture', version: '1' });
    try { await client.connect(transport); assert.equal((await client.listTools()).tools.length, 2); } finally { await client.close(); }
    assert.equal(stderr, '');
    await chmod(path, 0o644);
    const denied = new StdioClientTransport({ command: process.execPath, args: [new URL('./web-tools.mjs', import.meta.url).pathname], env: { SPARTAN_WEB_TOOLS_BINDING: path }, stderr: 'pipe' });
    let diagnostic = ''; denied.stderr.on('data', chunk => { diagnostic += chunk; });
    const rejected = new Client({ name: 'denied-fixture', version: '1' });
    try { await assert.rejects(rejected.connect(denied), /closed/i); } finally { await rejected.close(); }
    assert.match(diagnostic, /binding_unavailable/); assert.ok(!diagnostic.includes(TOKEN));
  } finally { await rm(directory, { recursive: true, force: true }); }
});
