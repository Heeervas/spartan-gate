'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { applyStealthEndpoint, buildBrowserlessCdpUrl, buildLaunch, normalizeRoute, stealthRoutePath } = require('./browserless-cdp-url');

test('buildLaunch is ephemeral by default without ignoreDefaultArgs', () => {
  const launch = buildLaunch({});

  assert.ok(launch.args.includes('--lang=es-ES'));
  assert.equal(Object.hasOwn(launch, 'headless'), false);
  assert.equal(Object.hasOwn(launch, 'stealth'), false);
  assert.equal(Object.hasOwn(launch, 'userDataDir'), false);
  assert.equal(Object.hasOwn(launch, 'ignoreDefaultArgs'), false);
});

test('buildLaunch resolves explicit persistent profile and opt-in ignoreDefaultArgs', () => {
  const launch = buildLaunch({
    BROWSERLESS_PROFILE_ROOT: '/custom-profiles/',
    BROWSERLESS_IGNORE_DEFAULT_ARGS: 'true',
  }, 'work');

  assert.equal(launch.userDataDir, '/custom-profiles/work');
  assert.equal(launch.ignoreDefaultArgs, true);
});

test('stealthRoutePath defaults to Browserless chromium and supports legacy stealth routes', () => {
  assert.equal(stealthRoutePath({}), '/chromium');
  assert.equal(normalizeRoute({ BROWSERLESS_ROUTE: 'stealth', BROWSERLESS_STEALTH_ROUTE: 'chromium' }), 'stealth');
  assert.equal(stealthRoutePath({ BROWSERLESS_ROUTE: 'stealth' }), '/stealth');
  assert.equal(stealthRoutePath({ BROWSERLESS_STEALTH_ROUTE: 'chromium' }), '/chromium');
  assert.equal(stealthRoutePath({ BROWSERLESS_STEALTH_ROUTE: 'chrome' }), '/chrome');
  assert.equal(stealthRoutePath({ BROWSERLESS_STEALTH_ROUTE: 'stealth' }), '/stealth');
  assert.equal(stealthRoutePath({ BROWSERLESS_STEALTH_ROUTE: 'chromium-stealth' }), '/chromium/stealth');
  assert.equal(stealthRoutePath({ BROWSERLESS_STEALTH_ROUTE: 'chrome-stealth' }), '/chrome/stealth');
});

test('applyStealthEndpoint defaults Browserless launch paths to chromium', () => {
  const chromium = new URL('ws://browserless:3000/chromium');
  applyStealthEndpoint(chromium, {});
  assert.equal(chromium.pathname, '/chromium');

  const chrome = new URL('ws://browserless:3000/chrome');
  applyStealthEndpoint(chrome, {});
  assert.equal(chrome.pathname, '/chromium');

  const root = new URL('ws://browserless:3000/');
  applyStealthEndpoint(root, {});
  assert.equal(root.pathname, '/chromium');
});

test('buildBrowserlessCdpUrl defaults to chromium endpoint and ephemeral Browserless launch', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_PROFILE: 'coach',
    BROWSERLESS_TOKEN: 'token',
  }));
  const launch = JSON.parse(url.searchParams.get('launch'));

  assert.equal(url.pathname, '/chromium');
  assert.equal(url.searchParams.get('token'), 'token');
  assert.equal(url.searchParams.get('headless'), 'true');
  assert.equal(url.searchParams.get('stealth'), 'true');
  assert.equal(Object.hasOwn(launch, 'headless'), false);
  assert.equal(Object.hasOwn(launch, 'stealth'), false);
  assert.equal(Object.hasOwn(launch, 'userDataDir'), false);
  assert.equal(Object.hasOwn(launch, 'ignoreDefaultArgs'), false);
});

test('buildBrowserlessCdpUrl can use canonical Chromium stealth route when explicitly requested', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_TOKEN: 'token',
    BROWSERLESS_ROUTE: 'chromium-stealth',
    BROWSERLESS_STEALTH_ROUTE: 'chromium',
    BROWSERLESS_WS_BASE: 'ws://browserless:3000/chromium',
  }));

  assert.equal(url.pathname, '/chromium/stealth');
});

test('buildBrowserlessCdpUrl still supports legacy route env', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_TOKEN: 'token',
    BROWSERLESS_STEALTH_ROUTE: 'stealth',
  }));

  assert.equal(url.pathname, '/stealth');
});

test('buildBrowserlessCdpUrl embeds explicit persistent profile on chromium endpoint', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_TOKEN: 'token',
    BROWSERLESS_WS_BASE: 'ws://browserless:3000/chromium',
  }, { profile: 'main' }));
  const launch = JSON.parse(url.searchParams.get('launch'));

  assert.equal(url.pathname, '/chromium');
  assert.equal(launch.userDataDir, '/profiles/main');
});

test('buildBrowserlessCdpUrl supports optional user agent diagnostic override', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_USER_AGENT: 'Custom UA',
  }));

  assert.equal(url.searchParams.get('userAgent'), 'Custom UA');
});

test('buildBrowserlessCdpUrl allows stealth endpoint opt-out for compatibility', () => {
  const url = new URL(buildBrowserlessCdpUrl({
    BROWSERLESS_STEALTH_ENDPOINT: 'false',
  }));

  assert.equal(url.pathname, '/chromium');
  assert.equal(url.searchParams.get('stealth'), 'true');
});

test('buildBrowserlessCdpUrl rejects invalid explicit profile', () => {
  assert.throws(() => buildBrowserlessCdpUrl({}, { profile: '../main' }), /Invalid Browserless profile name/);
});
