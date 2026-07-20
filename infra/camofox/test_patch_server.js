const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const patch = path.join(__dirname, 'patch-server.js');
const fixture = `import fs from 'fs';
import crypto from 'crypto';
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
const MAX_SESSIONS = 8;
const HANDLER_TIMEOUT_MS = 30000;
const TAB_LOCK_TIMEOUT_MS = 35000;
const sessions = new Map();

function getTabLock() {
  return { acquire: async () => {}, release() {} };
}
async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  const lock = getTabLock(tabId);
  await lock.acquire(TAB_LOCK_TIMEOUT_MS);
  try {
    return await withTimeout(operation(), timeoutMs, 'action');
  } finally {
    lock.release();
  }
}

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(\`\${label} timed out after \${ms}ms\`)), ms)
    )
  ]);
}
function requestTimeoutMs() { return HANDLER_TIMEOUT_MS; }
const userConcurrency = new Map();

async function withUserLimit(userId, operation) {
  return await operation();
}

let browser = null;
let browserLaunchPromise = null;
let _browserClosePromise = null;
let spartanTestLaunchFactory = async () => ({
  newContext: async () => ({ pages: () => [] }),
});
function spartanSetTestLaunchFactory(factory) {
  spartanTestLaunchFactory = factory;
  browser = null;
  browserLaunchPromise = null;
}
async function launchBrowserInstance() {
  const value = await spartanTestLaunchFactory();
  browser = value;
  return value;
}
async function restartBrowser(reason) {
  try {
    await closeAllSessions(\`browser_restart:\${reason}\`);
    await closeBrowserFully(reason);
    await ensureBrowser();
  } catch (err) {
    log('error', 'browser restart failed', { error: err.message });
  } finally {
    void reason;
  }
}

function getTotalTabCount() { return 0; }

async function closeBrowserFully(reason) {
  if (_browserClosePromise) return _browserClosePromise;
  _browserClosePromise = Promise.resolve(reason);
  try {
    return await _browserClosePromise;
  } finally {
    _browserClosePromise = null;
  }
}

async function ensureBrowser() {
  if (browser) return browser;
  if (browserLaunchPromise) return browserLaunchPromise;
  const launchTimeoutMs = 10;
  browserLaunchPromise = Promise.race([
    launchBrowserInstance(),
    new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error('Browser launch timeout')), launchTimeoutMs);
      timer.unref?.();
    }),
  ]).finally(() => { browserLaunchPromise = null; });
  return browserLaunchPromise;
}

// Helper to normalize userId to string (JSON body may parse as number)
function normalizeUserId(userId) { return String(userId); }

async function closeSession(userId, session, {
  reason = 'session_closed',
  clearDownloads = true,
  clearLocks = true,
} = {}) {
  if (!session) return;
  const key = normalizeUserId(userId);
  void reason; void clearDownloads; void clearLocks;
  await session.context.close().catch(() => {});
  sessions.delete(key);
}

async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {
  for (const [userId, session] of sessions) {
    await closeSession(userId, session, { reason, clearDownloads, clearLocks });
  }
}
async function getSession(userId, { trace = false } = {}) {
  const key = normalizeUserId(userId);
  let session = sessions.get(key);
  if (!session) {
    const b = await ensureBrowser();
    const context = await b.newContext();
    session = { context, tabGroups: new Map(), trace };
    sessions.set(key, session);
  }
  return session;
}

function attachPopupHandler(page, userId, sessionKey) {
  page.on('popup', (popupPage) => {
    const key = normalizeUserId(userId);
    const currentSession = sessions.get(key);
    if (!currentSession || currentSession._closing) return;

    const popupTabId = fly.makeTabId();
    const popupTabState = createTabState(popupPage);
    attachDownloadListener(popupTabState, popupTabId, log, pluginEvents, key);
    const popupGroup = getTabGroup(currentSession, sessionKey || '__popups__');
    popupGroup.set(popupTabId, popupTabState);
    currentSession.lastAccess = Date.now();
    refreshActiveTabsGauge();
    log('info', 'popup registered as managed tab', { userId: key, tabId: popupTabId, url: popupPage.url() });
    pluginEvents.emit('tab:created', { userId: key, tabId: popupTabId, page: popupPage, url: popupPage.url() });
    attachPopupHandler(popupPage, userId, sessionKey);
  });
}

function pressureHash(value) { return value; }

for (const [key, session] of sessions) {
  if (session._closing) continue;
  if (session.lastAccess < Infinity) void key;
}
setInterval(() => {
  const now = Date.now();
  for (const [userId, session] of sessions) {
    for (const [listItemId, group] of session.tabGroups) {
      if (group.size === 0) {
        session.tabGroups.delete(listItemId);
      }
    }
    // Clean up sessions with zero tabs remaining -- free browser context memory
    if (session.tabGroups.size === 0) {
      session._closing = true;
      log('info', 'session empty after tab reaper, closing', { userId });
      closeSession(userId, session).catch(() => {});
    }
  }
}, 60_000);
setInterval(() => {
  for (const session of sessions.values()) {
    if (session._closing) continue;
    let contextPages;
    try {
      contextPages = session.context.pages();
    } catch (_) {
      continue;
    }
    const registered = new Set();
    for (const group of session.tabGroups.values()) {
      for (const tabState of group.values()) registered.add(tabState.page);
    }
    for (const page of contextPages) {
      if (!registered.has(page)) {
        page.close({ runBeforeUnload: false }).catch(() => {});
      }
    }
  }
}, 60_000);
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
      // Memory admission control (Fly.io only) — reject new sessions when
      // system memory is critically low. 503 tells Fly Proxy to try another machine.

app.post('/sessions/cookies', async (req, res) => {
  try {
    const userId = req.body.userId;
    const sanitized = [];
    const session = await getSession(userId);
    await session.context.addCookies(sanitized);
    res.json({ ok: true });
  } catch (err) {
    handleRouteError(err, req, res);
  }
});

app.post('/tabs', async (req, res) => {
  try {
    const { userId, sessionKey, listItemId, url, trace } = req.body;
    const resolvedSessionKey = sessionKey || listItemId;
    const result = await withTimeout((async () => {
      let session = await getSession(userId, { trace: !!trace });
      const group = getTabGroup(session, resolvedSessionKey);
      const page = await session.context.newPage();
      const tabId = fly.makeTabId();
      let tabState = createTabState(page);
      group.set(tabId, tabState);
      if (url) {
        const urlErr = validateUrl(url);
        if (urlErr) throw new Error(urlErr);
        await page.goto(url);
      }
      return { tabId, url: page.url() };
    })(), requestTimeoutMs(), 'tab create');
    res.json(result);
  } catch (err) {
    log('error', 'tab create failed', { reqId: req.reqId, error: err.message });
    handleRouteError(err, req, res);
  }
});

// Navigate
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

app.post('/tabs/open', async (req, res) => {
  try {
    const { userId, url, listItemId = 'default' } = req.body;
    let session = await getSession(userId);
    let group = getTabGroup(session, listItemId);
    let page = await session.context.newPage();
    const tabId = fly.makeTabId();
    const tabState = createTabState(page);
    group.set(tabId, tabState);
    await page.goto(url);
    res.json({ tabId, url: page.url() });
  } catch (err) {
    log('error', 'openclaw tab open failed', { reqId: req.reqId, error: err.message });
    handleRouteError(err, req, res);
  }
});

// POST /start
app.post('/tabs/:tabId/viewport', async (req, res) => {
  res.json({ ok: true, width: req.body?.width, height: req.body?.height });
});
// Get captured downloads

app.get('/health', (_req, res) => res.json({ ok: true }));

// Active health probe -- detect hung browser even when isConnected() lies
setInterval(async () => {
  const context = await browser.newContext();
  await context.close();
}, 60_000);

// Crash logging

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
  fs.writeFileSync(path.join(temp, 'package.json'), '{"type":"module"}\n');
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
    /app\.post\(\['\/tabs\/:tabId\/spartan\/viewport', '\/tabs\/:tabId\/viewport'\], authMiddleware\(\)/,
    'viewport route must require bearer auth',
  );
  assert.match(once, /app\.post\('\/tabs\/:tabId\/upstream-viewport-disabled'/, 'lying upstream viewport route must be disabled');
  assert.match(once, /upstream_viewport_disabled/, 'renamed upstream viewport route must fail closed');
  assert.doesNotMatch(once, /app\.post\('\/tabs\/:tabId\/viewport', async/, 'unverified upstream viewport route must not keep the public path');
  assert.doesNotMatch(once, /app\.post\('\/tabs\/:tabId\/upstream-viewport-disabled', async/, 'renamed upstream viewport route must not keep the original live body');
  assert.doesNotMatch(once, /width: req\.body\?\.width/, 'renamed upstream viewport route body must be removed');
  assert.doesNotMatch(once, /import \* as spartanChildProcess from 'child_process';/, 'generated server should not keep unused child_process imports');
  assert.doesNotMatch(once, /require\('child_process'\)/, 'generated ESM server must not use CommonJS require');
  assert.match(once, /page\.setViewportSize\(requested\)/, 'viewport route must first try live page viewport resize');
  assert.match(once, /window: spartanLaunchViewportWindow\?\.window/, 'browser relaunch must pass Camoufox launch window');
  assert.match(once, /spartanViewportRelaunchLock/, 'viewport relaunch must be serialized');
  assert.match(once, /spartanRelaunchViewportTab/, 'viewport route must support explicit relaunch');
  assert.match(once, /closeAllSessions\('viewport_relaunch'/, 'viewport relaunch must close existing browser sessions explicitly');
  assert.match(once, /closeBrowserFully\('viewport_relaunch'/, 'viewport relaunch must restart the browser for launch-time window sizing');
  assert.match(once, /await page\.setViewportSize\(requested\);/, 'relaunched pages must set Playwright viewport for screenshot dimensions');
  assert.match(once, /camoufoxLaunchWindow/, 'viewport relaunch response must expose the launch-window strategy');
  assert.doesNotMatch(once, /xdotool/, 'viewport route must not depend on ineffective xdotool resizing');
  assert.match(once, /spartanEvaluateViewport\(page\)/, 'viewport route must return runtime dimensions');
  assert.match(once, /spartanUnsupportedViewportOptions\(payload, req\.body \|\| \{\}\)/, 'unsupported device emulation options must be checked at root and nested viewport levels');
  assert.match(once, /unsupported_viewport_options/, 'unsupported device emulation options must fail explicitly');
  assert.match(once, /Retry with relaunch=true/, 'viewport mismatch should tell callers how to request relaunch mode');
  assert.match(once, /viewport_mismatch/, 'viewport route must fail when runtime dimensions do not match');
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
  assert.match(once, /app\.get\('\/spartan\/health\/deep'/, 'deep health route must be installed');
  assert.match(once, /spartanInternalRequestAuthorized/, 'deep health must require internal authorization');
  assert.strictEqual(
    (once.match(/const SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1;/g) || []).length,
    1,
    'generated server must contain exactly one lifecycle coordinator version',
  );
  assert.strictEqual(
    (once.match(/const SPARTAN_LIFECYCLE_COORDINATOR_REVISION = 2;/g) || []).length,
    1,
    'generated server must contain exactly one narrow coordinator revision',
  );
  assert.match(once, /SPARTAN_MAX_CONCURRENT_CREATES_RAW !== 1/, 'lifecycle capacity must fail closed above one');
  assert.match(
    once,
    /SPARTAN_PAGE_LIFECYCLE_METHODS = Object\.freeze\(\['close', 'goBack', 'goForward', 'goto', 'reload'\]\)/,
    'only page navigation and close should join the global lifecycle lane',
  );
  assert.doesNotMatch(once, /spartanBrowserLifecycle\.run\('user_operation'/, 'ordinary user operations must retain upstream concurrency');
  assert.doesNotMatch(once, /spartanBrowserLifecycle\.run\('tab_operation'/, 'ordinary tab operations must retain upstream concurrency');
  assert.match(once, /spartanBrowserLifecycle\.run\(\s*'viewport_relaunch'/, 'viewport relaunch must join the lifecycle lane explicitly');
  assert.match(once, /spartanTrackLifecyclePromise\(promise\)/, 'timed-out browser work must retain its lease');
  assert.match(once, /spartanTrackLifecyclePromise\(launchBrowserInstance\(\)\)/, 'timed-out browser launch must retain its lease');
  assert.match(once, /spartanClosePromises\.delete\(target\)/, 'a settled close must remain retryable');
  assert.match(once, /SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS/, 'bounded busy health must have a finite real-probe TTL');
  assert.match(once, /ownerDeadlineExceeded/, 'health must distinguish bounded busy from stuck');
  assert.match(once, /browser_lifecycle_busy/, 'bounded busy must be explicit');
  assert.match(once, /spartanBrowserLifecycle\.run\('cookie_import'/, 'cookie context mutation must be atomic');
  assert.match(once, /spartanBrowserLifecycle\.run\('tab_open'/, 'OpenClaw tab creation must be atomic');
  assert.doesNotMatch(once, /spartanCreateWaiters|spartanActiveCreates/, 'legacy create FIFO must be fully migrated');
  assert.doesNotMatch(once, /Active health probe -- detect hung browser/, 'unbounded upstream probe must be removed');
  assert.match(once, /const spartanCreateFence =/, 'tab create must fence late completions');
  assert.match(once, /async function spartanWithCreateLimit\(operation, queueTimeoutMs/, 'tab creates must use the bounded global FIFO limiter');
  assert.match(once, /withTimeout\(spartanWithCreateLimit\(async \(\) =>/, 'create lifecycle must run inside the limiter');
  assert.match(once, /sessions\.get\(spartanCreateFence\.userKey\) === session/, 'late session cleanup must use compare-and-delete');
  assert.match(once, /spartanPagePromise\.then/, 'late pages must be observed for cleanup');
  assert.match(once, /code: 'tab_create_timeout'/, 'tab timeout must be semantic');
  assert.match(once, /stage: spartanCreateFence\.stage/, 'tab failure logs must identify only the stage');
  assert.match(once, /spartanCgroupAdmissionAllowed/, 'Compose memory admission must use cgroup v2');
  assert.match(
    once,
    /if \(sessions\.get\(key\) === session\) sessions\.delete\(key\);/,
    'closing an old session must not delete its same-user replacement',
  );
  assert.match(
    once,
    /const spartanSessionCreatePending =[\s\S]*session\.tabGroups\.size === 0[\s\S]*!spartanSessionCreatePending/,
    'empty-session reaping must preserve only sessions with active creates',
  );
  assert.match(
    once,
    /const spartanGroupCreatePending =[\s\S]*pending\.group === group[\s\S]*group\.size === 0 && !spartanGroupCreatePending/,
    'empty group deletion must preserve only a group with an active create',
  );
  assert.match(
    once,
    /\)\.finally\(\(\) => {[\s\S]*spartanPendingCreates\.delete\(spartanCreateFence\)/,
    'the underlying create promise must release its fence',
  );
  const memoryEviction = once.match(
    /for \(const \[key, session\] of sessions\) {[\s\S]*?if \(session\.lastAccess < Infinity\)/,
  )?.[0] || '';
  assert.doesNotMatch(
    memoryEviction,
    /_spartanPendingCreates/,
    'create fencing must not modify memory-pressure eviction',
  );
  const orphanContextIndex = once.indexOf('let contextPages;');
  const orphanFence = once.slice(
    once.lastIndexOf('if (session._closing) continue;', orphanContextIndex),
    orphanContextIndex,
  );
  assert.match(
    orphanFence,
    /_spartanPendingCreates instanceof Set && session\._spartanPendingCreates\.size > 0/,
    'only the orphan reaper must skip a session with an active create',
  );
  assert.doesNotMatch(
    once,
    /lastAccess[^\n]*requestTimeoutMs\(\)/,
    'reaper fences must not retain garbage because unrelated tabs are active',
  );
  const syntax = spawnSync(process.execPath, ['--check', serverPath], { cwd: temp, encoding: 'utf8' });
  const syntaxLine = Number(syntax.stderr.match(/server\.js:(\d+)/)?.[1] || 1);
  const syntaxContext = once.split('\n')
    .slice(Math.max(0, syntaxLine - 4), syntaxLine + 3)
    .map((line, index) => `${Math.max(1, syntaxLine - 3) + index}: ${line}`)
    .join('\n');
  assert.strictEqual(syntax.status, 0, `${syntax.stderr}\n${syntaxContext}`);
  console.log('patch-server tests: ok');
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
