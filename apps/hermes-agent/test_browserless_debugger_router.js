'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadRouterContext(search = '') {
  const script = fs.readFileSync(
    path.resolve(__dirname, '../../infra/caddy/browserless-debugger-router.js'),
    'utf8',
  );
  const context = {
    Event: class Event {},
    Headers,
    Response,
    URL,
    URLSearchParams,
    document: {
      querySelector: () => null,
    },
    location: {
      hash: '',
      origin: 'http://localhost:3005',
      pathname: '/debugger/',
      search,
    },
    window: {
      WebSocket: function WebSocket(url) {
        context.lastWebSocketUrl = String(url);
      },
      fetch: async () => new Response('[]'),
      history: {
        pushState: () => {},
      },
      localStorage: {
        getItem: () => null,
        setItem: () => {},
      },
      location: {
        href: `http://localhost:3005/debugger/${search}`,
        search,
      },
      setInterval: () => 0,
      clearInterval: () => {},
      setTimeout: (callback) => callback(),
    },
  };
  context.globalThis = context;
  vm.runInNewContext(script, context, { filename: 'browserless-debugger-router.js' });
  return context;
}

test('debugger router route mapping matches Browserless route names', () => {
  const context = loadRouterContext();

  const cases = [
    ['chromium', '/chromium'],
    ['chrome', '/chrome'],
    ['stealth', '/stealth'],
    ['chromium-stealth', '/chromium/stealth'],
    ['chromium/stealth', '/chromium/stealth'],
    ['chrome-stealth', '/chrome/stealth'],
    ['chrome/stealth', '/chrome/stealth'],
  ];

  for (const [route, expected] of cases) {
    const url = new URL('ws://browserless:3000/chromium');
    context.applyStealthEndpoint(url, route);
    assert.equal(url.pathname, expected, route);
  }
});

test('debugger router accepts canonical sgRoute over legacy sgStealthRoute', () => {
  const context = loadRouterContext('?sgRoute=stealth&sgStealthRoute=chromium');
  const launch = encodeURIComponent(JSON.stringify({ args: [] }));

  new context.window.WebSocket(`ws://browserless:3000/chromium?launch=${launch}`);

  assert.equal(new URL(context.lastWebSocketUrl).pathname, '/stealth');
});
