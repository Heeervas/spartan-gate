import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { normalizeCatalog, Discovery } from './extensions/spartan-route-provider/discovery.mjs';
import { agentScope, createResponsesStream, prepareRequest } from './extensions/spartan-route-provider/responses.mjs';
import { SessionFence, replayIdentity, LOCK } from './extensions/spartan-route-provider/session.mjs';

import { piDir, piDependency } from './pi_test_inputs.mjs';
const native = await import(pathToFileURL(piDependency('@earendil-works/pi-ai', 'dist/api/openai-responses.js')).href);
const { SessionManager } = await import(pathToFileURL(resolve(piDir, 'dist/core/session-manager.js')).href);
const { ExtensionRunner, createExtensionRuntime } = await import(pathToFileURL(resolve(piDir, 'dist/index.js')).href);
const session = 'a7becb62-8311-466d-89ca-e8b215d0a700';
const fork = 'ac10e7cc-cdb6-4e29-9b15-7c8d87bdf5bd';
const realm = 'codex_subscription';
const scope = 'a'.repeat(64);
const binding = { spartan_chain_id: 'b'.repeat(64), spartan_expected_realm: realm, spartan_binding_mac: 'c'.repeat(64) };
const model = { id: 'codex/gpt-example', provider: 'spartan-route', api: 'openai-responses',
  baseUrl: 'https://route.invalid/v1', input: ['text'], reasoning: true, contextWindow: 32768,
  maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const catalogRow = { id: model.id, owned_by: 'codex', tool_capable: true, max_context: 32768,
  reasoning: true, api_modes: ['chat_completions', 'responses'], reasoning_realm: realm };
const prompt = [{ role: 'user', content: 'Read the file', timestamp: 1 }];

function responseEvents(ciphertext = 'sgr:synthetic') {
  const reasoning = { id: 'rs_1', type: 'reasoning', summary: [], encrypted_content: ciphertext };
  const call = { type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'read', arguments: '{"path":"file.txt"}' };
  return [
    { type: 'response.created', response: { id: 'resp_1' } },
    { type: 'response.output_item.done', output_index: 0, item: reasoning },
    { type: 'response.output_item.added', output_index: 1, item: { ...call, arguments: '' } },
    { type: 'response.output_item.done', output_index: 1, item: call },
    { type: 'response.completed', response: { id: 'resp_1', status: 'completed', output: [reasoning, call],
      usage: { input_tokens: 10, output_tokens: 4, output_tokens_details: { reasoning_tokens: 2 } } } },
  ];
}
function fakeFetch(received, events = responseEvents()) {
  return async (url, init) => {
    received.push({ url: String(url), body: JSON.parse(init.body), headers: init.headers });
    return new Response(events.map(data => `data: ${JSON.stringify(data)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' },
    });
  };
}
function harness(manager = SessionManager.inMemory('.', { id: session }), request = async () => {
  throw new Error('New conversations must not request a binding');
}, selectedScope = scope) {
  const fence = new SessionFence(selectedScope, (type, data) => manager.appendCustomEntry(type, data));
  const runner = new ExtensionRunner([], createExtensionRuntime(), '.', manager, {});
  fence.activate(runner.createContext());
  return { manager, fence, runner, stream: createResponsesStream(native.streamSimple, request, selectedScope, () => realm, fence) };
}
async function run(stream, messages, received, options = {}) {
  return stream(model, { messages }, { apiKey: 'synthetic', sessionId: session, reasoning: 'high', maxRetries: 0,
    fetch: fakeFetch(received), ...options }).result();
}
function toolResult(message) {
  return { role: 'toolResult', toolCallId: message.content.find(block => block.type === 'toolCall').id,
    toolName: 'read', content: [{ type: 'text', text: 'file contents' }], isError: false, timestamp: 2 };
}

test('catalog requires advertised Responses and realm, preserving chat and supported thinking levels', () => {
  const rows = [catalogRow, { ...catalogRow, id: 'auto', reasoning_realm: null },
    { ...catalogRow, id: 'legacy', api_modes: undefined },
    { ...catalogRow, id: 'openai/example', reasoning_realm: 'openai_api', reasoning_efforts: ['low', 'high', 'xhigh'] }];
  const models = normalizeCatalog({ data: rows });
  assert.deepEqual(models.map(row => row.api), ['openai-responses', 'openai-completions', 'openai-completions', 'openai-responses']);
  assert.equal(models[3].thinkingLevelMap.xhigh, 'xhigh');
  assert.equal(models[3].thinkingLevelMap.max, null);
  assert.throws(() => normalizeCatalog({ data: [catalogRow, catalogRow] }), /duplicate/);
});

test('native wire omits unsupported none effort when thinking is unspecified', async () => {
  const { stream } = harness();
  for (const efforts of [['low', 'medium', 'max'], ['none', 'low', 'medium']]) {
    const [entry] = normalizeCatalog({ data: [{ ...catalogRow, reasoning_efforts: efforts }] });
    const received = [];
    const result = await stream({ ...model, ...entry }, { messages: prompt }, {
      apiKey: 'synthetic', sessionId: session, maxRetries: 0, fetch: fakeFetch(received),
    }).result();
    assert.notEqual(result.stopReason, 'error', result.errorMessage);
    assert.equal(received[0].body.reasoning?.effort, efforts.includes('none') ? 'none' : undefined);
  }
});

test('native tools and encrypted save/reopen preserve local identity without a binding RPC', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-native-response-'));
  try {
    const manager = SessionManager.create(directory, join(directory, 'sessions'), { id: session });
    const { stream } = harness(manager);
    const received = [];
    let observed = 0;
    const first = await run(stream, prompt, received, {
      onPayload: payload => ({ ...payload, prompt_cache_key: 'caller-cache-key' }),
      onResponse: () => { observed++; }, onProviderStreamEvent: () => { observed++; },
    });
    assert.equal(first.stopReason, 'toolUse', first.errorMessage);
    assert.equal(observed, 6);
    assert.equal(received[0].body.spartan_chain_id, undefined);
    assert.equal(received[0].body.prompt_cache_key, 'caller-cache-key');
    manager.appendMessage(prompt[0]); manager.appendMessage(first); manager.appendMessage(toolResult(first));
    const reopened = SessionManager.open(manager.getSessionFile());
    const second = await run(harness(reopened).stream, reopened.buildSessionContext().messages, received);
    assert.equal(second.stopReason, 'toolUse', second.errorMessage);
    const replay = received[1].body.input.find(item => item.type === 'reasoning');
    assert.equal(replay.encrypted_content, 'sgr:synthetic');
    assert.equal(Object.keys(replay).some(key => key.startsWith('_spartan_')), false);
    const call = received[1].body.input.find(item => item.type === 'function_call');
    assert.equal(call.id, undefined);
    assert.equal(call.call_id, received[1].body.input.find(item => item.type === 'function_call_output').call_id);
    assert.match(await readFile(manager.getSessionFile(), 'utf8'), /_spartan_replay_identity/);
    const forked = SessionManager.forkFrom(manager.getSessionFile(), directory, join(directory, 'sessions'), { id: fork });
    await run(harness(forked).stream, forked.buildSessionContext().messages, received, { sessionId: fork });
    assert.equal(received[2].body.input.some(item => item.type === 'reasoning'), false);
    assert.equal(received[2].body.input.find(item => item.type === 'function_call').id, undefined);
    await run(harness(reopened, undefined, 'd'.repeat(64)).stream, reopened.buildSessionContext().messages, received);
    assert.equal(received[3].body.input.some(item => item.type === 'reasoning'), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('zero-reasoning copied history removes item IDs without breaking call relationships or cache keys', () => {
  const input = [{ type: 'message', role: 'assistant', id: 'msg_old', content: [] },
    { type: 'function_call', id: 'fc_old', call_id: 'call_1' }, { type: 'function_call_output', call_id: 'call_1', output: 'ok' }];
  const result = prepareRequest({ input, previous_response_id: 'foreign', prompt_cache_key: 'explicit' }, 'new', realm, fork);
  assert.equal(result.input[0].id, undefined); assert.equal(result.input[1].id, undefined);
  assert.equal(result.input[1].call_id, result.input[2].call_id);
  assert.equal(result.prompt_cache_key, 'explicit'); assert.equal(result.previous_response_id, undefined);
  assert.equal(input[0].id, 'msg_old');
});

test('named native IDs keep canonical identity and original cache key', async () => {
  const named = 'subagent.0123456789abcdef';
  const manager = SessionManager.inMemory('.', { id: named });
  const received = [];
  const result = await run(harness(manager).stream, prompt, received, { sessionId: named });
  assert.equal(result.stopReason, 'toolUse', result.errorMessage);
  assert.equal(received[0].body.prompt_cache_key, named);
  assert.equal(manager.getSessionId(), named);
  assert.equal(replayIdentity(scope, named), replayIdentity(scope, '3bab243f-a0c3-8494-9c22-072bbf68d2f7'));
  assert.notEqual(replayIdentity(scope, named), replayIdentity(scope, 'subagent.0123456789abcdee'));
});

test('append failure after native memory mutation latches retries closed before any HTTP', async () => {
  const { manager, stream } = harness();
  const persist = manager._persist;
  manager._persist = () => { throw new Error('synthetic private disk error'); };
  const received = [];
  const first = await run(stream, prompt, received);
  assert.equal(first.stopReason, 'error');
  assert.match(first.errorMessage, /session state unavailable/);
  assert.equal(manager.getEntries().filter(entry => entry.customType === LOCK).length, 1);
  manager._persist = persist;
  const retry = await run(stream, prompt, received);
  assert.equal(retry.stopReason, 'error');
  assert.equal(received.length, 0);
});

test('append failure survives extension module reload on retained manager; a fresh process recovers only disk state', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-fence-failure-'));
  try {
    const manager = SessionManager.create(directory, join(directory, 'sessions'), { id: session });
    manager.appendMessage(prompt[0]);
    const assistant = await run(harness().stream, prompt, []);
    manager.appendMessage(assistant); // Ensure a real persisted session before injected failure.
    const before = await readFile(manager.getSessionFile(), 'utf8');
    const failed = harness(manager);
    manager._persist = () => { throw new Error('synthetic broken disk'); };
    const received = [];
    assert.equal((await run(failed.stream, [...prompt, assistant], received)).stopReason, 'error');
    assert.equal(manager.getEntries().filter(entry => entry.customType === LOCK).length, 1);
    assert.equal(await readFile(manager.getSessionFile(), 'utf8'), before);
    failed.fence.deactivate();
    const { SessionFence: ReloadedFence } = await import(`./extensions/spartan-route-provider/session.mjs?reload=${Date.now()}`);
    const replacement = new ReloadedFence(scope, (type, data) => manager.appendCustomEntry(type, data));
    replacement.activate({ sessionManager: manager });
    const stream = createResponsesStream(native.streamSimple, async () => { throw new Error('No migration'); }, scope, () => realm, replacement);
    const retry = await run(stream, [...prompt, assistant], received);
    assert.equal(retry.stopReason, 'error');
    assert.match(retry.errorMessage, /session state unavailable/);
    assert.equal(received.length, 0);
    const script = `import {SessionManager} from ${JSON.stringify(pathToFileURL(resolve(piDir, 'dist/core/session-manager.js')).href)};
      import {SessionFence,LOCK} from ${JSON.stringify(new URL('./extensions/spartan-route-provider/session.mjs', import.meta.url).href)};
      const manager=SessionManager.open(process.argv[1]);
      const fence=new SessionFence(${JSON.stringify(scope)},(t,d)=>manager.appendCustomEntry(t,d));
      fence.activate({sessionManager:manager});
      fence.claim(${JSON.stringify(session)},${JSON.stringify(realm)});
      console.log(JSON.stringify(manager.getEntries().filter(e=>e.customType===LOCK).map(e=>e.data)));`;
    const recovered = await promisify(execFile)(process.execPath, ['--input-type=module', '-e', script, manager.getSessionFile()], { timeout: 10000 });
    assert.deepEqual(JSON.parse(recovered.stdout), [{ version: 2, identity: replayIdentity(scope, session), realm }]);
    assert.match(await readFile(manager.getSessionFile(), 'utf8'), new RegExp(LOCK));
    assert.equal((await run(stream, [...prompt, assistant], received)).stopReason, 'error');
    assert.equal(received.length, 0); // Other process recovery cannot unpoison retained phantom memory.
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('realm lock survives zero-reasoning turns and branch navigation; copied locks do not authorize a new ID', () => {
  const { manager, fence } = harness();
  fence.claim(session, realm);
  manager.appendMessage(prompt[0]);
  manager.resetLeaf();
  assert.throws(() => fence.claim(session, 'openai_api'), /realm mismatch/);
  const entries = manager.getEntries().map(entry => ({ ...entry }));
  const other = SessionManager.inMemory('.', { id: fork });
  for (const entry of entries.filter(entry => entry.customType === LOCK)) other.appendCustomEntry(LOCK, entry.data);
  assert.doesNotThrow(() => harness(other).fence.claim(fork, 'openai_api'));
});

test('concurrent first requests cannot initialize the same identity in two realms', async () => {
  const { fence } = harness();
  const received = [];
  const stream = createResponsesStream(native.streamSimple, async () => { throw new Error('No migration'); }, scope,
    id => id.startsWith('codex/') ? realm : 'openai_api', fence);
  const options = { apiKey: 'synthetic', sessionId: session, maxRetries: 0, fetch: fakeFetch(received) };
  const [first, second] = await Promise.all([
    stream(model, { messages: prompt }, options).result(),
    stream({ ...model, id: 'openai/example' }, { messages: prompt }, options).result(),
  ]);
  assert.notEqual(first.stopReason, 'error', first.errorMessage);
  assert.equal(second.stopReason, 'error');
  assert.match(second.errorMessage, /realm mismatch/);
  assert.equal(received.length, 1);
});

test('independent routing IDs preserve native replay identity and caller cache routing', async () => {
  const { manager, stream } = harness();
  const received = [];
  const result = await run(stream, prompt, received, { sessionId: fork });
  assert.equal(result.stopReason, 'toolUse', result.errorMessage);
  assert.equal(received[0].body.prompt_cache_key, fork);
  const item = JSON.parse(result.content.find(block => block.type === 'thinking').thinkingSignature);
  assert.equal(item._spartan_replay_identity, replayIdentity(scope, session));
  assert.equal(manager.getEntries().find(entry => entry.customType === LOCK).data.identity, replayIdentity(scope, session));
});

test('invalid native IDs, unavailable context and cancellation cannot submit', async () => {
  const received = [];
  for (const id of [undefined, '', '../invalid', 'bad\nvalue', 'x'.repeat(257)]) {
    const { fence, stream } = harness();
    fence.activate({ sessionManager: { getSessionId: () => id } });
    assert.equal((await run(stream, prompt, received)).stopReason, 'error');
  }
  const { fence, runner, stream } = harness();
  runner.invalidate();
  assert.equal((await run(stream, prompt, received)).stopReason, 'error');
  fence.deactivate();
  assert.equal((await run(stream, prompt, received)).stopReason, 'error');
  const controller = new AbortController(); controller.abort();
  assert.equal((await run(harness().stream, prompt, received, { signal: controller.signal })).stopReason, 'aborted');
  assert.equal(received.length, 0);
});

async function suspendedOwnership(stage, replace) {
  let resume, began;
  const started = new Promise(resolve => { began = resolve; });
  const wait = () => new Promise(resolve => { resume = resolve; began(); });
  const request = async () => { await wait(); return { version: 1, binding }; };
  const h = harness(undefined, request);
  const messages = structuredClone(prompt);
  if (stage === 'legacy') {
    const history = await run(harness().stream, prompt, []);
    history.content.find(block => block.type === 'thinking').thinkingSignature = JSON.stringify({ type: 'reasoning', id: 'rs_old',
      encrypted_content: 'opaque', _spartan_chain_id: binding.spartan_chain_id,
      _spartan_binding_mac: binding.spartan_binding_mac, _spartan_reasoning_realm: realm });
    messages.push(history);
  }
  const received = [];
  const pending = run(h.stream, messages, received, stage === 'payload' ? { onPayload: wait } : {});
  await started;
  replace(h);
  resume();
  const result = await pending;
  assert.equal(result.stopReason, 'error');
  assert.match(result.errorMessage, /session state unavailable/);
  assert.equal(received.length, 0);
  assert.equal(h.manager.getEntries().filter(entry => entry.customType === LOCK).length, 0);
}

test('native ownership cannot change during payload callbacks or legacy lookup', async t => {
  for (const stage of ['payload', 'legacy']) {
    await t.test(`${stage}: shutdown`, () => suspendedOwnership(stage, h => h.fence.deactivate()));
    await t.test(`${stage}: invalidated guarded context`, () => suspendedOwnership(stage, h => h.runner.invalidate()));
    await t.test(`${stage}: different manager with same ID`, () => suspendedOwnership(stage, h => {
      h.runner.sessionManager = SessionManager.inMemory('.', { id: session });
    }));
    await t.test(`${stage}: native ID replaced on retained manager`, () => suspendedOwnership(stage, h => {
      h.manager.newSession({ id: fork });
    }));
    await t.test(`${stage}: reactivation on retained manager`, () => suspendedOwnership(stage, h => {
      h.fence.deactivate(); h.fence.activate(h.runner.createContext());
    }));
  }
});

test('legacy saved reasoning uses one lazy binding and remains raw; failure and cancellation never submit', async () => {
  const first = await run(harness().stream, prompt, []);
  const history = structuredClone(first);
  const item = JSON.parse(history.content.find(block => block.type === 'thinking').thinkingSignature);
  delete item._spartan_replay_version; delete item._spartan_replay_identity;
  Object.assign(item, { encrypted_content: 'old-opaque', _spartan_chain_id: binding.spartan_chain_id,
    _spartan_binding_mac: binding.spartan_binding_mac });
  history.content.find(block => block.type === 'thinking').thinkingSignature = JSON.stringify(item);
  let calls = 0;
  const request = async (_action, document) => {
    calls++;
    assert.equal(document.sessionId, session);
    return { version: 1, binding };
  };
  const { stream } = harness(undefined, request);
  const received = [];
  for (let i = 0; i < 2; i++) {
    const result = await run(stream, [...prompt, history, toolResult(history)], received,
      { sessionId: fork, fetch: fakeFetch(received, responseEvents('old-opaque')) });
    assert.notEqual(result.stopReason, 'error', result.errorMessage);
    assert.equal(received[i].body.spartan_binding_mac, binding.spartan_binding_mac);
  }
  assert.equal(calls, 1);
  await run(harness(undefined, request).stream, [...prompt, history], received);
  assert.equal(calls, 2); // A fresh runtime revalidates the old binding.
  const failed = harness(undefined, async () => { throw new Error('synthetic unavailable'); });
  const requests = [];
  assert.equal((await run(failed.stream, [...prompt, history], requests)).stopReason, 'error');
  const controller = new AbortController();
  const cancelled = harness(undefined, async () => { controller.abort(); return { version: 1, binding }; });
  assert.equal((await run(cancelled.stream, [...prompt, history], requests, { signal: controller.signal })).stopReason, 'aborted');
  assert.equal(requests.length, 0);
});

test('legacy wrong-realm migration leaves no durable lock and correct realm succeeds after reopen', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-legacy-realm-'));
  try {
    const manager = SessionManager.create(directory, join(directory, 'sessions'), { id: session });
    const legacy = await run(harness().stream, prompt, []);
    const block = legacy.content.find(block => block.type === 'thinking');
    block.thinkingSignature = JSON.stringify({ type: 'reasoning', id: 'rs_old', summary: [], encrypted_content: 'old-opaque',
      _spartan_chain_id: binding.spartan_chain_id, _spartan_binding_mac: binding.spartan_binding_mac, _spartan_reasoning_realm: realm });
    manager.appendMessage(prompt[0]); manager.appendMessage(legacy); manager.appendMessage(toolResult(legacy));
    let rejectWrong, began;
    const started = new Promise(resolve => { began = resolve; });
    const request = async (_action, document) => {
      if (document.realm === realm) return { version: 1, binding };
      return new Promise(resolve => {
        rejectWrong = () => resolve({ version: 1, error: { code: 'reasoning_realm_mismatch' } });
        began();
      });
    };
    const { fence } = harness(manager, request);
    const stream = createResponsesStream(native.streamSimple, request, scope,
      id => id.startsWith('codex/') ? realm : 'openai_api', fence);
    const received = [];
    const options = { apiKey: 'synthetic', sessionId: session, maxRetries: 0, fetch: fakeFetch(received) };
    const wrongPending = stream({ ...model, id: 'openai/example' }, manager.buildSessionContext(), options).result();
    await started;
    const concurrent = await stream(model, manager.buildSessionContext(), options).result();
    assert.equal(concurrent.stopReason, 'error');
    assert.match(concurrent.errorMessage, /realm mismatch/);
    assert.equal(manager.getEntries().some(entry => entry.customType === LOCK), false);
    rejectWrong();
    const wrong = await wrongPending;
    assert.equal(wrong.stopReason, 'error');
    assert.match(wrong.errorMessage, /reasoning_realm_mismatch/);
    assert.equal(received.length, 0);
    assert.equal(manager.getEntries().some(entry => entry.customType === LOCK), false);
    const reopened = SessionManager.open(manager.getSessionFile());
    assert.equal(reopened.getEntries().some(entry => entry.customType === LOCK), false);
    const correct = await run(harness(reopened, request).stream, reopened.buildSessionContext().messages, received,
      { fetch: fakeFetch(received, responseEvents('old-opaque')) });
    assert.equal(correct.stopReason, 'toolUse', correct.errorMessage);
    assert.equal(received.length, 1);
    assert.equal(received[0].body.spartan_binding_mac, binding.spartan_binding_mac);
    assert.equal(SessionManager.open(manager.getSessionFile()).getEntries().find(entry => entry.customType === LOCK).data.realm, realm);
    const sameManager = await run(stream, manager.buildSessionContext().messages, received,
      { fetch: fakeFetch(received, responseEvents('old-opaque')) });
    assert.equal(sameManager.stopReason, 'toolUse', sameManager.errorMessage);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('concurrent requests and late terminal encryption retain request-local provenance', async () => {
  const events = responseEvents();
  delete events[1].item.encrypted_content;
  events.at(-1).response.output[0] = { type: 'reasoning', id: 'rs_1', encrypted_content: 'late', summary: [] };
  const received = [];
  const results = await Promise.all([session, fork].map(id => run(harness(SessionManager.inMemory('.', { id })).stream,
    prompt, received, { sessionId: id, fetch: fakeFetch(received, structuredClone(events)) })));
  for (const [index, message] of results.entries()) {
    assert.notEqual(message.stopReason, 'error', message.errorMessage);
    const item = JSON.parse(message.content.find(block => block.type === 'thinking').thinkingSignature);
    assert.equal(item._spartan_replay_identity, replayIdentity(scope, [session, fork][index]));
    assert.equal(item.encrypted_content, 'late');
  }
});

test('agent scope follows canonical directory identity and changes between profiles', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-provider-scope-'));
  try {
    await mkdir(join(directory, 'profile-a')); await mkdir(join(directory, 'profile-b'));
    await symlink(join(directory, 'profile-a'), join(directory, 'alias'));
    const first = await agentScope(join(directory, 'profile-a'));
    assert.equal(await agentScope(join(directory, 'alias')), first);
    assert.notEqual(await agentScope(join(directory, 'profile-b')), first);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('dynamic discovery persists capabilities and refreshes without stale catalog', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-discovery-'));
  try {
    const config = { baseUrl: model.baseUrl, refreshSeconds: 300, cacheMaxAgeSeconds: 86400 };
    let document = { data: [catalogRow] };
    const discovery = new Discovery(directory, config, async () => document);
    assert.equal((await discovery.get())[0].api, 'openai-responses');
    document = { data: [{ ...catalogRow, api_modes: ['chat_completions'] }] };
    assert.equal((await discovery.get({ force: true }))[0].api, 'openai-completions');
    assert.equal((await new Discovery(directory, config).get({ network: false }))[0].api, 'openai-completions');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('real Pi loader registers provider from isolated config without startup network', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-provider-loader-'));
  const previous = { offline: process.env.PI_OFFLINE, config: process.env.PI_SPARTAN_ROUTE_CONFIG, agent: process.env.PI_CODING_AGENT_DIR };
  try {
    await mkdir(join(directory, 'agent'));
    const configPath = join(directory, 'config.json');
    await writeFile(configPath, JSON.stringify({ baseUrl: model.baseUrl, refreshSeconds: 300, cacheMaxAgeSeconds: 86400 }));
    await writeFile(join(directory, 'catalog.json'), JSON.stringify({ baseUrl: model.baseUrl, fetchedAt: Date.now(), document: { data: [catalogRow] } }));
    process.env.PI_OFFLINE = '1'; process.env.PI_SPARTAN_ROUTE_CONFIG = configPath;
    process.env.PI_CODING_AGENT_DIR = join(directory, 'agent');
    const { loadExtensions } = await import(pathToFileURL(resolve(piDir, 'dist/core/extensions/loader.js')).href);
    const path = fileURLToPath(new URL('./extensions/spartan-route-provider.ts', import.meta.url));
    const result = await loadExtensions([path], dirname(path));
    assert.deepEqual(result.errors, []);
    const provider = result.runtime.pendingProviderRegistrations.find(entry => entry.name === 'spartan-route')?.config;
    assert.ok(result.extensions[0].commands.has('spartan-route-refresh'));
    assert.equal(result.runtime.pendingProviderRegistrations.length, 1);
    assert.equal(provider.api, 'openai-responses'); assert.equal(provider.models[0].api, 'openai-responses');
    assert.equal(typeof provider.streamSimple, 'function');
  } finally {
    for (const [key, value] of [['PI_OFFLINE', previous.offline], ['PI_SPARTAN_ROUTE_CONFIG', previous.config], ['PI_CODING_AGENT_DIR', previous.agent]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  }
});
