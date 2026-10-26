// W4 only: explicit disposable fixture inputs, real native Pi, synthetic model transport.
// No Docker/process provisioning, provider inference, host discovery, or mutation retries.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdir, lstat, realpath, readFile, writeFile, readdir } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { privateFile, endpoint, loadConfig, checkSearchConfig, TOKEN } from './extensions/spartan-tools/config.mjs';

const receipt = { client: 'native-pi-synthetic-model', checks: [], browserMode: 'TEST-ONLY analytics/addons-disabled fixture; NOT default UBO proof' };
const secrets = new Set();
let session;
let stage = 'arguments';
let originalFetch;

function argumentsFor(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    assert.ok(['--connection', '--state-dir', '--mode'].includes(argv[i]) && argv[i + 1]);
    assert.ok(!Object.hasOwn(args, argv[i]));
    args[argv[i]] = argv[i + 1];
  }
  assert.equal(Object.keys(args).length, 3);
  assert.ok(['first', 'resume', 'fork', 'cleanup'].includes(args['--mode']));
  return { connection: resolve(args['--connection']), directory: resolve(args['--state-dir']), mode: args['--mode'] };
}

function within(directory, path) {
  assert.ok(isAbsolute(path));
  const part = relative(directory, path);
  assert.ok(part && !part.startsWith('..') && !isAbsolute(part));
  return path;
}

async function privateDirectory(path) {
  await mkdir(path, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
  const stat = await lstat(path);
  assert.ok(stat.isDirectory() && stat.uid === process.getuid());
  assert.equal(stat.mode & 0o7777, 0o700);
  assert.equal(await realpath(path), resolve(path));
}

async function fixtureConfiguration(args) {
  const connection = JSON.parse(privateFile(args.connection));
  const baseUrl = endpoint(connection.baseUrl);
  assert.equal(new URL(connection.browserUrl).origin, 'http://fixture:8080');
  const browserUrl = new URL('/page', connection.browserUrl).href;
  assert.equal(connection.readerUrl, 'http://fixture.public.example:8080/page');
  assert.match(connection.clientId, /^[a-zA-Z0-9_-]{1,64}$/);
  if (process.env.SEARXNG_BASE_URL !== undefined) assert.equal(endpoint(process.env.SEARXNG_BASE_URL), baseUrl);
  const token = privateFile(connection.credentialFile, 65).trim();
  assert.match(token, TOKEN);
  secrets.add(token);
  const agentDir = within(args.directory, process.env.PI_CODING_AGENT_DIR);
  const configPath = within(agentDir, process.env.PI_SPARTAN_TOOLS_CONFIG);
  assert.equal(dirname(agentDir), args.directory);
  assert.equal(dirname(configPath), agentDir);
  const webPath = join(agentDir, 'web-search.json');
  assert.equal(existsSync(configPath), existsSync(webPath));
  if (!existsSync(configPath)) {
    assert.equal(args.mode, 'first');
    // Complete fixture input/path validation before creating either private config.
    await privateDirectory(agentDir);
    const config = { version: 1, baseUrl, credentialFile: connection.credentialFile,
      stateDir: join(args.directory, 'workflows'), clientId: connection.clientId };
    const web = { provider: 'searxng', workflow: 'none', searxngBaseUrl: baseUrl,
      searxngHeaders: { Authorization: `Bearer ${token}` }, webSearch: { allowedProviders: ['searxng'] },
      tools: { sourceCheck: { enabled: false }, fetchContent: { enabled: false } },
      commands: Object.fromEntries(['websearch', 'curator', 'search', 'google-account'].map(name => [name, { enabled: false }])) };
    await writeFile(configPath, JSON.stringify(config), { flag: 'wx', mode: 0o600 });
    await writeFile(webPath, JSON.stringify(web), { flag: 'wx', mode: 0o600 });
  }
  await privateDirectory(agentDir);
  const config = loadConfig(configPath);
  assert.equal(config.baseUrl, baseUrl);
  assert.equal(config.clientId, connection.clientId);
  assert.equal(resolve(config.credentialFile), resolve(connection.credentialFile));
  within(args.directory, config.stateDir);
  checkSearchConfig(config, agentDir);
  return { ...args, baseUrl, browserUrl, readerUrl: connection.readerUrl, agentDir, config };
}

async function collectSecrets(f, manager) {
  const directory = f.config.stateDir;
  if (existsSync(directory)) {
    await privateDirectory(directory);
    for (const name of await readdir(directory)) {
      assert.match(name, /^[0-9a-f]{64}\.json$/);
      const record = JSON.parse(privateFile(join(directory, name)));
      assert.match(record.workflowToken, TOKEN);
      secrets.add(record.workflowToken);
    }
  }
  const entries = JSON.stringify(manager.getEntries());
  const saved = privateFile(manager.getSessionFile(), 33554432);
  for (const secret of secrets) {
    assert.ok(!entries.includes(secret), 'bearer in native entries');
    assert.ok(!saved.includes(secret), 'bearer in saved native session');
  }
}

async function nativeSession(f, manager) {
  assert.ok(process.env.PI_TEST_PACKAGE_DIR && process.env.PI_WEB_ACCESS_EXTENSION);
  const P = pathToFileURL(resolve(process.env.PI_TEST_PACKAGE_DIR) + '/').href;
  const sdk = await import(P + 'dist/index.js');
  receipt.nativeVersion = JSON.parse(await readFile(new URL('package.json', P), 'utf8')).version;
  assert.equal(receipt.nativeVersion, '1.4.0');
  const { loadExtensions } = await import(P + 'dist/core/extensions/loader.js');
  const require = createRequire(new URL('package.json', P));
  const ai = require.resolve.paths('@earendil-works/pi-ai').map(path => join(path, '@earendil-works/pi-ai/dist/index.js')).find(existsSync);
  assert.ok(ai);
  const { InMemoryCredentialStore, AssistantMessageEventStream } = await import(pathToFileURL(ai).href);
  const loaded = await loadExtensions([process.env.PI_WEB_ACCESS_EXTENSION,
    fileURLToPath(new URL('./extensions/spartan-tools.ts', import.meta.url))], f.directory);
  assert.equal(loaded.errors.length, 0, 'native extension load');
  const settingsManager = sdk.SettingsManager.inMemory({ cacheWarming: 'off', retry: { enabled: false } });
  const resourceLoader = new sdk.DefaultResourceLoader({ cwd: f.directory, agentDir: f.agentDir, settingsManager,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true });
  resourceLoader.getExtensions = () => loaded;
  const runtime = await sdk.ModelRuntime.create({ credentials: new InMemoryCredentialStore(), modelsPath: null,
    modelsStorePath: join(f.directory, 'model-cache'), refreshOnCreate: false, allowModelNetwork: false });
  const state = { next: undefined, sequence: 0, modelImages: new Set(), toolResults: new Map() };
  runtime.registerProvider('gate-acceptance', { api: 'gate-acceptance', baseUrl: 'http://127.0.0.1:1', apiKey: 'synthetic',
    models: [{ id: 'fixture', name: 'Fixture', reasoning: false, input: ['text', 'image'], contextWindow: 100000, maxTokens: 1000,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    streamSimple(model, context) {
      for (const message of context.messages) {
        if (!Array.isArray(message.content)) continue;
        for (const item of message.content) if (item.type === 'image') state.modelImages.add(imageKey(item));
      }
      const content = state.next ? [state.next] : [{ type: 'text', text: 'Fixture step complete.' }];
      state.next = undefined;
      const message = { role: 'assistant', provider: model.provider, api: model.api, model: model.id, content, timestamp: Date.now(),
        stopReason: content[0].type === 'toolCall' ? 'toolUse' : 'stop',
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
      const stream = new AssistantMessageEventStream();
      stream.push({ type: 'done', reason: message.stopReason, message }); stream.end();
      return stream;
    } });
  const sessionManager = manager ?? sdk.SessionManager.create(f.directory, join(f.directory, 'sessions'));
  ({ session } = await sdk.createAgentSession({ cwd: f.directory, agentDir: f.agentDir, sessionManager, resourceLoader,
    settingsManager, modelRuntime: runtime, model: runtime.getModel('gate-acceptance', 'fixture'), noTools: 'builtin' }));
  session.agent.toolExecution = 'sequential';
  session.subscribe(event => { if (event.type === 'tool_execution_end') state.toolResults.set(event.toolCallId, event.result); });
  await session.bindExtensions({ mode: 'rpc', onError: () => { throw new Error('native extension failure'); } });
  return { sdk, manager: sessionManager, state };
}

function imageKey(image) {
  return createHash('sha256').update(image.mimeType).update(image.data).digest('hex');
}

async function invoke(n, name, input, failure = false) {
  const id = `fixture-${++n.state.sequence}`;
  n.state.next = { type: 'toolCall', id, name, arguments: input };
  await session.prompt('Execute the single synthetic fixture tool call.');
  const result = session.agent.state.messages.findLast(message => message.role === 'toolResult' && message.toolCallId === id);
  assert.ok(result, 'native tool result');
  assert.equal(Boolean(result.isError), failure, 'native tool status');
  // Native history omits structuredContent; finalized execution events retain it.
  const finalized = n.state.toolResults.get(id);
  assert.ok(finalized, 'native finalized tool execution');
  return { ...result, structuredContent: finalized.structuredContent };
}

function objectResult(result) {
  return JSON.parse(result.content.filter(item => item.type === 'text').map(item => item.text).join('\n'));
}

async function browser(n, action, args = {}, failure = false) {
  return invoke(n, 'gate_browser', { action, ...args }, failure);
}

async function screenshot(n, tabId) {
  const result = await browser(n, 'screenshot', { tabId });
  const images = result.content.filter(item => item.type === 'image');
  assert.equal(images.length, 1);
  assert.ok(n.state.modelImages.has(imageKey(images[0])), 'image absent from subsequent synthetic model transcript');
  receipt.checks.push('actual-native-image-hook-and-subsequent-model-transcript');
}

async function first(f, n, progressPath) {
  const search = await invoke(n, 'web_search', { query: 'isolated fixture', provider: 'searxng' });
  assert.equal(search.structuredContent.queries[0].results[0].title, 'Synthetic search result');
  receipt.checks.push('native-finalized-tool-result-structured-search-history-projects-content-only');
  const read = await invoke(n, 'gate_reader', { url: f.readerUrl });
  const text = read.content[0].text;
  assert.ok(text.startsWith('BEGIN_UNTRUSTED_WEB_CONTENT\n') && text.endsWith('\nEND_UNTRUSTED_WEB_CONTENT\n'));
  assert.ok(text.includes('Isolated managed browser fixture'));
  receipt.checks.push('native-reader-complete-untrusted-fixture-text');
  const { tabId } = objectResult(await browser(n, 'create'));
  assert.equal(typeof tabId, 'string');
  await writeFile(progressPath, JSON.stringify({ version: 1, sessionFile: n.manager.getSessionFile(), sessionId: n.manager.getSessionId(), tabId }), { mode: 0o600, flag: 'wx' });
  assert.equal(objectResult(await browser(n, 'navigate', { tabId, url: f.browserUrl })).ok, true);
  assert.ok(objectResult(await browser(n, 'snapshot', { tabId })).snapshot.includes('Fixture button'));
  await browser(n, 'type', { tabId, selector: '#fixture-input', text: 'Synthetic input' });
  await browser(n, 'click', { tabId, selector: '#fixture-button' });
  assert.ok(objectResult(await browser(n, 'snapshot', { tabId })).snapshot.includes('Clicked once'));
  await browser(n, 'scroll', { tabId, direction: 'down', amount: 300 });
  await screenshot(n, tabId);
  assert.deepEqual(objectResult(await browser(n, 'tabs')).tabs, [{ tabId }]);
  receipt.checks.push('native-create-navigate-snapshot-type-single-click-scroll-owned-list', 'parent-tab-retained-for-fresh-process-resume');
}

async function resumed(n, progress) {
  assert.equal(n.manager.getSessionId(), progress.sessionId);
  assert.deepEqual(objectResult(await browser(n, 'tabs')).tabs, [{ tabId: progress.tabId }]);
  assert.ok(objectResult(await browser(n, 'snapshot', { tabId: progress.tabId })).snapshot.includes('Clicked once'));
  await screenshot(n, progress.tabId);
  receipt.checks.push('fresh-process-native-resume-same-owned-tab');
}

async function forked(f, n, progress) {
  assert.notEqual(n.manager.getSessionId(), progress.sessionId);
  assert.deepEqual(objectResult(await browser(n, 'tabs')).tabs, []);
  const denied = await browser(n, 'snapshot', { tabId: progress.tabId }, true);
  assert.ok(!denied.content.some(item => item.type === 'image'));
  const { tabId } = objectResult(await browser(n, 'create', { url: f.browserUrl }));
  assert.deepEqual(objectResult(await browser(n, 'tabs')).tabs, [{ tabId }]);
  await collectSecrets(f, n.manager);
  await browser(n, 'cleanup');
  receipt.checks.push('native-fork-distinct-session-empty-workflow-parent-tab-denied', 'fork-owned-tab-and-workflow-cleanup');
}

async function run() {
  process.umask(0o077);
  const args = argumentsFor(process.argv.slice(2));
  receipt.mode = args.mode;
  await privateDirectory(args.directory);
  stage = 'fixture-configuration';
  const f = await fixtureConfiguration(args);
  const progressPath = join(f.directory, 'native-parent.json');
  let progress;
  if (f.mode === 'first') assert.ok(!existsSync(progressPath), 'first mode already ran');
  else {
    progress = JSON.parse(privateFile(progressPath));
    within(f.directory, progress.sessionFile);
    privateFile(progress.sessionFile, 33554432);
  }
  let minted = 0;
  originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input);
    assert.equal(url.origin, f.baseUrl, 'unexpected fixture egress');
    if (url.pathname === '/v1/workflows' && init?.method === 'POST') minted++;
    return originalFetch(input, init);
  };
  stage = 'native-session';
  const P = pathToFileURL(resolve(process.env.PI_TEST_PACKAGE_DIR) + '/').href;
  const { SessionManager } = await import(P + 'dist/core/session-manager.js');
  let manager;
  if (f.mode === 'fork') manager = SessionManager.forkFrom(progress.sessionFile, f.directory, join(f.directory, 'sessions'));
  else if (progress) manager = SessionManager.open(progress.sessionFile);
  const n = await nativeSession(f, manager);
  receipt.sessionId = n.manager.getSessionId();
  stage = f.mode;
  if (f.mode === 'first') await first(f, n, progressPath);
  else if (f.mode === 'resume') await resumed(n, progress);
  else if (f.mode === 'fork') await forked(f, n, progress);
  else { await collectSecrets(f, n.manager); await browser(n, 'cleanup'); receipt.checks.push('parent-workflow-explicit-cleanup'); }
  assert.equal(minted, ['first', 'fork'].includes(f.mode) ? 1 : 0, 'unexpected workflow mint/replacement');
  await collectSecrets(f, n.manager);
  receipt.checks.push('bearers-absent-from-actual-saved-native-history', 'private-sidecar-modes', 'only-configured-ingress-no-real-model-transport');
  receipt.status = 'PASS';
}

try {
  await run();
  console.log(JSON.stringify(receipt));
} catch {
  console.error(JSON.stringify({ ...receipt, status: 'FAIL', stage, error: 'isolated native fixture acceptance failed; no automatic mutation retry' }));
  process.exitCode = 1;
} finally {
  session?.dispose();
  if (originalFetch) globalThis.fetch = originalFetch;
}
