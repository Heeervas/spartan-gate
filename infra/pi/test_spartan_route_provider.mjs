import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, readFile, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { normalizeCatalog, Discovery } from './extensions/spartan-route-provider/discovery.mjs';
import { agentScope, createResponsesStream, prepareRequest } from './extensions/spartan-route-provider/responses.mjs';

const require = createRequire(import.meta.url);
const piDir = process.env.PI_CODING_AGENT_DIR || dirname(require.resolve('@earendil-works/pi-coding-agent/package.json'));
const native = await import(pathToFileURL(resolve(piDir, 'node_modules/@earendil-works/pi-ai/dist/api/openai-responses.js')).href);
const { SessionManager } = await import(pathToFileURL(resolve(piDir, 'dist/core/session-manager.js')).href);
const session = 'a7becb62-8311-466d-89ca-e8b215d0a700';
const fork = 'ac10e7cc-cdb6-4e29-9b15-7c8d87bdf5bd';
const realm = 'codex_subscription';
const binding = (chain = 'a') => ({ spartan_chain_id: chain.repeat(64), spartan_expected_realm: realm, spartan_binding_mac: 'b'.repeat(64) });
const model = { id: 'codex/gpt-example', provider: 'spartan-route', api: 'openai-responses',
  baseUrl: 'https://route.invalid/v1', input: ['text'], reasoning: true, contextWindow: 32768,
  maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const catalogRow = { id: model.id, owned_by: 'codex', tool_capable: true, max_context: 32768,
  reasoning: true, api_modes: ['chat_completions', 'responses'], reasoning_realm: realm };
const prompt = [{ role: 'user', content: 'Read the file', timestamp: 1 }];

function responseEvents() {
  const reasoning = { id: 'rs_1', type: 'reasoning', summary: [], encrypted_content: 'opaque' };
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
    const body = events.map(data => `data: ${JSON.stringify(data)}\n\n`).join('');
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
  };
}

async function run(stream, messages, received, options = {}) {
  const result = await stream(model, { messages }, {
    apiKey: 'synthetic', sessionId: session, reasoning: 'high', maxRetries: 0,
    fetch: fakeFetch(received), ...options,
  }).result();
  assert.notEqual(result.stopReason, 'error', result.errorMessage);
  return result;
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
  const stream = createResponsesStream(native.streamSimple,
    async () => ({ version: 1, binding: binding() }), 'a'.repeat(64), () => realm);
  for (const efforts of [['low', 'medium', 'max'], ['none', 'low', 'medium']]) {
    const [entry] = normalizeCatalog({ data: [{ ...catalogRow, reasoning_efforts: efforts }] });
    const selected = { ...model, ...entry };
    const received = [];
    const result = await stream(selected, { messages: prompt }, {
      apiKey: 'synthetic', sessionId: session, maxRetries: 0, fetch: fakeFetch(received),
    }).result();
    assert.notEqual(result.stopReason, 'error', result.errorMessage);
    if (efforts.includes('none')) {
      assert.equal(received[0].body.reasoning.effort, 'none');
      assert.equal(entry.thinkingLevelMap.off, 'none');
    } else {
      assert.equal(received[0].body.reasoning, undefined);
      assert.equal(entry.thinkingLevelMap.off, null);
    }
  }
});

test('native Responses preserves tools, stamps opaque reasoning and replays after actual session save/resume', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-native-response-'));
  try {
    const scope = await agentScope(directory);
    const requests = [];
    const gateway = async (action, document) => {
      assert.equal(action, 'binding');
      assert.equal(document.scope, scope);
      assert.ok([session, fork].includes(document.sessionId));
      assert.equal(document.realm, realm);
      return { version: 1, binding: binding(document.sessionId === session ? 'a' : 'c') };
    };
    const stream = createResponsesStream(native.streamSimple, gateway, scope, () => realm);
    let observed = 0;
    const first = await run(stream, prompt, requests, {
      onPayload: payload => ({ ...payload, prompt_cache_key: 'caller-cache-key' }),
      onResponse: () => { observed++; }, onProviderStreamEvent: () => { observed++; },
    });
    assert.equal(first.stopReason, 'toolUse');
    assert.equal(observed, 6);
    assert.match(requests[0].url, /\/v1\/responses$/);
    assert.equal(requests[0].body.reasoning.effort, 'high');
    assert.equal(requests[0].body.spartan_chain_id, binding().spartan_chain_id);
    assert.equal(requests[0].body.prompt_cache_key, 'caller-cache-key');
    const manager = SessionManager.create(directory, join(directory, 'sessions'), { id: session });
    manager.appendMessage(prompt[0]);
    manager.appendMessage(first);
    const call = first.content.find(block => block.type === 'toolCall');
    assert.equal(call.id, 'call_1|fc_1');
    manager.appendMessage({ role: 'toolResult', toolCallId: call.id, toolName: 'read', content: [{ type: 'text', text: 'file contents' }], isError: false, timestamp: 2 });
    const reopened = SessionManager.open(manager.getSessionFile());
    const messages = reopened.buildSessionContext().messages;
    const second = await run(stream, messages, requests);
    assert.equal(second.stopReason, 'toolUse');
    const replay = requests[1].body.input.find(item => item.type === 'reasoning');
    assert.equal(replay.encrypted_content, 'opaque');
    assert.equal(Object.keys(replay).some(key => key.startsWith('_spartan_')), false);
    assert.equal(requests[1].body.input.find(item => item.type === 'function_call').call_id, 'call_1');
    assert.equal(requests[1].body.input.find(item => item.type === 'function_call_output').call_id, 'call_1');
    const file = await readFile(manager.getSessionFile(), 'utf8');
    assert.match(file, /_spartan_chain_id/);
    const forked = SessionManager.forkFrom(manager.getSessionFile(), directory, join(directory, 'sessions'), { id: fork });
    await run(stream, forked.buildSessionContext().messages, requests, { sessionId: forked.getSessionId() });
    assert.equal(requests[2].body.input.some(item => item.type === 'reasoning'), false);
    assert.equal(requests[2].body.input.find(item => item.type === 'function_call').id, undefined);
    assert.equal(requests[2].body.input.find(item => item.type === 'function_call_output').call_id, 'call_1');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('fork, profile scope and missing provenance reset encrypted replay and item IDs', () => {
  const stamped = { type: 'reasoning', encrypted_content: 'opaque', _spartan_chain_id: binding().spartan_chain_id,
    _spartan_reasoning_realm: realm, _spartan_binding_mac: binding().spartan_binding_mac };
  const input = [stamped, { type: 'message', role: 'assistant', id: 'msg_old', content: [] },
    { type: 'function_call', id: 'fc_old', call_id: 'call_1' }, { type: 'function_call_output', call_id: 'call_1', output: 'ok' }];
  for (const foreign of [binding('c'), { ...binding(), spartan_expected_realm: 'openai_api' }]) {
    const result = prepareRequest({ input, previous_response_id: 'foreign', prompt_cache_key: 'explicit' }, foreign, fork);
    assert.equal(result.input.some(item => item.type === 'reasoning'), false);
    assert.equal(result.input[0].id, undefined);
    assert.equal(result.input[1].id, undefined);
    assert.equal(result.input[1].call_id, result.input[2].call_id);
    assert.equal(result.previous_response_id, undefined);
    assert.equal(result.prompt_cache_key, 'explicit');
  }
  assert.equal(prepareRequest({ input: [{ type: 'reasoning', encrypted_content: 'old' }] }, binding(), session).input.length, 0);
  assert.equal(input[1].id, 'msg_old');
});

test('named subagent sessions bind and replay through native Pi save and reopen without renaming', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-named-responses-'));
  try {
    const named = 'subagent.0123456789abcdef';
    const manager = SessionManager.create(directory, join(directory, 'sessions'), { id: named });
    const identities = [];
    const stream = createResponsesStream(native.streamSimple, async (_action, body) => {
      assert.match(body.sessionId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
      identities.push(body.sessionId);
      return { version: 1, binding: binding(body.sessionId === '3bab243f-a0c3-8494-9c22-072bbf68d2f7' ? 'a' : 'c') };
    }, await agentScope(directory), () => realm);
    const requests = [];
    manager.appendMessage(prompt[0]);
    const first = await run(stream, prompt, requests, { sessionId: manager.getSessionId() });
    manager.appendMessage(first);
    const call = first.content.find(item => item.type === 'toolCall');
    manager.appendMessage({ role: 'toolResult', toolCallId: call.id, toolName: 'read',
      content: [{ type: 'text', text: 'synthetic' }], isError: false, timestamp: 2 });
    const reopened = SessionManager.open(manager.getSessionFile());
    assert.equal(reopened.getSessionId(), named);
    await run(stream, reopened.buildSessionContext().messages, requests, { sessionId: reopened.getSessionId() });
    assert.equal(identities.length, 2);
    assert.equal(identities[0], identities[1]);
    assert.equal(identities[0], '3bab243f-a0c3-8494-9c22-072bbf68d2f7');
    assert.equal(requests[0].body.prompt_cache_key, named);
    assert.equal(requests[1].body.input.find(item => item.type === 'reasoning').encrypted_content, 'opaque');
    const forked = SessionManager.forkFrom(manager.getSessionFile(), directory, join(directory, 'sessions'), { id: 'subagent.0123456789abcdee' });
    await run(stream, forked.buildSessionContext().messages, requests, { sessionId: forked.getSessionId() });
    assert.notEqual(identities[2], identities[0]);
    assert.equal(requests[2].body.input.some(item => item.type === 'reasoning'), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('binding failure or invalid session aborts before any provider HTTP request', async () => {
  for (const request of [async () => { throw new Error('synthetic unavailable'); }, async () => ({ version: 1, binding: binding('z') })]) {
    const received = [];
    const stream = createResponsesStream(native.streamSimple, request, 'scope', () => realm);
    const result = await stream(model, { messages: prompt }, { apiKey: 'synthetic', sessionId: session, fetch: fakeFetch(received) }).result();
    assert.equal(result.stopReason, 'error');
    assert.equal(received.length, 0);
  }
  const received = [];
  let calls = 0;
  const stream = createResponsesStream(native.streamSimple, async () => { calls++; return { version: 1, binding: binding() }; }, 'scope', () => realm);
  for (const sessionId of [undefined, '', '../invalid', 'bad\nvalue', 'x'.repeat(257)]) {
    const result = await stream(model, { messages: prompt }, { apiKey: 'synthetic', sessionId, fetch: fakeFetch(received) }).result();
    assert.equal(result.stopReason, 'error');
  }
  assert.equal(calls, 0);
  assert.equal(received.length, 0);
});

test('concurrent native requests retain their own session binding and late terminal encryption', async () => {
  const pending = new Map();
  let ready;
  const requested = new Promise(resolve => { ready = resolve; });
  const stream = createResponsesStream(native.streamSimple, (_action, document) => new Promise(resolve => {
    pending.set(document.sessionId, resolve);
    if (pending.size === 2) ready();
  }), 'a'.repeat(64), () => realm);
  const events = responseEvents();
  delete events[1].item.encrypted_content;
  events.at(-1).response.output[0] = { type: 'reasoning', id: 'rs_1', encrypted_content: 'late-opaque', summary: [] };
  const received = [];
  const first = stream(model, { messages: prompt }, { apiKey: 'synthetic', sessionId: session, fetch: fakeFetch(received, events) }).result();
  const second = stream(model, { messages: prompt }, { apiKey: 'synthetic', sessionId: fork, fetch: fakeFetch(received, events) }).result();
  await requested;
  pending.get(fork)({ version: 1, binding: binding('c') });
  pending.get(session)({ version: 1, binding: binding() });
  for (const [message, chain] of [[await first, 'a'], [await second, 'c']]) {
    assert.notEqual(message.stopReason, 'error', message.errorMessage);
    const item = JSON.parse(message.content.find(block => block.type === 'thinking').thinkingSignature);
    assert.equal(item._spartan_chain_id, chain.repeat(64));
    assert.equal(item.encrypted_content, 'late-opaque');
  }
});

test('agent scope follows canonical directory identity and changes between profiles', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-provider-scope-'));
  try {
    await mkdir(join(directory, 'profile-a'));
    await mkdir(join(directory, 'profile-b'));
    await symlink(join(directory, 'profile-a'), join(directory, 'alias'));
    const first = await agentScope(join(directory, 'profile-a'));
    assert.equal(await agentScope(join(directory, 'alias')), first);
    assert.notEqual(await agentScope(join(directory, 'profile-b')), first);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('cancellation while waiting for binding cannot send an unbound native request', async () => {
  const controller = new AbortController();
  const received = [];
  const stream = createResponsesStream(native.streamSimple, async () => {
    controller.abort();
    return { version: 1, binding: binding() };
  }, 'a'.repeat(64), () => realm);
  const result = await stream(model, { messages: prompt }, {
    apiKey: 'synthetic', sessionId: session, signal: controller.signal, fetch: fakeFetch(received),
  }).result();
  assert.equal(result.stopReason, 'aborted');
  assert.equal(received.length, 0);
});

test('dynamic discovery persists advertised capabilities and refreshes without a stale catalog', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-discovery-'));
  try {
    const config = { baseUrl: model.baseUrl, refreshSeconds: 300, cacheMaxAgeSeconds: 86400 };
    let document = { data: [catalogRow] };
    const discovery = new Discovery(directory, config, async () => document);
    assert.equal((await discovery.get())[0].api, 'openai-responses');
    document = { data: [{ ...catalogRow, api_modes: ['chat_completions'] }] };
    assert.equal((await discovery.get({ force: true }))[0].api, 'openai-completions');
    const reloaded = new Discovery(directory, config);
    assert.equal((await reloaded.get({ network: false }))[0].api, 'openai-completions');
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('real Pi loader registers the provider from isolated private config without startup network', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-provider-loader-'));
  const previous = { offline: process.env.PI_OFFLINE, config: process.env.PI_SPARTAN_ROUTE_CONFIG, agent: process.env.PI_CODING_AGENT_DIR };
  try {
    await mkdir(join(directory, 'agent'));
    const configPath = join(directory, 'config.json');
    await writeFile(configPath, JSON.stringify({ baseUrl: model.baseUrl, refreshSeconds: 300, cacheMaxAgeSeconds: 86400 }));
    await writeFile(join(directory, 'catalog.json'), JSON.stringify({ baseUrl: model.baseUrl, fetchedAt: Date.now(), document: { data: [catalogRow] } }));
    process.env.PI_OFFLINE = '1';
    process.env.PI_SPARTAN_ROUTE_CONFIG = configPath;
    process.env.PI_CODING_AGENT_DIR = join(directory, 'agent');
    const { loadExtensions } = await import(pathToFileURL(resolve(piDir, 'dist/core/extensions/loader.js')).href);
    const path = fileURLToPath(new URL('./extensions/spartan-route-provider.ts', import.meta.url));
    const result = await loadExtensions([path], dirname(path));
    assert.deepEqual(result.errors, []);
    assert.equal(result.extensions.length, 1);
    const provider = result.runtime.pendingProviderRegistrations.find(entry => entry.name === 'spartan-route')?.config;
    assert.ok(result.extensions[0].commands.has('spartan-route-refresh'));
    // Registration is queued by Pi's real loader until a model runtime attaches.
    assert.equal(result.runtime.pendingProviderRegistrations.length, 1);
    assert.equal(provider.api, 'openai-responses');
    assert.equal(provider.models[0].api, 'openai-responses');
    assert.equal(typeof provider.streamSimple, 'function');
  } finally {
    for (const [key, value] of [['PI_OFFLINE', previous.offline], ['PI_SPARTAN_ROUTE_CONFIG', previous.config], ['PI_CODING_AGENT_DIR', previous.agent]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  }
});
