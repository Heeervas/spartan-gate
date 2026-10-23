import assert from 'node:assert/strict';
import test, { beforeEach, afterEach } from 'node:test';
// The parent agent may itself run offline. Each scenario owns this input.
let inheritedOffline;
beforeEach(() => { inheritedOffline = process.env.PI_OFFLINE; delete process.env.PI_OFFLINE; });
afterEach(() => {
  if (inheritedOffline === undefined) delete process.env.PI_OFFLINE;
  else process.env.PI_OFFLINE = inheritedOffline;
});
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { nativeBaseline, presentation, dualBar } from './extensions/spartan-route-usage/core.mjs';
import { UsageClient } from './extensions/spartan-route-usage/client.mjs';
const id = 'a7becb62-8311-466d-89ca-e8b215d0a700';
const fork = 'ac10e7cc-cdb6-4e29-9b15-7c8d87bdf5bd';
const origin = 'b7becb62-8311-466d-89ca-e8b215d0a700';
const usage = { input: 100, output: 20, cacheRead: 50, cacheWrite: 10, reasoning: 15, totalTokens: 180 };
const assistant = { type: 'message', message: { role: 'assistant', provider: 'spartan-route', model: 'codex/gpt-test', usage } };
function context(sessionId = id, entries = [assistant], hasUI = false) {
  const statuses = [];
  return { hasUI, model: { provider: 'spartan-route' }, sessionManager: { getSessionId: () => sessionId, getEntries: () => entries },
    ui: { setStatus: (_key, value) => statuses.push(value), setWidget() {}, notify() {} }, statuses };
}
function canonical(sessionId = id, baseline = nativeBaseline([assistant])) {
  return { version: 1, session: { sessionId, originId: origin, baseline, totals: baseline, coverage: {}, estimatedQuotaUnits: 0.03 } };
}
async function clientTest(run) {
  const directory = await mkdtemp(join(tmpdir(), 'pi-usage-'));
  try { await run(directory); } finally { await rm(directory, { recursive: true, force: true }); }
}

test('all native usage owners normalize inclusive input without double counting reasoning or trusting totalTokens', () => {
  const entries = [assistant, { type: 'compaction', usage }, { type: 'branch_summary', usage },
    { type: 'usage', provider: 'anthropic', model: 'claude', usage },
    { type: 'message', message: { role: 'toolResult', usage } },
    { type: 'model_change', provider: 'openai-codex', modelId: 'gpt-test' },
    { ...assistant, id: 'off-branch' }];
  assert.deepEqual(nativeBaseline(entries), { inputTokens: 960, cachedInputTokens: 300, outputTokens: 120,
    totalTokens: 1080, codexTokens: 360, unknownQuotaTokens: 540 });
});

test('two-row quota bar has bounded unknown/stale windows and session last total survives offline', () => {
  const now = Date.now();
  const windows = { fiveHour: { remainingPercent: 100, ageSeconds: 0 }, weekly: { remainingPercent: 50, ageSeconds: 200 } };
  assert.equal(dualBar(windows, now), '[████▀▀▀▀] 5h 100% wk 50%~');
  assert.equal(dualBar({ fiveHour: { remainingPercent: 99, ageSeconds: 3601 } }), '[········] 5h ? wk ?');
  const rendered = presentation({ ...canonical(), pool: windows }, 'pool', { totalTokens: 999 }, true, now);
  assert.match(rendered.status, /180 tok ~0\.03u offline/);
});

test('awaited CAS attaches headers only after commit; concurrent requests register once; headless has no timers', async () => clientTest(async (directory) => {
  const calls = [];
  let commit;
  let reached;
  const registering = new Promise((resolve) => { reached = resolve; });
  const client = new UsageClient(directory, async (action, body) => {
    calls.push(action);
    if (action === 'status') return { version: 1, session: null };
    assert.equal(body.baseline.totalTokens, 180);
    reached();
    return new Promise((resolve) => { commit = () => resolve(canonical()); });
  });
  const ctx = context();
  const started = client.start(ctx);
  await registering;
  const first = { 'X-Spartan-Pi-Client': '1', 'X-Spartan-Pi-Origin': 'stale' };
  const second = { 'x-spartan-pi-client': '1' };
  const pending = Promise.all([client.headers(first, ctx), client.headers(second, ctx)]);
  assert.equal(first['X-Spartan-Pi-Origin'], undefined);
  commit();
  await Promise.all([started, pending]);
  assert.deepEqual(calls, ['status', 'register']);
  assert.equal(first['X-Spartan-Pi-Session'], id);
  assert.equal(second['X-Spartan-Pi-Origin'], origin);
  assert.equal(client.timers.size, 0);
  client.stop();
}));

test('resume recovers immutable origin without reading baseline; forks register copied complete history', async () => clientTest(async (directory) => {
  const calls = [];
  const client = new UsageClient(directory, async (action, body) => {
    calls.push([action, body]);
    return body.sessionId === id ? canonical() : action === 'status' ? { version: 1, session: null } : canonical(fork, body.baseline);
  });
  const resumed = context();
  resumed.sessionManager.getEntries = () => { throw new Error('baseline must not be computed on resume'); };
  await client.start(resumed);
  assert.deepEqual(calls.map(([action]) => action), ['status']);
  await client.start(context(fork, [assistant, assistant]));
  assert.equal(calls[2][1].baseline.totalTokens, 360);
  client.stop();
}));

test('named subagent usage registers a stable UUID and attaches it across native session resume', async () => clientTest(async (directory) => {
  const named = 'subagent.0123456789abcdef';
  const calls = [];
  let saved;
  const request = async (action, body) => {
    calls.push([action, body]);
    assert.match(body.sessionId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    if (action === 'register') saved = canonical(body.sessionId, body.baseline);
    return saved ?? { version: 1, session: null };
  };
  const client = new UsageClient(directory, request);
  const ctx = context(named);
  await client.start(ctx);
  const headers = { 'X-Spartan-Pi-Client': '1' };
  await client.headers(headers, ctx);
  assert.deepEqual(calls.map(([action]) => action), ['status', 'register']);
  assert.equal(headers['X-Spartan-Pi-Session'], saved.session.sessionId);
  assert.equal(headers['X-Spartan-Pi-Origin'], origin);
  assert.equal(client.sessionId, '3bab243f-a0c3-8494-9c22-072bbf68d2f7');
  assert.equal(client.rawSessionId, named);
  await client.headers({ 'X-Spartan-Pi-Client': '1' }, ctx);
  await client.settled(ctx);
  assert.deepEqual(calls.map(([action]) => action), ['status', 'register', 'status']);
  client.stop();
  assert.equal(client.rawSessionId, undefined);
  assert.equal(client.sessionId, undefined);
  const resumed = new UsageClient(directory, request);
  await resumed.start(context(named));
  assert.equal(resumed.originId, origin);
  assert.equal(resumed.sessionId, saved.session.sessionId);
  assert.deepEqual(calls.map(([action]) => action), ['status', 'register', 'status', 'status']);
  resumed.stop();
}));

test('telemetry failure leaves inference headers uncorrelated; recovery persists gap; unrelated provider untouched', async () => clientTest(async (directory) => {
  let online = false;
  const bodies = [];
  const client = new UsageClient(directory, async (action, body) => {
    if (!online) throw new Error('private backend error');
    if (action === 'register') bodies.push(body);
    const result = canonical();
    result.session.coverage.trackingIncomplete = true;
    return result;
  });
  const ctx = context();
  await client.start(ctx);
  const unrelated = { 'X-Spartan-Pi-Session': 'foreign' };
  await client.headers(unrelated, ctx);
  assert.deepEqual(unrelated, { 'X-Spartan-Pi-Session': 'foreign' });
  const headers = { 'X-Spartan-Pi-Client': '1', 'x-spartan-pi-session': 'stale' };
  await client.headers(headers, ctx);
  assert.deepEqual(headers, { 'X-Spartan-Pi-Client': '1' });
  online = true;
  await client.headers(headers, ctx);
  assert.equal(bodies[0].trackingIncomplete, true);
  assert.equal(headers['X-Spartan-Pi-Origin'], origin);
  await client.headers(headers, ctx);
  assert.equal(bodies.length, 1);
  client.stop();
}));

test('late previous-session response cannot publish or attach headers after switch', async () => clientTest(async (directory) => {
  let resolveOld;
  const client = new UsageClient(directory, (action, body) => body.sessionId === id
    ? new Promise((resolve) => { resolveOld = resolve; }) : Promise.resolve(canonical(fork)));
  const oldStart = client.start(context());
  await new Promise(setImmediate);
  const oldHeaders = { 'X-Spartan-Pi-Client': '1' };
  const oldRequest = client.headers(oldHeaders, context());
  await client.start(context(fork));
  resolveOld(canonical());
  await Promise.all([oldStart, oldRequest]);
  assert.equal(client.sessionId, fork);
  assert.equal(client.snapshot.session.sessionId, fork);
  assert.equal(oldHeaders['X-Spartan-Pi-Origin'], undefined);
  client.stop();
}));

test('native Pi loader registers lifecycle and header hooks without intercepting other transports', async () => {
  const { createRequire } = await import('node:module');
  const { dirname, resolve } = await import('node:path');
  const { pathToFileURL, fileURLToPath } = await import('node:url');
  const require = createRequire(import.meta.url);
  const piDir = process.env.PI_CODING_AGENT_DIR || dirname(require.resolve('@earendil-works/pi-coding-agent/package.json'));
  const { loadExtensions } = await import(pathToFileURL(resolve(piDir, 'dist/core/extensions/loader.js')).href);
  const path = fileURLToPath(new URL('./extensions/spartan-route-usage.ts', import.meta.url));
  const { extensions, errors } = await loadExtensions([path], dirname(path));
  assert.deepEqual(errors, []);
  assert.equal(extensions.length, 1);
  const extension = extensions[0];
  assert.ok(extension.commands.has('spartan-usage'));
  assert.ok(extension.handlers.has('agent_end'));
  const headers = { Authorization: 'synthetic-other-provider' };
  await extension.handlers.get('before_provider_headers')[0]({ headers }, context());
  assert.deepEqual(headers, { Authorization: 'synthetic-other-provider' });
  extension.handlers.get('session_shutdown')[0]();
});

test('interactive timers clean up on shutdown and transient outage schedules one retry', async () => clientTest(async (directory) => {
  const client = new UsageClient(directory, async () => canonical());
  await client.start(context(id, [assistant], true));
  assert.equal(client.timers.size, 2);
  client.stop();
  assert.equal(client.timers.size, 0);
  client.request = async () => { throw new Error('offline'); };
  await client.start(context(id, [assistant], true));
  assert.equal(client.timers.size, 1);
  assert.equal(client.scheduleMode, 'retry');
  assert.match(client.ctx.statuses.at(-1), /offline.*native fallback/);
  client.stop();
}));

test('backend-shaped pool snapshot displays quota and reset countdowns within compact footer width', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  const window = { capacityUnits: 200, usedUnits: 50, remainingUnits: 150,
    usedPercent: 25, remainingPercent: 75, accounts: 2, unknownAccounts: 0,
    ageSeconds: 5, updatedAt: '2025-12-31T23:59:55Z', resetAt: '2026-01-01T02:05:00Z', stale: false, capacityIncomplete: false };
  const snapshot = { ...canonical(), receivedAt: now, pool: { fiveHour: window, weekly: { ...window, resetAt: '2026-01-04T00:00:00Z' } } };
  const display = presentation(snapshot, 'pool', {}, false, now);
  assert.match(display.status, /\[██████··\] 5h:2h5m wk:3d0h/);
  assert.ok(display.status.length <= 80);
  assert.match(display.details, /5h 75% wk 75%/);
  assert.match(presentation(snapshot, 'pool', {}, true, now + 3601000).details, /5h \? wk \?/);
});

test('Pi offline startup and unrelated selections make zero network calls; explicit marked request registers lazily', async () => clientTest(async (directory) => {
  const previous = process.env.PI_OFFLINE;
  process.env.PI_OFFLINE = 'yes';
  const calls = [];
  const client = new UsageClient(directory, async (action) => { calls.push(action); return canonical(); });
  try {
    const ctx = context(id, [assistant], true);
    await client.start(ctx);
    assert.deepEqual(calls, []);
    assert.equal(client.timers.size, 0);
    const headers = { 'X-Spartan-Pi-Client': '1' };
    await client.headers(headers, ctx);
    assert.equal(headers['X-Spartan-Pi-Origin'], origin);
    assert.deepEqual(calls, ['status']);
    assert.equal(client.timers.size, 0);
    client.stop();
  } finally {
    if (previous === undefined) delete process.env.PI_OFFLINE; else process.env.PI_OFFLINE = previous;
  }
  const other = new UsageClient(directory, async (action) => { calls.push(action); return canonical(); });
  const ctx = context(id, [], true);
  ctx.model.provider = 'unrelated';
  calls.length = 0;
  await other.start(ctx);
  await other.settled(ctx);
  assert.deepEqual(calls, []);
  assert.equal(other.timers.size, 0);
  assert.equal(ctx.statuses.at(-1), undefined);
  other.stop();
}));

test('refresh accepts generic POST acknowledgment and then reads correlated canonical session', async () => clientTest(async (directory) => {
  const calls = [];
  const client = new UsageClient(directory, async (action) => {
    calls.push(action);
    return action === 'refresh' ? { version: 1, session: null } : canonical();
  });
  await client.start(context());
  calls.length = 0;
  await client.update(true);
  assert.deepEqual(calls, ['refresh', 'status']);
  assert.equal(client.offline, false);
  assert.equal(client.snapshot.session.originId, origin);
  client.stop();
}));

test('tracking gap survives client recreation and clears only after server acknowledgment', async () => clientTest(async (directory) => {
  const failed = new UsageClient(directory, async () => { throw new Error('offline'); });
  const ctx = context();
  await failed.start(ctx);
  await failed.headers({ 'X-Spartan-Pi-Client': '1' }, ctx);
  failed.stop();
  const registrations = [];
  const recovered = new UsageClient(directory, async (action, body) => {
    if (action === 'register') registrations.push(body);
    const result = canonical();
    result.session.coverage.trackingIncomplete = action === 'register';
    return result;
  });
  await recovered.start(ctx);
  assert.equal(registrations.length, 1);
  assert.equal(registrations[0].trackingIncomplete, true);
  const { readdir } = await import('node:fs/promises');
  assert.deepEqual(await readdir(join(directory, 'spartan-route-usage-gaps')), []);
  recovered.stop();
}));

test('pending poll cannot rearm after selecting another provider', async (t) => clientTest(async (directory) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let finish;
  let started;
  const entered = new Promise((resolve) => { started = resolve; });
  let polling = false;
  const client = new UsageClient(directory, async () => {
    if (!polling) return canonical();
    started();
    return new Promise((resolve) => { finish = resolve; });
  });
  const ctx = context(id, [assistant], true);
  await client.start(ctx);
  polling = true;
  t.mock.timers.tick(30000);
  await entered;
  const other = context(id, [], true);
  other.model.provider = 'other';
  await client.selected(other);
  const pending = client.polling;
  finish(canonical());
  await pending;
  await new Promise(setImmediate);
  assert.equal(client.timers.size, 0);
  assert.equal(other.statuses.at(-1), undefined);
  client.stop();
}));

test('transient outage retries with capped backoff then restores exactly two online timers', async (t) => clientTest(async (directory) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let online = false;
  const client = new UsageClient(directory, async () => {
    if (!online) throw new Error('temporary');
    return canonical();
  });
  await client.start(context(id, [assistant], true));
  for (const delay of [60000, 120000, 240000]) {
    assert.equal(client.timers.size, 1);
    t.mock.timers.tick(delay);
    // Retry uses native filesystem and Promise continuations before scheduling its successor.
    await new Promise(setImmediate);
    await new Promise(setImmediate);
  }
  assert.equal(client.retryDelay, 300000);
  online = true;
  t.mock.timers.tick(300000);
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(client.offline, false);
  assert.equal(client.timers.size, 2);
  assert.equal(client.scheduleMode, 'online');
  client.stop();
}));

test('expired resets, stale compact bar, single window and global lease state remain explicit', () => {
  const now = Date.now();
  const known = { ageSeconds: 10, remainingPercent: 67, resetAt: new Date(now + 3600000).toISOString() };
  const expired = { ...known, resetAt: new Date(now - 1).toISOString() };
  assert.equal(dualBar({ fiveHour: expired, weekly: expired }, now), '[········] 5h ? wk ?');
  const snapshot = { ...canonical(), receivedAt: now, pool: { fiveHour: null, weekly: known } };
  assert.match(presentation(snapshot, 'pool', {}, false, now).status, /wk 67%/);
  snapshot.pool = { fiveHour: { ...known, ageSeconds: 121 }, weekly: known };
  assert.match(presentation(snapshot, 'pool', {}, false, now).status, /\]~/);
  snapshot.accounts = [{ id: 'account-1', slotIndexes: [1], state: 'rate_limited', fiveHour: known, weekly: known }];
  snapshot.lease = { slotIndex: 1, state: 'active', scope: 'global' };
  assert.match(presentation(snapshot, 'lease', {}, false, now).status, /lease global active/);
  assert.match(presentation(snapshot, 'accounts', {}, false, now).rows[0], /rate_limited/);
});

function partialSnapshot(now) {
  const observed = (remainingPercent) => ({ remainingPercent, ageSeconds: 10,
    updatedAt: new Date(now - 10000).toISOString(), resetAt: new Date(now + 3600000).toISOString() });
  return { ...canonical(), receivedAt: now,
    pool: { fiveHour: { ...observed(null), capacityUnits: 700, remainingUnits: 87, accounts: 2, unknownAccounts: 2, capacityIncomplete: true },
      weekly: { ...observed(null), capacityUnits: 700, remainingUnits: 522, accounts: 3, unknownAccounts: 1, capacityIncomplete: true } },
    accounts: [
      { id: 'account-a', capacityMultiplier: 1, fiveHour: observed(50), weekly: observed(75) },
      { id: 'account-b', capacityMultiplier: 5, fiveHour: null, weekly: observed(75) },
      { id: 'account-c', capacityMultiplier: 1, fiveHour: observed(37), weekly: observed(72) },
    ], unresolvedSlots: [{ slotIndex: 9, state: 'identity_unknown' }] };
}

test('partial pool shows capacity-weighted observed subset and explicitly incomplete full-pool coverage', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  const snapshot = partialSnapshot(now);
  const before = structuredClone(snapshot);
  const display = presentation(snapshot, 'pool', {}, false, now);
  assert.match(display.status, /partial/);
  assert.doesNotMatch(display.status, /quota n\/a/);
  assert.match(display.details, /5h 44% wk 75%/);
  assert.match(display.details, /5h: 2 known \/ 2 unknown sources; known capacity 2x/);
  assert.match(display.details, /weekly: 3 known \/ 1 unknown sources; known capacity 7x/);
  assert.match(display.details, /Full-pool percentage unavailable/);
  assert.match(display.details, /capacity incomplete/);
  assert.deepEqual(snapshot, before);
});

test('partial eligibility applies receipt aging, expiry, timestamp validation, capacity validity and account deduplication', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  const snapshot = partialSnapshot(now);
  snapshot.accounts.push(structuredClone(snapshot.accounts[0]));
  const baseline = presentation(snapshot, 'pool', {}, false, now);
  assert.match(baseline.details, /5h: 2 known \/ 2 unknown sources; known capacity 2x/);
  assert.match(presentation(snapshot, 'pool', {}, false, now + 121000).status, /\]~.*partial/);
  assert.match(presentation(snapshot, 'pool', {}, false, now + 3601000).status, /quota n\/a/);
  for (const capacity of [NaN, Infinity, -1, 0, '5']) {
    const invalid = partialSnapshot(now);
    invalid.accounts[0].capacityMultiplier = capacity;
    invalid.accounts[2].fiveHour.resetAt = new Date(now - 1).toISOString();
    assert.match(presentation(invalid, 'pool', {}, false, now).details, /5h: 0 known \/ 4 unknown sources; known capacity 0x/);
  }
  const timestampAge = partialSnapshot(now);
  for (const account of timestampAge.accounts) {
    if (account.fiveHour) { delete account.fiveHour.ageSeconds; account.fiveHour.updatedAt = new Date(now - 121000).toISOString(); }
  }
  assert.match(presentation(timestampAge, 'pool', {}, false, now).status, /\]~.*partial/);
  const malformed = partialSnapshot(now);
  malformed.accounts[0].fiveHour.updatedAt = 'invalid';
  malformed.accounts[2].fiveHour.resetAt = null;
  assert.match(presentation(malformed, 'pool', {}, false, now).details, /5h: 0 known/);
});

test('authoritative pool percentages stay unchanged and entirely missing coverage explains quota n/a', () => {
  const now = Date.parse('2026-01-01T00:00:00Z');
  const snapshot = partialSnapshot(now);
  snapshot.pool.fiveHour.remainingPercent = 60;
  snapshot.pool.weekly.remainingPercent = 80;
  const display = presentation(snapshot, 'pool', {}, false, now);
  assert.doesNotMatch(display.status, /partial/);
  assert.match(display.details, /5h 60% wk 80%/);
  snapshot.pool.fiveHour.remainingPercent = null;
  snapshot.pool.weekly.remainingPercent = null;
  for (const account of snapshot.accounts) { account.fiveHour = null; account.weekly = null; }
  const missing = presentation(snapshot, 'pool', {}, false, now);
  assert.match(missing.status, /quota n\/a/);
  assert.doesNotMatch(missing.status, /partial/);
  assert.match(missing.details, /5h: 0 known \/ 4 unknown sources/);
  assert.match(missing.details, /Full-pool percentage unavailable/);
});

test('quota estimate formatting preserves tiny positive values and rejects invalid estimates', () => {
  const cases = [[0, '0.00u'], [0.004, '~0.004u'], [0.001, '~0.001u'], [0.0002, '<0.001u'], [0.03, '~0.03u'], [-0.1, '~?u'], [NaN, '~?u'], [Infinity, '~?u']];
  for (const [estimate, expected] of cases) {
    const snapshot = canonical();
    snapshot.session.estimatedQuotaUnits = estimate;
    const status = presentation(snapshot, 'pool', {}, false).status;
    assert.ok(status.includes(expected), `${estimate}: ${status}`);
    if (estimate > 0 && estimate < 0.01) assert.doesNotMatch(status, /~0\.00u/);
  }
});
