import crypto from 'node:crypto';

const base = process.env.CAMOFOX_ACCEPTANCE_BASE || 'http://127.0.0.1:9377';
const key = process.env.CAMOFOX_ACCEPTANCE_KEY || '';
const targetUrl = process.env.CAMOFOX_ACCEPTANCE_URL || 'https://example.com/';
const sequentialCycles = Number(process.env.CAMOFOX_ACCEPTANCE_CYCLES || 20);
const concurrentIdentities = Number(process.env.CAMOFOX_ACCEPTANCE_CONCURRENCY || 5);
const requestTimeoutMs = Number(process.env.CAMOFOX_ACCEPTANCE_TIMEOUT_MS || 35000);

if (!Number.isInteger(sequentialCycles) || sequentialCycles < 1 || sequentialCycles > 100) {
  throw new Error('invalid acceptance cycle count');
}
if (!Number.isInteger(concurrentIdentities) || concurrentIdentities < 5 || concurrentIdentities > 20) {
  throw new Error('acceptance concurrency must be between 5 and 20');
}

async function request(path, options = {}, authenticated = true) {
  const headers = { ...(options.headers || {}) };
  if (authenticated) headers.Authorization = `Bearer ${key}`;
  if (options.body) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      ...options,
      headers,
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
  } catch (error) {
    const wrapped = new Error('Camofox acceptance request timed out or disconnected');
    wrapped.cause = error;
    throw wrapped;
  }
  if (!response.ok) throw new Error(`Camofox acceptance HTTP ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function shallowHealth() {
  return request('/health', {}, false);
}

const openTabs = new Map();
const runId = crypto.randomUUID();
const identities = Array.from({ length: concurrentIdentities }, (_, index) => ({
  userId: `spartan-accept-${runId}-${index}`,
  sessionKey: `accept-${runId}-${index}`,
}));

async function closeTab(tabId, userId) {
  await request(`/tabs/${encodeURIComponent(tabId)}`, {
    method: 'DELETE',
    body: JSON.stringify({ userId }),
  });
  openTabs.delete(tabId);
}

async function lifecycle({ userId, sessionKey }) {
  let tabId;
  try {
    const created = await request('/tabs', {
      method: 'POST',
      body: JSON.stringify({ userId, sessionKey, url: targetUrl }),
    });
    tabId = created?.tabId;
    if (typeof tabId !== 'string' || !tabId) throw new Error('Camofox create omitted tabId');
    openTabs.set(tabId, userId);
    await request(`/tabs/${encodeURIComponent(tabId)}/snapshot?userId=${encodeURIComponent(userId)}`);
    const evaluated = await request(`/tabs/${encodeURIComponent(tabId)}/evaluate`, {
      method: 'POST',
      body: JSON.stringify({ userId, expression: 'document.title === "Example Domain"' }),
    });
    const evaluationResult = evaluated && typeof evaluated === 'object'
      ? (evaluated.result ?? evaluated.value ?? evaluated.data)
      : evaluated;
    if (evaluationResult !== true) throw new Error('Camofox evaluate returned false');
  } finally {
    if (tabId && openTabs.has(tabId)) await closeTab(tabId, userId);
  }
}

async function waitForBaseline(expectedTabs) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const health = await shallowHealth();
    if (health?.activeTabs === expectedTabs) return health;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('Camofox activeTabs did not return to baseline');
}

async function cleanup() {
  const pending = [...openTabs.entries()].map(([tabId, userId]) => closeTab(tabId, userId));
  const results = await Promise.allSettled(pending);
  if (results.some(result => result.status === 'rejected')) {
    throw new Error('Camofox acceptance cleanup failed');
  }
}

const baseline = await shallowHealth();
const baselineTabs = Number(baseline?.activeTabs);
if (!Number.isInteger(baselineTabs) || baselineTabs < 0) throw new Error('invalid Camofox baseline');

try {
  for (let index = 0; index < sequentialCycles; index++) {
    await lifecycle(identities[index % identities.length]);
  }
  await Promise.all(
    identities.map(identity => lifecycle(identity)),
  );
  await waitForBaseline(baselineTabs);
  process.stdout.write(JSON.stringify({
    ok: true,
    sequentialCycles,
    concurrentIdentities,
    baselineTabs,
    finalTabs: baselineTabs,
    failures: 0,
  }) + '\n');
} catch (error) {
  await cleanup().catch(() => {});
  process.stderr.write(`${error?.message || 'Camofox acceptance failed'}\n`);
  process.exitCode = 1;
}
