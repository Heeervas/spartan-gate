const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const asset = name => fs.readFileSync(`${__dirname}/web/assets/${name}.js`, 'utf8');

// Minimal browser boundary for the correlation renderer and asynchronous detail
// loads. Existing runtime tests retain their independent DOM-reconciliation fake.
class Element {
  constructor() { this.dataset = {}; this.children = []; this.value = ''; this.innerHTML = ''; this.isConnected = true; }
  addEventListener() {}
  setAttribute() {}
}
function harness() {
  const elements = new Map();
  const requests = [];
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    addEventListener() {},
  };
  const context = vm.createContext({
    document, window: {}, AbortController, URLSearchParams, TypeError,
    location: { search: '', pathname: '/dashboard' }, history: { replaceState() {} },
    sessionStorage: { getItem() { return ''; } }, setTimeout() {}, clearTimeout() {},
    fetch(path, options) { return new Promise(resolve => requests.push({ path, options, resolve })); },
  });
  vm.runInContext(asset('dashboard').replace(/    load\(\);\s*$/, ''), context);
  return {
    context, requests, run: code => vm.runInContext(code, context),
    respond: (request, payload) => request.resolve({ ok: true, json: async () => payload }),
  };
}

test('equal execution turn IDs load independent logical-session details', async () => {
  const h = harness();
  const rendered = [];
  h.context.window.SpartanRouteDetails = { renderRequests: (target, rows) => rendered.push([target.label, rows[0]]) };
  const first = new Element(); first.label = 'first';
  const second = new Element(); second.label = 'second';
  h.context.first = first; h.context.second = second;
  h.context.firstItem = { dataset: { sessionId: 'logical-1', detailVersion: 'v1' } };
  h.context.secondItem = { dataset: { sessionId: 'logical-2', detailVersion: 'v1' } };
  h.run('firstLoad = loadTurn("shared-turn", first, firstItem); secondLoad = loadTurn("shared-turn", second, secondItem)');
  assert.match(h.requests[0].path, /shared-turn\?sessionId=logical-1$/);
  assert.match(h.requests[1].path, /shared-turn\?sessionId=logical-2$/);
  assert.equal(h.requests[0].options.signal.aborted, false);
  assert.equal(h.run('turnLoads.size'), 2);
  h.respond(h.requests[0], { requests: ['one'] });
  h.respond(h.requests[1], { requests: ['two'] });
  await h.run('Promise.all([firstLoad, secondLoad])');
  assert.deepEqual(rendered, [['first', 'one'], ['second', 'two']]);
});

test('session relationship disclosures keep state but do not retain stale assertions', () => {
  const h = harness();
  const diagnostic = new Element(); diagnostic.open = true; diagnostic.innerHTML = 'old parent';
  const incoming = new Element(); incoming.innerHTML = 'new parent';
  incoming.replaceWith = item => { incoming.replacement = item; };
  h.context.old = { querySelector: () => diagnostic };
  h.context.next = { querySelector: () => incoming };
  h.run('retainSessionDiagnostics(next, old)');
  assert.equal(diagnostic.innerHTML, 'new parent');
  assert.equal(diagnostic.open, true);
  assert.equal(incoming.replacement, diagnostic);
});

test('correlated children group by visible root without merging sessions or cache cohorts', () => {
  const h = harness();
  h.context.sessions = [
    { id: 'child', correlation: { version: 1, source: 'client_asserted', client: 'ultimate', conversation: 'child', root: 'root', parent: 'root', purpose: 'agent' } },
    { id: 'root', correlation: { version: 1, source: 'client_asserted', client: 'ultimate', conversation: 'root', root: 'root', purpose: 'summary' } },
    { id: 'legacy' },
    { id: 'pi', correlation: { version: 1, source: 'client_asserted', client: 'pi', conversation: 'pi', root: 'root', purpose: 'agent' } },
  ];
  assert.equal(h.run('JSON.stringify(groupSessions(sessions).map(group => group.sessions.map(session => session.id)))'), '[["child","root"],["legacy"],["pi"]]');
  assert.match(h.run('correlationDetails(sessions[0], sessions)'), /data-session-jump="root"/);
  assert.doesNotMatch(h.run('correlationDetails(sessions[3], sessions)'), /data-session-jump=/);
  assert.match(h.run('correlationSummary(sessions[2])'), /Legacy identity/);
  assert.equal(h.run('sessions.length'), 4);
});

test('correlation labels escape diagnostic data and disclose client assertions', () => {
  const h = harness();
  h.context.session = { id: 'child', purposes: ['agent', 'summary'], correlation: {
    version: 1, source: 'client_asserted', client: 'ultimate', conversation: 'child',
    root: '<script>root</script>', parent: 'missing', purpose: 'agent',
  } };
  const summary = h.run('correlationSummary(session)');
  assert.match(summary, /Ultimate/);
  assert.match(summary, /Agent.*Summary/);
  const details = h.run('correlationDetails(session, [session])');
  assert.match(details, /Client asserted/);
  assert.match(details, /&lt;script&gt;root&lt;\/script&gt;/);
  assert.doesNotMatch(details, /<script>/);
  assert.match(details, /not in this view/);
  assert.equal(h.run('groupSessions([]).length'), 0);
});

test('request detail keeps auxiliary purpose, operation and recorded attempts distinct', () => {
  const h = harness();
  vm.runInContext(asset('dashboard-details'), h.context);
  h.context.row = { requestId: 'request-1', context: {
    correlation: { version: 1, source: 'client_asserted', client: 'ultimate', conversation: 'owner', purpose: 'summary', operation: '<operation>' },
    native_cache: { version: 2, comparison: 'append', attempts: [{}, {}] },
  } };
  const markup = h.run('renderRequest(row)');
  assert.match(markup, /Ultimate · Summary/);
  assert.match(markup, /2 native attempts recorded/);
  assert.match(markup, /Client-asserted operation: &lt;operation&gt;/);
  assert.match(markup, /request request-1/);
  assert.doesNotMatch(markup, /<operation>/);
});

test('passive cache observations are not labeled as additional inference checks', () => {
  const h = harness();
  assert.match(h.run('nativeCacheSummary({requests: 3, comparisons: {append: 2}, usageRequests: 3, cacheReadKnownRequests: 3})'), /3 observed requests/);
  assert.doesNotMatch(h.run('metricLine({nativeCache: {requests: 3}}, [])'), /Cache checks|3 checks/);
  assert.match(h.run('metricLine({nativeCache: {requests: 3}}, [])'), /Cache observations/);
});
