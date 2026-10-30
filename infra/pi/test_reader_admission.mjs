import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { ToolsClient } from './extensions/spartan-tools/client.mjs';
import { ReaderAdmission } from './extensions/spartan-tools/admission.mjs';

const config = { baseUrl: 'http://fixture.invalid', token: 'a'.repeat(64) };
const input = index => ({ url: `https://example.com/${index}` });
const text = 'BEGIN_UNTRUSTED_WEB_CONTENT\nfixture\nEND_UNTRUSTED_WEB_CONTENT\n';

async function boundary(run) {
  const original = globalThis.fetch;
  let inflight = 0;
  let peak = 0;
  const dispatched = [];
  globalThis.fetch = async (url, init) => {
    dispatched.push(new URL(url).searchParams.get('url'));
    if (inflight === 2) return Response.json({ error: { code: 'capacity' } }, { status: 429 });
    inflight++; peak = Math.max(peak, inflight);
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 40);
        init.signal.addEventListener('abort', () => { clearTimeout(timer); reject(init.signal.reason); }, { once: true });
      });
      return new Response(text, { headers: { 'x-spartan-content-trust': 'untrusted-web-content' } });
    } finally { inflight--; }
  };
  try { await run({ client: new ToolsClient('', ''), dispatched, peak: () => peak }); }
  finally { globalThis.fetch = original; }
}

test('six simultaneous production reader calls drain through two authoritative slots', async () => boundary(async f => {
  const results = await Promise.all(Array.from({ length: 6 }, (_, i) => f.client.reader(config, input(i))));
  assert.equal(results.length, 6);
  assert.ok(results.every(result => result[0].text === text));
  assert.equal(f.peak(), 2);
  assert.equal(f.dispatched.length, 6);
}));

test('queued abort and deadline prevent dispatch and admission recovers', async () => boundary(async f => {
  const first = [f.client.reader(config, input(0)), f.client.reader(config, input(1))];
  const abort = new AbortController();
  const cancelled = f.client.reader(config, input('cancel'), abort.signal);
  const expired = f.client.reader(config, input('expire'), AbortSignal.timeout(10));
  const denied = Promise.all([assert.rejects(cancelled), assert.rejects(expired)]);
  abort.abort();
  await denied;
  await Promise.all(first);
  assert.equal((await f.client.reader(config, input('recovery')))[0].text, text);
  assert.deepEqual(f.dispatched, [input(0).url, input(1).url, input('recovery').url]);
}));

test('absolute Reader deadline begins at enqueue, not at HTTP dispatch', async () => boundary(async f => {
  f.client.readerAdmission = new ReaderAdmission({ timeoutMs: 70 });
  const results = await Promise.allSettled(Array.from({ length: 6 }, (_, i) => f.client.reader(config, input(i))));
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 2);
  assert.ok(results.slice(2).every(result => result.reason.code === 'deadline'));
}));

test('session stop cancels queued calls before dispatch', async () => boundary(async f => {
  const ctx = { sessionManager: { getSessionId: () => 'fixture' } };
  f.client.config = () => config;
  f.client.start(ctx);
  const calls = Array.from({ length: 6 }, (_, i) => f.client.execute('reader', input(i), undefined, ctx));
  await new Promise(resolve => setTimeout(resolve, 5));
  f.client.stop();
  const results = await Promise.all(calls);
  assert.ok(results.every(result => result.isError));
  assert.equal(f.dispatched.length, 2);
}));

test('reader distinguishes local capacity, remote HTTP 429 and truncation without leaking bodies', async () => {
  const original = globalThis.fetch;
  const client = new ToolsClient('', '');
  try {
    for (const code of ['capacity', 'reader_overloaded', 'deadline', 'remote_http_error']) {
      globalThis.fetch = async () => Response.json({ error: { code, upstreamStatus: 429, message: 'PRIVATE' } }, { status: code === 'capacity' ? 429 : 502 });
      await assert.rejects(client.reader(config, input(0)), error => {
        assert.equal(error.code, code);
        assert.equal(error.message.includes('PRIVATE'), false);
        assert.equal(error.message.includes('upstream HTTP 429'), code === 'remote_http_error');
        return true;
      });
    }
    globalThis.fetch = async () => new Response(text, { headers: { 'x-spartan-content-trust': 'untrusted-web-content', 'x-spartan-truncated': 'true' } });
    const content = await client.reader(config, input(0));
    assert.equal(content[0].text, text);
    assert.match(content[1].text, /truncated/);
  } finally { globalThis.fetch = original; }
});

test('actual HTTP body consumption owns the slot and deadline abort recovers', async () => {
  let active = 0;
  let peak = 0;
  let mode = 'body';
  const server = createServer((_req, response) => {
    active++; peak = Math.max(peak, active);
    response.on('close', () => active--);
    response.writeHead(200, { 'x-spartan-content-trust': 'untrusted-web-content' });
    if (mode === 'stall') { response.write('BEGIN_UNTRUSTED_WEB_CONTENT\\n'); return; }
    response.write(text.slice(0, 20));
    setTimeout(() => response.end(text.slice(20)), 30);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const local = { ...config, baseUrl: `http://127.0.0.1:${server.address().port}` };
  const client = new ToolsClient('', '');
  try {
    const results = await Promise.all(Array.from({ length: 6 }, (_, i) => client.reader(local, input(i))));
    assert.ok(results.every(result => result[0].text === text));
    assert.equal(peak, 2, 'headers alone must not release admission');
    mode = 'stall';
    client.readerAdmission = new ReaderAdmission({ timeoutMs: 100 });
    await assert.rejects(client.reader(local, input('stall')), error => error.code === 'deadline');
    mode = 'body';
    assert.equal((await client.reader(local, input('recovery')))[0].text, text);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('finite pending overflow rejects without HTTP and reservations recover', async () => boundary(async f => {
  const calls = Array.from({ length: 19 }, (_, i) => f.client.reader(config, input(i)));
  const results = await Promise.allSettled(calls);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 18);
  assert.equal(results[18].reason.code, 'reader_queue_full');
  assert.equal(f.dispatched.length, 18);
  assert.equal((await f.client.reader(config, input('recovery')))[0].text, text);
}));
