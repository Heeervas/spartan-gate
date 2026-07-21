import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "infra" / "camofox" / "patch-server.js"
SPARTAN_HEALTH = ROOT / "infra" / "camofox" / "spartan-health.mjs"
SPARTAN_LIFECYCLE = ROOT / "infra" / "camofox" / "spartan-lifecycle.mjs"

SERVER_FIXTURE = """\
import crypto from 'crypto';
import fs from 'fs';
const routes = new Map();
const app = {
  get(route, ...handlers) { routes.set(String(route), handlers.at(-1)); },
  post(route, ...handlers) { routes.set(String(route), handlers.at(-1)); },
  delete(route, ...handlers) { routes.set(`DELETE ${String(route)}`, handlers.at(-1)); },
};
const spartanTestState = {
  totalTabs: 16,
  recycleCalls: 0,
  rotateCalls: 0,
  cleanupCalls: 0,
  failNavigation: false,
  lastGroup: null,
};
const MAX_SESSIONS = 8;
const sessions = new Map();
const tabLocks = new Map();
const failuresTotal = { labels() { return { inc() {} }; } };
const MAX_DOWNLOAD_INLINE_BYTES = 1024;
const HANDLER_TIMEOUT_MS = 30000;
const TAB_LOCK_TIMEOUT_MS = 35000;
const SESSION_TIMEOUT_MS = 60000;
const CONFIG = { nodeEnv: 'production' };
function authMiddleware() {}
function normalizeUserId(value) { return value; }
function findTab() { return null; }
function tabNotFoundResponse() {}
function getDownloadsList() { return []; }
function classifyError() { return 'test'; }
function log() {}
function safeError(err) {
  if (CONFIG.nodeEnv === 'production') {
    log('error', 'internal error', { error: err.message, stack: err.stack });
    return 'Internal server error';
  }
  return err.message;
}
class StaleRefsError extends Error {}
function sendError(res, err, extraFields = {}) {
  const status = err instanceof StaleRefsError ? 422 : (err.statusCode || 500);
  const body = { error: safeError(err), ...extraFields };
  if (err instanceof StaleRefsError) {
    body.code = 'stale_refs';
    body.ref = err.ref;
  }
  res.status(status).json(body);
}
async function clearTabDownloads() {}
async function safePageClose() { spartanTestState.cleanupCalls++; }
function refreshTabLockQueueDepth() {}
function refreshActiveTabsGauge() {}
function handleRouteError(err, _req, res) { sendError(res, err); }
function isProxyError() { return true; }
function isTimeoutError() { return true; }
function isDeadContextError() { return false; }
const reporter = {};
let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
const MAX_TABS_PER_SESSION = 4;
const MAX_TABS_GLOBAL = 16;
const proxyPool = { canRotateSessions: true };

function getTabLock(tabId) {
  if (!tabLocks.has(tabId)) {
    tabLocks.set(tabId, { acquire: async () => {}, release() {}, drain() {} });
  }
  return tabLocks.get(tabId);
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
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
    )
  ]);
}

function requestTimeoutMs() { return HANDLER_TIMEOUT_MS; }
const userConcurrency = new Map();

async function withUserLimit(userId, operation) {
  void userId;
  return await operation();
}

let browser = null;
let browserLaunchPromise = null;
let _browserClosePromise = null;
let spartanTestLaunchFactory = async () => ({
  newContext: async () => ({
    pages: () => [],
    addCookies: async () => {},
    close: async () => {},
    newPage: async () => ({
      on() {},
      url: () => 'https://example.invalid/',
      close: async () => {},
      goto: async () => {
        if (spartanTestState.failNavigation) throw new Error('proxy failed');
      },
    }),
  }),
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
    await closeAllSessions(`browser_restart:${reason}`);
    await closeBrowserFully(reason);
    await ensureBrowser();
  } catch (err) {
    log('error', 'browser restart failed', { error: err.message });
  } finally {
    void reason;
  }
}

function getTotalTabCount() { return spartanTestState.totalTabs; }
async function recycleOldestTab() {
  spartanTestState.recycleCalls++;
  return true;
}
async function closeSharedSessionForProxyRetry() {
  spartanTestState.rotateCalls++;
}

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

function destroySession(userId) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return;
  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
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

function reapTimedOutSessions(now = Date.now()) {
  for (const [userId, session] of Array.from(sessions.entries())) {
    if (now - session.lastAccess > SESSION_TIMEOUT_MS) {
      session._closing = true;
      closeSession(userId, session, {
        reason: 'session_timeout', clearDownloads: true, clearLocks: true,
      }).catch(() => {});
    }
  }
}

function evictOldestForMemory() {
  let oldestKey = null;
  let oldestAccess = Infinity;
  for (const [key, session] of sessions) {
        if (session._closing) continue;
        if (session.lastAccess < oldestAccess) {
      oldestAccess = session.lastAccess;
      oldestKey = key;
    }
  }
  if (!oldestKey) return;
  const session = sessions.get(oldestKey);
  session._closing = true;
  closeSession(oldestKey, session, {
    reason: 'memory_pressure', clearDownloads: true, clearLocks: true,
  }).catch(() => {});
}

async function pressureCleanupEmptySessions(closeEmptySessions = true) {
  for (const [userId, session] of Array.from(sessions.entries())) {
      for (const [listItemId, group] of Array.from(session.tabGroups.entries())) {
        if (group.size === 0) session.tabGroups.delete(listItemId);
      }
    if (closeEmptySessions && session.tabGroups.size === 0) {
      session._closing = true;
      await closeSession(userId, session, {
        reason: 'pressure_cleanup_empty_session', clearDownloads: true, clearLocks: true,
      });
    }
  }
}

function reapEmptySessions(now = Date.now()) {
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
      closeSession(userId, session, {
        reason: 'tab_reaper_empty_session', clearDownloads: true, clearLocks: true,
      }).catch(() => {});
    }
  }
}
const emptySessionReaper = setInterval(() => reapEmptySessions(), 60_000);
emptySessionReaper.unref();
function inspectMemoryPressureSessions() {
  for (const [key, session] of sessions) {
    if (session._closing) continue;
    if (session.lastAccess < Infinity) void key;
  }
}
function reapOrphanPages(now = Date.now()) {
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
      if (!registered.has(page)) page.close({ runBeforeUnload: false }).catch(() => {});
    }
  }
}
const orphanPageReaper = setInterval(() => reapOrphanPages(), 60_000);
orphanPageReaper.unref();

function getTabGroup(session, sessionKey) {
  let group = session.tabGroups.get(sessionKey);
  if (!group) {
    group = new Map();
    session.tabGroups.set(sessionKey, group);
  }
  spartanTestState.lastGroup = group;
  return group;
}
const fly = { makeTabId() { return 'tab'; } };
function createTabState(page) { return { page, visitedUrls: new Set() }; }
function attachDownloadListener() {}
const pluginEvents = { emit() {} };
function validateUrl() { return null; }

async function launch(vdDisplay, launchProxy) {
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
      let totalTabs = 0;
      for (const group of session.tabGroups.values()) totalTabs += group.size;
      if (totalTabs >= MAX_TABS_PER_SESSION || getTotalTabCount() >= MAX_TABS_GLOBAL) {
        const recycled = await recycleOldestTab(session, req.reqId, userId);
        if (!recycled) throw Object.assign(new Error('Maximum tabs per session reached'), { statusCode: 429 });
      }
      const group = getTabGroup(session, resolvedSessionKey);
      const page = await session.context.newPage();
      const tabId = fly.makeTabId();
      let tabState = createTabState(page);
      attachDownloadListener(tabState, tabId, log, pluginEvents, userId);
      group.set(tabId, tabState);
      attachPopupHandler(page, userId, resolvedSessionKey);
      if (url) {
        try {
          await page.goto(url);
        } catch (navErr) {
          if ((isProxyError(navErr) || isTimeoutError(navErr)) && proxyPool?.canRotateSessions) {
            await closeSharedSessionForProxyRetry();
          } else {
            throw navErr;
          }
        }
      }
      return { tabId };
    })(), requestTimeoutMs(), 'tab create');
    res.json(result);
  } catch (err) {
    handleRouteError(err, req, res);
  }
});

// Navigate

app.post('/tabs/:tabId/navigate', async (req, res) => {
  const tabId = req.params.tabId;
  try {
    const { userId, url } = req.body;
    await ensureBrowser();
    const targetUrl = url;
    if (!targetUrl) throw new Error('url or macro required');
    const urlErr = validateUrl(targetUrl);
    if (urlErr) throw new Error(urlErr);
    const navigateCurrentPage = async () => {
      await page.goto(targetUrl);
    };
    try {
      await navigateCurrentPage();
    } catch (navErr) {
      if ((isProxyError(navErr) || isTimeoutError(navErr)) && proxyPool?.canRotateSessions) {
        await rotate();
        await navigateCurrentPage();
      } else {
        throw navErr;
      }
    }
    // For Google SERP: skip eager ref building during navigate.
    res.json({ ok: true, tabId, userId });
  } catch (err) {
    handleRouteError(err, req, res);
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

app.delete('/tabs/:tabId', async (req, res) => {
  try {
    const userId = req.query.userId || req.body?.userId;
    const session = sessions.get(normalizeUserId(userId));
    const found = session && findTab(session, req.params.tabId);
    if (found) {
      if (found.tabState.navigateAbort) found.tabState.navigateAbort.abort();
      await clearTabDownloads(found.tabState);
      await safePageClose(found.tabState.page);
      found.group.delete(req.params.tabId);
      { const _l = tabLocks.get(req.params.tabId); if (_l) _l.drain(); tabLocks.delete(req.params.tabId); refreshTabLockQueueDepth(); }
      if (found.group.size === 0) {
        session.tabGroups.delete(found.listItemId);
      }
      refreshActiveTabsGauge();
      log('info', 'tab closed', { reqId: req.reqId, tabId: req.params.tabId, userId });
    }
    res.json({ ok: true });
  } catch (err) {
    log('error', 'tab close failed', { reqId: req.reqId, error: err.message });
    handleRouteError(err, req, res);
  }
});

// Close tab group

async function closeBrowser() {
  // Reset native memory baseline so next browser measures from fresh
  reporter.resetNativeMemBaseline();
  _nativeMemBaseline = null;
}

export {
  routes,
  spartanTestState,
  sessions,
  closeSession,
  destroySession,
  reapTimedOutSessions,
  evictOldestForMemory,
  pressureCleanupEmptySessions,
  reapEmptySessions,
  reapOrphanPages,
  spartanWithCreateLimit,
  spartanWithPendingUserCreate,
  spartanBindPendingCreateSession,
  spartanPendingUserCreateSnapshot,
  spartanBrowserLifecycle,
  spartanGuardPage,
  ensureBrowser,
  spartanSetTestLaunchFactory,
};
"""


class CamofoxPatchServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        shutil.copy2(PATCHER, self.root / "patch-server.cjs")
        shutil.copy2(SPARTAN_HEALTH, self.root / "spartan-health.mjs")
        shutil.copy2(SPARTAN_LIFECYCLE, self.root / "spartan-lifecycle.mjs")
        (self.root / "package.json").write_text('{"type":"module"}\n')
        self.server = self.root / "server.js"
        self.server.write_text(SERVER_FIXTURE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_patcher(self):
        return subprocess.run(
            ["node", "patch-server.cjs"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_applies_complete_authenticated_locked_route_idempotently(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        patched = self.server.read_text()

        self.assertIn("function camofoxAddonPaths()", patched)
        self.assertIn("addons: addonPaths", patched)
        self.assertIn(
            "app.post('/tabs/:tabId/extensions/obsidian-web-clipper/clip', "
            "authMiddleware(), async (req, res) =>",
            patched,
        )
        self.assertIn("spartanFindTabOwner(req.params.tabId)", patched)
        self.assertIn("code: 'tab_user_mismatch'", patched)
        self.assertIn(
            "app.post(['/tabs/:tabId/spartan/viewport', '/tabs/:tabId/viewport'], "
            "authMiddleware(), async (req, res) =>",
            patched,
        )
        self.assertIn("app.post('/tabs/:tabId/upstream-viewport-disabled'", patched)
        self.assertIn("code: 'upstream_viewport_disabled'", patched)
        self.assertNotIn("app.post('/tabs/:tabId/viewport', async", patched)
        self.assertNotIn("app.post('/tabs/:tabId/upstream-viewport-disabled', async", patched)
        self.assertNotIn("width: req.body?.width", patched)
        self.assertNotIn("import * as spartanChildProcess from 'child_process';", patched)
        self.assertNotIn("require('child_process')", patched)
        self.assertIn("page.setViewportSize(requested)", patched)
        self.assertIn("window: spartanLaunchViewportWindow?.window", patched)
        self.assertIn("const spartanViewportRelaunchLock = { tail: Promise.resolve() };", patched)
        self.assertIn("let spartanLaunchViewportWindow = null;", patched)
        self.assertIn("spartanWithViewportRelaunchLock", patched)
        self.assertIn("spartanRelaunchViewportTab", patched)
        self.assertIn("closeAllSessions('viewport_relaunch'", patched)
        self.assertIn("closeBrowserFully('viewport_relaunch'", patched)
        self.assertIn("await page.setViewportSize(requested);", patched)
        self.assertIn("strategy: 'camoufoxLaunchWindow'", patched)
        self.assertIn("relaunchSupported", patched)
        self.assertIn("relaunchRequired", patched)
        self.assertNotIn("xdotool", patched)
        self.assertNotIn("spartanChildProcess.spawnSync", patched)
        self.assertIn("err.code = 'viewport_mismatch'", patched)
        self.assertIn("unsupported_viewport_options", patched)
        self.assertIn("spartanUnsupportedViewportOptions(payload, req.body || {})", patched)
        self.assertIn("Retry with relaunch=true", patched)
        self.assertIn("failuresTotal.labels(classifyError(err), 'viewport').inc();", patched)
        viewport_route = patched.split("app.post(['/tabs/:tabId/spartan/viewport'", 1)[1].split(
            "function spartanDownloadRecordKey",
            1,
        )[0]
        self.assertIn("await withTabLock(req.params.tabId, async () =>", viewport_route)
        self.assertIn("await withTabLock(req.params.tabId, async () =>", viewport_route)
        self.assertIn("strategy = 'camoufoxLaunchWindow'", viewport_route)
        self.assertIn("payload.relaunch === true", viewport_route)
        self.assertIn("err.code = 'clipper_bridge_timeout'", patched)
        self.assertIn("navErr.code = 'proxy_forbidden'", patched)
        self.assertIn("navErr.statusCode = 403", patched)
        self.assertIn("if (err.code) return err.message;", patched)
        self.assertIn("if (err.code && !body.code) body.code = err.code;", patched)
        self.assertIn("await withTabLock(req.params.tabId, async () =>", patched)
        close_route = patched.split("app.delete('/tabs/:tabId'", 1)[1].split("// Close tab group", 1)[0]
        self.assertIn("await withTabLock(req.params.tabId, async () =>", close_route)
        self.assertLess(close_route.index("await withTabLock"), close_route.index("safePageClose"))
        self.assertIn("typeof reporter.resetNativeMemBaseline === 'function'", patched)
        self.assertIn("app.get('/spartan/health/deep'", patched)
        self.assertIn("spartanInternalRequestAuthorized", patched)
        self.assertEqual(
            patched.count("const SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1;"),
            1,
        )
        self.assertEqual(
            patched.count("const SPARTAN_LIFECYCLE_COORDINATOR_REVISION = 2;"),
            1,
        )
        self.assertEqual(
            patched.count("const SPARTAN_QUEUED_CREATE_FENCE_REVISION = 1;"),
            1,
        )
        self.assertIn(
            "const SPARTAN_PENDING_USER_CREATE_LIMIT = MAX_SESSIONS + 1;",
            patched,
        )
        self.assertIn("function spartanPendingUserCreateSnapshot(", patched)
        self.assertIn("pendingCreate.deadlineExceeded", patched)
        self.assertIn("now >= token.deadlineAt", patched)
        self.assertIn("const spartanCreateTimeoutMs = requestTimeoutMs();", patched)
        self.assertIn(
            "spartanDeferUserSessionDestroy(key, session)",
            patched,
        )
        self.assertIn("session_destroyed_during_create", patched)
        self.assertIn("spartanBindPendingCreateSession(userId, spartanCreateFence, session)", patched)
        self.assertIn("session_invalidated_during_create", patched)
        self.assertIn("legacy_tab_open_disabled", patched)
        self.assertIn(
            "spartanWithPendingUserCreate(userId, spartanCreateFence, "
            "() => spartanWithCreateLimit(async () =>",
            patched,
        )
        self.assertIn("SPARTAN_MAX_CONCURRENT_CREATES_RAW !== 1", patched)
        self.assertIn(
            "SPARTAN_PAGE_LIFECYCLE_METHODS = "
            "Object.freeze(['close', 'goBack', 'goForward', 'goto', 'reload'])",
            patched,
        )
        self.assertNotIn("spartanBrowserLifecycle.run('user_operation'", patched)
        self.assertNotIn("spartanBrowserLifecycle.run('tab_operation'", patched)
        self.assertIn("'viewport_relaunch'", patched)
        self.assertIn("spartanTrackLifecyclePromise(promise)", patched)
        self.assertIn("spartanTrackLifecyclePromise(launchBrowserInstance())", patched)
        self.assertIn("spartanClosePromises.delete(target)", patched)
        self.assertIn("SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS", patched)
        self.assertIn("ownerDeadlineExceeded", patched)
        self.assertIn("browser_lifecycle_busy", patched)
        self.assertIn("spartanBrowserLifecycle.run('cookie_import'", patched)
        self.assertNotIn("spartanBrowserLifecycle.run('tab_open'", patched)
        self.assertNotIn("spartanCreateWaiters", patched)
        self.assertNotIn("spartanActiveCreates", patched)
        self.assertNotIn("Active health probe -- detect hung browser", patched)
        self.assertIn("const spartanCreateFence =", patched)
        self.assertIn("sessions.get(spartanCreateFence.userKey) === session", patched)
        self.assertIn("spartanPagePromise.then", patched)
        self.assertIn("code: 'tab_create_timeout'", patched)
        self.assertIn("stage: spartanCreateFence.stage", patched)
        self.assertIn("spartanCgroupAdmissionAllowed", patched)
        self.assertIn("if (sessions.get(key) === session) sessions.delete(key);", patched)
        self.assertIn(
            "const spartanSessionCreatePending = "
            "spartanHasPendingUserCreate(userId)",
            patched,
        )
        self.assertIn(
            "const spartanGroupCreatePending = "
            "spartanHasPendingUserCreate(userId)",
            patched,
        )
        self.assertIn(
            "session._closing || spartanHasPendingUserCreate(key)",
            patched,
        )
        self.assertGreaterEqual(
            patched.count("&& !spartanHasPendingUserCreate(userId)"),
            2,
        )
        self.assertIn("spartanPendingCreates.delete(spartanCreateFence)", patched)
        self.assertNotIn(
            "now - session.lastAccess >= requestTimeoutMs() + 5000",
            patched,
        )
        memory_eviction = patched.split(
            "function inspectMemoryPressureSessions()", 1
        )[1].split("function reapOrphanPages", 1)[0]
        self.assertNotIn("_spartanPendingCreates", memory_eviction)
        orphan_reaper = patched.split(
            "function reapOrphanPages", 1
        )[1].split("const orphanPageReaper", 1)[0]
        self.assertIn(
            "session._spartanPendingCreates instanceof Set"
            " && session._spartanPendingCreates.size > 0",
            orphan_reaper,
        )

        syntax = subprocess.run(
            ["node", "--check", "server.js"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        second = self.run_patcher()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.server.read_text(), patched)

    def test_rejects_lifecycle_revision_one_mixed_with_readiness_v2(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        final = self.server.read_text()

        def replace_once(value, old, new):
            self.assertEqual(value.count(old), 1, old[:80])
            return value.replace(old, new, 1)

        legacy = replace_once(
            final,
            "const SPARTAN_LIFECYCLE_COORDINATOR_REVISION = 2;\n",
            "",
        )
        legacy = replace_once(
            legacy,
            "const SPARTAN_PAGE_LIFECYCLE_METHODS = "
            "Object.freeze(['close', 'goBack', 'goForward', 'goto', 'reload']);",
            "const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close']);",
        )
        legacy = replace_once(
            legacy,
            "spartanTrackLifecyclePromise(launchBrowserInstance()),",
            "launchBrowserInstance(),",
        )
        legacy = replace_once(
            legacy,
            """    if (methodName === 'close') {
      spartanClosePromises.set(target, running);
      const clearClosePromise = () => {
        if (spartanClosePromises.get(target) === running) spartanClosePromises.delete(target);
      };
      running.then(clearClosePromise, clearClosePromise);
    }""",
            "    if (methodName === 'close') spartanClosePromises.set(target, running);",
        )
        legacy = replace_once(
            legacy,
            """    ).catch(error => {
      const expectedRecoveryAbort = error?.code === 'browser_lifecycle_invalidated'
        || error?.code === 'browser_lifecycle_recovering';
      if (!expectedRecoveryAbort) {
        spartanBrowserLifecycle.markDegraded('session_close_failed');
      }
      throw error;
    });""",
            """    ).catch(error => {
      spartanBrowserLifecycle.markDegraded('session_close_failed');
      throw error;
    });""",
        )
        legacy = replace_once(
            legacy,
            "const SPARTAN_DEEP_BUSY_MAX_AGE_MS = "
            "Number(process.env.SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS || 45000);\n",
            "",
        )
        legacy = replace_once(
            legacy,
            "  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n",
            "",
        )
        legacy = replace_once(
            legacy,
            """async function spartanRelaunchViewportTab(options) {
  const { userId, sessionKey, tabId, tabState, requested, effective, timeoutMs } = options;
  if (!spartanBrowserLifecycle.hasLease()) {
    const lifecycleTimeoutMs = Number.isFinite(Number(timeoutMs)) && Number(timeoutMs) > 0
      ? Number(timeoutMs)
      : requestTimeoutMs();
    return await spartanBrowserLifecycle.run(
      'viewport_relaunch',
      () => spartanRelaunchViewportTab(options),
      {
        priority: 'foreground',
        waitTimeoutMs: Math.max(1, lifecycleTimeoutMs - 1000),
        deadlineAt: Date.now() + lifecycleTimeoutMs,
      },
    );
  }""",
            "async function spartanRelaunchViewportTab("
            "{ userId, sessionKey, tabId, tabState, requested, effective, timeoutMs }) {",
        )
        legacy = replace_once(
            legacy,
            "async function withUserLimit(userId, operation) {",
            """async function withUserLimit(userId, operation) {
  if (!spartanBrowserLifecycle.hasLease()) {
    const lifecycleTimeoutMs = requestTimeoutMs();
    return await spartanBrowserLifecycle.run('user_operation', () => withUserLimit(userId, operation), {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, lifecycleTimeoutMs - 1000),
      deadlineAt: Date.now() + lifecycleTimeoutMs,
    });
  }""",
        )
        legacy = replace_once(
            legacy,
            """async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  const lock = getTabLock(tabId);
  await lock.acquire(TAB_LOCK_TIMEOUT_MS);
  try {
    return await withTimeout(operation(), timeoutMs, 'action');
  } finally {
    lock.release();
  }""",
            """async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  if (spartanBrowserLifecycle.hasLease()) {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }
  const underlying = spartanBrowserLifecycle.run('tab_operation', async () => {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }, {
    priority: 'foreground',
    waitTimeoutMs: Math.max(1, Number(timeoutMs) - 1000),
    deadlineAt: Date.now() + Number(timeoutMs),
  });
  return await withTimeout(underlying, timeoutMs, 'action');""",
        )
        self.server.write_text(legacy)

        migrated = self.run_patcher()

        self.assertNotEqual(migrated.returncode, 0)
        self.assertIn("expected one Camofox lifecycle", migrated.stderr)
        self.assertEqual(self.server.read_text(), legacy)

    def test_rejects_queued_create_revision_mixed_with_readiness_v2(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        final = self.server.read_text()

        def replace_once(value, old, new):
            self.assertEqual(value.count(old), 1, old[:80])
            return value.replace(old, new, 1)

        revision_two = replace_once(
            final,
            "const SPARTAN_QUEUED_CREATE_FENCE_REVISION = 1;\n",
            "",
        )
        registry_start = revision_two.index(
            "\nconst SPARTAN_PENDING_USER_CREATE_LIMIT = MAX_SESSIONS + 1;"
        )
        registry_end = revision_two.index(
            "const spartanGuardedBrowsers = new WeakSet();",
            registry_start,
        )
        revision_two = revision_two[:registry_start] + revision_two[registry_end:]
        revision_two = replace_once(
            revision_two,
            """const spartanCreateTimeoutMs = requestTimeoutMs();
    const spartanCreateDeadlineAt = Date.now() + spartanCreateTimeoutMs;
    const result = await withTimeout(spartanWithPendingUserCreate(userId, spartanCreateFence, () => spartanWithCreateLimit(async () => {""",
            "const result = await withTimeout(spartanWithCreateLimit(async () => {",
        )
        revision_two = replace_once(
            revision_two,
            "    }, Math.max(1, spartanCreateDeadlineAt - Date.now() - 1000)"
            ").finally(() => {",
            "    }, Math.max(1, requestTimeoutMs() - 1000)).finally(() => {",
        )
        revision_two = replace_once(
            revision_two,
            "    }), spartanCreateDeadlineAt), "
            "spartanCreateTimeoutMs, 'tab create');",
            "    }), requestTimeoutMs(), 'tab create');",
        )
        revision_two = replace_once(
            revision_two,
            """function destroySession(userId) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return;
  if (spartanDeferUserSessionDestroy(key, session)) {
    log('warn', 'deferring dead session destroy until active create settles', { userId: key });
    return;
  }
  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
}""",
            """function destroySession(userId) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return;
  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
}""",
        )
        revision_two = replace_once(
            revision_two,
            "spartanBindPendingCreateSession(userId, spartanCreateFence, session);",
            "spartanCreateFence.session = session;",
        )
        revision_two = replace_once(
            revision_two,
            """const spartanSessionCreatePending = spartanHasPendingUserCreate(userId)
      || (
        session._spartanPendingCreates instanceof Set
        && session._spartanPendingCreates.size > 0
      );""",
            """const spartanSessionCreatePending = session._spartanPendingCreates instanceof Set
      && session._spartanPendingCreates.size > 0;""",
        )
        revision_two = replace_once(
            revision_two,
            """const spartanGroupCreatePending = spartanHasPendingUserCreate(userId)
        || (
          session._spartanPendingCreates instanceof Set
          && [...session._spartanPendingCreates].some(pending => pending.group === group)
        );""",
            """const spartanGroupCreatePending = session._spartanPendingCreates instanceof Set
        && [...session._spartanPendingCreates].some(pending => pending.group === group);""",
        )
        revision_two = replace_once(
            revision_two,
            """  if (
    reason === 'api_delete_session'
    && spartanHasPendingUserCreate(userId)
  ) {
    throw spartanBrowserLifecycleError(
      'session_create_pending',
      'session has pending tab creation',
    );
  }
""",
            "",
        )
        revision_two = replace_once(
            revision_two,
            "    const pendingCreate = spartanPendingUserCreateSnapshot();\n",
            "",
        )
        revision_two = replace_once(
            revision_two,
            "if (pendingCreate.deadlineExceeded || "
            "lifecycle.ownerDeadlineExceeded || fallbackDeadlineExceeded) {",
            "if (lifecycle.ownerDeadlineExceeded || fallbackDeadlineExceeded) {",
        )
        revision_two = replace_once(
            revision_two,
            """// Spartan queued-create fence: session timeout.
    if (
      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
    ) {""",
            "if (now - session.lastAccess > SESSION_TIMEOUT_MS) {",
        )
        revision_two = replace_once(
            revision_two,
            """// Spartan queued-create fence: memory pressure.
      for (const [key, session] of sessions) {
        if (session._closing || spartanHasPendingUserCreate(key)) continue;""",
            """for (const [key, session] of sessions) {
        if (session._closing) continue;""",
        )
        revision_two = replace_once(
            revision_two,
            """for (const [userId, session] of Array.from(sessions.entries())) {
      // Spartan queued-create fence: pressure cleanup.
      if (spartanHasPendingUserCreate(userId)) continue;
      for (const [listItemId, group] of Array.from(session.tabGroups.entries())) {""",
            """for (const [userId, session] of Array.from(sessions.entries())) {
      for (const [listItemId, group] of Array.from(session.tabGroups.entries())) {""",
        )
        revision_two = replace_once(
            revision_two,
            """if (
        closeEmptySessions
        && session.tabGroups.size === 0
        && !spartanHasPendingUserCreate(userId)
      ) {""",
            "if (closeEmptySessions && session.tabGroups.size === 0) {",
        )
        legacy_open_start = "app.post('/tabs/open', async (req, res) => {"
        legacy_open_end = "\n});\n\n// POST /start"
        fixture_open_start = SERVER_FIXTURE.index(legacy_open_start)
        fixture_open_end = SERVER_FIXTURE.index(
            legacy_open_end,
            fixture_open_start,
        )
        open_route_body = SERVER_FIXTURE[
            fixture_open_start + len(legacy_open_start):fixture_open_end
        ]
        indented_open_route_body = "\n".join(
            f"    {line}" if line else line
            for line in open_route_body.split("\n")
        )
        coordinated_open_route = (
            """app.post('/tabs/open', async (req, res) => {
  try {
    return await spartanBrowserLifecycle.run('tab_open', async () => {"""
            + indented_open_route_body
            + """
    }, {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, requestTimeoutMs() - 1000),
      deadlineAt: Date.now() + requestTimeoutMs(),
    });
  } catch (err) {
    log('error', 'openclaw tab lifecycle admission failed', {
      reqId: req.reqId,
      code: err?.code || 'browser_lifecycle_failed',
    });
    handleRouteError(err, req, res);
  }
});"""
        )
        revision_two = replace_once(
            revision_two,
            """app.post('/tabs/open', (_req, res) => {
  return res.status(410).json({
    error: 'Deprecated endpoint disabled; use POST /tabs',
    code: 'legacy_tab_open_disabled',
  });
});""",
            coordinated_open_route,
        )
        self.server.write_text(revision_two)

        migrated = self.run_patcher()

        self.assertNotEqual(migrated.returncode, 0)
        self.assertIn("readiness revision 2 marker is incomplete", migrated.stderr)
        self.assertEqual(self.server.read_text(), revision_two)

    def test_rejects_invalid_global_create_limits(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        for invalid in ("0", "2", "9", "not-a-number"):
            env = os.environ.copy()
            env["SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES"] = invalid
            result = subprocess.run(
                ["node", "server.js"],
                cwd=self.root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, invalid)
            self.assertIn("must be exactly 1", result.stderr)

    def test_session_reaper_threshold_and_compare_delete_are_behavioral(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        runner = self.root / "reaper-runtime.mjs"
        runner.write_text(
            """import assert from 'node:assert/strict';
import { sessions, closeSession, destroySession, reapTimedOutSessions, evictOldestForMemory, pressureCleanupEmptySessions, reapEmptySessions, reapOrphanPages, spartanWithCreateLimit, spartanWithPendingUserCreate, spartanBindPendingCreateSession, spartanPendingUserCreateSnapshot, spartanBrowserLifecycle, spartanGuardPage, ensureBrowser, spartanSetTestLaunchFactory } from './server.js';

let releaseOldContext;
const oldContextClosed = new Promise(resolve => { releaseOldContext = resolve; });
const oldSession = {
  lastAccess: 1_000,
  tabGroups: new Map(),
  context: { close: () => oldContextClosed },
};
const replacement = {
  lastAccess: 2_000,
  tabGroups: new Map([['live', new Map([['tab', {}]])]]),
  context: { close: async () => {} },
};

sessions.set('same-user', oldSession);
const closing = closeSession('same-user', oldSession);
sessions.set('same-user', replacement);
releaseOldContext();
await closing;
assert.equal(sessions.get('same-user'), replacement);

let reaperCloseCalls = 0;
let releaseReaperClose;
const reaperCloseGate = new Promise(resolve => { releaseReaperClose = resolve; });
const pendingGroup = new Map();
const pendingFence = { group: pendingGroup };
const empty = {
  lastAccess: 10_000,
  tabGroups: new Map([['pending', pendingGroup]]),
  _spartanPendingCreates: new Set([pendingFence]),
  context: {
    close: async () => { reaperCloseCalls++; await reaperCloseGate; },
    pages: () => [],
  },
};
sessions.clear();
sessions.set('reap-user', empty);
reapEmptySessions(44_999);
assert.equal(empty._closing, undefined);
assert.equal(empty.tabGroups.has('pending'), true);
assert.equal(reaperCloseCalls, 0);
empty._spartanPendingCreates.clear();
reapEmptySessions(45_000);
assert.equal(empty._closing, true);
assert.equal(empty.tabGroups.has('pending'), false);
await new Promise(resolve => setImmediate(resolve));
assert.equal(reaperCloseCalls, 1);
reapEmptySessions(105_000);
await new Promise(resolve => setImmediate(resolve));
assert.equal(reaperCloseCalls, 1);
releaseReaperClose();
await new Promise(resolve => setImmediate(resolve));

let queuedCreateCloseCalls = 0;
let releaseQueuedCreateA;
let releaseQueuedCreateB;
const queuedCreateGateA = new Promise(resolve => { releaseQueuedCreateA = resolve; });
const queuedCreateGateB = new Promise(resolve => { releaseQueuedCreateB = resolve; });
const queuedCreateSession = {
  lastAccess: 20_000,
  tabGroups: new Map([['queued-empty', new Map()]]),
  context: {
    close: async () => { queuedCreateCloseCalls++; },
    pages: () => [],
  },
};
sessions.clear();
sessions.set('queued-create-user', queuedCreateSession);
const queuedCreateA = spartanWithPendingUserCreate(
  'queued-create-user',
  {},
  async () => { await queuedCreateGateA; },
);
const queuedCreateB = spartanWithPendingUserCreate(
  'queued-create-user',
  {},
  async () => { await queuedCreateGateB; },
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(spartanPendingUserCreateSnapshot().count, 2);
assert.equal(spartanPendingUserCreateSnapshot(Date.now() + 30_001).deadlineExceeded, true);
reapEmptySessions(80_000);
reapOrphanPages(80_000);
await new Promise(resolve => setImmediate(resolve));
assert.equal(queuedCreateSession._closing, undefined);
assert.equal(queuedCreateSession.tabGroups.has('queued-empty'), true);
assert.equal(queuedCreateCloseCalls, 0);
releaseQueuedCreateA();
await queuedCreateA;
assert.equal(spartanPendingUserCreateSnapshot().count, 1);
reapEmptySessions(80_000);
assert.equal(queuedCreateSession._closing, undefined);
releaseQueuedCreateB();
await queuedCreateB;
assert.equal(spartanPendingUserCreateSnapshot().count, 0);
reapEmptySessions(80_000);
await new Promise(resolve => setImmediate(resolve));
assert.equal(queuedCreateSession._closing, true);
assert.equal(queuedCreateSession.tabGroups.has('queued-empty'), false);
assert.equal(queuedCreateCloseCalls, 1);
let releaseLongDeadlineCreate;
const longDeadlineGate = new Promise(resolve => { releaseLongDeadlineCreate = resolve; });
const longDeadlineStartedAt = Date.now();
const longDeadlineCreate = spartanWithPendingUserCreate(
  'long-deadline',
  {},
  async () => { await longDeadlineGate; },
  longDeadlineStartedAt + 180_000,
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(
  spartanPendingUserCreateSnapshot(longDeadlineStartedAt + 30_001).deadlineExceeded,
  false,
);
assert.equal(
  spartanPendingUserCreateSnapshot(longDeadlineStartedAt + 180_001).deadlineExceeded,
  true,
);
releaseLongDeadlineCreate();
await longDeadlineCreate;
await assert.rejects(
  spartanWithPendingUserCreate('invalid-deadline', {}, async () => {}, Number.NaN),
  /positive finite timestamp/,
);


const boundedReleases = [];
const boundedCreates = Array.from({ length: 9 }, (_, index) =>
  spartanWithPendingUserCreate(
    `bounded-${index}`,
    {},
    () => new Promise(resolve => { boundedReleases[index] = resolve; }),
  )
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(spartanPendingUserCreateSnapshot().count, 9);
await assert.rejects(
  spartanWithPendingUserCreate('bounded-overflow', {}, async () => {}),
  error => error?.code === 'browser_lifecycle_queue_full',
);
assert.equal(spartanPendingUserCreateSnapshot().count, 9);
for (const release of boundedReleases) release();
await Promise.all(boundedCreates);
assert.equal(spartanPendingUserCreateSnapshot().count, 0);

let releaseUnderlyingCreate;
const underlyingCreateGate = new Promise(resolve => { releaseUnderlyingCreate = resolve; });
const underlyingCreate = spartanWithPendingUserCreate(
  'underlying-timeout',
  {},
  async () => { await underlyingCreateGate; },
);
await assert.rejects(
  Promise.race([
    underlyingCreate,
    new Promise((_, reject) => setTimeout(() => reject(new Error('visible timeout')), 10)),
  ]),
  /visible timeout/,
);
assert.equal(spartanPendingUserCreateSnapshot().count, 1);
releaseUnderlyingCreate();
await underlyingCreate;
assert.equal(spartanPendingUserCreateSnapshot().count, 0);

for (const [name, invoke] of [
  ['timeout', () => reapTimedOutSessions(90_001)],
  ['memory', () => evictOldestForMemory()],
  ['pressure', () => pressureCleanupEmptySessions(true)],
]) {
  let automaticCloseCalls = 0;
  let releaseAutomaticCreate;
  const automaticGate = new Promise(resolve => { releaseAutomaticCreate = resolve; });
  const automaticSession = {
    lastAccess: 0,
    tabGroups: new Map(),
    context: {
      close: async () => { automaticCloseCalls++; },
      pages: () => [],
    },
  };
  sessions.clear();
  sessions.set(`automatic-${name}`, automaticSession);
  const pendingAutomaticCreate = spartanWithPendingUserCreate(
    `automatic-${name}`,
    {},
    async () => { await automaticGate; },
  );
  await new Promise(resolve => setImmediate(resolve));
  await invoke();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(automaticSession._closing, undefined, name);
  assert.equal(automaticCloseCalls, 0, name);
  releaseAutomaticCreate();
  await pendingAutomaticCreate;
  await invoke();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(automaticSession._closing, true, name);
  assert.equal(automaticCloseCalls, 1, name);
}

let orphanCloseCalls = 0;
const registeredPage = {};
const orphanPage = { close: async () => { orphanCloseCalls++; } };
const orphanSession = {
  lastAccess: Date.now(),
  tabGroups: new Map([['live', new Map([['tab', { page: registeredPage }]])]]),
  _spartanPendingCreates: new Set([{}]),
  context: {
    close: async () => {},
    pages: () => [registeredPage, orphanPage],
  },
};
sessions.clear();
sessions.set('orphan-user', orphanSession);
reapOrphanPages(44_999);
assert.equal(orphanCloseCalls, 0);
delete orphanSession._spartanPendingCreates;
reapOrphanPages(45_000);
await new Promise(resolve => setImmediate(resolve));
assert.equal(orphanCloseCalls, 1);

let limiterActive = 0;
let limiterPeak = 0;
let releaseFirst;
const firstGate = new Promise(resolve => { releaseFirst = resolve; });
const limitedOperation = async (gate = null) => {
  limiterActive++;
  limiterPeak = Math.max(limiterPeak, limiterActive);
  if (gate) await gate;
  await new Promise(resolve => setImmediate(resolve));
  limiterActive--;
};
const firstLimited = spartanWithCreateLimit(() => limitedOperation(firstGate));
await new Promise(resolve => setImmediate(resolve));
const secondLimited = spartanWithCreateLimit(() => limitedOperation());
await new Promise(resolve => setImmediate(resolve));
assert.equal(limiterActive, 1);
assert.equal(limiterPeak, 1);
releaseFirst();
await Promise.all([firstLimited, secondLimited]);
assert.equal(limiterActive, 0);
assert.equal(limiterPeak, 1);

let afterErrorRan = false;
let releaseError;
const errorGate = new Promise(resolve => { releaseError = resolve; });
const failingLimited = spartanWithCreateLimit(async () => {
  await errorGate;
  throw new Error('expected limiter failure');
});
await new Promise(resolve => setImmediate(resolve));
const afterError = spartanWithCreateLimit(async () => { afterErrorRan = true; });
releaseError();
await assert.rejects(failingLimited, /expected limiter failure/);
await afterError;
assert.equal(afterErrorRan, true);

let releaseTimeout;
const timeoutGate = new Promise(resolve => { releaseTimeout = resolve; });
const timeoutHolder = spartanWithCreateLimit(async () => {
  await timeoutGate;
});
await new Promise(resolve => setImmediate(resolve));
let expiredRan = false;
const expiredWaiter = spartanWithPendingUserCreate(
  'queue-timeout',
  {},
  () => spartanWithCreateLimit(async () => { expiredRan = true; }, 10),
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(spartanPendingUserCreateSnapshot().count, 1);
const queueTimerKeepalive = setTimeout(() => {}, 1000);
await assert.rejects(expiredWaiter, error => error?.code === 'browser_lifecycle_queue_timeout');
clearTimeout(queueTimerKeepalive);
assert.equal(spartanPendingUserCreateSnapshot().count, 0);
assert.equal(expiredRan, false);
releaseTimeout();
await timeoutHolder;

let releaseOverflow;
const overflowGate = new Promise(resolve => { releaseOverflow = resolve; });
const overflowHolder = spartanWithCreateLimit(async () => {
  await overflowGate;
});
await new Promise(resolve => setImmediate(resolve));
let overflowRuns = 0;
const queuedAtCapacity = Array.from({ length: 8 }, () =>
  spartanWithCreateLimit(async () => { overflowRuns++; }, 1000)
);
await new Promise(resolve => setImmediate(resolve));
await assert.rejects(
  spartanWithCreateLimit(async () => {}, 1000),
  error => error?.code === 'browser_lifecycle_queue_full',
);
releaseOverflow();
await overflowHolder;
await Promise.all(queuedAtCapacity);
assert.equal(overflowRuns, 8);

let releaseRecoveryClose;
const recoveryCloseGate = new Promise(resolve => { releaseRecoveryClose = resolve; });
const recoverySession = {
  tabGroups: new Map(),
  context: { close: async () => { await recoveryCloseGate; } },
};
sessions.set('recovery-close', recoverySession);
const interruptedClose = closeSession('recovery-close', recoverySession);
await new Promise(resolve => setImmediate(resolve));
let recoveryQueuedRan = false;
const queuedDuringRecovery = spartanWithPendingUserCreate(
  'recovery-queued',
  {},
  () => spartanWithCreateLimit(async () => { recoveryQueuedRan = true; }),
);
const queuedDuringRecoveryRejected = assert.rejects(
  queuedDuringRecovery,
  error => error?.code === 'browser_lifecycle_invalidated',
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(spartanPendingUserCreateSnapshot().count, 1);
const coordinatedRecovery = spartanBrowserLifecycle.recover(async () => {});
await queuedDuringRecoveryRejected;
assert.equal(spartanPendingUserCreateSnapshot().count, 0);
assert.equal(recoveryQueuedRan, false);
releaseRecoveryClose();
await assert.rejects(
  interruptedClose,
  error => error?.code === 'browser_lifecycle_invalidated',
);
await coordinatedRecovery;
assert.equal(spartanBrowserLifecycle.snapshot().degradedCode, null);

let explicitCloseCalls = 0;
let releaseExplicitCreate;
const explicitCreateGate = new Promise(resolve => { releaseExplicitCreate = resolve; });
const explicitSession = {
  tabGroups: new Map(),
  context: { close: async () => { explicitCloseCalls++; } },
};
sessions.set('explicit-pending', explicitSession);
const explicitPending = spartanWithPendingUserCreate(
  'explicit-pending',
  {},
  async () => { await explicitCreateGate; },
);
await assert.rejects(
  closeSession('explicit-pending', explicitSession, { reason: 'api_delete_session' }),
  error => error?.code === 'session_create_pending',
);
assert.equal(explicitCloseCalls, 0);
releaseExplicitCreate();
await explicitPending;
await closeSession('explicit-pending', explicitSession, { reason: 'api_delete_session' });
let deferredDestroyCloseCalls = 0;
let bindRaceCloseCalls = 0;
let resolveBindRaceSession;
const bindRaceSessionGate = new Promise(resolve => { resolveBindRaceSession = resolve; });
const bindRaceSession = {
  tabGroups: new Map(),
  context: { close: async () => { bindRaceCloseCalls++; } },
};
const bindRaceFence = { active: true, session: null, userKey: null };
sessions.set('bind-race', bindRaceSession);
const bindRaceCreate = spartanWithPendingUserCreate(
  'bind-race',
  bindRaceFence,
  async () => {
    const returnedSession = await bindRaceSessionGate;
    spartanBindPendingCreateSession('bind-race', bindRaceFence, returnedSession);
    return 'must-not-succeed';
  },
);
await new Promise(resolve => setImmediate(resolve));
resolveBindRaceSession(bindRaceSession);
destroySession('bind-race');
await assert.rejects(
  bindRaceCreate,
  error => error?.code === 'session_invalidated_during_create',
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(bindRaceFence.session, bindRaceSession);
assert.equal(bindRaceFence.active, false);
assert.equal(bindRaceSession._closing, true);
assert.equal(bindRaceCloseCalls, 1);
assert.equal(sessions.has('bind-race'), false);

let releaseDeferredDestroyCreate;
const deferredDestroyGate = new Promise(resolve => { releaseDeferredDestroyCreate = resolve; });
const deferredDestroySession = {
  tabGroups: new Map(),
  context: { close: async () => { deferredDestroyCloseCalls++; } },
};
const deferredDestroyReplacement = {
  tabGroups: new Map([['live', new Map([['tab', {}]])]]),
  context: { close: async () => {} },
};
const deferredDestroyFence = { active: true, session: null };
sessions.set('deferred-destroy', deferredDestroySession);
const deferredDestroyCreate = spartanWithPendingUserCreate(
  'deferred-destroy',
  deferredDestroyFence,
  async () => {
    deferredDestroyFence.session = deferredDestroySession;
    await deferredDestroyGate;
    return 'must-not-succeed';
  },
);
await new Promise(resolve => setImmediate(resolve));
destroySession('deferred-destroy');
assert.equal(sessions.has('deferred-destroy'), false);
assert.equal(deferredDestroySession._closing, true);
assert.equal(deferredDestroyFence.active, false);
assert.equal(deferredDestroyCloseCalls, 0);
sessions.set('deferred-destroy', deferredDestroyReplacement);
releaseDeferredDestroyCreate();
await assert.rejects(
  deferredDestroyCreate,
  error => error?.code === 'session_destroyed_during_create',
);
await new Promise(resolve => setImmediate(resolve));
assert.equal(deferredDestroyCloseCalls, 1);
assert.equal(deferredDestroySession._spartanDeferredDestroyTokens, undefined);
assert.equal(sessions.get('deferred-destroy'), deferredDestroyReplacement);

assert.equal(explicitCloseCalls, 1);

const alreadyClosedSession = {
  tabGroups: new Map(),
  context: { close: async () => { throw new Error('Target page, context or browser has been closed'); } },
};
sessions.set('already-closed', alreadyClosedSession);
await closeSession('already-closed', alreadyClosedSession);
assert.equal(sessions.has('already-closed'), false);

let guardedCloseCalls = 0;
const guardedPage = spartanGuardPage({
  close: async () => {
    guardedCloseCalls++;
    if (guardedCloseCalls === 1) throw new Error('first close failed');
  },
});
const firstGuardedClose = guardedPage.close();
const sameGuardedClose = guardedPage.close();
assert.equal(firstGuardedClose, sameGuardedClose);
const firstCloseResults = await Promise.allSettled([firstGuardedClose, sameGuardedClose]);
assert.deepEqual(firstCloseResults.map(result => result.status), ['rejected', 'rejected']);
assert.equal(guardedCloseCalls, 1);
await guardedPage.close();
assert.equal(guardedCloseCalls, 2);

let resolveLateLaunch;
spartanSetTestLaunchFactory(() => new Promise(resolve => { resolveLateLaunch = resolve; }));
let launchSettled = false;
const timedLaunch = ensureBrowser().then(
  value => { launchSettled = true; return value; },
  error => { launchSettled = true; throw error; },
);
await new Promise(resolve => setTimeout(resolve, 25));
assert.equal(launchSettled, false);
assert.equal(spartanBrowserLifecycle.snapshot().ownerKind, 'browser_ensure');
let afterLaunchRan = false;
const afterLateLaunch = spartanWithCreateLimit(async () => { afterLaunchRan = true; }, 1000);
await new Promise(resolve => setImmediate(resolve));
assert.equal(afterLaunchRan, false);
resolveLateLaunch({ newContext: async () => ({ pages: () => [] }) });
await assert.rejects(timedLaunch, /Browser launch timeout/);
await afterLateLaunch;
assert.equal(afterLaunchRan, true);

const failedCloseSession = {
  tabGroups: new Map(),
  context: { close: async () => { throw new Error('unexpected close failure'); } },
};
sessions.set('failed-close', failedCloseSession);
await assert.rejects(closeSession('failed-close', failedCloseSession), /unexpected close failure/);
await assert.rejects(
  spartanWithCreateLimit(async () => {}),
  error => error?.code === 'browser_lifecycle_degraded',
);
"""
        )

        result = subprocess.run(
            ["node", runner.name],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_destructive_create_never_recycles_or_rotates_shared_state(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        runner = self.root / "non-destructive-runtime.mjs"
        runner.write_text(
            """import assert from 'node:assert/strict';
import { routes, spartanTestState as state } from './server.js';

const handler = routes.get('/tabs');
const capabilitiesHandler = routes.get('/spartan/capabilities');
assert.equal(typeof handler, 'function');
assert.equal(typeof capabilitiesHandler, 'function');

function response() {
  return {
    statusCode: 200,
    body: null,
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  };
}

let res = response();
await capabilitiesHandler({}, res);
assert.equal(res.statusCode, 200);
assert.equal(res.body.operatorTabIsolationVersion, 1);
assert.equal(res.body.nonDestructiveTabCreate, true);
assert.equal(res.body.cleanupFailedOperatorNavigation, true);

state.totalTabs = 16;
res = response();
await handler(
  { body: { userId: 'profile', sessionKey: 'operator', nonDestructive: true }, reqId: 'capacity' },
  res,
);
assert.equal(res.statusCode, 429);
assert.equal(res.body.code, 'tab_capacity_reached');
assert.equal(state.recycleCalls, 0);
assert.equal(state.rotateCalls, 0);

state.totalTabs = 0;
state.failNavigation = true;
res = response();
await handler(
  {
    body: {
      userId: 'profile',
      sessionKey: 'operator',
      url: 'https://example.com',
      nonDestructive: true,
    },
    reqId: 'navigation',
  },
  res,
);
assert.ok(res.statusCode >= 400);
assert.equal(state.recycleCalls, 0);
assert.equal(state.rotateCalls, 0);
assert.equal(state.cleanupCalls, 1);
assert.equal(state.lastGroup.size, 0);

state.totalTabs = 16;
state.failNavigation = false;
res = response();
await handler(
  { body: { userId: 'profile', sessionKey: 'normal' }, reqId: 'normal-capacity' },
  res,
);
assert.equal(res.statusCode, 200);
assert.equal(state.recycleCalls, 1);

state.totalTabs = 0;
state.failNavigation = true;
res = response();
await handler(
  { body: { userId: 'profile', sessionKey: 'normal', url: 'https://example.com' }, reqId: 'normal-navigation' },
  res,
);
assert.equal(res.statusCode, 200);
assert.equal(state.rotateCalls, 1);
"""
        )

        result = subprocess.run(
            ["node", runner.name],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migrates_already_patched_server_without_viewport_route(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        patched = self.server.read_text()
        start = patched.index("function spartanViewportBody(req)")
        end = patched.index("function spartanDownloadRecordKey(record)")
        legacy = patched[:start] + patched[end:]
        self.assertNotIn("/tabs/:tabId/viewport", legacy)
        self.server.write_text(legacy)

        second = self.run_patcher()

        self.assertEqual(second.returncode, 0, second.stderr)
        migrated = self.server.read_text()
        self.assertIn("/tabs/:tabId/viewport", migrated)
        self.assertIn("page.setViewportSize(requested)", migrated)
        self.assertIn("spartanRelaunchViewportTab", migrated)
        self.assertIn("spartanLaunchViewportWindow", migrated)
        self.assertIn("closeBrowserFully('viewport_relaunch'", migrated)
        self.assertIn("unsupported_viewport_options", migrated)
        self.assertIn("spartanUnsupportedViewportOptions(payload, req.body || {})", migrated)
        self.assertIn("Retry with relaunch=true", migrated)
        self.assertNotIn("import * as spartanChildProcess from 'child_process';", migrated)
        self.assertNotIn("require('child_process')", migrated)

    def test_removes_legacy_child_process_helper_when_migrating(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        patched = self.server.read_text()
        legacy = patched.replace(
            "const spartanViewportRelaunchLock = { tail: Promise.resolve() };",
            "const spartanChildProcess = require('child_process');\nconst spartanViewportWindowLock = { tail: Promise.resolve() };\nconst spartanViewportRelaunchLock = { tail: Promise.resolve() };",
            1,
        )
        self.assertIn("require('child_process')", legacy)
        self.assertIn("spartanViewportWindowLock", legacy)
        self.server.write_text(legacy)

        second = self.run_patcher()

        self.assertEqual(second.returncode, 0, second.stderr)
        migrated = self.server.read_text()
        self.assertNotIn("import * as spartanChildProcess from 'child_process';", migrated)
        self.assertNotIn("require('child_process')", migrated)
        self.assertNotIn("spartanViewportWindowLock", migrated)
        self.assertIn("spartanViewportRelaunchLock", migrated)
        syntax = subprocess.run(
            ["node", "--check", "server.js"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_does_not_write_a_partial_patch_when_an_anchor_is_missing(self):
        original = SERVER_FIXTURE.replace("// Get captured downloads", "// missing")
        self.server.write_text(original)

        result = self.run_patcher()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected Camofox downloads route marker not found", result.stderr)
        self.assertEqual(self.server.read_text(), original)
    def test_refuses_unknown_destroy_session_variant_before_write(self):
        original = SERVER_FIXTURE.replace(
            "log('warn', 'destroying dead session', { userId: key });",
            "log('warn', 'destroying stale session', { userId: key });",
        )
        self.assertNotEqual(original, SERVER_FIXTURE)
        self.server.write_text(original)

        result = self.run_patcher()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "unsupported Camofox destroy-session variant",
            result.stderr,
        )
        self.assertEqual(self.server.read_text(), original)



if __name__ == "__main__":
    unittest.main()
