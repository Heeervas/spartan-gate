const fs = require('fs');

const path = 'server.js';
let source = fs.readFileSync(path, 'utf8');

const spartanHealthImport = "import { createSpartanDeepHealthController, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';\n";
if (!source.includes(spartanHealthImport)) {
  const importMarker = "import fs from 'fs';\n";
  if (!source.includes(importMarker)) throw new Error('expected Camofox fs import marker not found');
  source = source.replace(importMarker, importMarker + spartanHealthImport);
}

const spartanChildProcessRequire = "const spartanChildProcess = require('child_process');\n";
const spartanChildProcessImport = "import * as spartanChildProcess from 'child_process';\n";

source = source.replaceAll(spartanChildProcessRequire, '');
source = source.replaceAll(spartanChildProcessImport, '');

const helperMarker = `let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
`;
const helper = `${helperMarker}
const spartanObsidianClipperBridgeToken = crypto.randomBytes(32).toString('hex');
const spartanViewportRelaunchLock = { tail: Promise.resolve() };
let spartanLaunchViewportWindow = null;

function spartanIsObsidianClipperAddon(addonPath) {
  const manifestPath = \`\${addonPath}/manifest.json\`;
  if (!fs.existsSync(manifestPath)) return false;

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch {
    return false;
  }

  return manifest?.browser_specific_settings?.gecko?.id === 'clipper@obsidian.md'
    || manifest?.name === 'Obsidian Web Clipper';
}

function spartanStageObsidianClipperAddon(addonPath) {
  const contentPath = \`\${addonPath}/content.js\`;
  if (!fs.existsSync(contentPath)) {
    throw new Error(\`Obsidian Web Clipper addon is missing content.js: \${addonPath}\`);
  }

  const content = fs.readFileSync(contentPath, 'utf8');
  if (!content.includes('saveMarkdownToFile')) {
    throw new Error('Obsidian Web Clipper content.js does not expose the expected saveMarkdownToFile handler');
  }

  const stagedRoot = '/tmp/spartan-camofox-addons';
  const stagedPath = \`\${stagedRoot}/web-clipper-obsidian\`;
  fs.rmSync(stagedPath, { recursive: true, force: true });
  fs.mkdirSync(stagedRoot, { recursive: true });
  fs.cpSync(addonPath, stagedPath, { recursive: true });

  const stagedContentPath = \`\${stagedPath}/content.js\`;
  const marker = 'spartan-gate-obsidian-clipper-bridge';
  if (!fs.readFileSync(stagedContentPath, 'utf8').includes(marker)) {
    fs.appendFileSync(stagedContentPath, \`
;(() => {
  const marker = '\${marker}';
  const token = '\${spartanObsidianClipperBridgeToken}';
  const requestType = 'spartan-gate:obsidian-clipper:save-file';
  const resultType = 'spartan-gate:obsidian-clipper:result';
  const used = new Set();
  if (window.__spartanGateObsidianClipperBridge) return;
  window.__spartanGateObsidianClipperBridge = { marker };

  async function sendExtensionMessage(message) {
    if (globalThis.browser?.runtime?.sendMessage) {
      return await globalThis.browser.runtime.sendMessage(message);
    }
    if (globalThis.chrome?.runtime?.sendMessage) {
      return await new Promise((resolve, reject) => {
        globalThis.chrome.runtime.sendMessage(message, response => {
          const err = globalThis.chrome.runtime.lastError;
          if (err) reject(new Error(err.message));
          else resolve(response);
        });
      });
    }
    throw new Error('extension runtime messaging is unavailable');
  }

  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window || !data || data.type !== requestType) return;
    if (data.token !== token || typeof data.nonce !== 'string' || data.nonce.length < 24) return;
    if (used.has(data.nonce)) return;
    used.add(data.nonce);

    Promise.resolve()
      .then(() => sendExtensionMessage({ action: 'saveMarkdownToFile' }))
      .then(response => {
        window.postMessage({ type: resultType, nonce: data.nonce, ok: !!response?.success, response }, '*');
      })
      .catch(error => {
        window.postMessage({ type: resultType, nonce: data.nonce, ok: false, error: String(error?.message || error) }, '*');
      });
  });
})();
\`);
  }

  return stagedPath;
}

function camofoxAddonPaths() {
  const paths = (process.env.CAMOFOX_ADDONS || '')
    .split(',')
    .map(path => path.trim())
    .filter(Boolean);

  return paths.map(addonPath => {
    if (!fs.existsSync(addonPath) || !fs.existsSync(\`\${addonPath}/manifest.json\`)) {
      throw new Error(\`CAMOFOX_ADDONS path is not an extracted Firefox addon: \${addonPath}\`);
    }
    return spartanIsObsidianClipperAddon(addonPath)
      ? spartanStageObsidianClipperAddon(addonPath)
      : addonPath;
  });
}
`;

const spartanViewportGlobalsMarker = "const spartanObsidianClipperBridgeToken = crypto.randomBytes(32).toString('hex');\n";
source = source.replaceAll("const spartanViewportWindowLock = { tail: Promise.resolve() };\n", '');
source = source.replaceAll("const spartanViewportRelaunchLock = { tail: Promise.resolve() };\nlet spartanLaunchViewportWindow = null;\n", '');
if (!source.includes('const spartanViewportRelaunchLock') && source.includes('function camofoxAddonPaths()')) {
  if (!source.includes(spartanViewportGlobalsMarker)) {
    throw new Error('expected Camofox Spartan helper globals marker not found');
  }
  source = source.replace(
    spartanViewportGlobalsMarker,
    spartanViewportGlobalsMarker + "const spartanViewportRelaunchLock = { tail: Promise.resolve() };\nlet spartanLaunchViewportWindow = null;\n",
  );
}

if (!source.includes('function camofoxAddonPaths()')) {
  if (!source.includes(helperMarker)) {
    throw new Error('expected Camofox browser globals marker not found');
  }
  source = source.replace(helperMarker, helper);
}

const safeErrorCodeMarker = `function safeError(err) {
  if (CONFIG.nodeEnv === 'production') {`;
const safeErrorCodeReplacement = `function safeError(err) {
  if (err.code) return err.message;
  if (CONFIG.nodeEnv === 'production') {`;
if (source.includes(safeErrorCodeMarker)) {
  source = source.replace(safeErrorCodeMarker, safeErrorCodeReplacement);
} else if (!source.includes('if (err.code) return err.message;')) {
  throw new Error('expected Camofox safeError marker not found');
}

const sendErrorCodeMarker = `  const body = { error: safeError(err), ...extraFields };
  if (err instanceof StaleRefsError) {`;
const sendErrorCodeReplacement = `  const body = { error: safeError(err), ...extraFields };
  if (err.code && !body.code) body.code = err.code;
  if (err instanceof StaleRefsError) {`;
if (source.includes(sendErrorCodeMarker)) {
  source = source.replace(sendErrorCodeMarker, sendErrorCodeReplacement);
} else if (!source.includes('if (err.code && !body.code) body.code = err.code;')) {
  throw new Error('expected Camofox sendError body marker not found');
}

const displayMarker = `    const useVirtualDisplay = !!vdDisplay;
    log('info', 'launching camoufox', {`;
const displayReplacement = `    const useVirtualDisplay = !!vdDisplay;
    const addonPaths = camofoxAddonPaths();
    log('info', 'launching camoufox', {`;
if (!source.includes('const addonPaths = camofoxAddonPaths();')) {
  if (!source.includes(displayMarker)) {
    throw new Error('expected Camofox launch log marker not found');
  }
  source = source.replace(displayMarker, displayReplacement);
}

const logMarker = `      virtualDisplay: useVirtualDisplay,
    });`;
const logReplacement = `      virtualDisplay: useVirtualDisplay,
      addons: addonPaths,
    });`;
if (!source.includes('      addons: addonPaths,\n    });')) {
  if (!source.includes(logMarker)) {
    throw new Error('expected Camofox launch log fields marker not found');
  }
  source = source.replace(logMarker, logReplacement);
}

const launchMarker = `        enable_cache: true,
        proxy: launchProxy,`;
const launchReplacement = `        enable_cache: true,
        addons: addonPaths,
        window: spartanLaunchViewportWindow?.window,
        proxy: launchProxy,`;
if (!source.includes('        addons: addonPaths,')) {
  if (!source.includes(launchMarker)) {
    throw new Error('expected Camofox launchOptions marker not found');
  }
  source = source.replace(launchMarker, launchReplacement);
}
if (!source.includes('        window: spartanLaunchViewportWindow?.window,')) {
  const windowLaunchMarker = `        addons: addonPaths,
        proxy: launchProxy,`;
  const windowLaunchReplacement = `        addons: addonPaths,
        window: spartanLaunchViewportWindow?.window,
        proxy: launchProxy,`;
  if (!source.includes(windowLaunchMarker)) {
    throw new Error('expected Camofox launchOptions window marker not found');
  }
  source = source.replace(windowLaunchMarker, windowLaunchReplacement);
}

function spartanFindCallExpressionEnd(text, start) {
  const open = text.indexOf('(', start);
  if (open === -1) return -1;

  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = open; index < text.length; index++) {
    const char = text[index];
    const next = text[index + 1];

    if (lineComment) {
      if (char === '\n') lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === '*' && next === '/') {
        blockComment = false;
        index++;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }

    if (char === '/' && next === '/') {
      lineComment = true;
      index++;
      continue;
    }
    if (char === '/' && next === '*') {
      blockComment = true;
      index++;
      continue;
    }
    if (char === '\'' || char === '"' || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(') {
      depth++;
      continue;
    }
    if (char === ')') {
      depth--;
      if (depth === 0) {
        let end = index + 1;
        while (end < text.length && /\s/.test(text[end])) end++;
        if (text[end] === ';') end++;
        return end;
      }
    }
  }
  return -1;
}

function spartanReplaceFirstRouteCall(text, prefixes, replacement) {
  for (const prefix of prefixes) {
    const start = text.indexOf(prefix);
    if (start === -1) continue;
    const end = spartanFindCallExpressionEnd(text, start);
    if (end === -1) throw new Error('expected Camofox viewport route call end not found');
    return { source: text.slice(0, start) + replacement + text.slice(end), replaced: true };
  }
  return { source: text, replaced: false };
}

const upstreamViewportDisabledRoute = `app.post('/tabs/:tabId/upstream-viewport-disabled', authMiddleware(), async (_req, res) => {
  res.status(410).json({
    ok: false,
    error: 'The upstream viewport route is disabled because it does not verify effective runtime dimensions; use /tabs/{tabId}/spartan/viewport',
    code: 'upstream_viewport_disabled',
  });
});
`;
if (!source.includes("code: 'upstream_viewport_disabled'")) {
  const replaced = spartanReplaceFirstRouteCall(source, [
    "app.post('/tabs/:tabId/viewport',",
    'app.post("/tabs/:tabId/viewport",',
    "app.post('/tabs/:tabId/upstream-viewport-disabled',",
    'app.post("/tabs/:tabId/upstream-viewport-disabled",',
  ], upstreamViewportDisabledRoute);
  source = replaced.source;
}

const clipperRouteMarker = `// Get captured downloads`;
const clipperRoute = `function spartanFindTabOwner(tabId) {
  for (const [userId, session] of sessions) {
    const found = findTab(session, tabId);
    if (found) return { userId, session, ...found };
  }
  return null;
}

function spartanDownloadRecordKey(record) {
  return String(record?.id || '');
}

async function spartanWaitForSingleNewDownload(tabState, beforeKeys, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    const candidates = (Array.isArray(tabState.downloads) ? tabState.downloads : [])
      .filter(download => !beforeKeys.has(spartanDownloadRecordKey(download)));

    if (candidates.length === 1) return candidates[0];
    if (candidates.length > 1) {
      const err = new Error('Multiple downloads were captured after clip request; refusing ambiguous result');
      err.statusCode = 409;
      err.code = 'ambiguous_download';
      throw err;
    }

    await new Promise(resolve => setTimeout(resolve, 100));
  }

  const err = new Error('Timed out waiting for Obsidian Web Clipper download');
  err.statusCode = 504;
  err.code = 'download_timeout';
  throw err;
}

async function spartanInvokeObsidianClipperBridge(page, timeoutMs) {
  const nonce = crypto.randomBytes(24).toString('hex');
  try {
    return await page.evaluate(({ nonce, token, timeoutMs }) => {
    const requestType = 'spartan-gate:obsidian-clipper:save-file';
    const resultType = 'spartan-gate:obsidian-clipper:result';
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        window.removeEventListener('message', onMessage);
        reject(new Error('Obsidian Web Clipper bridge did not respond'));
      }, timeoutMs);

      function onMessage(event) {
        const data = event.data;
        if (event.source !== window || !data || data.type !== resultType || data.nonce !== nonce) return;
        clearTimeout(timeout);
        window.removeEventListener('message', onMessage);
        if (data.ok) resolve(data.response || { success: true });
        else reject(new Error(data.error || data.response?.error || 'Obsidian Web Clipper bridge rejected request'));
      }

      window.addEventListener('message', onMessage);
      window.postMessage({ type: requestType, nonce, token }, '*');
    });
    }, { nonce, token: spartanObsidianClipperBridgeToken, timeoutMs });
  } catch (err) {
    if (String(err?.message || err).includes('Obsidian Web Clipper bridge did not respond')) {
      err.statusCode = 504;
      err.code = 'clipper_bridge_timeout';
    }
    throw err;
  }
}

app.post('/tabs/:tabId/extensions/obsidian-web-clipper/clip', authMiddleware(), async (req, res) => {
  try {
    const userId = req.body?.userId || req.query?.userId;
    if (!userId) return res.status(400).json({ error: 'userId is required', code: 'missing_user_id' });

    const normalizedUserId = normalizeUserId(userId);
    const session = sessions.get(normalizedUserId);
    const found = session && findTab(session, req.params.tabId);
    if (!found) {
      const owner = spartanFindTabOwner(req.params.tabId);
      if (owner) {
        return res.status(403).json({ error: 'Tab does not belong to requested userId', code: 'tab_user_mismatch' });
      }
      return tabNotFoundResponse(res, req.params.tabId);
    }

    const { tabState } = found;
    tabState.toolCalls++;
    session.lastAccess = Date.now();

    const mode = req.body?.mode || 'saveFile';
    if (mode !== 'saveFile') {
      return res.status(400).json({ error: 'Only mode=saveFile is supported', code: 'unsupported_mode' });
    }

    const waitForDownloadMsRaw = Number(req.body?.waitForDownloadMs);
    const waitForDownloadMs = Number.isFinite(waitForDownloadMsRaw)
      ? Math.min(Math.max(waitForDownloadMsRaw, 1000), 10000)
      : 10000;
    const includeData = req.body?.includeData === true;
    const maxBytesRaw = Number(req.body?.maxBytes);
    const maxBytes = Number.isFinite(maxBytesRaw) && maxBytesRaw > 0
      ? Math.min(maxBytesRaw, MAX_DOWNLOAD_INLINE_BYTES)
      : MAX_DOWNLOAD_INLINE_BYTES;

    const { bridge, downloadView } = await withTabLock(req.params.tabId, async () => {
      const beforeKeys = new Set((Array.isArray(tabState.downloads) ? tabState.downloads : []).map(spartanDownloadRecordKey));
      const bridge = await spartanInvokeObsidianClipperBridge(tabState.page, Math.min(waitForDownloadMs, 10000));
      const download = await spartanWaitForSingleNewDownload(tabState, beforeKeys, waitForDownloadMs);
      const [downloadView] = (await getDownloadsList(tabState, { includeData, maxBytes }))
        .filter(item => item.id === download.id);
      return { bridge, downloadView };
    });

    if (!downloadView) {
      const err = new Error('Captured download could not be read back');
      err.statusCode = 500;
      err.code = 'download_missing';
      throw err;
    }

    if (downloadView.dataBase64) {
      downloadView.sha256 = crypto.createHash('sha256').update(Buffer.from(downloadView.dataBase64, 'base64')).digest('hex');
      downloadView.encoding = 'base64';
    }

    res.json({
      ok: true,
      tabId: req.params.tabId,
      userId: normalizedUserId,
      mode,
      bridge,
      download: downloadView,
    });
  } catch (err) {
    failuresTotal.labels(classifyError(err), 'obsidian_clip').inc();
    log('error', 'obsidian clip failed', { reqId: req.reqId, tabId: req.params.tabId, error: err.message, code: err.code });
    res.status(err.statusCode || 500).json({ error: safeError(err), code: err.code || 'obsidian_clip_failed' });
  }
});

${clipperRouteMarker}`;
if (!source.includes('/tabs/:tabId/extensions/obsidian-web-clipper/clip')) {
  if (!source.includes(clipperRouteMarker)) {
    throw new Error('expected Camofox downloads route marker not found');
  }
  source = source.replace(clipperRouteMarker, clipperRoute);
}

const viewportRouteMarker = `function spartanDownloadRecordKey(record) {`;
const viewportRouteOnly = `function spartanViewportBody(req) {
  const payload = req.body?.viewport && typeof req.body.viewport === 'object'
    ? req.body.viewport
    : (req.body || {});
  return payload;
}

function spartanParseViewportInteger(value, name, min, max) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    const err = new Error('Invalid viewport ' + name + '; expected integer ' + min + '-' + max);
    err.statusCode = 400;
    err.code = 'invalid_viewport';
    throw err;
  }
  return parsed;
}

function spartanUnsupportedViewportOptions(payload, rootPayload = null) {
  const names = ['deviceScaleFactor', 'isMobile', 'hasTouch', 'touch', 'userAgent'];
  const found = new Set();
  for (const candidate of [payload, rootPayload]) {
    if (!candidate || typeof candidate !== 'object') continue;
    for (const name of names) {
      if (Object.prototype.hasOwnProperty.call(candidate, name)) found.add(name);
    }
  }
  return Array.from(found);
}

function spartanViewportError(message, statusCode, code) {
  const err = new Error(message);
  err.statusCode = statusCode;
  err.code = code;
  return err;
}

function spartanSleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function spartanViewportMatches(requested, effective) {
  return Math.abs(Number(effective?.innerWidth) - requested.width) <= 2
    && Math.abs(Number(effective?.innerHeight) - requested.height) <= 2;
}

function spartanNativeOuterTarget(requested, effective) {
  const chromeWidth = Math.max(0, Number(effective?.outerWidth || 0) - Number(effective?.innerWidth || 0));
  const chromeHeight = Math.max(0, Number(effective?.outerHeight || 0) - Number(effective?.innerHeight || 0));
  return {
    width: requested.width + chromeWidth,
    height: requested.height + chromeHeight,
    chromeWidth,
    chromeHeight,
  };
}

function spartanWithViewportRelaunchLock(callback) {
  const run = spartanViewportRelaunchLock.tail.catch(() => undefined).then(callback);
  spartanViewportRelaunchLock.tail = run.catch(() => undefined);
  return run;
}

async function spartanEvaluateViewport(page) {
  return await page.evaluate(() => ({
    href: String(location.href).slice(0, 160),
    title: document.title,
    readyState: document.readyState,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    screenWidth: window.screen?.width ?? null,
    screenHeight: window.screen?.height ?? null,
    visualViewportWidth: window.visualViewport?.width ?? null,
    visualViewportHeight: window.visualViewport?.height ?? null,
    devicePixelRatio: window.devicePixelRatio,
    maxWidth767: window.matchMedia('(max-width: 767px)').matches,
    maxWidth430: window.matchMedia('(max-width: 430px)').matches,
    userAgent: navigator.userAgent,
    hasTouch: (navigator.maxTouchPoints || 0) > 0,
  }));
}

async function spartanRelaunchViewportTab({ userId, sessionKey, tabId, tabState, requested, effective, timeoutMs }) {
  const resumeUrl = String(tabState.page?.url?.() || '');
  const canResumeUrl = /^https?:\/\//i.test(resumeUrl);
  const target = spartanNativeOuterTarget(requested, effective);
  const launchWindow = [target.width, target.height];
  spartanLaunchViewportWindow = {
    requested,
    window: launchWindow,
    chromeWidth: target.chromeWidth,
    chromeHeight: target.chromeHeight,
    updatedAt: new Date().toISOString(),
  };

  await closeAllSessions('viewport_relaunch', { clearDownloads: true, clearLocks: true });
  await closeBrowserFully('viewport_relaunch');
  browserLaunchPromise = null;
  await ensureBrowser();

  const session = await getSession(userId);
  const group = getTabGroup(session, sessionKey);
  const page = await session.context.newPage();
  if (typeof page.setViewportSize === 'function') {
    await page.setViewportSize(requested);
  }
  const nextTabState = createTabState(page);
  nextTabState.lastRequestedUrl = canResumeUrl ? resumeUrl : null;
  attachDownloadListener(nextTabState, tabId, log, pluginEvents, userId);
  group.set(tabId, nextTabState);
  attachPopupHandler(page, userId, sessionKey);
  refreshActiveTabsGauge();

  if (canResumeUrl) {
    await page.goto(resumeUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    nextTabState.visitedUrls.add(resumeUrl);
  }

  const relaunchedEffective = await spartanEvaluateViewport(page);
  return {
    strategy: 'camoufoxLaunchWindow',
    effective: relaunchedEffective,
    relaunch: {
      resumeUrl: canResumeUrl ? resumeUrl : null,
      requested,
      launchWindow,
      chromeWidth: target.chromeWidth,
      chromeHeight: target.chromeHeight,
      closesOtherSessions: true,
    },
  };
}

app.post(['/tabs/:tabId/spartan/viewport', '/tabs/:tabId/viewport'], authMiddleware(), async (req, res) => {
  let requested = null;
  let before = null;
  let effective = null;
  let strategy = 'pageViewport';
  const attempts = [];
  try {
    const userId = req.body?.userId || req.query?.userId;
    if (!userId) return res.status(400).json({ ok: false, error: 'userId is required', code: 'missing_user_id' });

    const normalizedUserId = normalizeUserId(userId);
    const session = sessions.get(normalizedUserId);
    const found = session && findTab(session, req.params.tabId);
    if (!found) {
      const owner = spartanFindTabOwner(req.params.tabId);
      if (owner) {
        return res.status(403).json({ ok: false, error: 'Tab does not belong to requested userId', code: 'tab_user_mismatch' });
      }
      return tabNotFoundResponse(res, req.params.tabId);
    }

    const payload = spartanViewportBody(req);
    requested = {
      width: spartanParseViewportInteger(payload.width, 'width', 100, 4096),
      height: spartanParseViewportInteger(payload.height, 'height', 100, 4096),
    };

    const unsupported = spartanUnsupportedViewportOptions(payload, req.body || {});
    if (unsupported.length > 0) {
      const err = new Error('Unsupported live viewport option(s): ' + unsupported.join(', '));
      err.statusCode = 400;
      err.code = 'unsupported_viewport_options';
      throw err;
    }

    const timeoutMsRaw = Number(payload.timeoutMs);
    const timeoutMs = Number.isFinite(timeoutMsRaw)
      ? Math.min(Math.max(timeoutMsRaw, 1000), 60000)
      : 10000;

    const { tabState } = found;
    tabState.toolCalls++;
    session.lastAccess = Date.now();

    const result = await withTabLock(req.params.tabId, async () => {
      if (typeof tabState.page?.setViewportSize !== 'function') {
        const err = new Error('Camofox page does not support live viewport resizing');
        err.statusCode = 501;
        err.code = 'viewport_resize_unsupported';
        throw err;
      }

      const page = tabState.page;
      const shouldReload = payload.reload === true && typeof page.reload === 'function';
      before = await spartanEvaluateViewport(page);
      await page.setViewportSize(requested);
      effective = await spartanEvaluateViewport(page);
      attempts.push({ strategy: 'pageViewport', effective });
      if (spartanViewportMatches(requested, effective)) {
        if (shouldReload) {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: timeoutMs });
          effective = await spartanEvaluateViewport(page);
          attempts.push({ strategy: 'pageViewportReload', effective });
        }
        if (spartanViewportMatches(requested, effective)) return { strategy: 'pageViewport', effective };
      }

      if (payload.relaunch === true) {
        strategy = 'camoufoxLaunchWindow';
        return await spartanWithViewportRelaunchLock(async () => {
          const relaunched = await spartanRelaunchViewportTab({
            userId: normalizedUserId,
            sessionKey: found.listItemId,
            tabId: req.params.tabId,
            tabState,
            requested,
            effective,
            timeoutMs,
          });
          attempts.push({ strategy: 'camoufoxLaunchWindow', relaunch: relaunched.relaunch, effective: relaunched.effective });
          return relaunched;
        });
      }

      return { strategy: 'pageViewport', effective };
    });

    strategy = result.strategy;
    effective = result.effective;
    if (!spartanViewportMatches(requested, effective)) {
      const err = new Error('Viewport did not take effect in page runtime');
      err.statusCode = 409;
      err.code = 'viewport_mismatch';
      err.requested = requested;
      err.before = before;
      err.effective = effective;
      err.strategy = strategy;
      err.attempts = attempts;
      err.relaunchSupported = true;
      err.relaunchRequired = payload.relaunch !== true;
      err.hint = payload.relaunch === true
        ? 'Camoufox launch window relaunch completed but runtime dimensions still did not match'
        : 'Retry with relaunch=true to restart Camoufox with a launch-time window size';
      throw err;
    }

    res.json({
      ok: true,
      tabId: req.params.tabId,
      userId: normalizedUserId,
      requested,
      before,
      effective,
      strategy,
      attempts,
      relaunch: result.relaunch || undefined,
    });
  } catch (err) {
    failuresTotal.labels(classifyError(err), 'viewport').inc();
    log('error', 'viewport resize failed', { reqId: req.reqId, tabId: req.params.tabId, error: err.message, code: err.code, strategy });
    res.status(err.statusCode || 500).json({
      ok: false,
      error: safeError(err),
      code: err.code || 'viewport_failed',
      requested: err.requested || requested || undefined,
      before: err.before || before || undefined,
      effective: err.effective || effective || undefined,
      strategy: err.strategy || strategy || undefined,
      attempts: err.attempts || attempts || undefined,
      relaunchSupported: err.relaunchSupported || undefined,
      relaunchRequired: err.relaunchRequired || undefined,
      hint: err.hint || undefined,
    });
  }
});

${viewportRouteMarker}`;
if (!source.includes('/tabs/:tabId/viewport')) {
  if (!source.includes('function spartanFindTabOwner(tabId)')) {
    throw new Error('expected Camofox tab owner helper marker not found');
  }
  if (!source.includes(viewportRouteMarker)) {
    throw new Error('expected Camofox viewport route marker not found');
  }
  source = source.replace(viewportRouteMarker, viewportRouteOnly);
}

const navigateProxyErrorPattern = /(\n\s*} else \{\n)(\s*)throw navErr;\n(\s*}\n\s*})/;
let navigateProxyErrorPatchCount = 0;
source = source.replace(new RegExp(navigateProxyErrorPattern.source, 'g'), (_match, prefix, indent, suffix) => {
  navigateProxyErrorPatchCount++;
  return (
    `${prefix}${indent}if (isProxyError(navErr)) {\n`
    + `${indent}  navErr.statusCode = 403;\n`
    + `${indent}  navErr.code = 'proxy_forbidden';\n`
    + `${indent}}\n`
    + `${indent}throw navErr;\n${suffix}`
  );
});
if (navigateProxyErrorPatchCount === 0 && !source.includes("navErr.code = 'proxy_forbidden'")) {
  throw new Error('expected Camofox navigate proxy error marker not found');
}


const closeTabRouteStart = "app.delete('/tabs/:tabId', async (req, res) => {";
const closeTabRouteEnd = "\n});\n\n// Close tab group";
const closeTabRouteStartIndex = source.indexOf(closeTabRouteStart);
if (closeTabRouteStartIndex !== -1) {
  const closeTabRouteEndIndex = source.indexOf(closeTabRouteEnd, closeTabRouteStartIndex);
  if (closeTabRouteEndIndex === -1) throw new Error('expected Camofox close-tab route end marker not found');

  const closeTabRoute = source.slice(closeTabRouteStartIndex, closeTabRouteEndIndex);
  if (!closeTabRoute.includes('await withTabLock(req.params.tabId, async () =>')) {
    const foundMarker = "    if (found) {\n      if (found.tabState.navigateAbort) found.tabState.navigateAbort.abort();";
    const cleanupMarker = "      { const _l = tabLocks.get(req.params.tabId); if (_l) _l.drain(); tabLocks.delete(req.params.tabId); refreshTabLockQueueDepth(); }\n";
    const logMarker = "      log('info', 'tab closed', { reqId: req.reqId, tabId: req.params.tabId, userId });";
    if (!closeTabRoute.includes(foundMarker) || !closeTabRoute.includes(cleanupMarker) || !closeTabRoute.includes(logMarker)) {
      throw new Error('expected Camofox close-tab locking anchors not found');
    }
    const lockedCloseTabRoute = closeTabRoute
      .replace(foundMarker, "    if (found) {\n      await withTabLock(req.params.tabId, async () => {\n        if (found.tabState.navigateAbort) found.tabState.navigateAbort.abort();")
      .replace(cleanupMarker, '')
      .replace(logMarker, logMarker + "\n      });\n      { const _l = tabLocks.get(req.params.tabId); if (_l) _l.drain(); tabLocks.delete(req.params.tabId); refreshTabLockQueueDepth(); }");
    source = source.slice(0, closeTabRouteStartIndex) + lockedCloseTabRoute + source.slice(closeTabRouteEndIndex);
  }
}

const createTabRouteStart = "app.post('/tabs', async (req, res) => {";
const createTabRouteEnd = "\n});\n\n// Navigate";
const createTabRouteStartIndex = source.indexOf(createTabRouteStart);
if (createTabRouteStartIndex !== -1) {
  const createTabRouteEndIndex = source.indexOf(createTabRouteEnd, createTabRouteStartIndex);
  if (createTabRouteEndIndex === -1) throw new Error('expected Camofox create-tab route end marker not found');
  let createTabRoute = source.slice(createTabRouteStartIndex, createTabRouteEndIndex);
  if (!createTabRoute.includes('const spartanCreateFence =')) {
    const routeOpen = `${createTabRouteStart}\n  try {`;
    const routeOpenReplacement = `${createTabRouteStart}\n  const spartanCreateFence = {\n    active: true,\n    stage: 'validate',\n    startedAt: Date.now(),\n    session: null,\n    page: null,\n    group: null,\n    tabId: null,\n    userKey: null,\n  };\n  try {`;
    const getSessionMarker = '      let session = await getSession(userId, { trace: !!trace });';
    const getSessionReplacement = `      spartanCreateFence.stage = 'get_session';
      spartanCreateFence.userKey = normalizeUserId(userId);
      let session = await getSession(userId, { trace: !!trace });
      spartanCreateFence.session = session;
      if (!(session._spartanPendingCreates instanceof Set)) session._spartanPendingCreates = new Set();
      session._spartanPendingCreates.add(spartanCreateFence);
      if (!spartanCreateFence.active) {
        if (sessions.get(spartanCreateFence.userKey) === session) sessions.delete(spartanCreateFence.userKey);
        session._closing = true;
        closeSession(spartanCreateFence.userKey, session, { reason: 'late_tab_create', clearDownloads: true, clearLocks: true }).catch(() => {});
        throw Object.assign(new Error('tab create completed after deadline'), { statusCode: 503, code: 'tab_create_timeout' });
      }`;
    const groupMarker = '      const group = getTabGroup(session, resolvedSessionKey);';
    const groupReplacement = `${groupMarker}
      spartanCreateFence.group = group;`;
    const newPageMarker = '      const page = await session.context.newPage();';
    const newPageReplacement = `      spartanCreateFence.stage = 'new_page';
      const spartanPagePromise = session.context.newPage();
      spartanPagePromise.then(latePage => {
        if (!spartanCreateFence.active) safePageClose(latePage).catch(() => {});
      }).catch(() => {});
      const page = await spartanPagePromise;
      if (!spartanCreateFence.active) {
        safePageClose(page).catch(() => {});
        throw Object.assign(new Error('tab create completed after deadline'), { statusCode: 503, code: 'tab_create_timeout' });
      }
      spartanCreateFence.page = page;`;
    const tabIdMarker = '      const tabId = fly.makeTabId();';
    const tabIdReplacement = `${tabIdMarker}
      spartanCreateFence.tabId = tabId;`;
    const navigationMarker = `      if (url) {
        const urlErr = validateUrl(url);`;
    const navigationReplacement = `      if (url) {
        spartanCreateFence.stage = 'navigate';
        const urlErr = validateUrl(url);`;
    const createPromiseCloseMarker = `    })(), requestTimeoutMs(), 'tab create');`;
    const createPromiseCloseReplacement = `    })().finally(() => {
      const spartanPendingCreates = spartanCreateFence.session?._spartanPendingCreates;
      if (spartanPendingCreates instanceof Set) {
        spartanPendingCreates.delete(spartanCreateFence);
        if (spartanPendingCreates.size === 0) delete spartanCreateFence.session._spartanPendingCreates;
      }
    }), requestTimeoutMs(), 'tab create');`;
    const catchMarker = `  } catch (err) {
    log('error', 'tab create failed', { reqId: req.reqId, error: err.message });`;
    const catchReplacement = `  } catch (err) {
    spartanCreateFence.active = false;
    const spartanCreateTimedOut = err?.code === 'tab_create_timeout'
      || String(err?.message || '').includes('tab create timed out');
    if (spartanCreateTimedOut) {
      err.statusCode = 503;
      err.code = 'tab_create_timeout';
      log('error', 'tab create stage failed', {
        reqId: req.reqId,
        stage: spartanCreateFence.stage,
        durationMs: Date.now() - spartanCreateFence.startedAt,
        code: err.code,
      });
      if (spartanCreateFence.group && spartanCreateFence.tabId) {
        spartanCreateFence.group.delete(spartanCreateFence.tabId);
        refreshActiveTabsGauge();
      }
      if (spartanCreateFence.page) {
        await withTimeout(safePageClose(spartanCreateFence.page), 2000, 'tab create cleanup').catch(() => {
          restartBrowser('tab_create_cleanup_timeout').catch(() => {});
        });
      } else if (spartanCreateFence.session && spartanCreateFence.userKey) {
        const current = sessions.get(spartanCreateFence.userKey);
        if (current === spartanCreateFence.session) sessions.delete(spartanCreateFence.userKey);
        spartanCreateFence.session._closing = true;
        await withTimeout(closeSession(spartanCreateFence.userKey, spartanCreateFence.session, {
          reason: 'tab_create_timeout', clearDownloads: true, clearLocks: true,
        }), 2000, 'tab create session cleanup').catch(() => {
          restartBrowser('tab_create_session_cleanup_timeout').catch(() => {});
        });
      }
    }
    log('error', 'tab create failed', { reqId: req.reqId, error: err.message });`;

    for (const [marker, replacement, label] of [
      [routeOpen, routeOpenReplacement, 'route open'],
      [getSessionMarker, getSessionReplacement, 'getSession'],
      [groupMarker, groupReplacement, 'group'],
      [newPageMarker, newPageReplacement, 'newPage'],
      [tabIdMarker, tabIdReplacement, 'tabId'],
      [navigationMarker, navigationReplacement, 'navigation'],
      [createPromiseCloseMarker, createPromiseCloseReplacement, 'promise close'],
      [catchMarker, catchReplacement, 'catch'],
    ]) {
      if (!createTabRoute.includes(marker)) throw new Error(`expected Camofox create-tab ${label} marker not found`);
      createTabRoute = createTabRoute.replace(marker, replacement);
    }
    source = source.slice(0, createTabRouteStartIndex) + createTabRoute + source.slice(createTabRouteEndIndex);
  }
}

const spartanCloseSessionDeleteMarker = `  await session.context.close().catch(() => {});
  sessions.delete(key);`;
const spartanCloseSessionDeleteReplacement = `  await session.context.close().catch(() => {});
  // A reaped session may already have been replaced for the same user while
  // context.close() was pending. Never delete that replacement.
  if (sessions.get(key) === session) sessions.delete(key);`;
if (source.includes(spartanCloseSessionDeleteMarker)) {
  source = source.replace(
    spartanCloseSessionDeleteMarker,
    spartanCloseSessionDeleteReplacement,
  );
} else if (!source.includes(
  'if (sessions.get(key) === session) sessions.delete(key);',
)) {
  throw new Error('expected Camofox close-session delete marker not found');
}

const spartanEmptyGroupReaperMarker = `      if (group.size === 0) {
        session.tabGroups.delete(listItemId);
      }`;
const spartanEmptyGroupReaperReplacement = `      const spartanGroupCreatePending = session._spartanPendingCreates instanceof Set
        && [...session._spartanPendingCreates].some(pending => pending.group === group);
      if (group.size === 0 && !spartanGroupCreatePending) {
        session.tabGroups.delete(listItemId);
      }`;
if (source.includes(spartanEmptyGroupReaperMarker)) {
  source = source.replace(
    spartanEmptyGroupReaperMarker,
    spartanEmptyGroupReaperReplacement,
  );
} else if (!source.includes('const spartanGroupCreatePending =')) {
  throw new Error('expected Camofox empty-group reaper marker not found');
}

const spartanEmptySessionReaperMarker = `    // Clean up sessions with zero tabs remaining -- free browser context memory
    if (session.tabGroups.size === 0) {`;
const spartanEmptySessionReaperReplacement = `    // A create fence lives until the underlying work settles, not merely until
    // the HTTP timeout. Never tear down a context that is still creating a tab.
    const spartanSessionCreatePending = session._spartanPendingCreates instanceof Set
      && session._spartanPendingCreates.size > 0;
    if (
      session.tabGroups.size === 0
      && !spartanSessionCreatePending
    ) {`;
if (source.includes(spartanEmptySessionReaperMarker)) {
  source = source.replace(
    spartanEmptySessionReaperMarker,
    spartanEmptySessionReaperReplacement,
  );
} else if (!source.includes('const spartanSessionCreatePending =')) {
  throw new Error('expected Camofox empty-session reaper marker not found');
}

const spartanOrphanReaperMarker = `    if (session._closing) continue;
    let contextPages;
    try {
      contextPages = session.context.pages();`;
const spartanOrphanReaperReplacement = `    if (session._closing) continue;
    if (session._spartanPendingCreates instanceof Set && session._spartanPendingCreates.size > 0) continue;
    let contextPages;
    try {
      contextPages = session.context.pages();`;
if (source.includes(spartanOrphanReaperMarker)) {
  source = source.replace(
    spartanOrphanReaperMarker,
    spartanOrphanReaperReplacement,
  );
} else if (!source.includes(spartanOrphanReaperReplacement)) {
  throw new Error('expected Camofox orphan reaper marker not found');
}

const nativeMemResetMarker = `  // Reset native memory baseline so next browser measures from fresh
  reporter.resetNativeMemBaseline();
  _nativeMemBaseline = null;`;
const nativeMemResetReplacement = `  // Reset native memory baseline so next browser measures from fresh
  if (typeof reporter.resetNativeMemBaseline === 'function') {
    reporter.resetNativeMemBaseline();
  }
  _nativeMemBaseline = null;`;
if (source.includes(nativeMemResetMarker)) {
  source = source.replace(nativeMemResetMarker, nativeMemResetReplacement);
} else if (!source.includes("typeof reporter.resetNativeMemBaseline === 'function'")) {
  throw new Error('expected Camofox native memory reset marker not found');
}

const spartanCgroupAdmissionMarker = `      // Memory admission control (Fly.io only) — reject new sessions when
      // system memory is critically low. 503 tells Fly Proxy to try another machine.`;
const spartanCgroupAdmission = `      if (process.env.SPARTAN_CAMOFOX_CGROUP_ADMISSION === 'true') {
        const admission = spartanCgroupAdmissionAllowed({
          threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.90),
        });
        if (!admission.allowed) {
          const err = new Error('Camofox cgroup memory admission rejected');
          err.statusCode = 503;
          err.code = admission.code || 'memory_pressure';
          throw err;
        }
      }
`;
if (!source.includes('spartanCgroupAdmissionAllowed({')) {
  if (!source.includes(spartanCgroupAdmissionMarker)) {
    throw new Error('expected Camofox memory admission marker not found');
  }
  source = source.replace(spartanCgroupAdmissionMarker, spartanCgroupAdmission + spartanCgroupAdmissionMarker);
}

const spartanDeepHealthMarker = "app.get('/health',";
const spartanDeepHealthRoutes = `const spartanDeepHealthSecretPath = process.env.SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE
  || '/run/spartan-camofox/internal.key';
const spartanDeepHealth = createSpartanDeepHealthController({
  getBrowser: ensureBrowser,
  recoverBrowser: () => restartBrowser('spartan_deep_health'),
  probeTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_DEEP_TIMEOUT_MS || 8000),
  cleanupTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 2000),
  recoveryTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_SERVER_RECOVERY_TIMEOUT_MS || 20000),
});

function spartanRequireInternalHealth(req, res) {
  if (spartanInternalRequestAuthorized(req, spartanDeepHealthSecretPath)) return true;
  res.status(403).json({ ok: false, code: 'forbidden' });
  return false;
}

app.get('/spartan/health/deep', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.probe();
  res.status(result.ok ? 200 : 503).json(result);
});

app.post('/spartan/recover', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.recover();
  res.status(result.ok ? 200 : 503).json(result);
});

`;
if (!source.includes("app.get('/spartan/health/deep'")) {
  if (!source.includes(spartanDeepHealthMarker)) {
    throw new Error('expected Camofox health route marker not found');
  }
  source = source.replace(spartanDeepHealthMarker, spartanDeepHealthRoutes + spartanDeepHealthMarker);
}

const upstreamActiveHealthStart = '// Active health probe -- detect hung browser even when isConnected() lies\n';
const upstreamActiveHealthEnd = '\n// Crash logging\n';
const upstreamActiveHealthIndex = source.indexOf(upstreamActiveHealthStart);
if (upstreamActiveHealthIndex !== -1) {
  const end = source.indexOf(upstreamActiveHealthEnd, upstreamActiveHealthIndex);
  if (end === -1) throw new Error('expected Camofox active health probe end marker not found');
  source = source.slice(0, upstreamActiveHealthIndex) + source.slice(end + 1);
}

fs.writeFileSync(path, source);
