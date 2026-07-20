import crypto from 'node:crypto';
import fs from 'node:fs';

const DEFAULT_PROBE_TIMEOUT_MS = 8000;
const DEFAULT_CLEANUP_TIMEOUT_MS = 2000;

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

async function bounded(promise, timeoutMs, stage, lateCleanup) {
  let timer;
  let expired = false;
  const tracked = Promise.resolve(promise).then(async value => {
    if (expired && lateCleanup) await lateCleanup(value).catch(() => {});
    return value;
  });
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

async function boundedClose(resource, timeoutMs) {
  if (!resource?.close) return;
  await bounded(
    Promise.resolve().then(() => resource.close()),
    timeoutMs,
    'cleanup',
  ).catch(() => {});
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
  recoveryTimeoutMs = 20000,
  now = () => Date.now(),
}) {
  let inflightProbe = null;
  let inflightRecovery = null;

  async function runProbe() {
    const startedAt = now();
    const deadlineAt = startedAt + probeTimeoutMs;
    const remaining = () => Math.max(1, deadlineAt - now());
    let context;
    let page;
    let stage = 'ensure_browser';
    try {
      const browser = await bounded(Promise.resolve().then(() => getBrowser()), remaining(), stage);
      stage = 'new_context';
      context = await bounded(
        browser.newContext(),
        remaining(),
        stage,
        value => boundedClose(value, cleanupTimeoutMs),
      );
      stage = 'new_page';
      page = await bounded(
        context.newPage(),
        remaining(),
        stage,
        value => boundedClose(value, cleanupTimeoutMs),
      );
      stage = 'goto';
      await bounded(
        page.goto('data:text/html,<title>spartan-camofox-health</title><main id="sentinel">ready</main>', {
          waitUntil: 'domcontentloaded',
          timeout: remaining(),
        }),
        remaining(),
        stage,
      );
      stage = 'evaluate';
      const sentinel = await bounded(
        page.evaluate(() => document.querySelector('#sentinel')?.textContent),
        remaining(),
        stage,
      );
      if (sentinel !== 'ready') {
        const error = new Error('deep browser probe sentinel mismatch');
        error.code = 'deep_probe_sentinel';
        error.stage = stage;
        throw error;
      }
      return { ok: true, code: 'ready', stage: 'complete', durationMs: Math.max(0, now() - startedAt) };
    } catch (error) {
      return {
        ok: false,
        code: error?.code || 'deep_probe_failed',
        stage: error?.stage || stage,
        durationMs: Math.max(0, now() - startedAt),
      };
    } finally {
      await boundedClose(page, cleanupTimeoutMs);
      await boundedClose(context, cleanupTimeoutMs);
    }
  }

  function probe() {
    if (!inflightProbe) {
      inflightProbe = runProbe().finally(() => {
        inflightProbe = null;
      });
    }
    return inflightProbe;
  }

  function recover() {
    if (!inflightRecovery) {
      const recovery = Promise.resolve().then(async () => {
          await recoverBrowser();
          const verification = await probe();
          return verification.ok
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
