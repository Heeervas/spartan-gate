'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { buildDebuggerUrl } = require('./browserless-profile-live');

test('buildDebuggerUrl asks debugger to use Browserless chromium endpoint for profile seeding', () => {
  const url = new URL(buildDebuggerUrl({
    BROWSERLESS_DEBUG_PORT: '3005',
    BROWSERLESS_PROFILE_ROOT: '/profiles',
  }, 'main', 'https://accounts.google.com'));

  assert.equal(url.pathname, '/debugger/');
  assert.equal(url.searchParams.get('sgUserDataDir'), '/profiles/main');
  assert.equal(url.searchParams.get('sgHeadless'), 'true');
  assert.equal(url.searchParams.get('sgStealthEndpoint'), 'true');
  assert.equal(url.searchParams.get('sgRoute'), 'chromium');
  assert.equal(url.searchParams.get('sgStealthRoute'), 'chromium');
  assert.equal(url.searchParams.get('sgAutostart'), 'false');
  assert.equal(url.searchParams.get('sgEdgeToken'), null);
  assert.match(url.searchParams.get('sgFlags'), /--proxy-server=http:\/\/proxy:8888/);
  assert.match(url.searchParams.get('sgFlags'), /--lang=es-ES/);
  assert.match(url.searchParams.get('sgCode'), /accounts\.google\.com/);
});

test('buildDebuggerUrl forwards Browserless edge token without requiring WebSocket Basic Auth', () => {
  const url = new URL(buildDebuggerUrl({
    BROWSERLESS_EDGE_TOKEN: 'edge-secret',
  }, 'main', 'https://example.com'));

  assert.equal(url.searchParams.get('sgEdgeToken'), 'edge-secret');
});

test('buildDebuggerUrl autostarts non-Google profile seeding sessions', () => {
  const url = new URL(buildDebuggerUrl({}, 'main', 'https://example.com'));

  assert.equal(url.searchParams.get('sgAutostart'), 'true');
});

test('buildDebuggerUrl forwards route and user-agent diagnostic overrides', () => {
  const url = new URL(buildDebuggerUrl({
    BROWSERLESS_ROUTE: 'stealth',
    BROWSERLESS_STEALTH_ROUTE: 'chromium',
    BROWSERLESS_USER_AGENT: 'Custom UA',
  }, 'main', 'https://example.com'));

  assert.equal(url.searchParams.get('sgRoute'), 'stealth');
  assert.equal(url.searchParams.get('sgStealthRoute'), 'stealth');
  assert.equal(url.searchParams.get('sgUserAgent'), 'Custom UA');
});


test('buildDebuggerUrl allows profile seeding stealth endpoint opt-out', () => {
  const url = new URL(buildDebuggerUrl({
    BROWSERLESS_STEALTH_ENDPOINT: 'false',
  }, 'main', 'https://accounts.google.com'));

  assert.equal(url.searchParams.get('sgStealthEndpoint'), 'false');
});
