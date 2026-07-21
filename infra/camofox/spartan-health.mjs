import crypto from 'node:crypto';
import fs from 'node:fs';

const DEFAULT_PROBE_TIMEOUT_MS = 8000;
const DEFAULT_CLEANUP_TIMEOUT_MS = 1000;
const DEFAULT_BUSY_MAX_AGE_MS = 45000;

export function readSpartanCgroupMemory(root = '/sys/fs/cgroup') {
  try {
    const currentRaw = fs.readFileSync(`${root}/memory.current`, 'utf8').trim();
    const maxRaw = fs.readFileSync(`${root}/memory.max`, 'utf8').trim();
    if (maxRaw === 'max') return { state: 'unknown', code: 'memory_unlimited' };
    const current = Number(currentRaw);
    const max = Number(maxRaw);
    if (!Number.isFinite(current) || !Number.isFinite(max) || current < 0 || max <= 0) {
      return { state: 'unknown', code: 'memory_invalid' };
    }
    return { state: 'known', ratio: current / max, current, max };
  } catch {
    return { state: 'unknown', code: 'memory_unavailable' };
  }
}

export function spartanCgroupAdmissionAllowed({ root, threshold = 0.90 } = {}) {
  if (!Number.isFinite(threshold) || threshold <= 0 || threshold >= 1) {
    return { allowed: false, code: 'memory_threshold_invalid', state: 'unknown' };
  }
  const state = readSpartanCgroupMemory(root);
  if (state.state !== 'known') return { allowed: false, ...state };
  return {
    allowed: state.ratio < threshold,
    code: state.ratio < threshold ? 'memory_ok' : 'memory_pressure',
    ...state,
  };
}

function timeoutError(stage) {
  const error = new Error(`deep browser probe timed out at ${stage}`);
  error.code = 'deep_probe_timeout';
  error.stage = stage;
  return error;
}

async function bounded(promise, timeoutMs, stage, lateCleanup, settlements) {
  let timer;
  let expired = false;
  const tracked = Promise.resolve(promise).then(async value => {
    if (expired && lateCleanup) await lateCleanup(value).catch(() => {});
    return value;
  });
  settlements?.push(tracked.then(() => undefined, () => undefined));
  try {
    return await Promise.race([
      tracked,
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(timeoutError(stage));
        }, timeoutMs);
        timer.unref?.();
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function closeResource(resource) {
  if (!resource?.close) return;
  return Promise.resolve().then(() => resource.close());
}

export function spartanInternalRequestAuthorized(req, secretPath) {
  const address = req?.socket?.remoteAddress;
  if (address !== '127.0.0.1' && address !== '::1' && address !== '::ffff:127.0.0.1') {
    return false;
  }
  let expected;
  try {
    expected = fs.readFileSync(secretPath, 'utf8').trim();
  } catch {
    return false;
  }
  const supplied = req.get?.('X-Spartan-Camofox-Internal') || req.headers?.['x-spartan-camofox-internal'];
  if (typeof supplied !== 'string' || !expected || supplied.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(expected));
}

export function createSpartanNavigationReadiness({
  failureThreshold = 3,
  getGeneration,
  markDegraded,
  now = () => Date.now(),
} = {}) {
  if (!Number.isInteger(failureThreshold) || failureThreshold < 1 || failureThreshold > 100) {
    throw new TypeError('navigation readiness failureThreshold must be an integer between 1 and 100');
  }
  if (typeof getGeneration !== 'function') {
    throw new TypeError('navigation readiness getGeneration must be a function');
  }
  if (typeof markDegraded !== 'function') {
    throw new TypeError('navigation readiness markDegraded must be a function');
  }
  if (typeof now !== 'function') throw new TypeError('navigation readiness now must be a function');

  let state = null;

  function readGeneration() {
    const generation = Number(getGeneration());
    if (!Number.isSafeInteger(generation) || generation < 0) {
      throw new TypeError('navigation readiness generation must be a non-negative safe integer');
    }
    return generation;
  }

  function currentState() {
    const generation = readGeneration();
    if (!state || state.generation !== generation) {
      state = {
        generation,
        consecutiveFailures: 0,
        latched: false,
        latchedAt: null,
        lastSuccessAt: null,
      };
    }
    return state;
  }

  function begin({ participates = true } = {}) {
    const generation = readGeneration();
    let settled = false;

    function settle(kind, qualifying = false) {
      if (settled) return { accepted: false, reason: 'already_settled' };
      settled = true;
      if (!participates) return { accepted: false, reason: 'not_participating' };
      const current = currentState();
      if (current.generation !== generation) {
        return { accepted: false, reason: 'stale_generation' };
      }
      if (current.latched) return { accepted: false, reason: 'latched' };
      if (kind === 'success') {
        current.consecutiveFailures = 0;
        current.lastSuccessAt = now();
        return { accepted: true, latched: false, consecutiveFailures: 0 };
      }
      if (!qualifying) return { accepted: false, reason: 'not_qualifying' };
      current.consecutiveFailures++;
      if (current.consecutiveFailures >= failureThreshold) {
        current.latched = true;
        current.latchedAt = now();
        markDegraded('browser_navigation_degraded');
      }
      return {
        accepted: true,
        latched: current.latched,
        consecutiveFailures: current.consecutiveFailures,
      };
    }

    return {
      generation,
      success: () => settle('success'),
      failure: ({ qualifying = false } = {}) => settle('failure', qualifying),
    };
  }

  function snapshot() {
    const current = currentState();
    return { ...current };
  }

  return { begin, snapshot };
}

export function createSpartanDeepHealthController({
  getBrowser,
  recoverBrowser,
  probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
  cleanupTimeoutMs = DEFAULT_CLEANUP_TIMEOUT_MS,
  busyMaxAgeMs = DEFAULT_BUSY_MAX_AGE_MS,
  recoveryTimeoutMs = 20000,
  runExclusive = operation => operation(),
  now = () => Date.now(),
  getGeneration = null,
  cacheMaxAgeMs = busyMaxAgeMs,
}) {
  for (const [name, value] of [
    ['probeTimeoutMs', probeTimeoutMs],
    ['cleanupTimeoutMs', cleanupTimeoutMs],
    ['busyMaxAgeMs', busyMaxAgeMs],
    ['recoveryTimeoutMs', recoveryTimeoutMs],
    ['cacheMaxAgeMs', cacheMaxAgeMs],
  ]) {
    if (!Number.isFinite(Number(value)) || Number(value) < 1) {
      throw new TypeError(`deep health ${name} must be positive`);
    }
  }
  if (typeof runExclusive !== 'function') {
    throw new TypeError('deep health runExclusive must be a function');
  }
  const generationAware = getGeneration !== null && getGeneration !== undefined;
  if (generationAware && typeof getGeneration !== 'function') {
    throw new TypeError('deep health getGeneration must be a function or null');
  }
  const exposureTimeoutMs = Number(probeTimeoutMs) + Number(cleanupTimeoutMs);
  let inflightProbe = null;
  let inflightRecovery = null;
  let lastReadyAt = null;
  let lastReadyGeneration = null;
  let lastResult = null;
  let lastProbeAt = null;

  function readGeneration() {
    if (!generationAware) return null;
    const generation = Number(getGeneration());
    if (!Number.isSafeInteger(generation) || generation < 0) {
      const error = new Error('deep health generation must be a non-negative safe integer');
      error.code = 'deep_probe_generation_invalid';
      error.stage = 'generation';
      throw error;
    }
    return generation;
  }

  function withGeneration(result, generation = readGeneration()) {
    return generationAware ? { ...result, generation } : result;
  }

  function invalidate() {
    lastReadyAt = null;
    lastReadyGeneration = null;
    lastResult = null;
    lastProbeAt = null;
  }

  function publishResult(result) {
    let generation;
    try {
      generation = readGeneration();
    } catch {
      return;
    }
    if (generationAware && result?.generation !== generation) return;
    lastResult = { ...result };
    lastProbeAt = now();
    if (result?.ok && result.code === 'ready') {
      lastReadyAt = lastProbeAt;
      lastReadyGeneration = generation;
    }
  }

  function snapshot() {
    let generation;
    try {
      generation = readGeneration();
    } catch (error) {
      return { ok: false, code: error.code, stage: error.stage, generation: null, ageMs: null };
    }
    if (inflightRecovery) {
      return { ok: false, code: 'browser_recovering', stage: 'recovery', generation, ageMs: 0 };
    }
    if (!lastResult || lastProbeAt === null) {
      return { ok: false, code: 'deep_probe_uninitialized', stage: 'cache', generation, ageMs: null };
    }
    if (generationAware && lastResult.generation !== generation) {
      return { ok: false, code: 'deep_probe_generation_stale', stage: 'cache', generation, ageMs: Math.max(0, now() - lastProbeAt) };
    }
    const ageMs = Math.max(0, now() - lastProbeAt);
    if (ageMs > Number(cacheMaxAgeMs)) {
      return { ok: false, code: 'deep_probe_cache_stale', stage: 'cache', generation, ageMs };
    }
    return { ...lastResult, ageMs };
  }

  async function runProbe(reportFailure) {
    const startedAt = now();
    const deadlineAt = startedAt + probeTimeoutMs;
    const remaining = () => Math.max(1, deadlineAt - now());
    const settlements = [];
    let context;
    let page;
    let stage = 'ensure_browser';
    let generation = readGeneration();
    try {
      const browser = await bounded(
        Promise.resolve().then(() => getBrowser()),
        remaining(),
        stage,
        null,
        settlements,
      );
      generation = readGeneration();
      stage = 'new_context';
      context = await bounded(
        browser.newContext(),
        remaining(),
        stage,
        value => closeResource(value),
        settlements,
      );
      stage = 'new_page';
      page = await bounded(
        context.newPage(),
        remaining(),
        stage,
        value => closeResource(value),
        settlements,
      );
      stage = 'goto';
      await bounded(
        page.goto('data:text/html,<title>spartan-camofox-health</title><main id="sentinel">ready</main>', {
          waitUntil: 'domcontentloaded',
          timeout: remaining(),
        }),
        remaining(),
        stage,
        null,
        settlements,
      );
      stage = 'evaluate';
      const sentinel = await bounded(
        page.evaluate(() => document.querySelector('#sentinel')?.textContent),
        remaining(),
        stage,
        null,
        settlements,
      );
      if (sentinel !== 'ready') {
        const error = new Error('deep browser probe sentinel mismatch');
        error.code = 'deep_probe_sentinel';
        error.stage = stage;
        throw error;
      }
      return withGeneration({ ok: true, code: 'ready', stage: 'complete', durationMs: Math.max(0, now() - startedAt) }, generation);
    } catch (error) {
      const result = withGeneration({
        ok: false,
        code: error?.code || 'deep_probe_failed',
        stage: error?.stage || stage,
        durationMs: Math.max(0, now() - startedAt),
      }, generation);
      reportFailure(result);
      return result;
    } finally {
      // A timed-out create/navigation must settle before cleanup starts.
      // Otherwise context.close() can overlap the still-running browser call
      // and recreate the Camoufox deadlock this probe is meant to detect.
      await Promise.all(settlements);
      const cleanupErrors = [];
      for (const resource of [page, context]) {
        try {
          await closeResource(resource);
        } catch (error) {
          cleanupErrors.push(error);
        }
      }
      // The exclusive lifecycle lease is held until every timed-out operation
      // and each page -> context close has actually settled. PID 1 is the final
      // escape hatch for an operation that cannot settle.
      if (cleanupErrors.length > 0) {
        const error = new Error('deep browser probe cleanup failed');
        error.code = 'deep_probe_cleanup_failed';
        error.stage = 'cleanup';
        throw error;
      }
    }
  }

  function probe() {
    if (!inflightProbe) {
      let reportFailure;
      const earlyFailure = new Promise(resolve => { reportFailure = resolve; });
      const startedAt = now();
      const underlying = Promise.resolve()
        .then(() => runExclusive(() => runProbe(reportFailure)))
        .catch(error => {
          if (error?.code === 'browser_lifecycle_busy') {
            const generation = readGeneration();
            const readyAgeMs = lastReadyAt === null
              || (generationAware && lastReadyGeneration !== generation)
              ? null
              : Math.max(0, now() - lastReadyAt);
            if (readyAgeMs !== null && readyAgeMs <= Number(busyMaxAgeMs)) {
              return withGeneration({
                ok: true,
                code: 'busy',
                stage: 'lifecycle',
                readyAgeMs,
                durationMs: Math.max(0, now() - startedAt),
              }, generation);
            }
            return withGeneration({
              ok: false,
              code: 'deep_probe_busy_stale',
              stage: 'lifecycle',
              readyAgeMs,
              durationMs: Math.max(0, now() - startedAt),
            }, generation);
          }
          return withGeneration({
            ok: false,
            code: error?.code || 'deep_probe_failed',
            stage: error?.stage || 'lifecycle',
            durationMs: Math.max(0, now() - startedAt),
          });
        });
      const reported = Promise.race([earlyFailure, underlying]);
      const exposed = bounded(
        reported,
        exposureTimeoutMs,
        'lifecycle_settle',
      ).catch(error => withGeneration({
        ok: false,
        code: error?.code || 'deep_probe_failed',
        stage: error?.stage || 'lifecycle_settle',
        durationMs: Math.max(0, now() - startedAt),
      }));
      exposed.then(publishResult, () => {});
      inflightProbe = exposed;
      underlying.then(
        () => { if (inflightProbe === exposed) inflightProbe = null; },
        () => { if (inflightProbe === exposed) inflightProbe = null; },
      );
    }
    return inflightProbe;
  }

  function recover() {
    if (!inflightRecovery) {
      const recovery = Promise.resolve().then(async () => {
          const failedGeneration = readGeneration();
          invalidate();
          await recoverBrowser();
          const replacementGeneration = readGeneration();
          if (generationAware && replacementGeneration === failedGeneration) {
            return withGeneration({
              ok: false,
              code: 'recovery_generation_unchanged',
              stage: 'recovery',
            }, replacementGeneration);
          }
          const verification = await probe();
          return verification.code === 'ready'
            && (!generationAware || verification.generation === replacementGeneration)
            ? withGeneration({ ok: true, code: 'recovered' }, replacementGeneration)
            : withGeneration({
                ok: false,
                code: 'recovery_probe_failed',
                stage: verification.stage,
              }, replacementGeneration);
        });
      const exposedRecovery = bounded(
        recovery,
        recoveryTimeoutMs,
        'recovery',
      )
        .catch(error => ({ ok: false, code: error?.code || 'recovery_failed' }));
      inflightRecovery = exposedRecovery;
      // The HTTP deadline must not release the single-flight fence while the
      // uncancellable browser restart is still running. A stuck operation
      // therefore keeps returning the same bounded result until PID 1
      // recycles this Node process; a settled operation releases normally.
      recovery.then(
        () => { if (inflightRecovery === exposedRecovery) inflightRecovery = null; },
        () => { if (inflightRecovery === exposedRecovery) inflightRecovery = null; },
      );
    }
    return inflightRecovery;
  }

  return { probe, recover, snapshot, invalidate };
}
