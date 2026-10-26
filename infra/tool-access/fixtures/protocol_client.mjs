// Second, non-Pi HTTP client: only ingress network and its own scoped token.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash } from 'node:crypto';

const config = JSON.parse(fs.readFileSync('/fixture/client.json', 'utf8'));
const token = fs.readFileSync('/fixture/client.token', 'utf8').trim();
const mode = process.argv[2];
const receipt = { client: 'restricted-node-http', node: process.version, mode, checks: [] };

async function request(route, { method = 'GET', body, workflow, bearer = token, status = 200 } = {}) {
  const headers = {};
  if (bearer !== null) headers.Authorization = `Bearer ${bearer}`;
  if (workflow) headers['X-Spartan-Workflow'] = workflow;
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(config.baseUrl + route, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
    redirect: 'manual', signal: AbortSignal.timeout(30000),
  });
  if (response.status !== status) receipt.failureStatus = {
    method, route: route.split('?')[0], expected: status, actual: response.status,
  };
  assert.equal(response.status, status, `unexpected status for ${method} ${route.split('?')[0]}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  assert.ok(bytes.length <= 2 * 1024 * 1024, 'bounded fixture result');
  return { text: new TextDecoder().decode(bytes), bytes, headers: response.headers };
}

async function json(route, options) {
  return JSON.parse((await request(route, options)).text);
}

async function journey() {
  receipt.stage = 'search';
  const search = await json('/search?q=isolated%20fixture&format=json');
  assert.equal(search.query, 'isolated fixture');
  assert.equal(search.results[0].title, 'Synthetic search result');
  receipt.checks.push('actual-edge-auth-search-transport');
  receipt.stage = 'reader';
  const read = await request('/fetch?url=' + encodeURIComponent('http://fixture.public.example:8080/page'));
  assert.equal(read.headers.get('x-spartan-content-trust'), 'untrusted-web-content');
  assert.ok(read.text.startsWith('BEGIN_UNTRUSTED_WEB_CONTENT\n'));
  assert.ok(read.text.includes('Isolated managed browser fixture'));
  assert.ok(read.text.endsWith('\nEND_UNTRUSTED_WEB_CONTENT\n'));
  receipt.checks.push('actual-reader-http-test-only-global-transport');
  await request('/fetch?url=' + encodeURIComponent('http://127.0.0.1/'), { status: 400 });
  await request('/fetch?url=' + encodeURIComponent('http://169.254.169.254/latest/meta-data/'), { status: 400 });
  await request('/fetch?url=' + encodeURIComponent('http://fixture.public.example:8080/redirect-private'), { status: 502 });
  receipt.checks.push('private-linklocal-and-redirect-denied');
  receipt.stage = 'mint-browser-workflow';
  const { workflowToken: workflow } = await json('/v1/workflows', { method: 'POST', body: {} });
  assert.match(workflow, /^[a-f0-9]{64}$/);
  let tabId;
  try {
    receipt.stage = 'browser-create';
    ({ tabId } = await json('/v1/browser/create', {
      method: 'POST', body: { url: 'http://fixture:8080/page' }, workflow,
    }));
    assert.equal(typeof tabId, 'string');
    receipt.stage = 'browser-snapshot';
    let snapshot = await json('/v1/browser/snapshot', { method: 'POST', body: { tabId }, workflow });
    assert.ok(snapshot.snapshot.includes('Fixture button'));
    receipt.stage = 'browser-type';
    await json('/v1/browser/type', { method: 'POST', body: { tabId, selector: '#fixture-input', text: 'Synthetic input' }, workflow });
    receipt.stage = 'browser-click';
    await json('/v1/browser/click', { method: 'POST', body: { tabId, selector: '#fixture-button' }, workflow });
    receipt.stage = 'browser-post-click-snapshot';
    snapshot = await json('/v1/browser/snapshot', { method: 'POST', body: { tabId }, workflow });
    assert.ok(snapshot.snapshot.includes('Clicked once'));
    receipt.stage = 'browser-scroll';
    await json('/v1/browser/scroll', { method: 'POST', body: { tabId, direction: 'down', amount: 300 }, workflow });
    receipt.stage = 'browser-screenshot';
    const image = await request('/v1/browser/screenshot', { method: 'POST', body: { tabId }, workflow });
    assert.equal(image.headers.get('content-type'), 'image/png');
    assert.deepEqual([...image.bytes.slice(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    receipt.screenshot = { bytes: image.bytes.length, sha256: createHash('sha256').update(image.bytes).digest('hex') };
    receipt.stage = 'browser-owned-list';
    const listed = await json('/v1/browser/tabs', { workflow });
    assert.deepEqual(listed.tabs, [{ tabId }]);
    receipt.checks.push('actual-browser-create-snapshot-type-single-click-scroll-png-owned-list');
    receipt.stage = 'browser-close';
    await json('/v1/browser/close', { method: 'POST', body: { tabId }, workflow });
    assert.deepEqual((await json('/v1/browser/tabs', { workflow })).tabs, []);
    receipt.checks.push('owned-close');
  } finally {
    await json('/v1/workflows', { method: 'DELETE', body: {}, workflow });
  }
}

async function denied() {
  for (const bearer of [null, '0'.repeat(64)]) {
    await request('/search?q=denial&format=json', { bearer, status: 401 });
  }
  receipt.checks.push('missing-and-wrong-auth-denied');
}

async function bypass() {
  for (const target of config.bypass) {
    let connected = false;
    try {
      // Any HTTP response, including 401/404, would demonstrate network reachability.
      await fetch(target, { redirect: 'manual', signal: AbortSignal.timeout(1200) });
      connected = true;
    } catch { /* Network denial or deadline: never an HTTP permission verdict. */ }
    assert.equal(connected, false, 'restricted client reached a forbidden fixture address');
  }
  for (const port of [18789, 9119, 26080]) {
    let connected = false;
    try {
      await fetch(`http://tool-edge:${port}/`, { signal: AbortSignal.timeout(1200) });
      connected = true;
    } catch { /* Dedicated edge must not provide legacy listeners. */ }
    assert.equal(connected, false, 'dedicated edge exposed a legacy listener');
  }
  receipt.checks.push('owned-backend-intermediary-ips-and-legacy-ports-unreachable');
}

try {
  if (mode === 'journey') await journey();
  else if (mode === 'denied') await denied();
  else if (mode === 'revoked') {
    await request('/search?q=revoked&format=json', { status: 401 });
    receipt.checks.push('revoked-auth-denied');
  } else if (mode === 'bypass') await bypass();
  else throw new Error('unknown fixture mode');
  console.log(JSON.stringify({ ...receipt, status: 'PASS', browserMode: 'TEST-ONLY addons disabled; NOT default UBO proof' }));
} catch {
  console.error(JSON.stringify({ ...receipt, status: 'FAIL', error: 'isolated protocol assertion failed' }));
  process.exitCode = 1;
}
