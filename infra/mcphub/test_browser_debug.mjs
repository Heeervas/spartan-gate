import assert from 'node:assert/strict';
import vm from 'node:vm';
import test from 'node:test';
import { debugScript, waitScript } from './browser-debug.mjs';

function page() {
  const timers = new Map(); const listeners = new Map(); let clock = 0; let timerId = 0;
  const console = Object.fromEntries(['log', 'info', 'warn', 'error', 'debug'].map(key => [key, () => {}]));
  const calls = [];
  class XHR extends EventTarget {
    open(method, target) { calls.push([method, target]); }
    send() { this.status = 403; this.dispatchEvent(new Event('loadend')); return 'sent'; }
  }
  const originalFetch = () => Promise.resolve({ status: 201, body: 'not read' });
  const context = vm.createContext({ console, URL, TextEncoder, Request, XMLHttpRequest: XHR,
    location: { href: 'https://app.example/form' }, document: { querySelector: selector => selector === '#ready' ? {} : null },
    fetch: originalFetch, Date: class extends Date { static now() { return clock; } },
    setTimeout: (callback, ms) => { const id = ++timerId; timers.set(id, { callback, at: clock + ms }); return id; },
    clearTimeout: id => timers.delete(id),
    addEventListener: (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener: (name, callback) => listeners.get(name)?.delete(callback),
  });
  const run = (action, args = {}) => vm.runInContext(debugScript(action, { capture_id: 'capture', ttl_ms: 1000, cursor: 0, limit: 20, ...args }), context);
  const advance = ms => { clock += ms; for (const [id, timer] of [...timers]) if (timer.at <= clock) { timers.delete(id); timer.callback(); } };
  return { context, console, calls, listeners, run, advance, originalFetch, timers };
}
const plain = value => JSON.parse(JSON.stringify(value));

test('actual injected producer observes future console/fetch/XHR, preserves return values and omits bodies/query/auth', async () => {
  const p = page(); assert.equal(p.run('start').ok, true);
  let getterCalls = 0;
  p.console.log('hello', { get secret() { getterCalls++; return 'LEAK'; } });
  const promise = p.context.fetch('https://user:password@store.example/upload?token=SECRET#secret', { method: 'PUT', body: 'PRIVATE_PDF' });
  assert.equal((await promise).status, 201);
  await new Promise(resolve => setImmediate(resolve)); // Flush the VM's cross-realm promise observer.
  const xhr = new p.context.XMLHttpRequest(); xhr.open('POST', '/cv?key=TOKEN'); assert.equal(xhr.send('SECRET_BODY'), 'sent');
  const result = plain(p.run('read'));
  assert.deepEqual(result.events.map(row => row.kind), ['console', 'fetch_start', 'fetch_end', 'xhr_start', 'xhr_end']);
  assert.equal(result.events[0].text, 'hello [object omitted]'); assert.equal(getterCalls, 0);
  assert.equal(result.events[2].status, 201); assert.equal(result.events[4].status, 403);
  assert.equal(result.events[1].url, 'https://store.example/upload');
  for (const secret of ['PRIVATE_PDF', 'SECRET_BODY', 'password', 'token=', 'TOKEN', 'LEAK']) assert.ok(!JSON.stringify(result).includes(secret));
  const next = p.run('read', { cursor: result.next_cursor }); assert.equal(next.events.length, 0);
  p.run('stop'); assert.equal(p.context.fetch, p.originalFetch); assert.equal(p.timers.size, 0);
});

test('explicit stop, expiry, foreign replacements and navigation loss have honest lifecycle results', () => {
  const p = page(); const original = p.console.log;
  p.run('start'); assert.equal(p.run('start', { capture_id: 'other' }).code, 'capture_busy');
  assert.equal(p.run('stop', { capture_id: 'wrong' }).code, 'capture_missing');
  p.console.log('before'); const foreign = () => {}; p.console.log = foreign;
  p.advance(1000);
  assert.equal(p.console.log, foreign); assert.notEqual(foreign, original);
  assert.equal(p.context.fetch, p.originalFetch); assert.equal(p.run('read').active, false); assert.equal(p.run('read').events.length, 0);
  p.run('stop'); assert.equal(p.run('read').code, 'capture_missing');
  assert.equal(page().run('read').code, 'capture_missing');
});

test('capture is bounded and does not inspect object reasons; native console errors still execute', () => {
  const p = page(); p.run('start');
  for (let i = 0; i < 1000; i++) p.console.error('x'.repeat(2000));
  for (const callback of p.listeners.get('unhandledrejection')) callback({ reason: { secret: 'not serialized' } });
  const result = p.run('read'); assert.equal(result.events.length, 20); assert.ok(result.dropped > 0);
  const all = p.context.__spartan_mcp_debug_v1__;
  assert.ok(all.events.length <= 200); assert.ok(all.bytes <= 65536);
  p.run('stop'); assert.equal([...p.listeners.values()].reduce((sum, set) => sum + set.size, 0), 0);
});

test('selector wait observes selector, distinguishes absence, and respects bounded polling', async () => {
  const p = page();
  assert.deepEqual(plain(await vm.runInContext(waitScript('#ready', 5000), p.context)), { found: true });
  assert.deepEqual(plain(await vm.runInContext(waitScript('#missing', 0), p.context)), { found: false });
  const pending = vm.runInContext(waitScript('#missing', 100), p.context);
  p.advance(50); p.advance(50); assert.deepEqual(plain(await pending), { found: false });
});

test('a non-writable hook refuses capture and restores already installed hooks', () => {
  const p = page(); const log = p.console.log;
  Object.defineProperty(p.console, 'warn', { writable: false });
  assert.equal(p.run('start').code, 'capture_unsupported');
  assert.equal(p.console.log, log); assert.equal(p.context.fetch, p.originalFetch);
  assert.equal(p.run('read').code, 'capture_missing');
});

test('fetch observer count is bounded while pending requests do not settle', () => {
  const p = page(); let originals = 0;
  p.context.fetch = () => { originals++; return new Promise(() => {}); };
  p.run('start'); for (let i = 0; i < 500; i++) p.context.fetch('/pending');
  assert.equal(originals, 500); assert.ok(p.run('read').dropped >= 400);
  p.run('stop');
});
