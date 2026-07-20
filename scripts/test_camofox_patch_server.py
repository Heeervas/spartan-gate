import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "infra" / "camofox" / "patch-server.js"
SPARTAN_HEALTH = ROOT / "infra" / "camofox" / "spartan-health.mjs"

SERVER_FIXTURE = """\
import crypto from 'crypto';
import fs from 'fs';
const app = { get() {}, post() {}, delete() {} };
const sessions = new Map();
const tabLocks = new Map();
const failuresTotal = { labels() { return { inc() {} }; } };
const MAX_DOWNLOAD_INLINE_BYTES = 1024;
const CONFIG = { nodeEnv: 'production' };
function authMiddleware() {}
function normalizeUserId(value) { return value; }
function findTab() { return null; }
function tabNotFoundResponse() {}
function withTabLock(_tabId, callback) { return callback(); }
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
function clearTabDownloads() {}
function safePageClose() {}
function refreshTabLockQueueDepth() {}
function refreshActiveTabsGauge() {}
function handleRouteError() {}
function isProxyError() { return true; }
const reporter = {};
let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
function requestTimeoutMs() { return 30000; }
async function ensureBrowser() { return {}; }
async function restartBrowser() { return {}; }
async function closeSession(key, session) {
  await session.context.close().catch(() => {});
  sessions.delete(key);
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

export { sessions, closeSession, reapEmptySessions, reapOrphanPages };
"""


class CamofoxPatchServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        shutil.copy2(PATCHER, self.root / "patch-server.cjs")
        shutil.copy2(SPARTAN_HEALTH, self.root / "spartan-health.mjs")
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
        self.assertNotIn("Active health probe -- detect hung browser", patched)
        self.assertIn("const spartanCreateFence =", patched)
        self.assertIn("sessions.get(spartanCreateFence.userKey) === session", patched)
        self.assertIn("spartanPagePromise.then", patched)
        self.assertIn("code: 'tab_create_timeout'", patched)
        self.assertIn("stage: spartanCreateFence.stage", patched)
        self.assertIn("spartanCgroupAdmissionAllowed", patched)
        self.assertIn("if (sessions.get(key) === session) sessions.delete(key);", patched)
        self.assertIn("const spartanSessionCreatePending =", patched)
        self.assertIn("const spartanGroupCreatePending =", patched)
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

    def test_session_reaper_threshold_and_compare_delete_are_behavioral(self):
        first = self.run_patcher()
        self.assertEqual(first.returncode, 0, first.stderr)
        runner = self.root / "reaper-runtime.mjs"
        runner.write_text(
            """import assert from 'node:assert/strict';
import { sessions, closeSession, reapEmptySessions, reapOrphanPages } from './server.js';

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
const pendingGroup = new Map();
const pendingFence = { group: pendingGroup };
const empty = {
  lastAccess: 10_000,
  tabGroups: new Map([['pending', pendingGroup]]),
  _spartanPendingCreates: new Set([pendingFence]),
  context: {
    close: async () => { reaperCloseCalls++; },
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


if __name__ == "__main__":
    unittest.main()
