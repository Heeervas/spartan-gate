const assert = require('assert');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const vm = require('vm');

const { patchMemoryPressure } = require('./patch-memory-pressure.js');

function fixture() {
  return `import { createSpartanDeepHealthController, createSpartanNavigationReadiness, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';

function admission() {
  return spartanCgroupAdmissionAllowed({
          threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.90),
  });
}

// Memory pressure eviction (Fly.io only) — evict oldest session when RAM is high.
// Prevents Camoufox OOM by proactively freeing BrowserContexts.
if (FLY_MACHINE_ID) {
  setInterval(() => {}, 30_000);
}

// Per-tab inactivity reaper — close tabs idle for TAB_INACTIVITY_MS
function tabReaper() {}
`;
}

test('pressure policy waits for two samples, evicts one non-leased LRU, and hysteresis resets', async () => {
  const patched = patchMemoryPressure(fixture());
  assert.strictEqual(patchMemoryPressure(patched), patched);
  assert.match(patched, /SOFT_RATIO \|\| 0\.75/);
  assert.match(patched, /SPARTAN_CAMOFOX_MEMORY_EVICT_RATIO \|\| 0\.82/);
  assert.match(patched, /SPARTAN_CAMOFOX_MEMORY_RESET_RATIO \|\| 0\.70/);
  assert.match(patched, /spartanPressureSamples < 2/);
  assert.match(patched, /reason: 'memory_pressure_soft'/);

  let ratio = 0.83;
  let interval;
  const closed = [];
  const leased = { lastAccess: 1, leased: true, tabGroups: new Map() };
  const pageLeased = { lastAccess: 0, pageLeased: true, tabGroups: new Map() };
  const oldest = { lastAccess: 2, tabGroups: new Map() };
  const newest = { lastAccess: 3, tabGroups: new Map() };
  const sessions = new Map([
    ['leased', leased],
    ['page-leased', pageLeased],
    ['oldest', oldest],
    ['newest', newest],
  ]);
  const context = {
    process: { env: {} },
    sessions,
    readSpartanCgroupMemory: () => ({ state: 'known', ratio }),
    spartanHasPendingUserCreate: () => false,
    hasActivePageLeases: session => session.pageLeased === true,
    spartanOperatorSessionLeaseActive: session => session.leased === true,
    closeSession: async key => {
      closed.push(key);
      sessions.delete(key);
    },
    log: () => {},
    setInterval: callback => { interval = callback; },
  };
  vm.runInNewContext(patched.replace(/^import .*$/m, ''), context);

  await interval();
  assert.deepStrictEqual(closed, []);
  await interval();
  assert.deepStrictEqual(closed, ['oldest']);
  await interval();
  assert.deepStrictEqual(closed, ['oldest'], 'latched pressure closes only one context');

  ratio = 0.69;
  await interval();
  sessions.set('after-reset', { lastAccess: 0, tabGroups: new Map() });
  ratio = 0.83;
  await interval();
  await interval();
  assert.deepStrictEqual(closed, ['oldest', 'after-reset']);
});

test('Camofox image applies pressure policy after persistence and lease guards', () => {
  const dockerfile = fs.readFileSync(path.join(__dirname, 'Dockerfile'), 'utf8');
  const persistence = dockerfile.indexOf('patch-persistence.js');
  const lease = dockerfile.indexOf('patch-operator-lease.js');
  const pressure = dockerfile.indexOf('patch-memory-pressure.js');
  assert.ok(persistence >= 0 && persistence < lease && lease < pressure);
});
