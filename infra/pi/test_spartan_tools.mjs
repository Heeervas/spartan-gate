import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, writeFile, chmod, mkdir, rm, readdir, stat, symlink } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { deflateSync, gzipSync } from 'node:zlib';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { loadConfig, FAILURE } from './extensions/spartan-tools/config.mjs';
import { pngImage } from './extensions/spartan-tools/http.mjs';
import { ToolsClient, browserBody } from './extensions/spartan-tools/client.mjs';
import { configure } from '../../scripts/configure-shared-tools.mjs';

const packageDir = process.env.PI_TEST_PACKAGE_DIR || process.env.PI_CODING_AGENT_DIR;
const webExtension = process.env.PI_WEB_ACCESS_EXTENSION;
const extensionPath = fileURLToPath(new URL('./extensions/spartan-tools.ts', import.meta.url));
const token = 'a'.repeat(64);
const workflowTokens = ['b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64)];

function png() {
  const chunk = (type, data) => {
    const block = Buffer.concat([Buffer.from(type), data]);
    let crc = 0xffffffff;
    for (const byte of block) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0); }
    const result = Buffer.alloc(data.length + 12);
    result.writeUInt32BE(data.length); block.copy(result, 4); result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4);
    return result;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(1); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.from([0,255,0,0,255]))), chunk('IEND', Buffer.alloc(0))]);
}

async function fixture(run) {
  assert.ok(packageDir, 'PI_TEST_PACKAGE_DIR or PI_CODING_AGENT_DIR must identify actual Pi program package');
  assert.ok(webExtension, 'PI_WEB_ACCESS_EXTENSION must identify installed public program entry');
  const directory = await mkdtemp(join(tmpdir(), 'gate-pi-tools-'));
  const saved = { ...process.env };
  const calls = [];
  const faults = new Map();
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    calls.push({ path: req.url, method: req.method, headers: req.headers, body });
    if (req.headers.authorization !== `Bearer ${token}`) { res.writeHead(401); res.end('private auth error'); return; }
    if (faults.has(req.url.split('?')[0])) { faults.get(req.url.split('?')[0])(req, res); return; }
    if (req.url.startsWith('/fetch?')) {
      res.writeHead(200, { 'x-spartan-content-trust': 'untrusted-web-content' });
      res.end('BEGIN_UNTRUSTED_WEB_CONTENT\nfixture text\nEND_UNTRUSTED_WEB_CONTENT\n'); return;
    }
    if (req.url === '/v1/browser/screenshot') { res.writeHead(200, { 'content-type': 'image/png' }); res.end(png()); return; }
    res.setHeader('content-type', 'application/json');
    if (req.url.startsWith('/search?')) { res.end(JSON.stringify({ results: [{ url: 'https://example.com', title: 'Managed fixture', content: `fixture ${token}` }] })); return; }
    if (req.url === '/v1/workflows' && req.method === 'POST') { res.end(JSON.stringify({ workflowToken: workflowTokens[calls.filter(c => c.path === '/v1/workflows' && c.method === 'POST').length - 1] })); return; }
    if (req.url === '/v1/browser/create') { res.end(JSON.stringify({ tabId: 'owned-tab' })); return; }
    res.end(JSON.stringify({ ok: true }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => {
    assert.equal(new URL(url).origin, baseUrl, 'no substitute outbound transport');
    return originalFetch(url, init);
  };
  const agentDir = join(directory, 'agent');
  await mkdir(agentDir, { mode: 0o700 });
  const configPath = join(directory, 'tools.json');
  const config = { version: 1, baseUrl, credentialFile: join(directory, 'token'), stateDir: join(directory, 'state'), clientId: 'fixture' };
  const webConfig = { provider: 'searxng', workflow: 'none', searxngBaseUrl: baseUrl, searxngHeaders: { Authorization: `Bearer ${token}` },
    webSearch: { allowedProviders: ['searxng'] }, tools: { sourceCheck: { enabled: false }, fetchContent: { enabled: false } },
    commands: Object.fromEntries(['websearch','curator','search','google-account'].map(key => [key,{ enabled: false }])) };
  await writeFile(configPath, JSON.stringify(config), { mode: 0o600 });
  await writeFile(config.credentialFile, token, { mode: 0o600 });
  await writeFile(join(agentDir, 'web-search.json'), JSON.stringify(webConfig), { mode: 0o600 });
  // No live home, model credentials, package discovery, or inherited endpoint overrides.
  process.env.PI_CODING_AGENT_DIR = agentDir;
  process.env.PI_SPARTAN_TOOLS_CONFIG = configPath;
  process.env.JITI_FS_CACHE = '0';
  delete process.env.SEARXNG_BASE_URL;
  delete process.env.PI_OFFLINE;
  try { await run({ directory, agentDir, configPath, config, calls, faults, baseUrl }); }
  finally {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
    globalThis.fetch = originalFetch;
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
}

async function native(f, manager, turns) {
  const P = pathToFileURL(resolve(packageDir) + '/').href;
  const sdk = await import(P + 'dist/index.js');
  const { loadExtensions } = await import(P + 'dist/core/extensions/loader.js');
  const require = createRequire(new URL('package.json', P));
  const aiEntry = require.resolve.paths('@earendil-works/pi-ai').map(path => join(path, '@earendil-works/pi-ai/dist/index.js')).find(existsSync);
  assert.ok(aiEntry, 'actual Pi dependency pi-ai must be provisioned');
  const { InMemoryCredentialStore, AssistantMessageEventStream } = await import(pathToFileURL(aiEntry).href);
  const loaded = await loadExtensions([webExtension, f.extensionPath ?? extensionPath], f.directory);
  assert.deepEqual(loaded.errors, []);
  const settingsManager = sdk.SettingsManager.inMemory({ cacheWarming: 'off', retry: { enabled: false } });
  const resourceLoader = new sdk.DefaultResourceLoader({ cwd: f.directory, agentDir: f.agentDir, settingsManager,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true });
  resourceLoader.getExtensions = () => loaded;
  const runtime = await sdk.ModelRuntime.create({ credentials: new InMemoryCredentialStore(), modelsPath: null,
    modelsStorePath: join(f.directory, 'models-cache'), refreshOnCreate: false, allowModelNetwork: false });
  const transcripts = [];
  let turn = 0;
  runtime.registerProvider('gate-fixture', { baseUrl: 'http://127.0.0.1:1', api: 'gate-fixture', apiKey: 'synthetic',
    models: [{ id: 'fixture', name: 'Fixture', reasoning: false, input: ['text','image'], contextWindow: 100000, maxTokens: 1000,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    streamSimple(model, context) {
      transcripts.push(context);
      const content = turns[turn++] ?? [{ type: 'text', text: 'done' }];
      const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, content,
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        stopReason: content.some(c => c.type === 'toolCall') ? 'toolUse' : 'stop', timestamp: Date.now() };
      const stream = new AssistantMessageEventStream();
      stream.push({ type: 'done', reason: message.stopReason, message }); stream.end();
      return stream;
    } });
  const sessionManager = manager ?? sdk.SessionManager.create(f.directory, join(f.directory, 'sessions'));
  const { session } = await sdk.createAgentSession({ cwd: f.directory, agentDir: f.agentDir, sessionManager,
    modelRuntime: runtime, model: runtime.getModel('gate-fixture','fixture'), settingsManager, resourceLoader, noTools: 'builtin',
    customTools: [{ name: 'bridge', label: 'Fixture bridge', description: 'Fixture nested tool call',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      execute: (_id, _args, _signal, _update, ctx) => ctx.executeTool('web_search', { query: 'nested forbidden', includeContent: true }) },
    { name: 'local_fixture', label: 'Local fixture', description: 'Local non-managed operation',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      execute: async () => ({ content: [{ type: 'text', text: 'local operation works' }], details: {} }) },
    { name: 'break_config_fixture', label: 'Fixture invalid config', description: 'Change only disposable fixture permissions',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      execute: async () => { await chmod(f.configPath, 0o644); return { content: [{ type: 'text', text: 'fixture changed' }], details: {} }; } }] });
  session.agent.toolExecution = 'sequential';
  const toolResults = new Map();
  session.subscribe(event => { if (event.type === 'tool_execution_end') toolResults.set(event.toolCallId, event.result); });
  const hookCalls = [];
  loaded.extensions[0].handlers.set('tool_call', [...(loaded.extensions[0].handlers.get('tool_call') ?? []), e => { hookCalls.push(e); }]);
  await session.bindExtensions({ mode: 'rpc', onError: error => { throw new Error(JSON.stringify(error)); } });
  return { session, sdk, transcripts, hookCalls, loaded, sessionManager, toolResults };
}

const call = (id, name, args) => ({ type: 'toolCall', id, name, arguments: args });

test('installer replaces prior provider routing and installed native search loads without changing foreign fields', async () => fixture(async f => {
  const webPath = join(f.agentDir, 'web-search.json');
  const previous = JSON.parse(await readFile(webPath, 'utf8'));
  previous.searchRouting = { providers: ['tavily'], fallbackOn: ['network'] };
  previous.foreign = { keep: 'unrelated setting' };
  await writeFile(webPath, JSON.stringify(previous));
  const output = [];
  assert.equal(configure(['--agent-dir', f.agentDir, '--base-url', f.baseUrl,
    '--client-id', f.config.clientId, '--credential-file', f.config.credentialFile,
    '--state-dir', f.config.stateDir, '--apply', '--backup-dir', join(f.directory, 'backups')],
    process.env, line => output.push(line)), 0);
  assert.ok(!output.join('\n').includes(token));
  f.configPath = join(f.agentDir, 'spartan-tools.json');
  f.extensionPath = join(f.agentDir, 'extensions/spartan-tools.ts');
  process.env.PI_SPARTAN_TOOLS_CONFIG = f.configPath;
  const n = await native(f, undefined, [[call('installed', 'web_search', { query: 'installed strict search' })]]);
  try {
    await n.session.prompt('Synthetic installed extension only.');
    assert.equal(f.calls.length, 1);
    assert.ok(f.calls[0].path.startsWith('/search?'));
    assert.equal(n.toolResults.get('installed').structuredContent?.queries[0].results[0].title, 'Managed fixture');
    const installed = JSON.parse(await readFile(webPath, 'utf8'));
    assert.equal(Object.hasOwn(installed, 'searchRouting'), false);
    assert.deepEqual(installed.foreign, previous.foreign);
    assert.deepEqual(installed.webSearch.allowedProviders, ['searxng']);
  } finally { n.session.dispose(); }
}));

for (const scenario of ['unset-existing', 'explicit-override', 'explicit-empty', 'explicit-relative',
  'explicit-invalid', 'explicit-missing', 'default-missing', 'default-unsafe', 'default-wrong-profile']) {
  test(`native selector ${scenario} uses only its selected configuration`, async () => fixture(async f => {
    const defaultPath = join(f.agentDir, 'spartan-tools.json');
    await writeFile(defaultPath, JSON.stringify(f.config), { mode: 0o600 });
    delete process.env.PI_SPARTAN_TOOLS_CONFIG;
    switch (scenario) {
      case 'explicit-override':
        process.env.PI_SPARTAN_TOOLS_CONFIG = f.configPath;
        await writeFile(defaultPath, 'invalid default');
        break;
      case 'explicit-empty': process.env.PI_SPARTAN_TOOLS_CONFIG = ''; break;
      case 'explicit-relative': process.env.PI_SPARTAN_TOOLS_CONFIG = 'tools.json'; break;
      case 'explicit-invalid':
        process.env.PI_SPARTAN_TOOLS_CONFIG = f.configPath;
        await writeFile(f.configPath, 'invalid explicit config');
        break;
      case 'explicit-missing': process.env.PI_SPARTAN_TOOLS_CONFIG = join(f.directory, 'missing.json'); break;
      case 'default-missing': await rm(defaultPath); break;
      case 'default-unsafe': await chmod(defaultPath, 0o644); break;
      case 'default-wrong-profile': {
        const webPath = join(f.agentDir, 'web-search.json');
        const web = JSON.parse(await readFile(webPath, 'utf8'));
        web.searxngHeaders.Authorization = `Bearer ${'e'.repeat(64)}`;
        await writeFile(webPath, JSON.stringify(web));
        break;
      }
    }
    const n = await native(f, undefined, [[call('selected-reader', 'gate_reader', { url: 'https://example.com' })]]);
    try {
      await n.session.prompt('Synthetic configuration selection only.');
      const result = n.session.agent.state.messages.find(message => message.role === 'toolResult' && message.toolCallId === 'selected-reader');
      assert.ok(result, 'native extension must finish the managed tool call');
      if (['unset-existing', 'explicit-override'].includes(scenario)) {
        assert.notEqual(result.isError, true);
        assert.match(JSON.stringify(result.content), /fixture text/);
        assert.equal(f.calls.length, 1);
        assert.ok(f.calls[0].path.startsWith('/fetch?'));
      } else {
        assert.equal(result.isError, true);
        assert.match(JSON.stringify(result.content), new RegExp(FAILURE));
        assert.equal(f.calls.length, 0, 'rejected selection must not issue HTTP or fall back');
      }
    } finally { n.session.dispose(); }
  }));
}

test('native search errors retain a safe renderer error and an exact public query reason', async () => fixture(async f => {
  const n = await native(f, undefined, [
    [call('missing-query', 'web_search', { queries: [] })], [{ type: 'text', text: 'done' }],
    [call('query-only', 'web_search', { query: 'IANA example domains' })], [{ type: 'text', text: 'done' }],
  ]);
  try {
    await n.session.prompt('Synthetic empty queries precedence candidate, not a user-cause claim.');
    const failed = n.toolResults.get('missing-query');
    assert.equal(failed.isError, true);
    assert.equal(f.calls.length, 0, 'native empty-query rejection must precede HTTP');
    assert.equal(failed.details.error, 'Managed search requires a non-empty query or queries.');
    const renderer = n.loaded.extensions[0].tools.get('web_search').definition.renderResult;
    const colors = [];
    const rendered = renderer(failed, { expanded: false, isPartial: false }, { fg: (color, text) => { colors.push(color); return text; }, bg: (_color, text) => text, bold: text => text }).render(120).join('\n');
    assert.match(rendered, /Managed search requires/);
    assert.ok(colors.includes('error'));
    assert.ok(!rendered.includes('undefined/undefined'));
    const hidden = new ToolsClient(f.configPath, f.agentDir).searchResult({ toolName: 'web_search', isError: true,
      content: [{ type: 'text', text: `private upstream ${token}` }], details: { error: `private upstream ${token}` } });
    assert.equal(hidden.details.error, FAILURE);
    assert.ok(!JSON.stringify(hidden).includes(token));
    await n.session.prompt('Synthetic query-only control.');
    assert.equal(Boolean(n.toolResults.get('query-only').isError), false);
    assert.equal(f.calls.length, 1);
  } finally { n.session.dispose(); }
}));

test('native search accepts real neutral defaults and only the configured provider', async () => fixture(async f => {
  const defaults = { query: 'Spartan Gate GitHub', queries: [], numResults: 3,
    includeContent: false, recencyFilter: 'year', domainFilter: [], category: '',
    provider: 'auto', workflow: 'none', proxy: '' };
  const n = await native(f, undefined, [[
    call('actual-search', 'web_search', defaults),
    call('provider-list', 'web_search', { query: 'allowed', provider: ['searxng'] }),
    call('all-providers', 'web_search', { query: 'denied', provider: 'all' }),
    call('mixed-providers', 'web_search', { query: 'denied', provider: ['searxng', 'tavily'] }),
  ]]);
  try {
    await n.session.prompt('Synthetic actual search arguments; no real engines.');
    for (const id of ['actual-search', 'provider-list']) {
      assert.equal(Boolean(n.toolResults.get(id).isError), false, id);
      assert.equal(n.toolResults.get(id).structuredContent?.queries[0].results[0].title, 'Managed fixture', id);
    }
    assert.equal(f.calls.length, 2, 'forbidden providers must issue zero HTTP');
    assert.equal(new URL(f.calls[0].path, f.baseUrl).searchParams.get('q'), 'Spartan Gate GitHub');
    const messages = n.session.agent.state.messages.filter(message => message.role === 'toolResult');
    for (const id of ['all-providers', 'mixed-providers']) assert.equal(messages.find(message => message.toolCallId === id).isError, true);
  } finally { n.session.dispose(); }
}));

test('native prompt enforces strict search for model and nested calls with zero substitute requests', async () => fixture(async f => {
  const n = await native(f, undefined, [[
    call('valid','web_search',{ query: 'allowed', provider: 'searxng' }),
    call('content','web_search',{ query: 'forbidden', includeContent: true }),
    call('summary','web_search',{ query: 'forbidden', workflow: 'auto-summary' }),
    call('proxy','web_search',{ query: 'forbidden', proxy: 'http://127.0.0.1:1' }),
    call('provider','web_search',{ query: 'forbidden', provider: 'tavily' }),
    call('nested','bridge',{}),
    call('local-valid','local_fixture',{}),
    call('invalidate','break_config_fixture',{}),
    call('invalid-config','web_search',{ query: 'blocked config' }),
    call('local-invalid','local_fixture',{}),
  ]]);
  try {
    await n.session.prompt('Synthetic tools only; no real model.');
    assert.equal(f.calls.length, 1);
    assert.ok(f.calls[0].path.startsWith('/search?'));
    const messages = n.session.agent.state.messages;
    const results = messages.filter(m => m.role === 'toolResult');
    assert.equal(results.length, 10);
    assert.match(JSON.stringify(results[0]), /Managed fixture/);
    // Pi intentionally omits structuredContent from ToolResultMessage/history.
    // The finalized execution event is the native structured tool contract after hooks.
    const structured = n.toolResults.get('valid').structuredContent;
    assert.equal(structured?.queries[0].results[0].title, 'Managed fixture');
    assert.equal(structured.queries[0].results[0].snippet, 'fixture [redacted]');
    for (const result of results.slice(1, 6)) assert.equal(result.isError, true);
    assert.match(JSON.stringify(results[6]), /local operation works/);
    assert.equal(results[8].isError, true);
    assert.match(JSON.stringify(results[9]), /local operation works/);
    assert.ok(n.hookCalls.some(e => e.parentToolCallId && e.toolName === 'web_search'));
    assert.ok(!JSON.stringify(messages).includes(token));
    assert.ok(!n.loaded.extensions[0].commands.has('curator'));
    assert.ok(!n.loaded.extensions[0].commands.has('websearch'));
  } finally { n.session.dispose(); }
}));

test('native browser tabs drops the actual user optional defaults without weakening active fields', async () => fixture(async f => {
  const defaults = { tabId: '', url: '', ref: '', selector: '', text: '', direction: 'down', amount: 1 };
  f.faults.set('/v1/browser/tabs', (_req, res) => {
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ tabs: [{ tabId: 'owned-tab' }] }));
  });
  f.faults.set('/v1/browser/navigate', (_req, res) => {
    res.writeHead(400, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { code: 'invalid_request' } }));
  });
  const n = await native(f, undefined, [[
    call('actual-tabs', 'gate_browser', { action: 'tabs', ...defaults }),
    call('empty-tab', 'gate_browser', { action: 'snapshot', ...defaults }),
    call('invalid-amount', 'gate_browser', { action: 'scroll', ...defaults, tabId: 'owned-tab', amount: 0 }),
    call('invalid-url', 'gate_browser', { action: 'navigate', ...defaults, tabId: 'owned-tab' }),
    call('unknown-null', 'gate_browser', { action: 'tabs', extra: null }),
    call('unsupported-action', 'gate_browser', { action: 'evaluate' }),
  ]]);
  try {
    await n.session.prompt('Synthetic browser argument regression only.');
    const results = n.session.agent.state.messages.filter(message => message.role === 'toolResult');
    assert.equal(results.length, 6);
    assert.equal(results[0].isError, false, 'the actual user tabs arguments must succeed');
    assert.match(JSON.stringify(results[0].content), /owned-tab/);
    assert.ok(results.slice(1).every(result => result.isError === true));
    assert.deepEqual(f.calls.map(request => request.path), ['/v1/workflows', '/v1/browser/tabs', '/v1/browser/navigate']);
    assert.equal(f.calls[1].method, 'GET');
    assert.equal(f.calls[1].body, '', 'tabs must not forward optional defaults');
    assert.equal(f.calls[1].headers['x-spartan-workflow'], workflowTokens[0]);
    assert.deepEqual(JSON.parse(f.calls[2].body), { tabId: 'owned-tab', url: '' }, 'invalid active URL must reach backend validation unchanged');
  } finally { n.session.dispose(); }

  const activeByAction = {
    create: { url: 'https://example.com' }, tabs: {}, cleanup: {}, snapshot: { tabId: 'owned-tab' },
    navigate: { tabId: 'owned-tab', url: 'https://example.com' },
    click: { tabId: 'owned-tab', ref: 'button', selector: '#button' },
    type: { tabId: 'owned-tab', ref: 'input', selector: '#input', text: 'typed text' },
    scroll: { tabId: 'owned-tab', direction: 'up', amount: 7 },
    screenshot: { tabId: 'owned-tab' }, close: { tabId: 'owned-tab' },
  };
  for (const [action, active] of Object.entries(activeByAction)) {
    assert.deepEqual(browserBody({ action, ...defaults, ...active }), active, `${action} keeps only its active fields`);
    assert.throws(() => browserBody({ action, ...defaults, ...active, extra: null }), new RegExp(FAILURE));
  }
  assert.deepEqual(browserBody({ action: 'type', tabId: 'owned-tab', text: null }), { tabId: 'owned-tab', text: null });
  assert.deepEqual(browserBody({ action: 'scroll', tabId: 'owned-tab', direction: 'sideways', amount: 0 }),
    { tabId: 'owned-tab', direction: 'sideways', amount: 0 });
  assert.throws(() => browserBody({ action: 'snapshot', ...defaults }), new RegExp(FAILURE));
  assert.throws(() => browserBody({ action: 'evaluate', ...defaults }), new RegExp(FAILURE));
}));

test('native prompt Reader/image pipeline and actual disk resume/fork keep workflow authority outside history', async () => fixture(async f => {
  const first = await native(f, undefined, [[call('read','gate_reader',{ url: 'https://example.com' }),
    call('create','gate_browser',{ action: 'create' }), call('image','gate_browser',{ action: 'screenshot', tabId: 'owned-tab' })]]);
  try {
    await first.session.prompt('Synthetic managed lifecycle.');
    const images = first.session.agent.state.messages.filter(m => m.role === 'toolResult').flatMap(m => m.content).filter(c => c.type === 'image');
    assert.equal(images.length, 1);
    assert.equal(images[0].mimeType, 'image/png');
    assert.ok(first.transcripts.some(t => JSON.stringify(t).includes('image/png')));
    assert.ok(!JSON.stringify(first.sessionManager.getEntries()).includes(workflowTokens[0]));
    const sessionFile = first.sessionManager.getSessionFile();
    const id = first.sessionManager.getSessionId();
    first.session.dispose();
    const resumedManager = first.sdk.SessionManager.open(sessionFile);
    assert.equal(resumedManager.getSessionId(), id);
    const resumed = await native(f, resumedManager, [[call('resume','gate_browser',{ action: 'create' })]]);
    try { await resumed.session.prompt('Synthetic resume.'); } finally { resumed.session.dispose(); }
    assert.equal(f.calls.filter(c => c.path === '/v1/workflows').length, 1);
    const forkedManager = first.sdk.SessionManager.forkFrom(sessionFile, f.directory, join(f.directory, 'sessions'));
    assert.notEqual(forkedManager.getSessionId(), id);
    const forked = await native(f, forkedManager, [[call('fork','gate_browser',{ action: 'create' })]]);
    try { await forked.session.prompt('Synthetic fork.'); } finally { forked.session.dispose(); }
    assert.equal(f.calls.filter(c => c.path === '/v1/workflows').length, 2);
    const creates = f.calls.filter(c => c.path === '/v1/browser/create');
    assert.equal(creates[0].headers['x-spartan-workflow'], workflowTokens[0]);
    assert.equal(creates[1].headers['x-spartan-workflow'], workflowTokens[0]);
    assert.equal(creates[2].headers['x-spartan-workflow'], workflowTokens[1]);
    assert.equal((await stat(f.config.stateDir)).mode & 0o777, 0o700);
    for (const name of await readdir(f.config.stateDir)) assert.equal((await stat(join(f.config.stateDir,name))).mode & 0o777, 0o600);
  } finally { first.session.dispose(); }
}));

test('native service errors and redirects are sanitized; stale workflow authority is never reminted', async () => fixture(async f => {
  const turns = [[call('search-error','web_search',{ query: 'synthetic failure' }),
    call('encoded-reader','gate_reader',{ url: 'https://example.com' }), call('create','gate_browser',{ action: 'create' }),
    call('redirect','gate_browser',{ action: 'screenshot', tabId: 'owned-tab' })]];
  f.faults.set('/search', (_req,res) => { res.writeHead(502); res.end(`private upstream error ${token}`); });
  f.faults.set('/fetch', (_req,res) => { res.writeHead(200, { 'content-encoding': 'gzip', 'x-spartan-content-trust': 'untrusted-web-content' }); res.end(gzipSync('BEGIN_UNTRUSTED_WEB_CONTENT\nencoded fixture\nEND_UNTRUSTED_WEB_CONTENT\n')); });
  f.faults.set('/v1/browser/screenshot', (_req,res) => { res.writeHead(302, { location: `${f.baseUrl}/unexpected` }); res.end(token); });
  const n = await native(f, undefined, turns);
  try {
    await n.session.prompt('Synthetic redirect refusal.');
    assert.equal(f.calls.length, 5);
    assert.equal(f.calls.find(c => c.path.startsWith('/fetch?')).headers['accept-encoding'], 'identity');
    const encodedResult = n.session.agent.state.messages.find(m => m.role === 'toolResult' && m.toolCallId === 'encoded-reader');
    assert.equal(encodedResult.isError, true);
    assert.ok(!JSON.stringify(n.session.agent.state.messages).includes('encoded fixture'));
    assert.ok(!JSON.stringify(n.session.agent.state.messages).includes('private upstream error'));
    assert.ok(!n.session.agent.state.messages.some(m => m.role === 'toolResult' && m.content.some(c => c.type === 'image')));
    f.faults.set('/v1/browser/create', (_req,res) => { res.writeHead(409, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: { code: 'stale_workflow', message: token } })); });
    turns[2] = [call('stale','gate_browser',{ action: 'create' })];
    await n.session.prompt('Synthetic stale workflow.');
    assert.equal(f.calls.filter(c => c.path === '/v1/workflows').length, 1);
    assert.equal((await readdir(f.config.stateDir)).length, 1);
    assert.match(JSON.stringify(n.session.agent.state.messages), /stale_workflow/);
    assert.ok(!JSON.stringify(n.session.agent.state.messages).includes(token));
    assert.ok(!JSON.stringify(n.session.agent.state.messages).includes(workflowTokens[0]));
    turns[4] = [call('cleanup','gate_browser',{ action: 'cleanup' })];
    await n.session.prompt('Explicit synthetic cleanup.');
    assert.equal((await readdir(f.config.stateDir)).length, 0);
    assert.equal(f.calls.at(-1).method, 'DELETE');
    assert.equal(f.calls.at(-1).path, '/v1/workflows');
  } finally { n.session.dispose(); }
}));

test('native browser exposes only the fixed egress denial code and never remints stale authority', async () => fixture(async f => {
  f.faults.set('/v1/browser/create', (_req, res) => {
    res.writeHead(403, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { code: 'egress_denied', message: token } }));
  });
  f.faults.set('/v1/browser/tabs', (_req, res) => {
    res.writeHead(409, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { code: 'stale_workflow' } }));
  });
  const n = await native(f, undefined, [[
    call('denied-create', 'gate_browser', { action: 'create', url: 'https://www.wikipedia.org' }),
    call('retired-list', 'gate_browser', { action: 'tabs' }),
  ]]);
  try {
    await n.session.prompt('Synthetic proxy denial only; no public navigation.');
    const results = n.session.agent.state.messages.filter(message => message.role === 'toolResult');
    assert.ok(results.every(result => result.isError));
    assert.match(JSON.stringify(results[0].content), /egress_denied/);
    assert.match(JSON.stringify(results[1].content), /stale_workflow/);
    assert.ok(!JSON.stringify(results).includes(token));
    assert.deepEqual(f.calls.map(request => request.path), ['/v1/workflows', '/v1/browser/create', '/v1/browser/tabs']);
  } finally { n.session.dispose(); }
}));

test('workflow mint is single-flight and a replaced session cannot publish a late capability', async () => fixture(async f => {
  let release;
  let entered;
  let arrival = new Promise(resolve => { entered = resolve; });
  f.faults.set('/v1/workflows', (_req,res) => { release = () => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ workflowToken: workflowTokens[0] })); }; entered(); });
  const client = new ToolsClient(f.configPath, f.agentDir);
  const context = id => ({ sessionManager: { getSessionId: () => id } });
  const first = context('fixture-first');
  client.start(first);
  try {
    const requests = [client.execute('browser',{ action: 'create' },undefined,first), client.execute('browser',{ action: 'create' },undefined,first)];
    await arrival;
    assert.equal(f.calls.filter(c => c.path === '/v1/workflows').length, 1);
    release();
    const results = await Promise.all(requests);
    assert.ok(results.every(result => !result.isError));
    assert.equal(f.calls.filter(c => c.path === '/v1/browser/create').length, 2);
    arrival = new Promise(resolve => { entered = resolve; });
    const replaced = context('fixture-replaced');
    client.start(replaced);
    const pending = client.execute('browser',{ action: 'create' },undefined,replaced);
    await arrival;
    client.start(context('fixture-new'));
    release();
    assert.equal((await pending).isError, true);
    assert.equal(f.calls.filter(c => c.path === '/v1/browser/create').length, 2);
    assert.equal((await readdir(f.config.stateDir)).length, 1);
  } finally { client.stop(); }
}));

test('unsafe config, credential symlinks and oversized PNG fail closed', async () => fixture(async f => {
  assert.equal(loadConfig(f.configPath).baseUrl, f.baseUrl);
  await chmod(f.configPath, 0o644);
  assert.throws(() => loadConfig(f.configPath), new RegExp(FAILURE));
  await chmod(f.configPath, 0o600);
  process.env.SEARXNG_BASE_URL = 'http://127.0.0.1:1';
  assert.throws(() => loadConfig(f.configPath));
  delete process.env.SEARXNG_BASE_URL;
  const link = join(f.directory, 'token-link');
  await symlink(f.config.credentialFile, link);
  await writeFile(f.configPath, JSON.stringify({ ...f.config, credentialFile: link }));
  assert.throws(() => loadConfig(f.configPath));
  const image = png();
  assert.equal(pngImage(image).mimeType, 'image/png');
  const huge = Buffer.from(image); huge.writeUInt32BE(4097,16);
  assert.throws(() => pngImage(huge));
  assert.throws(() => pngImage(Buffer.alloc(2097153)));
}));
