import { AsyncLocalStorage } from 'node:async_hooks';

const PRIORITIES = Object.freeze(['health', 'foreground', 'background']);

export function spartanBrowserLifecycleError(code, message) {
  const error = new Error(message);
  error.statusCode = 503;
  error.code = code;
  return error;
}

function validateKind(kind) {
  if (typeof kind !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(kind)) {
    throw new TypeError('browser lifecycle kind must be a short lowercase identifier');
  }
}

export function createSpartanBrowserLifecycleCoordinator({
  maxQueued = 8,
  backgroundAgingMs = 1000,
  now = () => Date.now(),
} = {}) {
  if (!Number.isInteger(maxQueued) || maxQueued < 1 || maxQueued > 1024) {
    throw new TypeError('browser lifecycle maxQueued must be an integer between 1 and 1024');
  }
  if (!Number.isFinite(Number(backgroundAgingMs)) || Number(backgroundAgingMs) < 1) {
    throw new TypeError('browser lifecycle backgroundAgingMs must be positive');
  }
  if (typeof now !== 'function') throw new TypeError('browser lifecycle now must be a function');

  const token = {};
  const scope = new AsyncLocalStorage();
  const queues = { health: [], foreground: [], background: [] };
  const ownerReleaseWaiters = new Set();
  let generation = 0;
  let nextLeaseId = 1;
  let owner = null;
  let recovering = false;
  let recoveryPromise = null;
  let degradedCode = null;

  const queuedCount = () => PRIORITIES.reduce(
    (total, priority) => total + queues[priority].length,
    0,
  );

  function makeLease(kind, deadlineAt = null) {
    const lease = {
      id: nextLeaseId++,
      generation,
      kind,
      startedAt: now(),
      deadlineAt,
      active: true,
      invalidated: false,
      pending: new Set(),
    };
    lease.assertCurrent = () => {
      if (!isCurrentLease(lease)) {
        throw spartanBrowserLifecycleError(
          'browser_lifecycle_invalidated',
          'browser lifecycle lease is no longer current',
        );
      }
    };
    return lease;
  }

  function isCurrentLease(lease) {
    return owner === lease
      && lease?.active === true
      && lease.invalidated === false
      && lease.generation === generation;
  }

  async function waitForTracked(lease) {
    while (lease.pending.size > 0) {
      await Promise.allSettled([...lease.pending]);
    }
  }

  function track(promise) {
    const current = scope.getStore();
    if (current?.token !== token || !isCurrentLease(current.lease)) {
      throw new TypeError('browser lifecycle track requires the active lease');
    }
    const tracked = Promise.resolve(promise);
    current.lease.pending.add(tracked);
    tracked.then(
      () => current.lease.pending.delete(tracked),
      () => current.lease.pending.delete(tracked),
    );
    return tracked;
  }

  function removeWaiter(waiter) {
    const queue = queues[waiter.priority];
    const index = queue.indexOf(waiter);
    if (index < 0) return false;
    queue.splice(index, 1);
    return true;
  }

  function nextWaiter() {
    const health = queues.health.shift();
    if (health) return health;
    const background = queues.background[0];
    if (
      background
      && now() - background.queuedAt >= Number(backgroundAgingMs)
    ) {
      return queues.background.shift();
    }
    return queues.foreground.shift() || queues.background.shift() || null;
  }

  function dispatch() {
    if (owner || recovering || degradedCode) return;
    while (true) {
      const waiter = nextWaiter();
      if (!waiter) return;
      clearTimeout(waiter.timer);
      if (waiter.generation !== generation) {
        waiter.reject(spartanBrowserLifecycleError(
          'browser_lifecycle_invalidated',
          'browser lifecycle generation changed while queued',
        ));
        continue;
      }
      if (waiter.deadlineAt !== null && now() >= waiter.deadlineAt) {
        waiter.reject(spartanBrowserLifecycleError(
          'browser_lifecycle_queue_timeout',
          'browser lifecycle absolute deadline expired while queued',
        ));
        continue;
      }
      owner = makeLease(waiter.kind, waiter.deadlineAt);
      waiter.resolve(owner);
      return;
    }
  }

  function acquire(kind, priority, waitTimeoutMs, deadlineAt) {
    if (deadlineAt !== null && now() >= deadlineAt) {
      return Promise.reject(spartanBrowserLifecycleError(
        'browser_lifecycle_queue_timeout',
        'browser lifecycle absolute deadline expired',
      ));
    }
    if (!owner && queuedCount() === 0 && !recovering) {
      owner = makeLease(kind, deadlineAt);
      return Promise.resolve(owner);
    }

    const queueLimit = priority === 'health' ? maxQueued + 1 : maxQueued;
    if (queuedCount() >= queueLimit) {
      return Promise.reject(spartanBrowserLifecycleError(
        'browser_lifecycle_queue_full',
        'browser lifecycle queue is full',
      ));
    }

    let effectiveWaitTimeoutMs = waitTimeoutMs;
    if (deadlineAt !== null) {
      const remainingMs = Math.max(0, deadlineAt - now());
      effectiveWaitTimeoutMs = effectiveWaitTimeoutMs === null
        || effectiveWaitTimeoutMs === undefined
        ? remainingMs
        : Math.min(Number(effectiveWaitTimeoutMs), remainingMs);
    }
    const finiteTimeout = effectiveWaitTimeoutMs !== null
      && effectiveWaitTimeoutMs !== undefined;
    if (
      finiteTimeout
      && (!Number.isFinite(Number(effectiveWaitTimeoutMs)) || Number(effectiveWaitTimeoutMs) < 1)
    ) {
      return Promise.reject(spartanBrowserLifecycleError(
        'browser_lifecycle_queue_timeout',
        'browser lifecycle queue deadline expired',
      ));
    }

    return new Promise((resolve, reject) => {
      const waiter = {
        generation,
        kind,
        deadlineAt,
        queuedAt: now(),
        priority,
        resolve,
        reject,
        timer: null,
      };
      if (finiteTimeout) {
        waiter.timer = setTimeout(() => {
          if (!removeWaiter(waiter)) return;
          reject(spartanBrowserLifecycleError(
            'browser_lifecycle_queue_timeout',
            'browser lifecycle queue deadline expired',
          ));
        }, Number(effectiveWaitTimeoutMs));
        waiter.timer.unref?.();
      }
      queues[priority].push(waiter);
    });
  }

  async function run(kind, operation, {
    priority = 'foreground',
    waitTimeoutMs = 30000,
    deadlineAt = null,
    allowDegraded = false,
  } = {}) {
    validateKind(kind);
    if (typeof operation !== 'function') {
      throw new TypeError('browser lifecycle operation must be a function');
    }
    if (!PRIORITIES.includes(priority)) {
      throw new TypeError('invalid browser lifecycle priority');
    }
    if (
      deadlineAt !== null
      && deadlineAt !== undefined
      && !Number.isFinite(Number(deadlineAt))
    ) {
      throw new TypeError('browser lifecycle deadlineAt must be finite or null');
    }
    const absoluteDeadline = deadlineAt === null || deadlineAt === undefined
      ? null
      : Number(deadlineAt);

    const current = scope.getStore();
    if (current?.token === token && isCurrentLease(current.lease)) {
      return await operation(current.lease);
    }
    if (recovering) {
      throw spartanBrowserLifecycleError(
        'browser_lifecycle_recovering',
        'browser lifecycle recovery is in progress',
      );
    }
    if (degradedCode && !allowDegraded) {
      throw spartanBrowserLifecycleError(
        'browser_lifecycle_degraded',
        'browser lifecycle requires recovery',
      );
    }

    const lease = await acquire(kind, priority, waitTimeoutMs, absoluteDeadline);
    try {
      let result;
      try {
        result = await scope.run({ token, lease }, () => operation(lease));
      } finally {
        await waitForTracked(lease);
      }
      lease.assertCurrent();
      return result;
    } finally {
      if (owner === lease) {
        lease.active = false;
        owner = null;
        notifyOwnerReleased();
        dispatch();
      }
    }
  }

  function invalidate(reason = 'browser lifecycle invalidated') {
    generation++;
    if (owner) owner.invalidated = true;
    for (const priority of PRIORITIES) {
      for (const waiter of queues[priority].splice(0)) {
        clearTimeout(waiter.timer);
        waiter.reject(spartanBrowserLifecycleError(
          'browser_lifecycle_invalidated',
          reason,
        ));
      }
    }
  }

  function notifyOwnerReleased() {
    if (owner) return;
    for (const waiter of ownerReleaseWaiters) {
      clearTimeout(waiter.timer);
      waiter.resolve();
    }
    ownerReleaseWaiters.clear();
  }

  function waitForOwnerRelease(timeoutMs) {
    if (!owner) return Promise.resolve();
    if (!Number.isFinite(Number(timeoutMs)) || Number(timeoutMs) < 1) {
      return Promise.reject(spartanBrowserLifecycleError(
        'browser_lifecycle_drain_timeout',
        'browser lifecycle recovery requires a positive drain deadline',
      ));
    }
    return new Promise((resolve, reject) => {
      const waiter = {
        resolve,
        timer: setTimeout(() => {
          ownerReleaseWaiters.delete(waiter);
          reject(spartanBrowserLifecycleError(
            'browser_lifecycle_drain_timeout',
            'browser lifecycle owner did not drain before recovery deadline',
          ));
        }, Number(timeoutMs)),
      };
      waiter.timer.unref?.();
      ownerReleaseWaiters.add(waiter);
    });
  }

  function recover(operation, { drainTimeoutMs = 2000 } = {}) {
    if (typeof operation !== 'function') {
      throw new TypeError('browser lifecycle recovery must be a function');
    }
    if (!Number.isFinite(Number(drainTimeoutMs)) || Number(drainTimeoutMs) < 1) {
      throw new TypeError('browser lifecycle recovery drainTimeoutMs must be positive');
    }
    const current = scope.getStore();
    if (current?.token === token && current.lease === owner) {
      markDegraded('browser_lifecycle_recovery_reentrant');
      return Promise.reject(spartanBrowserLifecycleError(
        'browser_lifecycle_recovery_reentrant',
        'browser lifecycle recovery cannot replace its active owner',
      ));
    }
    if (recoveryPromise) return recoveryPromise;
    recovering = true;
    invalidate('browser lifecycle recovery invalidated queued work');
    let recoveryLease = null;
    const recovery = Promise.resolve()
      .then(() => waitForOwnerRelease(drainTimeoutMs))
      .then(() => {
        recoveryLease = makeLease('recovery');
        owner = recoveryLease;
        return scope.run(
          { token, lease: recoveryLease },
          async () => {
            let result;
            try {
              result = await operation(recoveryLease);
            } finally {
              await waitForTracked(recoveryLease);
            }
            recoveryLease.assertCurrent();
            return result;
          },
        );
      })
      .then(result => {
        degradedCode = null;
        return result;
      })
      .catch(error => {
        degradedCode = error?.code === 'browser_lifecycle_drain_timeout'
          ? 'browser_lifecycle_drain_timeout'
          : 'browser_recovery_failed';
        throw error;
      })
      .finally(() => {
        if (recoveryLease && owner === recoveryLease) {
          recoveryLease.active = false;
          owner = null;
          notifyOwnerReleased();
        }
        recovering = false;
        if (recoveryPromise === recovery) recoveryPromise = null;
        dispatch();
      });
    recoveryPromise = recovery;
    return recovery;
  }

  function markDegraded(code = 'browser_lifecycle_degraded') {
    validateKind(code);
    invalidate('browser lifecycle degraded');
    degradedCode = code;
  }

  function hasLease() {
    const current = scope.getStore();
    return current?.token === token && isCurrentLease(current.lease);
  }

  function isRecoveryLease() {
    const current = scope.getStore();
    return current?.token === token
      && isCurrentLease(current.lease)
      && current.lease?.kind === 'recovery';
  }

  function snapshot() {
    return {
      generation,
      recovering,
      degradedCode,
      ownerKind: owner?.kind || null,
      ownerAgeMs: owner ? Math.max(0, now() - owner.startedAt) : 0,
      ownerDeadlineAt: owner?.deadlineAt ?? null,
      ownerDeadlineExceeded: Boolean(
        owner?.deadlineAt !== null && owner?.deadlineAt !== undefined && now() >= owner.deadlineAt,
      ),
      queued: {
        health: queues.health.length,
        foreground: queues.foreground.length,
        background: queues.background.length,
      },
    };
  }

  return {
    run,
    track,
    recover,
    invalidate,
    markDegraded,
    hasLease,
    isRecoveryLease,
    snapshot,
  };
}
