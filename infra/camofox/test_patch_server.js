const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const patch = path.join(__dirname, 'patch-server.js');
const fixture = `const fs = require('fs');
const crypto = require('crypto');
const reporter = {};
const CONFIG = { nodeEnv: 'production' };
function isProxyError() { return true; }
class StaleRefsError extends Error {}
function log() {}
function safeError(err) {
  if (CONFIG.nodeEnv === 'production') {
    log('error', 'internal error', { error: err.message, stack: err.stack });
    return 'Internal server error';
  }
  return err.message;
}
function sendError(res, err, extraFields = {}) {
  const status = err instanceof StaleRefsError ? 422 : (err.statusCode || 500);
  const body = { error: safeError(err), ...extraFields };
  if (err instanceof StaleRefsError) {
    body.code = 'stale_refs';
    body.ref = err.ref;
  }
  res.status(status).json(body);
}
let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
async function launch() {
    const useVirtualDisplay = !!vdDisplay;
    log('info', 'launching camoufox', {
      virtualDisplay: useVirtualDisplay,
    });
    return {
        enable_cache: true,
        proxy: launchProxy,
    };
}
app.post('/tabs/:tabId/navigate', async (req, res) => {
  try {
    await navigateCurrentPage();
  } catch (navErr) {
    if ((isProxyError(navErr) || isTimeoutError(navErr)) && proxyPool?.canRotateSessions) {
      await rotate();
    } else {
      throw navErr;
    }
  }
});
// Get captured downloads

async function closeBrowser() {
  // Reset native memory baseline so next browser measures from fresh
  reporter.resetNativeMemBaseline();
  _nativeMemBaseline = null;
}
`;

function runPatch(cwd) {
  const result = spawnSync(process.execPath, [patch], {
    cwd,
    encoding: 'utf8',
  });
  assert.strictEqual(result.status, 0, result.stderr);
}

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-camofox-patch-'));
try {
  const serverPath = path.join(temp, 'server.js');
  fs.writeFileSync(serverPath, fixture);
  runPatch(temp);
  const once = fs.readFileSync(serverPath, 'utf8');
  runPatch(temp);
  const twice = fs.readFileSync(serverPath, 'utf8');

  assert.strictEqual(twice, once, 'patch must be idempotent');
  assert.match(once, /authMiddleware\(\)/, 'clip route must require bearer auth');
  assert.match(once, /tab_user_mismatch/, 'clip route must reject another user owner');
  assert.match(
    once,
    /await withTabLock\(req\.params\.tabId,[\s\S]*spartanInvokeObsidianClipperBridge/,
    'bridge and download capture must run under the tab lock',
  );
  assert.match(
    once,
    /await withTabLock\(req\.params\.tabId,[\s\S]*spartanWaitForSingleNewDownload/,
    'download wait must remain inside the tab lock',
  );
  assert.match(
    once,
    /typeof reporter\.resetNativeMemBaseline === 'function'/,
    'native memory reset must be guarded for upstream reporter versions without the method',
  );
  assert.match(once, /navErr\.code = 'proxy_forbidden'/, 'proxy denials must return a semantic code');
  assert.match(once, /err\.code = 'clipper_bridge_timeout'/, 'clipper bridge timeouts must return a semantic code');
  assert.match(once, /if \(err\.code\) return err\.message;/, 'coded errors must keep actionable messages');
  assert.match(once, /body\.code = err\.code/, 'global errors must include semantic codes in the response body');
  console.log('patch-server tests: ok');
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
