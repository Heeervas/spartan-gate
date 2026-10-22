import assert from 'node:assert/strict';
import test, { beforeEach, afterEach } from 'node:test';
// Keep unref production deadlines observable in this standalone test process.
let keepalive;
beforeEach(() => { keepalive = setTimeout(() => {}, 10_000); });
afterEach(() => { clearTimeout(keepalive); });

import { createSpartanBrowserLifecycleCoordinator } from './spartan-lifecycle.mjs';

const turn = () => new Promise(resolve => setImmediate(resolve));

test('lifecycle is fixed at one owner and prioritizes health over queued work', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator({ maxQueued: 4 });
  const order = [];
  let releaseOwner;
  const ownerGate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => {
    order.push('owner');
    await ownerGate;
  });
  await turn();
  const background = coordinator.run('background_close', async () => {
    order.push('background');
  }, { priority: 'background', waitTimeoutMs: null });
  const foreground = coordinator.run('foreground_create', async () => {
    order.push('foreground');
  });
  const health = coordinator.run('health_probe', async () => {
    order.push('health');
  }, { priority: 'health', waitTimeoutMs: 100 });
  releaseOwner();
  await Promise.all([owner, background, foreground, health]);
  assert.deepEqual(order, ['owner', 'health', 'foreground', 'background']);
});

test('aged background cleanup runs before fresh foreground work', async () => {
  let clock = 0;
  const coordinator = createSpartanBrowserLifecycleCoordinator({
    maxQueued: 4,
    backgroundAgingMs: 100,
    now: () => clock,
  });
  const order = [];
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => { await gate; });
  await turn();
  const background = coordinator.run('old_cleanup', async () => {
    order.push('background');
  }, { priority: 'background', waitTimeoutMs: null });
  clock = 100;
  const foreground = coordinator.run('fresh_create', async () => {
    order.push('foreground');
  });
  releaseOwner();
  await Promise.all([owner, background, foreground]);
  assert.deepEqual(order, ['background', 'foreground']);
});

test('nested lifecycle work reuses the active lease without deadlock', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let outerLease;
  let innerLease;
  await coordinator.run('outer', async lease => {
    outerLease = lease;
    await coordinator.run('inner', async nested => {
      innerLease = nested;
    });
  });
  assert.equal(innerLease, outerLease);
  assert.equal(coordinator.snapshot().ownerKind, null);
});

test('expired waiter is removed and never executes later', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => { await gate; });
  await turn();
  let staleRan = false;
  await assert.rejects(
    coordinator.run('stale', async () => { staleRan = true; }, { waitTimeoutMs: 10 }),
    error => error?.code === 'browser_lifecycle_queue_timeout',
  );
  releaseOwner();
  await owner;
  await turn();
  assert.equal(staleRan, false);
});

test('queue is bounded while reserving one health waiter', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator({ maxQueued: 2 });
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => { await gate; });
  await turn();
  const first = coordinator.run('first', async () => {}, { waitTimeoutMs: null });
  const second = coordinator.run('second', async () => {}, { waitTimeoutMs: null });
  await assert.rejects(
    coordinator.run('overflow', async () => {}, { waitTimeoutMs: null }),
    error => error?.code === 'browser_lifecycle_queue_full',
  );
  const health = coordinator.run('health_probe', async () => {}, {
    priority: 'health',
    waitTimeoutMs: 100,
  });
  await assert.rejects(
    coordinator.run('second_health', async () => {}, {
      priority: 'health',
      waitTimeoutMs: 100,
    }),
    error => error?.code === 'browser_lifecycle_queue_full',
  );
  releaseOwner();
  await Promise.all([owner, first, second, health]);
});

test('recovery invalidates queued work and drains the active owner', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => { await gate; });
  await turn();
  let queuedRan = false;
  const queued = coordinator.run('queued', async () => { queuedRan = true; }, {
    waitTimeoutMs: null,
  });
  const queuedRejected = assert.rejects(
    queued,
    error => error?.code === 'browser_lifecycle_invalidated',
  );
  let recoveryRan = false;
  const recovery = coordinator.recover(async () => {
    recoveryRan = true;
    assert.equal(coordinator.isRecoveryLease(), true);
  });
  await queuedRejected;
  await turn();
  assert.equal(recoveryRan, false);
  assert.equal(queuedRan, false);
  const ownerRejected = assert.rejects(
    owner,
    error => error?.code === 'browser_lifecycle_invalidated',
  );
  releaseOwner();
  await Promise.all([ownerRejected, recovery]);
  assert.equal(recoveryRan, true);
  await coordinator.run('after_recovery', async () => {});
});

test('recovery drain timeout fails closed without competing with the owner', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async () => { await gate; });
  await turn();
  let recoveryRan = false;
  await assert.rejects(
    coordinator.recover(async () => { recoveryRan = true; }, { drainTimeoutMs: 10 }),
    error => error?.code === 'browser_lifecycle_drain_timeout',
  );
  assert.equal(recoveryRan, false);
  assert.equal(coordinator.snapshot().ownerKind, 'owner');
  assert.equal(
    coordinator.snapshot().degradedCode,
    'browser_lifecycle_drain_timeout',
  );
  await assert.rejects(
    coordinator.run('blocked', async () => {}),
    error => error?.code === 'browser_lifecycle_degraded',
  );
  const ownerRejected = assert.rejects(
    owner,
    error => error?.code === 'browser_lifecycle_invalidated',
  );
  releaseOwner();
  await ownerRejected;
  await coordinator.recover(async () => {});
  assert.equal(coordinator.snapshot().degradedCode, null);
});

test('degraded state fails closed until successful recovery', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  coordinator.markDegraded('session_close_failed');
  await assert.rejects(
    coordinator.run('create', async () => {}),
    error => error?.code === 'browser_lifecycle_degraded',
  );
  assert.equal(coordinator.snapshot().degradedCode, 'session_close_failed');
  await coordinator.recover(async () => {});
  await coordinator.run('create', async () => {});
  assert.equal(coordinator.snapshot().degradedCode, null);
});

test('response timeout cannot release a lease before underlying work settles', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let releaseUnderlying;
  const underlyingGate = new Promise(resolve => { releaseUnderlying = resolve; });
  const underlying = coordinator.run('deep_probe', async () => {
    await underlyingGate;
  });
  await turn();
  let nextRan = false;
  const next = coordinator.run('create', async () => { nextRan = true; }, {
    waitTimeoutMs: null,
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(nextRan, false);
  assert.equal(coordinator.snapshot().ownerKind, 'deep_probe');
  releaseUnderlying();
  await Promise.all([underlying, next]);
  assert.equal(nextRan, true);
});

test('tracked nested work retains its lease after the operation returns early', async () => {
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  let releaseNested;
  const nestedGate = new Promise(resolve => { releaseNested = resolve; });
  let operationReturned = false;
  let lifecycleSettled = false;
  const lifecycle = coordinator.run('outer', async () => {
    coordinator.track(nestedGate);
    operationReturned = true;
  }).then(() => { lifecycleSettled = true; });
  await turn();
  assert.equal(operationReturned, true);
  assert.equal(lifecycleSettled, false);
  assert.equal(coordinator.snapshot().ownerKind, 'outer');
  let nextRan = false;
  const next = coordinator.run('next', async () => { nextRan = true; }, {
    waitTimeoutMs: null,
  });
  await turn();
  assert.equal(nextRan, false);
  releaseNested();
  await Promise.all([lifecycle, next]);
  assert.equal(nextRan, true);
  assert.throws(() => coordinator.track(Promise.resolve()), /active lease/);
});

test('absolute deadlines are exposed and expired work never acquires', async () => {
  let clock = 100;
  const coordinator = createSpartanBrowserLifecycleCoordinator({ now: () => clock });
  let releaseOwner;
  const gate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('owner', async lease => {
    assert.equal(lease.deadlineAt, 150);
    lease.assertCurrent();
    await gate;
  }, { deadlineAt: 150 });
  await turn();
  assert.equal(coordinator.snapshot().ownerDeadlineExceeded, false);
  clock = 151;
  assert.equal(coordinator.snapshot().ownerDeadlineExceeded, true);
  await assert.rejects(
    coordinator.run('expired', async () => {}, { deadlineAt: 150, waitTimeoutMs: null }),
    error => error?.code === 'browser_lifecycle_queue_timeout',
  );
  releaseOwner();
  await owner;
});

test('invalid configuration and metadata fail closed', async () => {
  assert.throws(
    () => createSpartanBrowserLifecycleCoordinator({ maxQueued: 0 }),
    /maxQueued/,
  );
  assert.throws(
    () => createSpartanBrowserLifecycleCoordinator({ backgroundAgingMs: 0 }),
    /backgroundAgingMs/,
  );
  const coordinator = createSpartanBrowserLifecycleCoordinator();
  await assert.rejects(
    coordinator.run('INVALID KIND', async () => {}),
    /short lowercase identifier/,
  );
  await assert.rejects(
    coordinator.run('valid', async () => {}, { priority: 'unknown' }),
    /invalid browser lifecycle priority/,
  );
  await assert.rejects(
    coordinator.run('valid', async () => {}, { deadlineAt: Number.NaN }),
    /deadlineAt/,
  );
  assert.throws(
    () => coordinator.recover(async () => {}, { drainTimeoutMs: 0 }),
    /drainTimeoutMs/,
  );
});
