import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { beforeEach, afterEach } from 'node:test';
// Production deadline timers intentionally unref. A bounded per-test handle
// models the live server without changing production liveness semantics.
let keepalive;
beforeEach(() => { keepalive = setTimeout(() => {}, 10_000); });
afterEach(() => { clearTimeout(keepalive); });

import {
  createSpartanDeepHealthController,
  createSpartanNavigationReadiness,
  spartanCgroupAdmissionAllowed,
  spartanInternalRequestAuthorized,
} from './spartan-health.mjs';

function fakePage(overrides = {}) {
  return {
    goto: async () => {},
    evaluate: async () => 'ready',
    close: async () => {},
    ...overrides,
  };
}

test('navigation readiness latches only terminal qualifying failures for one generation', () => {
  let generation = 7;
  let clock = 100;
  const degraded = [];
  const readiness = createSpartanNavigationReadiness({
    getGeneration: () => generation,
    markDegraded: code => degraded.push(code),
    now: () => clock++,
  });

  readiness.begin().failure({ qualifying: true });
  readiness.begin().failure({ qualifying: true });
  readiness.begin().success();
  assert.equal(readiness.snapshot().consecutiveFailures, 0);

  const terminal = readiness.begin();
  assert.equal(terminal.failure({ qualifying: true }).consecutiveFailures, 1);
  assert.equal(terminal.success().reason, 'already_settled');
  readiness.begin().failure({ qualifying: false });
  readiness.begin({ participates: false }).failure({ qualifying: true });
  assert.equal(readiness.snapshot().consecutiveFailures, 1);

  readiness.begin().failure({ qualifying: true });
  const latched = readiness.begin().failure({ qualifying: true });
  assert.equal(latched.latched, true);
  assert.deepEqual(degraded, ['browser_navigation_degraded']);
  readiness.begin().success();
  readiness.begin().failure({ qualifying: true });
  assert.equal(readiness.snapshot().latched, true);
  assert.equal(readiness.snapshot().consecutiveFailures, 3);
  assert.deepEqual(degraded, ['browser_navigation_degraded']);

  generation++;
  assert.deepEqual(readiness.snapshot(), {
    generation: 8,
    consecutiveFailures: 0,
    latched: false,
    latchedAt: null,
    lastSuccessAt: null,
  });
});

test('late navigation completion from an old generation cannot mutate the replacement', () => {
  let generation = 1;
  const readiness = createSpartanNavigationReadiness({
    getGeneration: () => generation,
    markDegraded: () => assert.fail('stale result must not degrade replacement'),
  });
  const oldAttempt = readiness.begin();
  generation = 2;
  assert.equal(oldAttempt.failure({ qualifying: true }).reason, 'stale_generation');
  assert.equal(readiness.snapshot().consecutiveFailures, 0);
});

test('deep probe completes the raw lifecycle and cleans up', async () => {
  let pageClosed = 0;
  let contextClosed = 0;
  const page = fakePage({ close: async () => { pageClosed++; } });
  const context = {
    newPage: async () => page,
    close: async () => { contextClosed++; },
  };
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({ newContext: async () => context }),
    recoverBrowser: async () => {},
    probeTimeoutMs: 50,
    cleanupTimeoutMs: 20,
  });

  const result = await controller.probe();
  assert.equal(result.ok, true);
  assert.equal(result.code, 'ready');
  assert.equal(result.stage, 'complete');
  assert.ok(result.durationMs >= 0);
  assert.equal(pageClosed, 1);
  assert.equal(contextClosed, 1);
});

test('newPage timeout reports the stage but keeps the lease until late cleanup settles', async () => {
  let resolvePage;
  let pageClosed = 0;
  let contextClosed = 0;
  const closeOrder = [];
  let exclusiveActive = false;
  let resolveExclusiveDone;
  const exclusiveDone = new Promise(resolve => { resolveExclusiveDone = resolve; });
  const latePage = fakePage({
    close: async () => {
      pageClosed++;
      closeOrder.push('page');
    },
  });
  const context = {
    newPage: () => new Promise(resolve => { resolvePage = resolve; }),
    close: async () => {
      contextClosed++;
      closeOrder.push('context');
    },
  };
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({ newContext: async () => context }),
    recoverBrowser: async () => {},
    probeTimeoutMs: 10,
    cleanupTimeoutMs: 20,
    runExclusive: async operation => {
      exclusiveActive = true;
      try {
        return await operation();
      } finally {
        exclusiveActive = false;
        resolveExclusiveDone();
      }
    },
  });

  const result = await controller.probe();
  assert.equal(result.ok, false);
  assert.equal(result.code, 'deep_probe_timeout');
  assert.equal(result.stage, 'new_page');
  assert.equal(exclusiveActive, true);
  assert.equal(pageClosed, 0);
  assert.equal(contextClosed, 0);
  resolvePage(latePage);
  await exclusiveDone;
  assert.equal(exclusiveActive, false);
  assert.equal(pageClosed, 1);
  assert.equal(contextClosed, 1);
  assert.deepEqual(closeOrder, ['page', 'context']);
});

test('concurrent probes and recoveries are single flight', async () => {
  let contextCalls = 0;
  let recoveryCalls = 0;
  const page = fakePage();
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({
      newContext: async () => {
        contextCalls++;
        await new Promise(resolve => setTimeout(resolve, 5));
        return { newPage: async () => page, close: async () => {} };
      },
    }),
    recoverBrowser: async () => { recoveryCalls++; },
    probeTimeoutMs: 50,
    cleanupTimeoutMs: 10,
  });

  await Promise.all([controller.probe(), controller.probe(), controller.probe()]);
  await Promise.all([controller.recover(), controller.recover()]);
  assert.equal(contextCalls, 2);
  assert.equal(recoveryCalls, 1);
});

test('sentinel mismatch is unhealthy and resources close', async () => {
  let closed = 0;
  const page = fakePage({ evaluate: async () => 'wrong', close: async () => { closed++; } });
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({
      newContext: async () => ({ newPage: async () => page, close: async () => { closed++; } }),
    }),
    recoverBrowser: async () => {},
  });
  const result = await controller.probe();
  assert.equal(result.code, 'deep_probe_sentinel');
  assert.equal(closed, 2);
});

test('cgroup admission uses the container limit and fails closed', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-cgroup-'));
  try {
    fs.writeFileSync(path.join(root, 'memory.current'), '950\n');
    fs.writeFileSync(path.join(root, 'memory.max'), '1000\n');
    assert.deepEqual(spartanCgroupAdmissionAllowed({ root, threshold: 0.9 }), {
      allowed: false, code: 'memory_pressure', state: 'known', ratio: 0.95, current: 950, max: 1000,
    });
    fs.writeFileSync(path.join(root, 'memory.max'), 'max\n');
    assert.equal(spartanCgroupAdmissionAllowed({ root }).allowed, false);
    fs.writeFileSync(path.join(root, 'memory.max'), 'invalid\n');
    assert.equal(spartanCgroupAdmissionAllowed({ root }).code, 'memory_invalid');
    for (const threshold of [Number.NaN, Number.POSITIVE_INFINITY, 0, 1, 2]) {
      assert.deepEqual(spartanCgroupAdmissionAllowed({ root, threshold }), {
        allowed: false, code: 'memory_threshold_invalid', state: 'unknown',
      });
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('internal health authorization requires loopback and the private secret', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-secret-'));
  const secretPath = path.join(root, 'internal.key');
  try {
    const secret = 'a'.repeat(64);
    fs.writeFileSync(secretPath, `${secret}\n`, { mode: 0o600 });
    const request = (address, supplied) => ({
      socket: { remoteAddress: address },
      get: name => name === 'X-Spartan-Camofox-Internal' ? supplied : undefined,
    });
    assert.equal(spartanInternalRequestAuthorized(request('127.0.0.1', secret), secretPath), true);
    assert.equal(spartanInternalRequestAuthorized(request('::1', 'b'.repeat(64)), secretPath), false);
    assert.equal(spartanInternalRequestAuthorized(request('172.28.0.5', secret), secretPath), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('server recovery has a hard deadline', async () => {
  let recoveryCalls = 0;
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({
      newContext: async () => ({ newPage: async () => fakePage(), close: async () => {} }),
    }),
    recoverBrowser: () => {
      recoveryCalls++;
      return new Promise(() => {});
    },
    probeTimeoutMs: 20,
    cleanupTimeoutMs: 5,
    recoveryTimeoutMs: 10,
  });
  const started = Date.now();
  const [first, second] = await Promise.all([controller.recover(), controller.recover()]);
  assert.equal(first.ok, false);
  assert.equal(first.code, 'deep_probe_timeout');
  assert.equal(second.code, first.code);
  const afterDeadline = await controller.recover();
  assert.equal(afterDeadline.code, first.code);
  assert.equal(recoveryCalls, 1);
  assert.ok(Date.now() - started < 100);
});

test('deep probe timeout is global rather than renewed per stage', async () => {
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => {
      await delay(6);
      return {
        newContext: async () => {
          await delay(7);
          return {
            newPage: async () => fakePage(),
            close: async () => {},
          };
        },
      };
    },
    recoverBrowser: async () => {},
    probeTimeoutMs: 10,
    cleanupTimeoutMs: 5,
  });
  const result = await controller.probe();
  assert.equal(result.ok, false);
  assert.equal(result.stage, 'new_context');
});

test('hung cleanup returns bounded unhealthy while retaining exclusivity', async () => {
  let releaseClose;
  const closeGate = new Promise(resolve => { releaseClose = resolve; });
  let exclusiveActive = false;
  let resolveExclusiveDone;
  const exclusiveDone = new Promise(resolve => { resolveExclusiveDone = resolve; });
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({
      newContext: async () => ({
        newPage: async () => fakePage({ close: async () => { await closeGate; } }),
        close: async () => {},
      }),
    }),
    recoverBrowser: async () => {},
    probeTimeoutMs: 10,
    cleanupTimeoutMs: 10,
    runExclusive: async operation => {
      exclusiveActive = true;
      try {
        return await operation();
      } finally {
        exclusiveActive = false;
        resolveExclusiveDone();
      }
    },
  });

  const started = Date.now();
  const result = await controller.probe();
  assert.equal(result.ok, false);
  assert.equal(result.code, 'deep_probe_timeout');
  assert.equal(result.stage, 'lifecycle_settle');
  assert.equal(exclusiveActive, true);
  assert.ok(Date.now() - started < 100);
  releaseClose();
  await exclusiveDone;
  assert.equal(exclusiveActive, false);
});

test('cleanup failure is unhealthy even after a successful sentinel', async () => {
  let contextClosed = 0;
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({
      newContext: async () => ({
        newPage: async () => fakePage({ close: async () => { throw new Error('close failed'); } }),
        close: async () => { contextClosed++; },
      }),
    }),
    recoverBrowser: async () => {},
  });
  const result = await controller.probe();
  assert.equal(result.ok, false);
  assert.equal(result.code, 'deep_probe_cleanup_failed');
  assert.equal(result.stage, 'cleanup');
  assert.equal(contextClosed, 1);
});

test('bounded lifecycle busy is healthy only after a recent completed ready probe', async () => {
  let browserCalls = 0;
  let clock = 100;
  let busy = false;
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => {
      browserCalls++;
      return {
        newContext: async () => ({
          newPage: async () => fakePage(),
          close: async () => {},
        }),
      };
    },
    recoverBrowser: async () => {},
    busyMaxAgeMs: 20,
    now: () => clock,
    runExclusive: async operation => {
      if (!busy) return await operation();
      const error = new Error('busy');
      error.code = 'browser_lifecycle_busy';
      throw error;
    },
  });

  assert.equal((await controller.probe()).code, 'ready');
  await new Promise(resolve => setImmediate(resolve));
  busy = true;
  clock = 110;
  const recent = await controller.probe();
  assert.equal(recent.ok, true);
  assert.equal(recent.code, 'busy');
  assert.equal(recent.readyAgeMs, 10);
  assert.equal(browserCalls, 1);

  clock = 121;
  assert.equal(controller.snapshot().code, 'deep_probe_cache_stale');
  await new Promise(resolve => setImmediate(resolve));
  const stale = await controller.probe();
  assert.equal(stale.ok, false);
  assert.equal(stale.code, 'deep_probe_busy_stale');
  assert.equal(stale.readyAgeMs, 21);
  assert.equal(browserCalls, 1);
});

test('busy before any real probe is unhealthy and cannot verify recovery', async () => {
  let recoveryCalls = 0;
  const busyExclusive = async () => {
    const error = new Error('busy');
    error.code = 'browser_lifecycle_busy';
    throw error;
  };
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => { throw new Error('must not launch'); },
    recoverBrowser: async () => { recoveryCalls++; },
    runExclusive: busyExclusive,
  });

  const initial = await controller.probe();
  assert.equal(initial.ok, false);
  assert.equal(initial.code, 'deep_probe_busy_stale');
  await new Promise(resolve => setImmediate(resolve));
  const recovery = await controller.recover();
  assert.equal(recovery.ok, false);
  assert.equal(recovery.code, 'recovery_probe_failed');
  assert.equal(recoveryCalls, 1);
});

test('passive readiness cache fails closed when absent, stale, or from another generation', async () => {
  let generation = 3;
  let clock = 1000;
  const controller = createSpartanDeepHealthController({
    getGeneration: () => generation,
    getBrowser: async () => ({
      newContext: async () => ({
        newPage: async () => fakePage(),
        close: async () => {},
      }),
    }),
    recoverBrowser: async () => {},
    cacheMaxAgeMs: 45,
    now: () => clock,
  });

  assert.deepEqual(controller.snapshot(), {
    ok: false,
    code: 'deep_probe_uninitialized',
    stage: 'cache',
    generation: 3,
    ageMs: null,
  });
  const ready = await controller.probe();
  assert.equal(ready.generation, 3);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.snapshot().ok, true);

  clock = 1046;
  assert.equal(controller.snapshot().code, 'deep_probe_cache_stale');
  generation = 4;
  const replacement = controller.snapshot();
  assert.equal(replacement.ok, false);
  assert.equal(replacement.code, 'deep_probe_generation_stale');
  assert.equal(replacement.generation, 4);
});

test('recovery requires a newly published generation and verifies that generation', async () => {
  let generation = 1;
  let publishReplacement = true;
  const controller = createSpartanDeepHealthController({
    getGeneration: () => generation,
    getBrowser: async () => ({
      newContext: async () => ({
        newPage: async () => fakePage(),
        close: async () => {},
      }),
    }),
    recoverBrowser: async () => {
      if (publishReplacement) generation++;
    },
  });

  const recovered = await controller.recover();
  assert.deepEqual(recovered, { ok: true, code: 'recovered', generation: 2 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.snapshot().generation, 2);
  assert.equal(controller.snapshot().ok, true);

  publishReplacement = false;
  const unchanged = await controller.recover();
  assert.deepEqual(unchanged, {
    ok: false,
    code: 'recovery_generation_unchanged',
    stage: 'recovery',
    generation: 2,
  });
  assert.equal(controller.snapshot().code, 'deep_probe_uninitialized');
});

test('generation readers reject coercible non-number values', () => {
  for (const invalid of [null, false, '', '1']) {
    const navigation = createSpartanNavigationReadiness({
      getGeneration: () => invalid,
      markDegraded: () => {},
    });
    assert.throws(() => navigation.snapshot(), /non-negative safe integer/);

    const controller = createSpartanDeepHealthController({
      getGeneration: () => invalid,
      getBrowser: async () => ({}),
      recoverBrowser: async () => {},
    });
    assert.equal(controller.snapshot().code, 'deep_probe_generation_invalid');
  }
});

test('recovery does not reuse an inflight probe from the failed generation', async () => {
  let generation = 1;
  let contextCalls = 0;
  let releaseOld;
  const oldGate = new Promise(resolve => { releaseOld = resolve; });
  const controller = createSpartanDeepHealthController({
    getGeneration: () => generation,
    getBrowser: async () => ({
      newContext: async () => {
        contextCalls++;
        if (generation === 1) await oldGate;
        return {
          newPage: async () => fakePage(),
          close: async () => {},
        };
      },
    }),
    recoverBrowser: async () => { generation = 2; },
    probeTimeoutMs: 40,
    cleanupTimeoutMs: 5,
  });

  const oldProbe = controller.probe();
  await new Promise(resolve => setImmediate(resolve));
  const recovered = await controller.recover();
  assert.deepEqual(recovered, { ok: true, code: 'recovered', generation: 2 });
  assert.equal(contextCalls, 2);
  releaseOld();
  await oldProbe;
});

test('recovery fails if the verified replacement is no longer current', async () => {
  let generation = 1;
  let changeDuringVerification = false;
  const controller = createSpartanDeepHealthController({
    getGeneration: () => generation,
    getBrowser: async () => ({
      newContext: async () => ({
        newPage: async () => fakePage({
          evaluate: async () => {
            if (changeDuringVerification) generation = 3;
            return 'ready';
          },
        }),
        close: async () => {},
      }),
    }),
    recoverBrowser: async () => {
      generation = 2;
      changeDuringVerification = true;
    },
  });

  const result = await controller.recover();
  assert.deepEqual(result, {
    ok: false,
    code: 'recovery_probe_failed',
    stage: 'complete',
    generation: 3,
  });
  assert.equal(controller.snapshot().code, 'deep_probe_uninitialized');
  assert.equal(controller.snapshot().generation, 3);
});

test('invalid health deadlines and exclusivity fail closed', () => {
  const base = { getBrowser: async () => ({}), recoverBrowser: async () => {} };
  assert.throws(() => createSpartanDeepHealthController({ ...base, probeTimeoutMs: 0 }), /probeTimeoutMs/);
  assert.throws(() => createSpartanDeepHealthController({ ...base, busyMaxAgeMs: Number.POSITIVE_INFINITY }), /busyMaxAgeMs/);
  assert.throws(() => createSpartanDeepHealthController({ ...base, runExclusive: null }), /runExclusive/);
});
