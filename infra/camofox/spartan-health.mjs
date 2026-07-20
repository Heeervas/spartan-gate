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

export function createSpartanDeepHealthController({
  getBrowser,
  recoverBrowser,
  probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
  cleanupTimeoutMs = DEFAULT_CLEANUP_TIMEOUT_MS,
  busyMaxAgeMs = DEFAULT_BUSY_MAX_AGE_MS,
  recoveryTimeoutMs = 20000,
  runExclusive = operation => operation(),
  now = () => Date.now(),
}) {
  for (const [name, value] of [
    ['probeTimeoutMs', probeTimeoutMs],
    ['cleanupTimeoutMs', cleanupTimeoutMs],
    ['busyMaxAgeMs', busyMaxAgeMs],
    ['recoveryTimeoutMs', recoveryTimeoutMs],
  ]) {
    if (!Number.isFinite(Number(value)) || Number(value) < 1) {
      throw new TypeError(`deep health ${name} must be positive`);
    }
  }
  if (typeof runExclusive !== 'function') {
    throw new TypeError('deep health runExclusive must be a function');
  }
  const exposureTimeoutMs = Number(probeTimeoutMs) + Number(cleanupTimeoutMs);
  let inflightProbe = null;
  let inflightRecovery = null;
  let lastReadyAt = null;

  async function runProbe(reportFailure) {
    const startedAt = now();
    const deadlineAt = startedAt + probeTimeoutMs;
    const remaining = () => Math.max(1, deadlineAt - now());
    const settlements = [];
    let context;
    let page;
    let stage = 'ensure_browser';
    try {
      const browser = await bounded(
        Promise.resolve().then(() => getBrowser()),
        remaining(),
        stage,
        null,
        settlements,
      );
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
      return { ok: true, code: 'ready', stage: 'complete', durationMs: Math.max(0, now() - startedAt) };
    } catch (error) {
      const result = {
        ok: false,
        code: error?.code || 'deep_probe_failed',
        stage: error?.stage || stage,
        durationMs: Math.max(0, now() - startedAt),
      };
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
        .then(result => {
          if (result?.ok && result.code === 'ready') lastReadyAt = now();
          return result;
        })
        .catch(error => {
          if (error?.code === 'browser_lifecycle_busy') {
            const readyAgeMs = lastReadyAt === null
              ? null
              : Math.max(0, now() - lastReadyAt);
            if (readyAgeMs !== null && readyAgeMs <= Number(busyMaxAgeMs)) {
              return {
                ok: true,
                code: 'busy',
                stage: 'lifecycle',
                readyAgeMs,
                durationMs: Math.max(0, now() - startedAt),
              };
            }
            return {
              ok: false,
              code: 'deep_probe_busy_stale',
              stage: 'lifecycle',
              readyAgeMs,
              durationMs: Math.max(0, now() - startedAt),
            };
          }
          return {
            ok: false,
            code: error?.code || 'deep_probe_failed',
            stage: error?.stage || 'lifecycle',
            durationMs: Math.max(0, now() - startedAt),
          };
        });
      const reported = Promise.race([earlyFailure, underlying]);
      const exposed = bounded(
        reported,
        exposureTimeoutMs,
        'lifecycle_settle',
      ).catch(error => ({
        ok: false,
        code: error?.code || 'deep_probe_failed',
        stage: error?.stage || 'lifecycle_settle',
        durationMs: Math.max(0, now() - startedAt),
      }));
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
          lastReadyAt = null;
          await recoverBrowser();
          // A successful probe from the previous browser generation must not
          // validate the replacement.
          lastReadyAt = null;
          const verification = await probe();
          return verification.code === 'ready'
            ? { ok: true, code: 'recovered' }
            : { ok: false, code: 'recovery_probe_failed', stage: verification.stage };
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

  return { probe, recover };
}
