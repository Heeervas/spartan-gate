import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createSpartanDeepHealthController, spartanCgroupAdmissionAllowed, spartanInternalRequestAuthorized } from './spartan-health.mjs';

function fakePage(overrides = {}) {
  return {
    goto: async () => {},
    evaluate: async () => 'ready',
    close: async () => {},
    ...overrides,
  };
}

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

test('newPage timeout returns unhealthy and late page is closed', async () => {
  let resolvePage;
  let pageClosed = 0;
  const latePage = fakePage({ close: async () => { pageClosed++; } });
  const context = {
    newPage: () => new Promise(resolve => { resolvePage = resolve; }),
    close: async () => {},
  };
  const controller = createSpartanDeepHealthController({
    getBrowser: async () => ({ newContext: async () => context }),
    recoverBrowser: async () => {},
    probeTimeoutMs: 10,
    cleanupTimeoutMs: 10,
  });

  const result = await controller.probe();
  assert.equal(result.ok, false);
  assert.equal(result.code, 'deep_probe_timeout');
  assert.equal(result.stage, 'new_page');
  resolvePage(latePage);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(pageClosed, 1);
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
