// Disposable native producer/consumer for process-resume tests, no host config.
import assert from 'node:assert/strict';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { piDir, piDependency } from './pi_test_inputs.mjs';
import { createResponsesStream } from './extensions/spartan-route-provider/responses.mjs';
import { SessionFence } from './extensions/spartan-route-provider/session.mjs';

let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw);
assert.match(input.baseURL, /^http:\/\/127\.0\.0\.1:\d+\/v1$/);
const native = await import(pathToFileURL(piDependency('@earendil-works/pi-ai', 'dist/api/openai-responses.js')).href);
const { SessionManager } = await import(pathToFileURL(resolve(piDir, 'dist/core/session-manager.js')).href);
const manager = input.sessionFile ? SessionManager.open(input.sessionFile) :
  SessionManager.create(input.directory, join(input.directory, 'sessions'), { id: 'subagent.0123456789abcdef' });
const fence = new SessionFence('a'.repeat(64), (type, data) => manager.appendCustomEntry(type, data));
fence.activate({ sessionManager: manager });
const stream = createResponsesStream(native.streamSimple, async () => { throw new Error('Unexpected binding RPC'); },
  'a'.repeat(64), () => 'codex_subscription', fence);
const model = { id: 'codex/gpt-6-sol', provider: 'spartan-route', api: 'openai-responses',
  baseUrl: input.baseURL, input: ['text'], reasoning: true, contextWindow: 32768,
  maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
manager.appendMessage({ role: 'user', content: input.sessionFile ? 'Continue after restart' : 'Use answer', timestamp: Date.now() });
async function turn() {
  const result = await stream(model, manager.buildSessionContext(), {
    apiKey: 'secret', sessionId: manager.getSessionId(), maxRetries: 0, reasoning: 'high',
  }).result();
  assert.notEqual(result.stopReason, 'error', result.errorMessage);
  manager.appendMessage(result);
  return result;
}
let result = await turn();
if (!input.sessionFile) {
  assert.equal(result.stopReason, 'toolUse');
  const call = result.content.find(block => block.type === 'toolCall');
  assert.equal(call.name, 'answer');
  const answer = String(6 * 7);
  manager.appendMessage({ role: 'toolResult', toolCallId: call.id, toolName: call.name,
    content: [{ type: 'text', text: answer }], isError: false, timestamp: Date.now() });
  result = await turn();
}
console.log(JSON.stringify({ stopReason: result.stopReason, sessionId: manager.getSessionId(), sessionFile: manager.getSessionFile(),
  opaque: manager.getEntries().filter(entry => entry.message?.role === 'assistant').flatMap(entry => entry.message.content)
    .filter(block => block.type === 'thinking' && JSON.parse(block.thinkingSignature).encrypted_content).length }));
