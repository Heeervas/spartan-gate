const fs = require('fs');
const { spawnSync } = require('child_process');

const serverPath = process.argv[2] || 'server.js';
const MARKER = '// SPARTAN_CGROUP_PRESSURE_EVICTION_V1';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchMemoryPressure(source) {
  if (source.includes(MARKER)) return source;

  source = replaceOnce(
    source,
    "import { createSpartanDeepHealthController, createSpartanNavigationReadiness, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';\n",
    "import { createSpartanDeepHealthController, createSpartanNavigationReadiness, readSpartanCgroupMemory, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';\n",
    'cgroup memory reader import',
  );
  source = replaceOnce(
    source,
    'threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.90),',
    'threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.75),',
    'admission threshold',
  );

  const startMarker = '// Memory pressure eviction (Fly.io only) — evict oldest session when RAM is high.\n';
  const endMarker = '// Per-tab inactivity reaper — close tabs idle for TAB_INACTIVITY_MS\n';
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  if (start < 0 || end < 0) throw new Error('memory eviction block anchors not found');

  const replacement = `${MARKER}
const SPARTAN_MEMORY_EVICT_RATIO = Number(process.env.SPARTAN_CAMOFOX_MEMORY_EVICT_RATIO || 0.82);
const SPARTAN_MEMORY_RESET_RATIO = Number(process.env.SPARTAN_CAMOFOX_MEMORY_RESET_RATIO || 0.70);
if (
  !Number.isFinite(SPARTAN_MEMORY_EVICT_RATIO)
  || !Number.isFinite(SPARTAN_MEMORY_RESET_RATIO)
  || SPARTAN_MEMORY_RESET_RATIO <= 0
  || SPARTAN_MEMORY_RESET_RATIO >= SPARTAN_MEMORY_EVICT_RATIO
  || SPARTAN_MEMORY_EVICT_RATIO >= 1
) {
  throw new Error('invalid Spartan Camofox memory eviction thresholds');
}
let spartanPressureSamples = 0;
let spartanPressureLatched = false;
let spartanPressureEvictionRunning = false;

setInterval(async () => {
  const memory = readSpartanCgroupMemory();
  if (memory.state !== 'known') return;
  if (memory.ratio < SPARTAN_MEMORY_RESET_RATIO) {
    spartanPressureSamples = 0;
    spartanPressureLatched = false;
    return;
  }
  if (memory.ratio < SPARTAN_MEMORY_EVICT_RATIO) {
    spartanPressureSamples = 0;
    return;
  }
  spartanPressureSamples += 1;
  if (spartanPressureSamples < 2 || spartanPressureLatched || spartanPressureEvictionRunning) return;

  let oldestKey = null;
  let oldestAccess = Infinity;
  for (const [key, session] of sessions) {
    if (session._closing || spartanHasPendingUserCreate(key)) continue;
    if (spartanOperatorSessionLeaseActive(session)) continue;
    if (session.lastAccess < oldestAccess) {
      oldestAccess = session.lastAccess;
      oldestKey = key;
    }
  }
  if (!oldestKey) return;

  const session = sessions.get(oldestKey);
  spartanPressureEvictionRunning = true;
  try {
    await closeSession(oldestKey, session, {
      reason: 'memory_pressure_soft',
      clearDownloads: true,
      clearLocks: true,
    });
    spartanPressureLatched = true;
    log('warn', 'cgroup pressure evicted one browser context', {
      userId: oldestKey,
      usedPct: (memory.ratio * 100).toFixed(1),
      sessions: sessions.size,
    });
  } catch (error) {
    log('warn', 'cgroup pressure eviction retained browser context', {
      userId: oldestKey,
      code: error?.code || 'close_failed',
    });
  } finally {
    spartanPressureEvictionRunning = false;
  }
}, 5_000);

`;
  return source.slice(0, start) + replacement + source.slice(end);
}

if (require.main === module) {
  const original = fs.readFileSync(serverPath, 'utf8');
  const patched = patchMemoryPressure(original);
  const tmpPath = `${serverPath}.spartan-memory-pressure.js`;
  try {
    fs.writeFileSync(tmpPath, patched);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) {
      throw new Error(`patched server syntax check failed: ${checked.stderr || checked.stdout}`);
    }
    fs.renameSync(tmpPath, serverPath);
  } finally {
    fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchMemoryPressure };
