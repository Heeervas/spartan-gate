import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHER = ROOT / "infra" / "camofox" / "patch-server.js"

SERVER_FIXTURE = """\
const crypto = require('crypto');
const fs = require('fs');
const app = { post() {}, delete() {} };
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
"""


class CamofoxPatchServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        shutil.copy2(PATCHER, self.root / "patch-server.js")
        self.server = self.root / "server.js"
        self.server.write_text(SERVER_FIXTURE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_patcher(self):
        return subprocess.run(
            ["node", "patch-server.js"],
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

    def test_does_not_write_a_partial_patch_when_an_anchor_is_missing(self):
        original = SERVER_FIXTURE.replace("// Get captured downloads", "// missing")
        self.server.write_text(original)

        result = self.run_patcher()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected Camofox downloads route marker not found", result.stderr)
        self.assertEqual(self.server.read_text(), original)


if __name__ == "__main__":
    unittest.main()
