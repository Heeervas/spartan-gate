import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { piDir, piDependency } from './pi_test_inputs.mjs';
import { createResponsesStream } from './extensions/spartan-route-provider/responses.mjs';
import { SessionFence, LOCK, replayIdentity } from './extensions/spartan-route-provider/session.mjs';

const sdk = await import(pathToFileURL(resolve(piDir, 'dist/index.js')).href);
const ai = await import(pathToFileURL(piDependency('@earendil-works/pi-ai', 'dist/index.js')).href);
const native = await import(pathToFileURL(piDependency('@earendil-works/pi-ai', 'dist/api/openai-responses.js')).href);
const scope = 'a'.repeat(64);
const realm = 'codex_subscription';
const model = { id: 'codex/summary-fixture', name: 'Summary fixture', provider: 'spartan-route', api: 'openai-responses',
  baseUrl: 'https://route.invalid/v1', input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 1024,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const usage = { input: 3000, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 3010,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function fetchSummary(requests) {
  return async (url, init) => {
    assert.equal(String(url), `${model.baseUrl}/responses`);
    requests.push(JSON.parse(init.body));
    const item = { type: 'message', id: 'msg_fixture', role: 'assistant', content: [{ type: 'output_text', text: 'Preserved branch work' }] };
    const events = [
      { type: 'response.created', response: { id: 'resp_fixture' } },
      { type: 'response.output_item.added', output_index: 0, item: { ...item, content: [] } },
      { type: 'response.content_part.added', output_index: 0, content_index: 0, part: { type: 'output_text', text: '' } },
      { type: 'response.output_text.delta', output_index: 0, content_index: 0, delta: 'Preserved branch work' },
      { type: 'response.output_item.done', output_index: 0, item },
      { type: 'response.completed', response: { id: 'resp_fixture', status: 'completed', output: [item],
        usage: { input_tokens: 10, output_tokens: 2 } } },
    ];
    return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' },
    });
  };
}

function seed(manager) {
  const targets = [];
  for (let turn = 0; turn < 3; turn++) {
    manager.appendMessage({ role: 'user', content: `Task ${turn}: ${'synthetic work '.repeat(100)}`, timestamp: turn * 2 + 1 });
    targets.push(manager.appendMessage({ role: 'assistant', api: model.api, provider: model.provider, model: model.id,
      content: [{ type: 'text', text: `Finished step ${turn}` }], stopReason: 'stop', usage, timestamp: turn * 2 + 2 }));
  }
  return targets;
}

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'pi-route-summary-'));
  let session;
  try {
    const manager = sdk.SessionManager.inMemory(directory);
    const targets = seed(manager);
    const requests = [], options = [], events = [];
    const control = {};
    const settingsManager = sdk.SettingsManager.inMemory({ cacheWarming: 'off', retry: { enabled: false },
      compaction: { enabled: false, reserveTokens: 7000, keepRecentTokens: 100 }, branchSummary: { reserveTokens: 1024 } });
    const resourceLoader = new sdk.DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager,
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
      extensionFactories: [pi => {
        const fence = new SessionFence(scope, (type, data) => pi.appendEntry(type, data));
        const stream = createResponsesStream(native.streamSimple, async () => { throw new Error('Unexpected binding'); }, scope, () => realm, fence);
        pi.registerProvider('spartan-route', { apiKey: 'synthetic', baseUrl: model.baseUrl, api: model.api, models: [model],
          streamSimple(m, context, requestOptions) {
            options.push({ sessionId: requestOptions.sessionId, cacheRetention: requestOptions.cacheRetention });
            return stream(m, context, { ...requestOptions, fetch: fetchSummary(requests), maxRetries: 0,
              onPayload: async (payload, requestModel) => {
                await control.beforePayload?.();
                return requestOptions.onPayload?.(payload, requestModel);
              } });
          } });
        pi.on('session_start', (_event, ctx) => fence.activate(ctx));
        pi.on('session_shutdown', () => fence.deactivate());
        pi.registerCommand('return-fixture', { description: 'Exercise provider-neutral tree navigation',
          handler: async (_args, ctx) => { await ctx.navigateTree(targets[0], { summarize: true }); } });
      }] });
    await resourceLoader.reload();
    const runtime = await sdk.ModelRuntime.create({ credentials: new ai.InMemoryCredentialStore(), modelsPath: null,
      modelsStorePath: join(directory, 'models-cache'), refreshOnCreate: false, allowModelNetwork: false });
    ({ session } = await sdk.createAgentSession({ cwd: directory, agentDir: directory, model, modelRuntime: runtime,
      sessionManager: manager, resourceLoader, settingsManager, noTools: 'builtin' }));
    await session.bindExtensions({ mode: 'rpc', onError: error => { throw new Error(JSON.stringify(error)); },
      commandContextActions: { waitForIdle: () => session.waitForIdle(),
        navigateTree: (targetId, options) => session.navigateTree(targetId, options) } });
    session.subscribe(event => events.push(event));
    // Observe the actual SDK cache classifier, without replacing its behavior.
    let cacheStarts = 0;
    const start = session._cacheWarmer.start.bind(session._cacheWarmer);
    session._cacheWarmer.start = (...args) => { cacheStarts++; return start(...args); };
    await run({ session, manager, targets, requests, options, events, control, cacheStarts: () => cacheStarts });
  } finally {
    session?.dispose();
    await rm(directory, { recursive: true, force: true });
  }
}

function assertSummary(f, type) {
  const entry = f.manager.getEntries().find(entry => entry.type === type);
  assert.ok(entry, `${type} attached`);
  assert.match(entry.summary, /Preserved branch work/);
  assert.equal(entry.usage.input, 10);
  assert.equal(entry.usage.output, 2);
  assert.notEqual(f.options[0].sessionId, f.manager.getSessionId());
  assert.equal(f.options[0].cacheRetention, 'none');
  assert.equal(f.requests[0].prompt_cache_key, `pi:${f.options[0].sessionId}`);
  assert.equal(f.manager.getEntries().find(entry => entry.customType === LOCK).data.identity,
    replayIdentity(scope, f.manager.getSessionId()));
  return entry;
}

test('native command navigateTree attaches summary and usage without changing routing/cache classification', async () => fixture(async f => {
  const oldLeaf = f.manager.getLeafId();
  await f.session.prompt('/return-fixture');
  const entry = assertSummary(f, 'branch_summary');
  assert.ok(f.manager.getBranch(entry.fromId).some(entry => entry.id === oldLeaf));
  assert.equal(entry.parentId, f.targets[0]);
  assert.equal(f.manager.getLeafId(), entry.id);
  assert.equal(f.requests.length, 1);
  assert.equal(f.cacheStarts(), 0);
}));

test('native reload on the retained manager cannot revive an awaited old summary request', async () => fixture(async f => {
  let resume, began;
  const started = new Promise(resolve => { began = resolve; });
  f.control.beforePayload = () => new Promise(resolve => { resume = resolve; began(); });
  const pending = f.session.compact();
  const rejected = assert.rejects(pending, /session state unavailable/);
  await started;
  try { await f.session.reload(); }
  finally { delete f.control.beforePayload; resume(); }
  await rejected;
  assert.equal(f.requests.length, 0);
  assert.equal(f.manager.getEntries().some(entry => entry.customType === LOCK || entry.type === 'compaction'), false);
  await f.session.compact();
  assert.equal(f.requests.length, 1);
  assertSummary({ ...f, options: f.options.slice(1) }, 'compaction');
}));

test('native default manual compaction preserves independent routing and persisted summary usage', async () => fixture(async f => {
  await f.session.compact();
  assertSummary(f, 'compaction');
  assert.equal(f.requests.length, 1);
  assert.equal(f.cacheStarts(), 0);
}));

test('native automatic threshold compaction completes before the ordinary turn', async () => fixture(async f => {
  f.session.setAutoCompactionEnabled(true);
  await f.session.prompt('Continue the synthetic task');
  assertSummary(f, 'compaction');
  assert.ok(f.events.some(event => event.type === 'compaction_start' && event.reason === 'threshold'));
  assert.equal(f.requests.length, 2);
  assert.equal(f.options[1].sessionId, f.manager.getSessionId());
  assert.equal(f.cacheStarts(), 1);
  assert.equal(f.session.getLastAssistantText(), 'Preserved branch work');
}));
