// cdp-patch.js — Fix browserless CDP WebSocket URL for Docker networking.
// Loaded via NODE_OPTIONS=--require=/opt/hermes/bootstrap/cdp-patch.js
//
// Problem: browserless /json/version returns webSocketDebuggerUrl: ws://0.0.0.0:3000/
// (no token, wrong host) — unreachable from other Docker containers.
//
// Preferred config: BROWSER_CDP_URL=ws://browserless:3000?token=<TOKEN>
// With ws://, browser_tool.py's _resolve_cdp_override returns the URL directly
// (skips the broken HTTP /json/version discovery) and agent-browser connects
// via WebSocket with the token already in the URL.
//
// This patch is a safety net for any Playwright code that still goes through
// connectOverCDP() with an http:// URL or receives a raw webSocketDebuggerUrl.
// It rewrites 0.0.0.0/localhost endpoints to use the host from BROWSER_CDP_URL
// AND copies the token query param so Browserless accepts the WS connection.

'use strict';

const BROWSER_CDP_URL = process.env.BROWSER_CDP_URL;
if (!BROWSER_CDP_URL) return;

const LOCAL_HOSTS = new Set(['0.0.0.0', 'localhost', '127.0.0.1', '::1']);

let baseUrl;
try {
  baseUrl = new URL(BROWSER_CDP_URL);
} catch (e) {
  console.error(`[cdp-patch] Invalid BROWSER_CDP_URL: ${BROWSER_CDP_URL}`);
  return;
}

/**
 * Rewrite a WebSocket/HTTP endpoint URL, replacing local-only hostnames
 * with the configured BROWSER_CDP_URL host while preserving path/query,
 * and copying the token query param from BROWSER_CDP_URL if not already set.
 */
function rewriteEndpoint(endpoint) {
  try {
    const parsed = new URL(endpoint);
    if (!LOCAL_HOSTS.has(parsed.hostname)) return endpoint;

    // Map http→ws, https→wss if the original was a ws URL
    if (parsed.protocol === 'ws:' || parsed.protocol === 'wss:') {
      parsed.protocol = baseUrl.protocol === 'https:' ? 'wss:' : 'ws:';
    } else {
      parsed.protocol = baseUrl.protocol;
    }
    parsed.hostname = baseUrl.hostname;
    parsed.port = baseUrl.port;

    // Copy token from BROWSER_CDP_URL if the endpoint doesn't already have one
    if (baseUrl.searchParams.has('token') && !parsed.searchParams.has('token')) {
      parsed.searchParams.set('token', baseUrl.searchParams.get('token'));
    }

    const rewritten = parsed.toString();
    console.error(`[cdp-patch] Rewrote CDP endpoint: ${endpoint} → ${rewritten}`);
    return rewritten;
  } catch (e) {
    return endpoint;
  }
}

// ── Strategy 1: Patch Playwright connectOverCDP via Module._load hook ──
// This catches the URL right before the actual WebSocket connection.
const Module = require('module');
const origLoad = Module._load;

Module._load = function (request, parent, isMain) {
  const mod = origLoad.apply(this, arguments);

  if (
    (request === 'playwright' || request === 'playwright-core') &&
    mod && mod.chromium && typeof mod.chromium.connectOverCDP === 'function' &&
    !mod.chromium.__cdpPatched
  ) {
    const origConnect = mod.chromium.connectOverCDP.bind(mod.chromium);
    mod.chromium.connectOverCDP = function (endpointURL, options) {
      return origConnect(rewriteEndpoint(endpointURL), options);
    };
    mod.chromium.__cdpPatched = true;
    console.error('[cdp-patch] Patched playwright chromium.connectOverCDP');
  }

  return mod;
};

// ── Strategy 2: Patch http.get responses for /json/version ──
// Rewrites webSocketDebuggerUrl in the JSON body so that any code
// that reads the URL from /json/version also gets the corrected value.
const http = require('http');
const origHttpGet = http.get;

http.get = function (...args) {
  // Determine the URL string from the arguments
  let urlStr = '';
  if (typeof args[0] === 'string') {
    urlStr = args[0];
  } else if (args[0] && typeof args[0] === 'object') {
    urlStr = args[0].href || args[0].path || '';
  }

  if (!urlStr.includes('/json/version')) {
    return origHttpGet.apply(this, args);
  }

  // Find the callback (last function argument)
  let cbIndex = -1;
  for (let i = args.length - 1; i >= 0; i--) {
    if (typeof args[i] === 'function') { cbIndex = i; break; }
  }
  if (cbIndex === -1) return origHttpGet.apply(this, args);

  const origCb = args[cbIndex];
  args[cbIndex] = function (res) {
    // Buffer the response, rewrite, then emit to original callback
    const chunks = [];
    const origOn = res.on;
    const listeners = { data: [], end: [], error: [] };

    res.on = function (event, fn) {
      if (event === 'data' || event === 'end' || event === 'error') {
        listeners[event].push(fn);
        return res;
      }
      return origOn.call(res, event, fn);
    };

    origOn.call(res, 'data', (chunk) => chunks.push(chunk));
    origOn.call(res, 'error', (err) => {
      listeners.error.forEach((fn) => fn(err));
    });
    origOn.call(res, 'end', () => {
      let body = Buffer.concat(chunks).toString();
      try {
        const json = JSON.parse(body);
        if (json.webSocketDebuggerUrl) {
          json.webSocketDebuggerUrl = rewriteEndpoint(json.webSocketDebuggerUrl);
        }
        body = JSON.stringify(json);
      } catch (e) {
        // Not JSON — pass through unchanged
      }
      const buf = Buffer.from(body);
      listeners.data.forEach((fn) => fn(buf));
      listeners.end.forEach((fn) => fn());
    });

    origCb(res);
  };

  return origHttpGet.apply(this, args);
};
